from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from contracts.execution import TestCase
from contracts.statistics import StatisticalRequest
from evaluation.binomial_ci import BinomialCIService
from evaluation.dkw import DKWService
from evaluation.gp_boundary import (
    GPBoundaryModel,
    fit_gp_boundary_model,
    predict_uncertainty as predict_gp_boundary_uncertainty,
)
from orchestration.binomial_mode import BinomialModeRunner, BinomialModeState
from orchestration.dkw_mode import DKWModeRunner, DKWModeState
from orchestration.final_report import FinalReport, build_final_report
from contracts.statistical_region import StatisticalRegionPolicy
import point_extractors
from runtime.repository.boundary_gap_progress import BoundaryGapProgressRepository
from runtime.repository.consistency_classification import (
    ConsistencyClassificationRepository,
)
from runtime.repository.consistency_dkw_summary import (
    ConsistencyDkwSummaryRepository,
)
from runtime.repository.statistical_history import StatisticalHistoryRepository
from runtime.repository.statistical_samples import StatisticalSamplesRepository
from runtime.repository.strategy_dataset import StrategyDatasetRepository

EXTRACTOR_FOCUS_MODES = frozenset({"jama_edge", "ttc_edge", "worst_ttc"})


@dataclass(frozen=True)
class FixedCaseStrategyConfig:
    fixture: str
    case_id: str | None = None
    case_kind: str = "uturn"
    target: str = "awsim"
    reason: str = "manual_orchestrator_run"
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParameterCaseStrategyConfig:
    params: Mapping[str, object]
    case_id: str | None = None
    case_kind: str = "uturn"
    target: str = "awsim"
    reason: str = "manual_orchestrator_run"
    tags: list[str] = field(default_factory=list)
    scenario_type: str | None = None
    simulation_output_dir: str | None = None
    expected_trace_path: str | None = None
    local_loop_num: int | None = None


class FixedCaseStrategy:
    def __init__(self, config: FixedCaseStrategyConfig):
        self.config = config
        self._consumed = False

    def next_test_case(self) -> TestCase | None:
        if self._consumed:
            return None
        self._consumed = True

        fixture_path = Path(self.config.fixture).expanduser().resolve()
        case_id = self.config.case_id or fixture_path.stem
        return TestCase(
            case_id=case_id,
            target=self.config.target,
            case_kind=self.config.case_kind,
            input={"fixture_path": str(fixture_path)},
            tags=list(self.config.tags),
            reason=self.config.reason,
            meta={"source_module": "orchestration.strategy"},
        )


class ParameterCaseStrategy:
    def __init__(self, config: ParameterCaseStrategyConfig):
        self.config = config
        self._consumed = False

    def next_test_case(self) -> TestCase | None:
        if self._consumed:
            return None
        self._consumed = True

        case_id = self.config.case_id or f"{self.config.case_kind}_direct"
        input_payload = dict(self.config.params)
        if self.config.target == "awsim":
            input_payload["scenario_type"] = self.config.scenario_type or self.config.case_kind
            if self.config.simulation_output_dir is not None:
                input_payload["output_dir"] = self.config.simulation_output_dir
            if self.config.expected_trace_path is not None:
                input_payload["expected_trace_path"] = self.config.expected_trace_path
            if self.config.local_loop_num is not None:
                input_payload["local_loop_num"] = self.config.local_loop_num

        return TestCase(
            case_id=case_id,
            target=self.config.target,
            case_kind=self.config.case_kind,
            input=input_payload,
            tags=list(self.config.tags),
            reason=self.config.reason,
            meta={"source_module": "orchestration.strategy"},
        )


def _load_case_definition_from_config(config: object, scenario_name: str) -> dict[str, object]:
    explicit_loader = getattr(config, "get_case_definition", None)
    if callable(explicit_loader):
        loaded = dict(explicit_loader())
        return {
            "scenario_type": loaded.get("scenario_type", scenario_name),
            "repeat_count": int(loaded.get("repeat_count", 0)),
            "timeout_sec": float(loaded.get("timeout_sec", 200.0)),
            "target_npcs": list(loaded.get("target_npcs", [])),
            "param_ranges": dict(loaded.get("param_ranges", {})),
            "fixed_params": dict(loaded.get("fixed_params", {})),
        }

    return {
        "scenario_type": getattr(config, "SCENARIO_TYPE", scenario_name),
        "repeat_count": int(getattr(config, "REPEAT_COUNT", 0)),
        "timeout_sec": float(getattr(config, "TIMEOUT_SEC", 200.0)),
        "target_npcs": list(getattr(config, "TARGET_NPCS", [])),
        "param_ranges": dict(getattr(config, "PARAM_RANGES", {})),
        "fixed_params": dict(getattr(config, "FIXED_PARAMS", {})),
    }


