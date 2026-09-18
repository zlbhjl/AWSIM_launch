from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import pandas as pd

from contracts.statistics import BoundsMap, StatisticalReport, StatisticalRequest
from evaluation.ebstop import EBStopService
from contracts.statistical_region import (
    PassthroughStatisticalRegionPolicy,
    StatisticalRegionPolicy,
)
from runtime.repository.statistical_samples import StatisticalSamplesRepository

import point_extractors


@dataclass(slots=True)
class EBStopModeConfig:
    param_names: list[str]
    target: str
    epsilon: float
    value_range: float | tuple[float, float]
    confidence: float = 0.95
    max_samples: int = 2000
    region: str = "custom"
    case_kind: str = "uturn"
    config_module_name: str | None = None


@dataclass(slots=True)
class EBStopModeState:
    bounds: BoundsMap | None = None
    random_index: int = 0
    dispatched_task_count: int = 0


class EBStopModeRunner:
    def __init__(
        self,
        *,
        config: EBStopModeConfig,
        ebstop_service: EBStopService,
        statistical_history_repository: object | None = None,
        statistical_samples_repository: StatisticalSamplesRepository | None = None,
        region_policy: StatisticalRegionPolicy | None = None,
    ) -> None:
        self.config = config
        self.ebstop_service = ebstop_service
        self.statistical_history_repository = statistical_history_repository
        self.statistical_samples_repository = statistical_samples_repository
        self.region_policy = region_policy or PassthroughStatisticalRegionPolicy()

    @classmethod
    def from_runtime_config(
        cls,
        *,
        param_names: Sequence[str],
        target: str,
        epsilon: float,
        value_range: float | tuple[float, float],
        confidence: float,
        max_samples: int,
        region: str,
        ebstop_service: EBStopService,
        statistical_history_repository: object | None = None,
        statistical_samples_repository: StatisticalSamplesRepository | None = None,
        config: object | None = None,
        case_kind: str = "uturn",
        config_module_name: str | None = None,
        region_policy: StatisticalRegionPolicy | None = None,
    ) -> "EBStopModeRunner":
        return cls(
            config=EBStopModeConfig(
                param_names=list(param_names),
                target=str(target),
                epsilon=float(epsilon),
                value_range=value_range,
                confidence=float(confidence),
                max_samples=int(max_samples),
                region=str(region),
                case_kind=str(case_kind),
                config_module_name=(
                    str(config_module_name)
                    if config_module_name is not None
                    else getattr(config, "__name__", None)
                ),
            ),
            ebstop_service=ebstop_service,
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

    def handle(
        self,
        df_dataset: pd.DataFrame | None,
        *,
        state: EBStopModeState,
        get_random_point: Callable[[int], dict[str, object]],
    ) -> dict[str, object]:
        report = self.ebstop_service.evaluate_request(
            df_dataset,
            self._build_request(bounds=state.bounds),
        )
        self._append_history(report, state)
        if report.next_action == "stop":
            self._save_samples(report)
            return {
                "system_command": "stop",
                "reason": (
                    f"EBStop Complete: {self.config.target} "
                    f"estimate={report.estimate:.5f} (n={report.sample_count})"
                ),
            }
        if report.next_action == "stop_max_samples":
            return {
                "system_command": "stop",
                "reason": f"EBStop reached max_samples={self.config.max_samples} unconverged",
            }
        if report.next_action == "error":
            return {
                "system_command": "stop",
                "reason": str(report.diagnostics.get("message", "EBStop evaluation error")),
            }

        payload, state.random_index, state.dispatched_task_count = (
            self.issue_region_aware_random_task(
                reason=f"EBSTOP: Sampling ({report.sample_count + 1})",
                get_random_point=get_random_point,
                random_index=state.random_index,
                base_index=self.max_loop_num(df_dataset),
                dispatched_task_count=state.dispatched_task_count,
            )
        )
        return payload

    def _build_request(self, *, bounds: BoundsMap | None) -> StatisticalRequest:
        return StatisticalRequest(
            method="ebstop",
            metric=self.config.target,
            bounds=bounds,
            confidence=self.config.confidence,
            options={
                "epsilon": self.config.epsilon,
                "value_range": self.config.value_range,
                "region": self.config.region,
                "reason_pattern": r"EBSTOP:",
                "max_samples": self.config.max_samples,
            },
        )

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

    def _append_history(self, report: object, state: EBStopModeState) -> None:
        if not isinstance(report, StatisticalReport):
            return
        if report.diagnostics.get("status") != "success":
            return
        repository = self.statistical_history_repository
        if repository is None or not hasattr(repository, "append_ebstop_record"):
            return
        repository.append_ebstop_record(
            {
                "task_count": state.dispatched_task_count,
                "metric": report.metric,
                "sample_size": report.sample_count,
                "estimate": report.estimate,
                "lower_bound": report.interval[0] if report.interval else None,
                "upper_bound": report.interval[1] if report.interval else None,
                "epsilon": report.diagnostics.get("epsilon"),
                "value_range": report.diagnostics.get("value_range"),
                "mean": report.diagnostics.get("mean"),
                "std": report.diagnostics.get("std"),
            }
        )

    def _save_samples(self, report: StatisticalReport) -> None:
        repository = self.statistical_samples_repository
        if repository is None:
            return
        filtered_df = report.diagnostics.get("filtered_df")
        if isinstance(filtered_df, pd.DataFrame):
            repository.save_ebstop_samples(filtered_df)


__all__ = [
    "EBStopModeConfig",
    "EBStopModeRunner",
    "EBStopModeState",
]
