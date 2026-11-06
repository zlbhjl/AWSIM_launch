"""GP-based exploration shared by every target (docs/exploration_unification_plan.md §3.1).

Three phases: uniform exploration until a boundary appears, then GP-guided
sampling (highest uncertainty or closest to the 0.5 contour), then margin
checking in the safe area; plus exact repeats of given points (focus points,
or points a target's rules extract from earlier results). What a target knows
comes from its :class:`ExplorationSpec`: the GP training rules, which result
rows count as failed runs to recover, and the point rules of target-specific
modes. AWSIM's spec is targets/awsim/exploration.py.

TestCases keep ``meta.source_module = "orchestration.strategy"``: the value is
stored in datasets as provenance, so it stays the same across moves.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from orchestration.seeding import INPUT_STREAM, derive_seed
from contracts.execution import TestCase
from contracts.statistics import StatisticalReport, StatisticalRequest
from evaluation.dkw import DKWService
from evaluation.gp_boundary import (
    GPBoundaryModel,
    fit_gp_boundary_model,
    predict_uncertainty as predict_gp_boundary_uncertainty,
)
from orchestration.final_report import FinalReport, build_final_report
from orchestration.exploration.spec import ExplorationSpec
from orchestration.exploration.settings import (  # noqa: F401  (names kept for importers)
    _RegionPolicyWithVocabulary,
    _load_case_definition_from_config,
    _load_strategy_settings_from_config,
    _with_vocabulary,
)
from runtime.repository.boundary_gap_progress import BoundaryGapProgressRepository
from runtime.repository.consistency_classification import (
    ConsistencyClassificationRepository,
)
from runtime.repository.consistency_dkw_summary import (
    ConsistencyDkwSummaryRepository,
)
from runtime.repository.strategy_dataset import StrategyDatasetRepository

EXTRACTOR_FOCUS_MODES = frozenset({"jama_edge", "ttc_edge", "worst_ttc"})
STATISTICAL_MODES = frozenset({"binomial_ci", "sprt", "ebstop", "dkw", "dkw_fixed"})
GENERIC_SPEC = ExplorationSpec()


class ActiveLearningStrategist:
    def __init__(
        self,
        scenario_name: str,
        config: object,
        *,
        target: str = "awsim",
        num_candidates: int = 10000,
        focus_points: list[dict[str, object]] | None = None,
        run_mode: str = "explore",
        dataset_repository: StrategyDatasetRepository | None = None,
        boundary_gap_progress_repository: BoundaryGapProgressRepository | None = None,
        consistency_classification_repository: ConsistencyClassificationRepository | None = None,
        consistency_dkw_summary_repository: ConsistencyDkwSummaryRepository | None = None,
        boundary_model_service: object | None = None,
        dkw_service: DKWService | None = None,
        max_samples: int | None = None,
        cache_size: int = 4,
        random_seed: int = 42,
        spec: ExplorationSpec | None = None,
    ) -> None:
        # What the target knows (training rules, failed rows, point rules).
        self.spec = spec or GENERIC_SPEC
        if run_mode in STATISTICAL_MODES:
            raise ValueError(
                f"--mode {run_mode} runs on the shared statistical path (orchestration/region_sampling.py), "
                "not in the GP exploration"
            )
        self.target = target
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
        self.dkw_total_delta = float(getattr(self.config, "DKW_TOTAL_DELTA", 0.05))
        self.dkw_target_epsilon = float(getattr(self.config, "DKW_TARGET_EPSILON", 0.15))
        self.consistency_threshold = float(
            getattr(self.config, "CONSISTENCY_THRESHOLD", 0.2)
        )
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
        self.active_bounds = dict(self.param_ranges)
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
            target=self.target,
            case_kind=self.scenario_name,
            input=task_input,
            reason=str(payload.get("reason", "")),
            meta={
                "source_module": "orchestration.strategy",
                "run_mode": self.run_mode,
            },
        )

    def get_random_point(self, index: int) -> dict[str, float]:
        rng = np.random.default_rng(seed=derive_seed(self.random_seed, int(index), INPUT_STREAM))
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
        extractor = self._point_rules().EXTRACTORS.get("boundary_gap")
        if extractor is None:
            return []
        return [dict(point) for point in extractor(df_dataset, self.param_names, self.config)]

    def _extract_verify_consistency_points(
        self,
        df_dataset: pd.DataFrame | None,
    ) -> list[dict[str, object]]:
        extractor = self._point_rules().EXTRACTORS.get("verify_consistency")
        if extractor is None:
            return []
        return [dict(point) for point in extractor(df_dataset, self.param_names, self.config)]

    def _extract_focus_points_for_mode(
        self,
        run_mode: str,
        df_dataset: pd.DataFrame | None,
    ) -> list[dict[str, object]]:
        extractor = self._point_rules().EXTRACTORS.get(run_mode)
        if extractor is None:
            return []
        return [dict(point) for point in extractor(df_dataset, self.param_names, self.config)]

    def _pop_error_recovery_task(
        self,
        df_dataset: pd.DataFrame | None,
    ) -> dict[str, object] | None:
        if df_dataset is None or "loop_num" not in df_dataset.columns:
            return None

        if self.spec.failed_rows is None:
            return None
        loop_num_numeric = pd.to_numeric(df_dataset["loop_num"], errors="coerce")

        error_mask = self.spec.failed_rows(df_dataset)
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
        summary = self._point_rules().summarize_boundary_gap_progress(
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

        consistent_df, stochastic_df = self._point_rules().classify_consistency(
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

    # --- stable numbering of statistical samples ------------------------------
    # (docs/exploration_unification_plan.md G3). Every sample of a statistical
    # run gets a sample number at issue and draws its point from a seed index
    # that does not depend on how many results have arrived; the rules see the
    # run's samples in sample order, contiguous prefix only, one row each.

    def _point_rules(self):
        rules = self.spec.point_rules
        if rules is None:
            raise ValueError(
                f"--mode {self.run_mode} needs point rules from the target (ExplorationSpec.point_rules)"
            )
        return rules

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
        return self._point_rules().save_dataframe_to_csv(df_dataset, str(output_path))

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
        if sync_df.empty:
            return

        max_loop_val = pd.to_numeric(sync_df["loop_num"], errors="coerce").max()
        if pd.isna(max_loop_val):
            return

        max_loop = int(max_loop_val)
        if self.dispatched_task_count < max_loop:
            self.dispatched_task_count = max_loop

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
            training_rules=self.spec.training_rules,
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
