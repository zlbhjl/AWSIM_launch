from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from contracts.statistics import BoundsMap, StatisticalReport, StatisticalRequest
from evaluation.binomial_ci import BinomialCIService
from runtime.repository.statistical_samples import StatisticalSamplesRepository
from targets.awsim.theory import build_theory_metrics, default_theory_columns

import point_extractors


@dataclass(slots=True)
class BinomialModeConfig:
    param_names: list[str]
    target: str = "c_collision"
    method: str = "wilson"
    confidence: float = 0.95
    target_width: float = 0.02
    min_samples: int = 100
    max_samples: int = 2000
    region: str = "custom"
    case_kind: str = "uturn"
    config_module_name: str | None = None


@dataclass(slots=True)
class BinomialModeState:
    bounds: BoundsMap | None = None
    random_index: int = 0
    dispatched_task_count: int = 0


class BinomialModeRunner:
    def __init__(
        self,
        *,
        config: BinomialModeConfig,
        binomial_ci_service: BinomialCIService,
        statistical_history_repository: object | None = None,
        statistical_samples_repository: StatisticalSamplesRepository | None = None,
    ) -> None:
        self.config = config
        self.binomial_ci_service = binomial_ci_service
        self.statistical_history_repository = statistical_history_repository
        self.statistical_samples_repository = statistical_samples_repository

    @classmethod
    def from_runtime_config(
        cls,
        *,
        param_names: Sequence[str],
        target: str,
        method: str,
        confidence: float,
        target_width: float,
        min_samples: int,
        max_samples: int,
        region: str,
        binomial_ci_service: BinomialCIService,
        statistical_history_repository: object | None = None,
        statistical_samples_repository: StatisticalSamplesRepository | None = None,
        config: object | None = None,
        case_kind: str = "uturn",
        config_module_name: str | None = None,
    ) -> "BinomialModeRunner":
        return cls(
            config=BinomialModeConfig(
                param_names=list(param_names),
                target=str(target),
                method=str(method),
                confidence=float(confidence),
                target_width=float(target_width),
                min_samples=int(min_samples),
                max_samples=int(max_samples),
                region=str(region),
                case_kind=str(case_kind),
                config_module_name=(
                    str(config_module_name)
                    if config_module_name is not None
                    else getattr(config, "__name__", None)
                ),
            ),
            binomial_ci_service=binomial_ci_service,
            statistical_history_repository=statistical_history_repository,
            statistical_samples_repository=statistical_samples_repository,
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
        state: BinomialModeState,
        get_random_point: Callable[[int], dict[str, object]],
    ) -> dict[str, object]:
        report = self.binomial_ci_service.evaluate_request(
            df_dataset,
            self._build_request(bounds=state.bounds),
        )
        self._append_history(report, state)
        if report.next_action == "stop":
            self._save_samples(report)
            return {
                "system_command": "stop",
                "reason": (
                    f"Binomial CI Complete: {self.config.target} "
                    f"CI width {report.interval_width:.5f}"
                ),
            }
        if report.next_action == "stop_max_samples":
            return {
                "system_command": "stop",
                "reason": (
                    f"Binomial CI reached max_samples={self.config.max_samples}"
                ),
            }
        if report.next_action == "error":
            return {
                "system_command": "stop",
                "reason": str(
                    report.diagnostics.get("message", "Binomial CI evaluation error")
                ),
            }

        payload, state.random_index, state.dispatched_task_count = (
            self.issue_region_aware_random_task(
                reason=f"BINOMIAL_CI: Sampling ({report.sample_count + 1})",
                get_random_point=get_random_point,
                random_index=state.random_index,
                base_index=self.max_loop_num(df_dataset),
                dispatched_task_count=state.dispatched_task_count,
            )
        )
        return payload

    def _build_request(self, *, bounds: BoundsMap | None) -> StatisticalRequest:
        return StatisticalRequest(
            method="binomial_ci",
            metric=self.config.target,
            bounds=bounds,
            confidence=self.config.confidence,
            target_width=self.config.target_width,
            options={
                "method": self.config.method,
                "region": self.config.region,
                "reason_pattern": r"BINOMIAL_CI:",
                "min_samples": self.config.min_samples,
                "max_samples": self.config.max_samples,
            },
        )

    def _build_region_filter_frame(self, point: Mapping[str, object]) -> pd.DataFrame:
        row: dict[str, object] = dict(point)
        row.update(default_theory_columns())
        row.update(
            build_theory_metrics(
                case_kind=self.config.case_kind,
                values=row,
                config_module_name=self.config.config_module_name,
            )
        )
        row.setdefault("c_collision", 0)
        row.setdefault("min_ttc", 99.9)
        row.setdefault("min_distance", 99.9)
        row.setdefault("min_ttb", 99.9)
        return pd.DataFrame([row])

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

    def _append_history(self, report: object, state: BinomialModeState) -> None:
        if not isinstance(report, StatisticalReport):
            return
        if report.diagnostics.get("status") != "success":
            return
        if report.interval is None:
            return
        repository = self.statistical_history_repository
        if repository is None or not hasattr(repository, "append_binomial_ci_record"):
            return
        repository.append_binomial_ci_record(
            {
                "task_count": state.dispatched_task_count,
                "metric": report.metric,
                "method": report.diagnostics.get("method", self.config.method),
                "confidence_level": self.config.confidence,
                "sample_size": report.sample_count,
                "success_count": report.diagnostics.get("success_count"),
                "estimate": report.estimate,
                "lower_bound": report.interval[0],
                "upper_bound": report.interval[1],
                "interval_width": report.interval_width,
                "target_width": self.config.target_width,
            }
        )

    def _save_samples(self, report: StatisticalReport) -> None:
        repository = self.statistical_samples_repository
        if repository is None:
            return
        filtered_df = report.diagnostics.get("filtered_df")
        if isinstance(filtered_df, pd.DataFrame):
            repository.save_binomial_ci_samples(filtered_df)


__all__ = [
    "BinomialModeConfig",
    "BinomialModeRunner",
    "BinomialModeState",
]