def _load_strategy_settings_from_config(config: object) -> dict[str, object]:
    explicit_loader = getattr(config, "get_strategy_settings", None)
    if callable(explicit_loader):
        return dict(explicit_loader())

    return {
        "target_priorities": list(getattr(config, "TARGET_PRIORITIES", [])),
        "initial_exploration_limit": getattr(config, "INITIAL_EXPLORATION_LIMIT", 100),
        "min_samples": getattr(config, "MIN_SAMPLES", 500),
        "max_samples": getattr(config, "MAX_SAMPLES", 2000),
        "stability_reference_points": getattr(config, "STABILITY_REFERENCE_POINTS", 2000),
        "stability_history_length": getattr(config, "STABILITY_HISTORY_LENGTH", 50),
        "stability_hysteresis": getattr(config, "STABILITY_HYSTERESIS", (0.40, 0.60)),
        "stability_shift_threshold": getattr(config, "STABILITY_SHIFT_THRESHOLD", 0.01),
        "stability_required_streak": getattr(config, "STABILITY_REQUIRED_STREAK", 3),
        "step2_max_exploration": getattr(config, "STEP2_MAX_EXPLORATION", 500),
        "margin_range": getattr(config, "MARGIN_RANGE", (0.3, 0.48)),
        "margin_max_uncertainty": getattr(config, "MARGIN_MAX_UNCERTAINTY", 0.05),
        "focus_points": list(getattr(config, "FOCUS_POINTS", [])),
        "focus_noise": getattr(config, "FOCUS_NOISE", 0.05),
        "dkw_target_metric": getattr(config, "DKW_TARGET_METRIC", "min_ttc"),
        "dkw_target_metrics": list(
            getattr(config, "DKW_TARGET_METRICS", ["min_ttc", "min_distance"])
        ),
        "binomial_ci_target": getattr(config, "BINOMIAL_CI_TARGET", "c_collision"),
        "binomial_ci_method": getattr(config, "BINOMIAL_CI_METHOD", "wilson"),
        "binomial_ci_confidence": getattr(config, "BINOMIAL_CI_CONFIDENCE", 0.95),
        "binomial_ci_target_width": getattr(config, "BINOMIAL_CI_TARGET_WIDTH", 0.02),
        "binomial_ci_min_samples": getattr(config, "BINOMIAL_CI_MIN_SAMPLES", 100),
    }


