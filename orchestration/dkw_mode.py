from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import pandas as pd

from contracts.statistics import BoundsMap, StatisticalReport, StatisticalRequest
from evaluation.dkw import DKWService
from contracts.statistical_region import (
    PassthroughStatisticalRegionPolicy,
    StatisticalRegionPolicy,
)
from runtime.repository.statistical_samples import StatisticalSamplesRepository

import point_extractors


@dataclass(slots=True)
class DKWModeConfig:
    param_names: list[str]
    target_metric: str
    target_metrics: list[str]
    total_delta: float = 0.05
    target_epsilon: float = 0.15
    base_samples: int = 50
    region: str = "custom"
    pure_smc: bool = False
    simultaneous: bool = False
    max_samples: int = 2000
    case_kind: str = "uturn"
    config_module_name: str | None = None
    minimum_value: float | None = None


@dataclass(slots=True)
class DKWModeState:
    bounds: BoundsMap | None = None
    stage: int = 1
    random_index: int = 0
    dispatched_task_count: int = 0


class DKWModeRunner:
    def __init__(
        self,
        *,
        config: DKWModeConfig,
        dkw_service: DKWService,
        statistical_history_repository: object | None = None,
        statistical_samples_repository: StatisticalSamplesRepository | None = None,
        region_policy: StatisticalRegionPolicy | None = None,
    ) -> None:
        self.config = config
        self.dkw_service = dkw_service
        self.statistical_history_repository = statistical_history_repository
        self.statistical_samples_repository = statistical_samples_repository
        self.region_policy = region_policy or PassthroughStatisticalRegionPolicy()

    @classmethod
    def from_runtime_config(
        cls,
        *,
        param_names: Sequence[str],
        target_metric: str,
        target_metrics: Sequence[str],
        total_delta: float,
        target_epsilon: float,
        base_samples: int,
        region: str,
        pure_smc: bool,
        simultaneous: bool,
        max_samples: int,
        dkw_service: DKWService,
        statistical_history_repository: object | None = None,
        statistical_samples_repository: StatisticalSamplesRepository | None = None,
        config: object | None = None,
        case_kind: str = "uturn",
        config_module_name: str | None = None,
        region_policy: StatisticalRegionPolicy | None = None,
        minimum_value: float | None = None,
    ) -> "DKWModeRunner":
        return cls(
            config=DKWModeConfig(
                param_names=list(param_names),
                target_metric=str(target_metric),
                target_metrics=[str(metric) for metric in target_metrics],
                total_delta=float(total_delta),
                target_epsilon=float(target_epsilon),
                base_samples=int(base_samples),
                region=str(region),
                pure_smc=bool(pure_smc),
                simultaneous=bool(simultaneous),
                max_samples=int(max_samples),
                case_kind=str(case_kind),
                config_module_name=(
                    str(config_module_name)
                    if config_module_name is not None
                    else getattr(config, "__name__", None)
                ),
                minimum_value=(
                    float(minimum_value) if minimum_value is not None else None
                ),
            ),
            dkw_service=dkw_service,
            statistical_history_repository=statistical_history_repository,
            statistical_samples_repository=statistical_samples_repository,
            region_policy=region_policy,
        )

    def initialize_bounds(
        self,
        df_dataset: pd.DataFrame | None,
        current_bounds: BoundsMap | None,
    ) -> BoundsMap | None:
        if self.config.region == "custom" or current_bounds:
            return current_bounds
        if df_dataset is None or df_dataset.empty:
            return current_bounds
        try:
            filtered = point_extractors.filter_by_region_and_bounds(
                df_dataset,
                region=self.config.region,
            )
        except Exception:
            return current_bounds
        if filtered is None or filtered.empty:
            return current_bounds

        computed_bounds: BoundsMap = {}
        for name in self.config.param_names:
            if name not in filtered.columns:
                continue
            values = pd.to_numeric(filtered[name], errors="coerce").dropna()
            if values.empty:
                continue
            computed_bounds[name] = (float(values.min()), float(values.max()))
        return computed_bounds or current_bounds

    def max_loop_num(self, df_dataset: pd.DataFrame | None) -> int:
        if df_dataset is None or "loop_num" not in df_dataset.columns:
            return 0
        max_loop_val = pd.to_numeric(df_dataset["loop_num"], errors="coerce").max()
        if pd.isna(max_loop_val):
            return 0
        return int(max_loop_val)

    def issue_region_aware_random_task(
        self,
        *,
        reason: str,
        get_random_point: Callable[[int], dict[str, object]],
        random_index: int,
        base_index: int,
        dispatched_task_count: int,
    ) -> tuple[dict[str, object], int, int]:
        next_random_index = int(random_index)
        next_dispatched_task_count = int(dispatched_task_count)
        while True:
            point = get_random_point(base_index + next_random_index)
            next_random_index += 1
            if not self._point_satisfies_region(point):
                continue
            point["reason"] = reason
            next_dispatched_task_count += 1
            return point, next_random_index, next_dispatched_task_count

    def handle_sequential(
        self,
        df_dataset: pd.DataFrame | None,
        *,
        state: DKWModeState,
        get_random_point: Callable[[int], dict[str, object]],
    ) -> dict[str, object]:
        dkw_dataset = self._select_dataset(df_dataset)
        request_confidence = 1.0 - (self.config.total_delta / (2**state.stage))
        target_samples = self.config.base_samples * (state.stage**2)

        if self.config.simultaneous:
            summary = self.dkw_service.evaluate_request_multiple_summary(
                dkw_dataset,
                metrics=self.config.target_metrics,
                request=self._build_request(
                    metric=(
                        self.config.target_metrics[0]
                        if self.config.target_metrics
                        else self.config.target_metric
                    ),
                    confidence=request_confidence,
                    use_kde_weighting=not self.config.pure_smc,
                    stage_target_samples=target_samples,
                    bounds=state.bounds,
                ),
            )
            self._append_history_from_summary(summary, state)
            if summary.get("next_action") == "stop":
                self._save_samples_from_summary(summary)
                return {
                    "system_command": "stop",
                    "reason": "SMC Simultaneous Verification Complete",
                }
            if summary.get("next_action") == "error":
                return {
                    "system_command": "stop",
                    "reason": "DKW simultaneous evaluation error",
                }
            if summary.get("next_action") == "advance_stage":
                state.stage += 1
        else:
            report = self.dkw_service.evaluate_request(
                dkw_dataset,
                self._build_request(
                    metric=self.config.target_metric,
                    confidence=request_confidence,
                    use_kde_weighting=not self.config.pure_smc,
                    stage_target_samples=target_samples,
                    bounds=state.bounds,
                ),
            )
            self._append_history(report, state)
            if report.next_action == "stop":
                self._save_samples_from_report(report)
                return {
                    "system_command": "stop",
                    "reason": "SMC Verification Complete",
                }
            if report.next_action == "error":
                return {
                    "system_command": "stop",
                    "reason": str(report.diagnostics.get("message", "DKW evaluation error")),
                }
            if report.next_action == "advance_stage":
                state.stage += 1

        if state.dispatched_task_count >= self.config.max_samples:
            return {
                "system_command": "stop",
                "reason": (
                    "DKW sequential sampling reached max_samples="
                    f"{self.config.max_samples} without converging"
                ),
            }

        base_index = (
            len(dkw_dataset)
            if self.config.pure_smc and dkw_dataset is not None
            else self.max_loop_num(df_dataset)
        )
        payload, state.random_index, state.dispatched_task_count = (
            self.issue_region_aware_random_task(
                reason=f"SMC: Sequential-DKW Sampling (Stage {state.stage})",
                get_random_point=get_random_point,
                random_index=state.random_index,
                base_index=base_index,
                dispatched_task_count=state.dispatched_task_count,
            )
        )
        return payload

    def handle_fixed(
        self,
        df_dataset: pd.DataFrame | None,
        *,
        state: DKWModeState,
        get_random_point: Callable[[int], dict[str, object]],
    ) -> dict[str, object]:
        if state.dispatched_task_count < self.config.max_samples:
            payload, state.random_index, state.dispatched_task_count = (
                self.issue_region_aware_random_task(
                    reason=(
                        f"SMC: Fixed Sampling "
                        f"({state.dispatched_task_count + 1}/{self.config.max_samples})"
                    ),
                    get_random_point=get_random_point,
                    random_index=state.random_index,
                    base_index=self.max_loop_num(df_dataset),
                    dispatched_task_count=state.dispatched_task_count,
                )
            )
            return payload

        dkw_dataset = self._select_dataset(df_dataset)
        if self.config.simultaneous:
            summary = self.dkw_service.evaluate_request_multiple_summary(
                dkw_dataset,
                metrics=self.config.target_metrics,
                request=self._build_request(
                    metric=(
                        self.config.target_metrics[0]
                        if self.config.target_metrics
                        else self.config.target_metric
                    ),
                    confidence=1.0 - self.config.total_delta,
                    use_kde_weighting=False,
                    bounds=state.bounds,
                ),
            )
            self._append_history_from_summary(summary, state)
            if summary.get("next_action") == "stop":
                self._save_samples_from_summary(summary)
                return {
                    "system_command": "stop",
                    "reason": (
                        "Fixed Sampling + DKW Complete "
                        f"({len(summary.get('reports', {}))} metrics)"
                    ),
                }
        else:
            report = self.dkw_service.evaluate_request(
                dkw_dataset,
                self._build_request(
                    metric=self.config.target_metric,
                    confidence=1.0 - self.config.total_delta,
                    use_kde_weighting=False,
                    bounds=state.bounds,
                ),
            )
            self._append_history(report, state)
            if report.next_action == "stop":
                self._save_samples_from_report(report)
                return {
                    "system_command": "stop",
                    "reason": "Fixed Sampling + DKW Complete",
                }
        return {"system_command": "stop", "reason": "DKW evaluation failed"}

    def _build_request(
        self,
        *,
        metric: str,
        confidence: float,
        use_kde_weighting: bool,
        bounds: BoundsMap | None,
        stage_target_samples: int | None = None,
    ) -> StatisticalRequest:
        return StatisticalRequest(
            method="dkw",
            metric=metric,
            bounds=bounds,
            confidence=confidence,
            target_width=self.config.target_epsilon,
            options={
                "q": 0.05,
                "region": self.config.region,
                "use_kde_weighting": use_kde_weighting,
                "epsilon": self.config.target_epsilon,
                "stage_target_samples": stage_target_samples,
                "minimum_value": self.config.minimum_value,
            },
        )

    def _select_dataset(self, df_dataset: pd.DataFrame | None) -> pd.DataFrame | None:
        if df_dataset is None or not self.config.pure_smc:
            return df_dataset
        if "reason" not in df_dataset.columns:
            return pd.DataFrame()
        reason_series = df_dataset["reason"].fillna("").astype(str)
        return df_dataset[reason_series.str.contains("SMC", na=False)]

    def _build_region_filter_frame(self, point: Mapping[str, object]) -> pd.DataFrame:
        return self.region_policy.build_filter_frame(point)

    def _point_satisfies_region(self, point: Mapping[str, object]) -> bool:
        if self.config.region == "custom":
            return True
        try:
            filtered = point_extractors.filter_by_region_and_bounds(
                self._build_region_filter_frame(point),
                region=self.config.region,
            )
        except Exception:
            return False
        return filtered is not None and not filtered.empty

    def _append_history(self, report: object, state: DKWModeState) -> None:
        if not isinstance(report, StatisticalReport):
            return
        if report.diagnostics.get("status") != "success":
            return
        if report.interval is None:
            return
        repository = self.statistical_history_repository
        if repository is None or not hasattr(repository, "append_dkw_record"):
            return
        repository.append_dkw_record(
            {
                "stage": state.stage,
                "task_count": state.dispatched_task_count,
                "metric": report.metric,
                "ess": report.sample_count,
                "estimate": report.estimate,
                "lower_bound": report.interval[0],
                "upper_bound": report.interval[1],
                "interval_width": report.interval_width,
                "target_epsilon": self.config.target_epsilon,
            }
        )

    def _append_history_from_summary(
        self,
        summary: Mapping[str, object],
        state: DKWModeState,
    ) -> None:
        reports = summary.get("reports")
        if not isinstance(reports, Mapping):
            return
        repository = self.statistical_history_repository
        if repository is None or not hasattr(repository, "append_dkw_records"):
            return
        history_records: list[dict[str, object]] = []
        for report in reports.values():
            if not isinstance(report, StatisticalReport):
                continue
            if report.diagnostics.get("status") != "success" or report.interval is None:
                continue
            history_records.append(
                {
                    "stage": state.stage,
                    "task_count": state.dispatched_task_count,
                    "metric": report.metric,
                    "ess": report.sample_count,
                    "estimate": report.estimate,
                    "lower_bound": report.interval[0],
                    "upper_bound": report.interval[1],
                    "interval_width": report.interval_width,
                    "target_epsilon": self.config.target_epsilon,
                }
            )
        if history_records:
            repository.append_dkw_records(history_records)

    def _save_samples_from_report(self, report: StatisticalReport) -> None:
        repository = self.statistical_samples_repository
        if repository is None:
            return
        filtered_df = report.diagnostics.get("filtered_df")
        if isinstance(filtered_df, pd.DataFrame):
            repository.save_dkw_samples(filtered_df)

    def _save_samples_from_summary(self, summary: Mapping[str, object]) -> None:
        reports = summary.get("reports")
        if not isinstance(reports, Mapping):
            return
        for report in reports.values():
            if not isinstance(report, StatisticalReport):
                continue
            filtered_df = report.diagnostics.get("filtered_df")
            if isinstance(filtered_df, pd.DataFrame) and not filtered_df.empty:
                repository = self.statistical_samples_repository
                if repository is not None:
                    repository.save_dkw_samples(filtered_df)
                return


__all__ = [
    "DKWModeConfig",
    "DKWModeRunner",
    "DKWModeState",
]