class ActiveLearningStrategist:
    def __init__(
        self,
        scenario_name: str,
        config: object,
        *,
        num_candidates: int = 10000,
        focus_points: list[dict[str, object]] | None = None,
        run_mode: str = "explore",
        dataset_repository: StrategyDatasetRepository | None = None,
        statistical_history_repository: StatisticalHistoryRepository | None = None,
        statistical_samples_repository: StatisticalSamplesRepository | None = None,
        boundary_gap_progress_repository: BoundaryGapProgressRepository | None = None,
        consistency_classification_repository: ConsistencyClassificationRepository | None = None,
        consistency_dkw_summary_repository: ConsistencyDkwSummaryRepository | None = None,
        boundary_model_service: object | None = None,
        dkw_service: DKWService | None = None,
        binomial_ci_service: BinomialCIService | None = None,
        dkw_bounds: Mapping[str, tuple[float, float]] | None = None,
        dkw_region: str = "custom",
        dkw_pure_smc: bool = False,
        dkw_simultaneous: bool = False,
        max_samples: int | None = None,
        binomial_target: str | None = None,
        binomial_method: str | None = None,
        binomial_confidence: float | None = None,
        binomial_target_width: float | None = None,
        binomial_min_samples: int | None = None,
        cache_size: int = 4,
        random_seed: int = 42,
        statistical_region_policy: StatisticalRegionPolicy | None = None,
        dkw_minimum_value: float | None = None,
    ) -> None:
        self.scenario_name = scenario_name
        self.config = config
        self.num_candidates = num_candidates
        self.run_mode = run_mode
        self.case_definition = _load_case_definition_from_config(config, scenario_name)
        self.strategy_settings = _load_strategy_settings_from_config(config)
        self.param_ranges = dict(self.case_definition["param_ranges"])
        self.param_names = list(self.param_ranges.keys())
        self.dataset_repository = dataset_repository or StrategyDatasetRepository(scenario_name)
        dataset_paths = getattr(self.dataset_repository, "paths", None)
        default_traces_dir = (
            dataset_paths.traces_dir
            if dataset_paths is not None and hasattr(dataset_paths, "traces_dir")
            else "~/simulation_traces"
        )
        self.traces_dir = Path(default_traces_dir).expanduser()
        self.statistical_history_repository = (
            statistical_history_repository
            or StatisticalHistoryRepository(
                scenario_name=scenario_name,
                traces_dir=self.traces_dir,
            )
        )
        self.statistical_samples_repository = (
            statistical_samples_repository
            or StatisticalSamplesRepository(
                scenario_name=scenario_name,
                traces_dir=self.traces_dir,
            )
        )
        self.boundary_gap_progress_repository = (
            boundary_gap_progress_repository
            or BoundaryGapProgressRepository(
                scenario_name=scenario_name,
                traces_dir=self.traces_dir,
            )
        )
        self.consistency_classification_repository = (
            consistency_classification_repository
            or ConsistencyClassificationRepository(
                scenario_name=scenario_name,
                traces_dir=self.traces_dir,
            )
        )
        self.consistency_dkw_summary_repository = (
            consistency_dkw_summary_repository
            or ConsistencyDkwSummaryRepository(
                scenario_name=scenario_name,
                traces_dir=self.traces_dir,
            )
        )
        self.boundary_model_service = boundary_model_service
        self.boundary_model: GPBoundaryModel | None = None
        self.dkw_service = dkw_service or DKWService(feature_names=list(self.param_names))
        self.binomial_ci_service = binomial_ci_service or BinomialCIService()

        self.target_priorities = list(self.strategy_settings.get("target_priorities", []))
        self.INITIAL_EXPLORATION_LIMIT = int(
            self.strategy_settings.get("initial_exploration_limit", 100)
        )
        self.MIN_SAMPLES = int(self.strategy_settings.get("min_samples", 500))
        self.MAX_SAMPLES = int(self.strategy_settings.get("max_samples", 2000))
        self.max_samples = int(max_samples) if max_samples is not None else self.MAX_SAMPLES
        self.STABILITY_REFERENCE_POINTS = int(
            self.strategy_settings.get("stability_reference_points", 2000)
        )
        self.STABILITY_HISTORY_LENGTH = int(
            self.strategy_settings.get("stability_history_length", 50)
        )
        self.STABILITY_HYSTERESIS = tuple(
            self.strategy_settings.get("stability_hysteresis", (0.40, 0.60))
        )
        self.STABILITY_SHIFT_THRESHOLD = float(
            self.strategy_settings.get("stability_shift_threshold", 0.01)
        )
        self.STABILITY_REQUIRED_STREAK = int(
            self.strategy_settings.get("stability_required_streak", 3)
        )
        self.STEP2_MAX_EXPLORATION = int(
            self.strategy_settings.get("step2_max_exploration", 500)
        )
        self.MARGIN_RANGE = tuple(self.strategy_settings.get("margin_range", (0.3, 0.48)))
        self.MARGIN_MAX_UNCERTAINTY = float(
            self.strategy_settings.get("margin_max_uncertainty", 0.05)
        )
        if self.run_mode in {"boundary_gap", "verify_consistency"} | EXTRACTOR_FOCUS_MODES:
            self.FOCUS_POINTS = []
        else:
            self.FOCUS_POINTS = list(
                focus_points
                if focus_points is not None
                else self.strategy_settings.get("focus_points", [])
            )
        self.FOCUS_NOISE = float(self.strategy_settings.get("focus_noise", 0.05))
        self.dkw_target_metric = str(
            self.strategy_settings.get("dkw_target_metric", "min_ttc")
        )
        self.dkw_target_metrics = [
            str(metric)
            for metric in self.strategy_settings.get(
                "dkw_target_metrics",
                ["min_ttc", "min_distance"],
            )
        ]
        self.binomial_target = str(
            binomial_target
            if binomial_target is not None
            else self.strategy_settings.get("binomial_ci_target", "c_collision")
        )
        self.binomial_method = str(
            binomial_method
            if binomial_method is not None
            else self.strategy_settings.get("binomial_ci_method", "wilson")
        )
        self.binomial_confidence = float(
            binomial_confidence
            if binomial_confidence is not None
            else self.strategy_settings.get("binomial_ci_confidence", 0.95)
        )
        self.binomial_target_width = float(
            binomial_target_width
            if binomial_target_width is not None
            else self.strategy_settings.get("binomial_ci_target_width", 0.02)
        )
        self.binomial_min_samples = int(
            binomial_min_samples
            if binomial_min_samples is not None
            else self.strategy_settings.get("binomial_ci_min_samples", 100)
        )
        self.dkw_bounds = (
            {
                str(name): (float(raw_bounds[0]), float(raw_bounds[1]))
                for name, raw_bounds in dict(dkw_bounds).items()
            }
            if dkw_bounds is not None
            else None
        )
        self.dkw_region = str(dkw_region)
        self.dkw_pure_smc = bool(dkw_pure_smc)
        self.dkw_simultaneous = bool(dkw_simultaneous)
        self.dkw_base_samples = int(getattr(self.config, "DKW_BASE_SAMPLES", 50))
        self.dkw_total_delta = float(getattr(self.config, "DKW_TOTAL_DELTA", 0.05))
        self.dkw_target_epsilon = float(getattr(self.config, "DKW_TARGET_EPSILON", 0.15))
        self.consistency_threshold = float(
            getattr(self.config, "CONSISTENCY_THRESHOLD", 0.2)
        )
        self.dkw_mode = DKWModeRunner.from_runtime_config(
            param_names=self.param_names,
            target_metric=self.dkw_target_metric,
            target_metrics=self.dkw_target_metrics,
            total_delta=self.dkw_total_delta,
            target_epsilon=self.dkw_target_epsilon,
            base_samples=self.dkw_base_samples,
            region=self.dkw_region,
            pure_smc=self.dkw_pure_smc,
            simultaneous=self.dkw_simultaneous,
            max_samples=self.max_samples,
            dkw_service=self.dkw_service,
            statistical_history_repository=self.statistical_history_repository,
            statistical_samples_repository=self.statistical_samples_repository,
            config=self.config,
            case_kind=self.scenario_name,
            config_module_name=getattr(self.config, "__name__", None),
            region_policy=statistical_region_policy,
            minimum_value=dkw_minimum_value,
        )
        self.dkw_mode_state = DKWModeState(bounds=self.dkw_bounds)
        self.binomial_mode = BinomialModeRunner.from_runtime_config(
            param_names=self.param_names,
            target=self.binomial_target,
            method=self.binomial_method,
            confidence=self.binomial_confidence,
            target_width=self.binomial_target_width,
            min_samples=self.binomial_min_samples,
            max_samples=self.max_samples,
            region=self.dkw_region,
            binomial_ci_service=self.binomial_ci_service,
            statistical_history_repository=self.statistical_history_repository,
            statistical_samples_repository=self.statistical_samples_repository,
            config=self.config,
            case_kind=self.scenario_name,
            config_module_name=getattr(self.config, "__name__", None),
            region_policy=statistical_region_policy,
        )
        self.binomial_mode_state = BinomialModeState(bounds=self.dkw_bounds)
        self.random_seed = random_seed
        self.rng = np.random.default_rng(random_seed)
        df_init = self.dataset_repository.load_dataset()
        if df_init is not None and "loop_num" in df_init.columns:
            max_loop_val = pd.to_numeric(df_init["loop_num"], errors="coerce").max()
            if pd.notna(max_loop_val):
                self.last_recovered_loop = int(max_loop_val)
            else:
                self.last_recovered_loop = 0
        else:
            self.last_recovered_loop = 0
        if self.run_mode in {"dkw", "dkw_fixed"}:
            self.dkw_mode_state.bounds = self.dkw_mode.initialize_bounds(
                df_init,
                self.dkw_mode_state.bounds,
            )
            self.dkw_bounds = self.dkw_mode_state.bounds
        elif self.run_mode == "binomial_ci":
            self.binomial_mode_state.bounds = self.binomial_mode.initialize_bounds(
                df_init,
                self.binomial_mode_state.bounds,
            )
            self.dkw_bounds = self.binomial_mode_state.bounds

        self.active_bounds = dict(self.param_ranges)
        if self.run_mode in {"dkw", "dkw_fixed", "binomial_ci"} and self.dkw_bounds:
            for name in self.param_names:
                if name in self.dkw_bounds:
                    self.active_bounds[name] = self.dkw_bounds[name]
        self.reference_points = self.generate_candidate_points(num=self.STABILITY_REFERENCE_POINTS)
        self.stability_history: list[np.ndarray] = []
        self.stability_streak = 0
        self.step2_exploration_count = 0
        self.current_phase = "STEP3" if self.run_mode == "margin" else "STEP1"
        self.dispatched_task_count = 0
        self.task_cache: list[dict[str, object]] = []
        self.CACHE_SIZE = max(int(cache_size), 1)
        self.focus_exact_test_count = 0
        self.boundary_gap_initial_summary: dict[str, object] | None = None
        self.boundary_gap_cycle = 1
        self._latest_final_report: FinalReport | None = None
        self._sync_strategy_from_dkw_mode_state()
        if self.run_mode == "binomial_ci":
            self._sync_strategy_from_binomial_mode_state()

        if self.run_mode == "boundary_gap":
            self.FOCUS_POINTS = self._extract_boundary_gap_points(df_init)
            self.boundary_gap_initial_summary = self._summarize_boundary_gap_progress(df_init)
            self._append_boundary_gap_progress(
                self.boundary_gap_initial_summary,
                snapshot_kind="initial",
            )
        elif self.run_mode == "verify_consistency":
            self.FOCUS_POINTS = self._extract_verify_consistency_points(df_init)
        elif self.run_mode in EXTRACTOR_FOCUS_MODES:
            extracted_points = self._extract_focus_points_for_mode(self.run_mode, df_init)
            if extracted_points:
                self.FOCUS_POINTS = extracted_points

    def next_test_case(self) -> TestCase | None:
        payload = self.decide_next_target()
        if payload.get("system_command") == "stop":
            self._capture_final_report(payload)
            return None

        task_input = dict(self.case_definition.get("fixed_params", {}))
        task_input.update({k: v for k, v in payload.items() if k != "reason"})
        task_input.setdefault(
            "scenario_type",
            str(self.case_definition.get("scenario_type", self.scenario_name)),
        )
        case_index = max(self.dispatched_task_count, 1)
        return TestCase(
            case_id=f"{self.scenario_name}_strategy_{case_index}",
            target="awsim",
            case_kind=self.scenario_name,
            input=task_input,
            reason=str(payload.get("reason", "")),
            meta={
                "source_module": "orchestration.strategy",
                "run_mode": self.run_mode,
            },
        )

    def get_random_point(self, index: int) -> dict[str, float]:
        rng = np.random.default_rng(seed=self.random_seed + int(index))
        return {
            name: float(rng.uniform(self.active_bounds[name][0], self.active_bounds[name][1]))
            for name in self.param_names
        }

    def get_best_target(self, df: pd.DataFrame | None) -> str | None:
        if df is None or len(df) == 0:
            return None
        for target in self.target_priorities:
            if target in df.columns:
                valid = df[df[target].isin([0, 1])]
                if 1 in valid[target].values and 0 in valid[target].values:
                    return target
        return None

    def decide_next_target(self) -> dict[str, object]:
        if self.task_cache:
            self.dispatched_task_count += 1
            return self.task_cache.pop(0)

        df_dataset = self.dataset_repository.load_dataset()
        recovery_task = self._pop_error_recovery_task(df_dataset)
        if recovery_task is not None:
            self.dispatched_task_count += 1
            return recovery_task
        self._sync_dispatched_task_count(df_dataset)
        best_target = self.get_best_target(df_dataset)
        num_violations = (
            int((df_dataset[best_target] == 1).sum())
            if (df_dataset is not None and best_target is not None)
            else 0
        )
        current_idx = self.dispatched_task_count

        if self.run_mode == "binomial_ci":
            payload = self._handle_binomial_ci_mode(df_dataset)
            if payload.get("system_command") == "stop":
                self._capture_final_report(payload)
            return payload
        if self.run_mode == "dkw":
            payload = self._handle_dkw_mode(df_dataset)
            if payload.get("system_command") == "stop":
                self._capture_final_report(payload)
            return payload
        if self.run_mode == "dkw_fixed":
            payload = self._handle_dkw_fixed_mode(df_dataset)
            if payload.get("system_command") == "stop":
                self._capture_final_report(payload)
            return payload
        if self.run_mode in EXTRACTOR_FOCUS_MODES and not self.FOCUS_POINTS:
            return self._stop_payload(
                reason=f"No target points found for mode '{self.run_mode}'",
            )
        if self.run_mode == "verify_consistency" and not self.FOCUS_POINTS:
            return self._stop_payload(
                reason="No target points found for mode 'verify_consistency'",
            )
        if self.run_mode == "boundary_gap" and not self.FOCUS_POINTS:
            refresh = self._refresh_boundary_gap_targets()
            if refresh["continue"]:
                return self.decide_next_target()
            summary = refresh["summary"]
            remaining = int(summary.get("candidate_cells", 0))
            return self._stop_payload(
                reason=f"boundary_gap 検証完了: 残り要追加セル {remaining}",
                target="Boundary Gap",
            )

        if self.FOCUS_POINTS:
            exact_target = self._build_focus_exact_task()
            if exact_target is not None:
                self.dispatched_task_count += 1
                return exact_target
            if self.run_mode == "boundary_gap":
                refresh = self._refresh_boundary_gap_targets()
                if refresh["continue"]:
                    return self.decide_next_target()
                summary = refresh["summary"]
                remaining = int(summary.get("candidate_cells", 0))
                unresolved = sum(
                    1
                    for row in summary.get("target_cells", [])
                    if row.get("status") == "needs_more_data"
                )
                return self._stop_payload(
                    reason=(
                        "boundary_gap 検証完了: "
                        f"残り要追加セル {remaining} (最後のターゲットで未解消 {unresolved})"
                    ),
                    target="Boundary Gap",
                )
            if self.run_mode == "verify_consistency":
                return self._handle_verify_consistency_mode(df_dataset)

        if self.current_phase == "STEP1" and (
            current_idx < self.INITIAL_EXPLORATION_LIMIT or num_violations == 0
        ):
            if self.FOCUS_POINTS:
                candidates = self.generate_candidate_points()
                best_point = candidates[self.rng.integers(len(candidates))]
                result = {
                    name: float(best_point[i]) for i, name in enumerate(self.param_names)
                }
                result["reason"] = f"STEP1: Focus Neighborhood Search (V:{num_violations})"
            else:
                result = {
                    **self.get_random_point(current_idx),
                    "reason": f"STEP1: Global Search (V:{num_violations})",
                }
            self.dispatched_task_count += 1
            return result

        if self.current_phase == "STEP3" and num_violations == 0:
            result = {
                **self.get_random_point(current_idx),
                "reason": "STEP3 Fallback: Global Search (No Violations)",
            }
            self.dispatched_task_count += 1
            return result

        if current_idx >= self.MAX_SAMPLES:
            return self._stop_payload(reason="Max Samples Reached")

        if best_target is None:
            result = {
                **self.get_random_point(current_idx),
                "reason": "Fallback (No Target)",
            }
            self.dispatched_task_count += 1
            return result

        self._train_boundary_model(df_dataset, best_target)
        candidates = self.generate_candidate_points()
        mean, std = self._predict_boundary_uncertainty(candidates)
        if mean is None or std is None:
            result = {
                **self.get_random_point(current_idx),
                "reason": "Fallback (Error)",
            }
            self.dispatched_task_count += 1
            return result

        if self.current_phase == "STEP1":
            self.current_phase = "STEP2"

        if self.current_phase == "STEP2":
            self.step2_exploration_count += 1
            is_stable, _shift_rate = self._evaluate_boundary_stability()
            if is_stable or self.step2_exploration_count >= self.STEP2_MAX_EXPLORATION:
                self.current_phase = "STEP3"

        best_indices: list[int]
        reason: str
        safe_idx = np.where(mean <= 0.5)[0]

        if self.current_phase == "STEP3":
            if len(safe_idx) > 0:
                max_std = float(np.max(std[safe_idx]))
                if max_std < self.MARGIN_MAX_UNCERTAINTY:
                    return self._stop_payload(
                        reason="Safe Area Verification Complete",
                        target=best_target or self._default_report_target(),
                    )

                sorted_idx = np.argsort(std[safe_idx])[::-1]
                top_candidates = safe_idx[sorted_idx[: self.CACHE_SIZE * 5]]
                best_indices = self._sample_candidate_indices(top_candidates)
                if len(best_indices) < self.CACHE_SIZE:
                    best_indices = self._fill_candidate_pool(best_indices, std)
                reason = f"STEP3: Safe Area Cleanup (σ={max_std:.4f})"
            else:
                sorted_idx = np.argsort(std)[::-1]
                top_candidates = sorted_idx[: self.CACHE_SIZE * 5]
                best_indices = self._sample_candidate_indices(top_candidates)
                reason = "STEP3: Backup Search"
        else:
            if self.rng.random() < 0.3:
                sorted_idx = np.argsort(std)[::-1]
                top_candidates = sorted_idx[: self.CACHE_SIZE * 5]
                best_indices = self._sample_candidate_indices(top_candidates)
                reason = "STEP2: Exploration (Max σ)"
            else:
                sorted_idx = np.argsort(np.abs(mean - 0.5))
                top_candidates = sorted_idx[: self.CACHE_SIZE * 5]
                best_indices = self._sample_candidate_indices(top_candidates)
                reason = "STEP2: Boundary 0.5"

        for candidate_index in best_indices:
            best_point = candidates[candidate_index]
            result = {
                name: float(best_point[i]) for i, name in enumerate(self.param_names)
            }
            result["reason"] = f"[FOCUS] {reason}" if self.FOCUS_POINTS else reason
            self.task_cache.append(result)

        self.dispatched_task_count += 1
        return self.task_cache.pop(0)

    def generate_candidate_points(self, num: int | None = None) -> np.ndarray:
        num_points = num if num is not None else self.num_candidates
        if not self.param_names:
            return np.empty((0, 0))
        if not self.FOCUS_POINTS:
            cols = [
                self.rng.uniform(
                    self.active_bounds[name][0],
                    self.active_bounds[name][1],
                    num_points,
                )
                for name in self.param_names
            ]
            return np.column_stack(cols)

        cols = []
        num_per_point = max(num_points // len(self.FOCUS_POINTS), 0)
        for name in self.param_names:
            param_range = self.param_ranges[name][1] - self.param_ranges[name][0]
            std_dev = param_range * self.FOCUS_NOISE
            param_candidates: list[float] = []
            for point in self.FOCUS_POINTS:
                center = point.get(name, sum(self.param_ranges[name]) / 2.0)
                samples = self.rng.normal(loc=center, scale=std_dev, size=num_per_point)
                param_candidates.extend(float(sample) for sample in samples)

            while len(param_candidates) < num_points:
                param_candidates.append(
                    float(
                        self.rng.uniform(
                            self.param_ranges[name][0],
                            self.param_ranges[name][1],
                        )
                    )
                )

            clipped = np.clip(
                param_candidates[:num_points],
                self.param_ranges[name][0],
                self.param_ranges[name][1],
            )
            cols.append(clipped)
        return np.column_stack(cols)

    def _build_focus_exact_task(self) -> dict[str, object] | None:
        exact_repeats = int(getattr(self.config, "FOCUS_EXACT_REPEATS", 10))
        total_exact_samples = len(self.FOCUS_POINTS) * exact_repeats
        if self.focus_exact_test_count >= total_exact_samples:
            return None

        point_idx = (self.focus_exact_test_count // exact_repeats) % len(self.FOCUS_POINTS)
        repeat_idx = (self.focus_exact_test_count % exact_repeats) + 1
        exact_point = self.FOCUS_POINTS[point_idx]
        result = {
            name: exact_point.get(name, sum(self.param_ranges[name]) / 2.0)
            for name in self.param_names
        }
        if self.run_mode == "boundary_gap":
            result["reason"] = (
                f"[BOUNDARY_GAP] Cycle {self.boundary_gap_cycle} "
                f"Point {point_idx+1}/{len(self.FOCUS_POINTS)} "
                f"(Repeat {repeat_idx}/{exact_repeats})"
            )
        elif self.run_mode == "verify_consistency":
            result["reason"] = (
                f"[CONSISTENCY] Exact Point {point_idx+1}/{len(self.FOCUS_POINTS)} "
                f"(Repeat {repeat_idx}/{exact_repeats})"
            )
        else:
            result["reason"] = (
                f"[FOCUS] Exact Point {point_idx+1}/{len(self.FOCUS_POINTS)} "
                f"(Repeat {repeat_idx}/{exact_repeats})"
            )
        self.focus_exact_test_count += 1
        return result

    def _extract_boundary_gap_points(
        self,
        df_dataset: pd.DataFrame | None,
    ) -> list[dict[str, object]]:
        extractor = point_extractors.EXTRACTORS.get("boundary_gap")
        if extractor is None:
            return []
        return [dict(point) for point in extractor(df_dataset, self.param_names, self.config)]

    def _extract_verify_consistency_points(
        self,
        df_dataset: pd.DataFrame | None,
    ) -> list[dict[str, object]]:
        extractor = point_extractors.EXTRACTORS.get("verify_consistency")
        if extractor is None:
            return []
        return [dict(point) for point in extractor(df_dataset, self.param_names, self.config)]

    def _extract_focus_points_for_mode(
        self,
        run_mode: str,
        df_dataset: pd.DataFrame | None,
    ) -> list[dict[str, object]]:
        extractor = point_extractors.EXTRACTORS.get(run_mode)
        if extractor is None:
            return []
        return [dict(point) for point in extractor(df_dataset, self.param_names, self.config)]

    def _pop_error_recovery_task(
        self,
        df_dataset: pd.DataFrame | None,
    ) -> dict[str, object] | None:
        if df_dataset is None or "loop_num" not in df_dataset.columns:
            return None

        min_ttc_numeric = pd.to_numeric(
            df_dataset.get("min_ttc", pd.Series(np.nan, index=df_dataset.index)),
            errors="coerce",
        )
        c_collision_numeric = pd.to_numeric(
            df_dataset.get("c_collision", pd.Series(np.nan, index=df_dataset.index)),
            errors="coerce",
        )
        loop_num_numeric = pd.to_numeric(df_dataset["loop_num"], errors="coerce")

        error_mask = (min_ttc_numeric == -1) | (c_collision_numeric == -1)
        new_errors = df_dataset[error_mask & (loop_num_numeric > self.last_recovered_loop)]
        if new_errors.empty:
            return None

        self.task_cache.extend(self._build_error_recovery_tasks(new_errors))
        max_loop_val = pd.to_numeric(new_errors["loop_num"], errors="coerce").max()
        if pd.notna(max_loop_val):
            self.last_recovered_loop = int(max_loop_val)
        if not self.task_cache:
            return None
        return self.task_cache.pop(0)

    def _build_error_recovery_tasks(
        self,
        error_rows: pd.DataFrame,
    ) -> list[dict[str, object]]:
        log_prefix = "[FOCUS] " if self.FOCUS_POINTS else ""
        recovery_points: list[dict[str, object]] = []
        for _, err_row in error_rows.iterrows():
            shifted_point: dict[str, object] = {}
            for name in self.param_names:
                param_min, param_max = self.param_ranges[name]
                noise = self.rng.normal(0.0, (param_max - param_min) * 0.03)
                try:
                    base_value = float(err_row[name])
                except (TypeError, ValueError, KeyError):
                    base_value = (param_min + param_max) / 2.0
                shifted_point[name] = float(np.clip(base_value + noise, param_min, param_max))
            loop_num = pd.to_numeric(err_row.get("loop_num"), errors="coerce")
            loop_num_label = int(loop_num) if pd.notna(loop_num) else 0
            shifted_point["reason"] = (
                f"{log_prefix}Error Recovery (Shifted from Loop {loop_num_label})"
            )
            recovery_points.append(shifted_point)
        return recovery_points

    def _summarize_boundary_gap_progress(
        self,
        df_dataset: pd.DataFrame | None,
    ) -> dict[str, object]:
        summary = point_extractors.summarize_boundary_gap_progress(
            df_dataset,
            self.param_names,
            self.config,
            target_points=self.FOCUS_POINTS,
        )
        return dict(summary)

    def _refresh_boundary_gap_targets(self) -> dict[str, object]:
        df_dataset = self.dataset_repository.load_dataset()
        summary = self._summarize_boundary_gap_progress(df_dataset)
        self._append_boundary_gap_progress(summary, snapshot_kind="cycle_complete")
        next_points = self._extract_boundary_gap_points(df_dataset)
        if next_points:
            self.FOCUS_POINTS = next_points
            self.focus_exact_test_count = 0
            self.boundary_gap_cycle += 1
            self._append_boundary_gap_progress(
                self._summarize_boundary_gap_progress(df_dataset),
                snapshot_kind="initial",
            )
            return {
                "continue": True,
                "summary": summary,
            }
        self.FOCUS_POINTS = []
        return {
            "continue": False,
            "summary": summary,
        }

    def _handle_verify_consistency_mode(
        self,
        df_dataset: pd.DataFrame | None,
    ) -> dict[str, object]:
        consistency_df = self._filter_reason_rows(df_dataset, reason_tag="[CONSISTENCY]")
        if consistency_df.empty:
            return self._stop_payload(
                reason="No consistency data found",
                target="一貫性検証 (Verify Consistency)",
            )

        consistent_df, stochastic_df = point_extractors.classify_consistency(
            consistency_df,
            self.param_names,
            target_metric=self.dkw_target_metric,
            threshold=self.consistency_threshold,
            min_repeats=2,
        )
        self._save_consistency_csv(consistent_df, suffix="consistent_risk")
        self._save_consistency_csv(stochastic_df, suffix="stochastic_risk")
        self._append_consistency_dkw_summary(
            classification="consistent",
            report=self._evaluate_consistency_dkw(consistent_df),
        )
        self._append_consistency_dkw_summary(
            classification="stochastic",
            report=self._evaluate_consistency_dkw(stochastic_df),
        )
        return self._stop_payload(
            reason="Consistency Verification Complete",
            target="一貫性検証 (Verify Consistency)",
        )

    @property
    def latest_final_report(self) -> FinalReport | None:
        return self._latest_final_report

    def _capture_final_report(self, payload: Mapping[str, object]) -> None:
        reason = str(payload.get("reason", ""))
        if not reason:
            return
        report_target = str(payload.get("report_target") or self._default_report_target())
        report_num_samples = int(
            payload.get("report_num_samples", self.dispatched_task_count)
        )
        self._latest_final_report = build_final_report(
            num_samples=report_num_samples,
            target=report_target,
            reason=reason,
        )

    def _stop_payload(
        self,
        *,
        reason: str,
        target: str | None = None,
        num_samples: int | None = None,
    ) -> dict[str, object]:
        report_payload = {
            "system_command": "stop",
            "reason": str(reason),
            "report_target": str(target or self._default_report_target()),
            "report_num_samples": int(
                self.dispatched_task_count if num_samples is None else num_samples
            ),
        }
        self._capture_final_report(report_payload)
        return {
            "system_command": "stop",
            "reason": str(reason),
        }

    def _default_report_target(self) -> str:
        if self.run_mode == "boundary_gap":
            return "Boundary Gap"
        if self.run_mode == "verify_consistency":
            return "一貫性検証 (Verify Consistency)"
        if self.run_mode == "dkw_fixed":
            return "DKW Fixed"
        if self.run_mode == "binomial_ci":
            return "Binomial CI"
        if self.run_mode == "dkw":
            if self.dkw_simultaneous:
                return "SMC (DKW Simultaneous)"
            return "SMC (DKW)"
        return self.run_mode

    def _filter_reason_rows(
        self,
        df_dataset: pd.DataFrame | None,
        *,
        reason_tag: str,
    ) -> pd.DataFrame:
        if df_dataset is None or df_dataset.empty or "reason" not in df_dataset.columns:
            return pd.DataFrame()
        reason_series = df_dataset["reason"].fillna("").astype(str)
        return df_dataset[reason_series.str.contains(reason_tag, regex=False, na=False)].copy()

    def _save_consistency_csv(
        self,
        df_dataset: pd.DataFrame | None,
        *,
        suffix: str,
    ) -> bool:
        if suffix == "consistent_risk":
            return self.consistency_classification_repository.save_consistent(df_dataset)
        if suffix == "stochastic_risk":
            return self.consistency_classification_repository.save_stochastic(df_dataset)
        output_path = self.traces_dir / f"{self.scenario_name}_{suffix}.csv"
        return point_extractors.save_dataframe_to_csv(df_dataset, str(output_path))

    def _evaluate_consistency_dkw(
        self,
        df_dataset: pd.DataFrame | None,
    ) -> StatisticalReport:
        return self.dkw_service.evaluate_request(
            df_dataset,
            StatisticalRequest(
                method="dkw",
                metric=self.dkw_target_metric,
                bounds=None,
                confidence=1.0 - self.dkw_total_delta,
                target_width=self.dkw_target_epsilon,
                options={
                    "q": 0.05,
                    "region": "custom",
                    "use_kde_weighting": False,
                    "epsilon": self.dkw_target_epsilon,
                },
            ),
        )

    def _append_consistency_dkw_summary(
        self,
        *,
        classification: str,
        report: StatisticalReport,
    ) -> None:
        self.consistency_dkw_summary_repository.append_report(
            classification=classification,
            report=report,
            confidence_level=1.0 - self.dkw_total_delta,
            target_epsilon=self.dkw_target_epsilon,
        )

    def _evaluate_boundary_stability(self) -> tuple[bool, float]:
        mean, _std = self._predict_boundary_uncertainty(self.reference_points)
        if mean is None:
            return False, 0.0

        states = np.full(mean.shape, -1)
        states[mean < self.STABILITY_HYSTERESIS[0]] = 0
        states[mean > self.STABILITY_HYSTERESIS[1]] = 1
        self.stability_history.append(states)
        if len(self.stability_history) > self.STABILITY_HISTORY_LENGTH:
            self.stability_history.pop(0)
        if len(self.stability_history) < self.STABILITY_HISTORY_LENGTH:
            return False, 0.0

        oldest = self.stability_history[0]
        newest = self.stability_history[-1]
        flips = np.sum(((oldest == 0) & (newest == 1)) | ((oldest == 1) & (newest == 0)))
        shift_rate = float(flips) / float(self.STABILITY_REFERENCE_POINTS)
        if shift_rate < self.STABILITY_SHIFT_THRESHOLD:
            self.stability_streak += 1
        else:
            self.stability_streak = 0
        is_stable = self.stability_streak >= self.STABILITY_REQUIRED_STREAK
        return is_stable, shift_rate

    def _sample_candidate_indices(self, candidate_indices: np.ndarray) -> list[int]:
        if len(candidate_indices) == 0:
            return []
        sample_size = min(len(candidate_indices), self.CACHE_SIZE)
        sampled = self.rng.choice(candidate_indices, size=sample_size, replace=False)
        return [int(index) for index in np.atleast_1d(sampled)]

    def _fill_candidate_pool(
        self,
        current_indices: list[int],
        std: np.ndarray,
    ) -> list[int]:
        filled = list(current_indices)
        for index in np.argsort(std)[::-1]:
            idx = int(index)
            if idx not in filled:
                filled.append(idx)
            if len(filled) >= self.CACHE_SIZE:
                break
        return filled

    def _sync_dispatched_task_count(self, df_dataset: pd.DataFrame | None) -> None:
        if df_dataset is None or "loop_num" not in df_dataset.columns:
            return
        sync_df = df_dataset
        if self.run_mode == "dkw_fixed" and self.dkw_pure_smc:
            if "reason" not in df_dataset.columns:
                sync_df = pd.DataFrame()
            else:
                reason_series = df_dataset["reason"].fillna("").astype(str)
                sync_df = df_dataset[reason_series.str.contains("SMC", na=False)]
        if sync_df.empty:
            return

        max_loop_val = pd.to_numeric(sync_df["loop_num"], errors="coerce").max()
        if pd.isna(max_loop_val):
            return

        max_loop = int(max_loop_val)
        if self.dispatched_task_count < max_loop:
            self.dispatched_task_count = max_loop

    def _sync_dkw_mode_state_from_strategy(self) -> None:
        self.dkw_mode_state.bounds = self.dkw_bounds
        if self.dkw_mode_state.dispatched_task_count < self.dispatched_task_count:
            self.dkw_mode_state.dispatched_task_count = self.dispatched_task_count

    def _sync_strategy_from_dkw_mode_state(self) -> None:
        self.dkw_bounds = self.dkw_mode_state.bounds
        self.dkw_stage = self.dkw_mode_state.stage
        self.dkw_random_index = self.dkw_mode_state.random_index
        self.dispatched_task_count = max(
            self.dispatched_task_count,
            self.dkw_mode_state.dispatched_task_count,
        )

    def _sync_binomial_mode_state_from_strategy(self) -> None:
        self.binomial_mode_state.bounds = self.dkw_bounds
        if self.binomial_mode_state.dispatched_task_count < self.dispatched_task_count:
            self.binomial_mode_state.dispatched_task_count = self.dispatched_task_count

    def _sync_strategy_from_binomial_mode_state(self) -> None:
        self.dkw_bounds = self.binomial_mode_state.bounds
        self.binomial_random_index = self.binomial_mode_state.random_index
        self.dispatched_task_count = max(
            self.dispatched_task_count,
            self.binomial_mode_state.dispatched_task_count,
        )

    def _handle_binomial_ci_mode(self, df_dataset: pd.DataFrame | None) -> dict[str, object]:
        self._sync_binomial_mode_state_from_strategy()
        payload = self.binomial_mode.handle(
            df_dataset,
            state=self.binomial_mode_state,
            get_random_point=self.get_random_point,
        )
        self._sync_strategy_from_binomial_mode_state()
        return payload

    def _handle_dkw_mode(self, df_dataset: pd.DataFrame | None) -> dict[str, object]:
        self._sync_dkw_mode_state_from_strategy()
        payload = self.dkw_mode.handle_sequential(
            df_dataset,
            state=self.dkw_mode_state,
            get_random_point=self.get_random_point,
        )
        self._sync_strategy_from_dkw_mode_state()
        return payload

    def _handle_dkw_fixed_mode(self, df_dataset: pd.DataFrame | None) -> dict[str, object]:
        self._sync_dkw_mode_state_from_strategy()
        payload = self.dkw_mode.handle_fixed(
            df_dataset,
            state=self.dkw_mode_state,
            get_random_point=self.get_random_point,
        )
        self._sync_strategy_from_dkw_mode_state()
        return payload

    def _issue_random_task(
        self,
        *,
        reason: str,
        index: int | None = None,
        advance_dkw_index: bool = False,
    ) -> dict[str, object]:
        point = self.get_random_point(self.dispatched_task_count if index is None else index)
        point["reason"] = reason
        self.dispatched_task_count += 1
        if advance_dkw_index:
            self.dkw_random_index += 1
        return point

    def _append_boundary_gap_progress(
        self,
        summary: Mapping[str, object],
        *,
        snapshot_kind: str,
    ) -> None:
        if not isinstance(summary, Mapping):
            return
        self.boundary_gap_progress_repository.append_snapshot(
            cycle=self.boundary_gap_cycle,
            summary=summary,
            snapshot_kind=snapshot_kind,
        )

    def _train_boundary_model(
        self,
        df_dataset: pd.DataFrame | None,
        target_column: str,
    ) -> bool:
        service = self.boundary_model_service
        if service is not None:
            train = getattr(service, "train", None)
            if callable(train):
                trained = bool(train(df_dataset, target_column=target_column))
                self.boundary_model = getattr(service, "boundary_model", None) if trained else None
                return trained

        self.boundary_model = fit_gp_boundary_model(
            df_dataset,
            target_column=target_column,
            feature_names=self.param_names,
        )
        return self.boundary_model is not None

    def _predict_boundary_uncertainty(
        self,
        candidates: np.ndarray,
    ) -> tuple[np.ndarray | None, np.ndarray | None]:
        service = self.boundary_model_service
        if service is not None:
            predict = getattr(service, "predict_uncertainty", None)
            if callable(predict):
                return predict(candidates)

        if self.boundary_model is None:
            return None, None
        return predict_gp_boundary_uncertainty(self.boundary_model, candidates)
