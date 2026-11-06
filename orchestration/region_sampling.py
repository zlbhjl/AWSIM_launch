"""Statistical modes that sample a region of the input space (docs/statistics_single_path_plan.md).

Targets whose inputs are a box of ranges (a settings module with PARAM_RANGES,
like AWSIM and any target using the GP exploration) run their statistical
modes here: ``binomial_ci``, ``sprt``, ``ebstop``, ``dkw`` and ``dkw_fixed``.
Each sample is drawn uniformly from the ranges; with a named region
(``--dkw-region``) the ranges come from the earlier results inside the region,
and drawn points outside it are discarded. The GP exploration takes no part:
it only leaves earlier results in the dataset, which a named region takes its
ranges from and which DKW without ``--dkw-pure-smc`` reuses with KDE weights.

The mechanisms are those of the other statistical strategies: samples are
numbered at issue and judged on the ordered contiguous prefix, failed samples
are retried by the queue, and values outside the target's metric declaration
stop the run without an interval. The decisions, the history and sample CSVs
and the stop reasons are those of the former per-method runners
(binomial_mode.py, sprt_mode.py, ebstop_mode.py) and of the statistical part
of the GP exploration, which this module replaces; DKW keeps its stage engine
(orchestration/dkw_mode.py), shared with the fixed-parameter DKW.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from contracts.execution import TestCase
from contracts.statistics import BoundsMap, StatisticalReport, StatisticalRequest
from evaluation.binomial_ci import BinomialCIService
from evaluation.dkw import DKWService
from evaluation.ebstop import EBStopService
from evaluation.region_filter import check_region_name, filter_rows
from evaluation.sprt import SPRTService
from orchestration.dkw_mode import WAIT_COMMAND, DKWModeRunner, DKWModeState
from orchestration.exploration.settings import (
    _load_case_definition_from_config,
    _load_strategy_settings_from_config,
    _with_vocabulary,
)
from orchestration.final_report import FinalReport, build_final_report
from orchestration.seeding import INPUT_STREAM, derive_seed

REGION_SAMPLING_MODES = frozenset({"binomial_ci", "sprt", "ebstop", "dkw", "dkw_fixed"})

# Kept as provenance in datasets: the value the GP exploration's statistical modes wrote.
SOURCE_MODULE = "orchestration.strategy"


def region_bounds(
    dataset: pd.DataFrame | None,
    *,
    region: str,
    vocabulary: object | None,
    param_names: list[str],
    current: BoundsMap | None,
) -> BoundsMap | None:
    """The ranges to sample: the given ones, else those of the earlier results inside a named region."""
    if region == "custom" or current:
        return current
    if dataset is None or dataset.empty:
        return current
    try:
        inside = filter_rows(dataset, region=region, vocabulary=vocabulary)
    except Exception:
        return current
    if inside is None or inside.empty:
        return current
    bounds: BoundsMap = {}
    for name in param_names:
        if name not in inside.columns:
            continue
        values = pd.to_numeric(inside[name], errors="coerce").dropna()
        if not values.empty:
            bounds[name] = (float(values.min()), float(values.max()))
    return bounds or current


class RegionSampler:
    """Uniform draws over the ranges; draw ``k`` uses its own seed, and points outside the region are skipped.

    Every draw, kept or skipped, advances the draw number, so a run continued
    from its records (``draw_base``) draws the same points as an uninterrupted one.
    """

    def __init__(self, *, ranges: Mapping[str, tuple[float, float]], seed: int, region: str,
                 region_policy: object) -> None:
        self.ranges = {name: (float(low), float(high)) for name, (low, high) in ranges.items()}
        self.seed = int(seed)
        self.region = str(region)
        self.region_policy = region_policy

    def point(self, draw: int) -> dict[str, float]:
        rng = np.random.default_rng(seed=derive_seed(self.seed, int(draw), INPUT_STREAM))
        return {name: float(rng.uniform(low, high)) for name, (low, high) in self.ranges.items()}

    def inside(self, point: Mapping[str, object]) -> bool:
        if self.region == "custom":
            return True
        try:
            kept = filter_rows(self.region_policy.build_filter_frame(point), region=self.region,
                               vocabulary=getattr(self.region_policy, "vocabulary", None))
        except Exception:
            return False
        return kept is not None and not kept.empty

    def next_inside(self, first_draw: int) -> tuple[dict[str, float], int]:
        """The first point inside the region from draw ``first_draw`` on, and the draw after it."""
        draw = int(first_draw)
        while True:
            point = self.point(draw)
            draw += 1
            if self.inside(point):
                return point, draw


@dataclass(frozen=True)
class _Method:
    """What differs between the 0/1 and mean methods: request, history record, samples file and messages."""

    tag: str  # reason prefix of the samples (the services select their rows by it)
    report_target: str
    request: Callable[["RegionSamplingStrategy", BoundsMap | None], StatisticalRequest]
    history: Callable[["RegionSamplingStrategy", StatisticalReport, int], dict[str, object] | None]
    history_method: str  # StatisticalHistoryRepository method
    samples_method: str  # StatisticalSamplesRepository method
    complete: Callable[["RegionSamplingStrategy", StatisticalReport], str]
    exhausted: Callable[["RegionSamplingStrategy"], str]
    error: str


def _binomial_request(strategy: "RegionSamplingStrategy", bounds: BoundsMap | None) -> StatisticalRequest:
    s = strategy.settings
    return StatisticalRequest(
        method="binomial_ci", metric=s["binomial_target"], bounds=bounds, confidence=s["binomial_confidence"],
        target_width=s["binomial_target_width"],
        options={"method": s["binomial_method"], "region": strategy.region,
                 "region_vocabulary": strategy.vocabulary, "reason_pattern": r"BINOMIAL_CI:",
                 "min_samples": s["binomial_min_samples"], "max_samples": strategy.max_samples,
                 "anytime_valid": s["binomial_anytime_valid"]},
    )


def _binomial_history(strategy: "RegionSamplingStrategy", report: StatisticalReport, task_count: int):
    if report.interval is None:
        return None
    s = strategy.settings
    return {
        "task_count": task_count, "metric": report.metric,
        "method": report.diagnostics.get("method", s["binomial_method"]),
        "confidence_level": s["binomial_confidence"], "sample_size": report.sample_count,
        "success_count": report.diagnostics.get("success_count"), "estimate": report.estimate,
        "lower_bound": report.interval[0], "upper_bound": report.interval[1],
        "interval_width": report.interval_width, "target_width": s["binomial_target_width"],
    }


def _sprt_request(strategy: "RegionSamplingStrategy", bounds: BoundsMap | None) -> StatisticalRequest:
    s = strategy.settings
    return StatisticalRequest(
        method="sprt", metric=s["sprt_target"], bounds=bounds, confidence=s["sprt_confidence"],
        options={"p0": s["sprt_p0"], "p1": s["sprt_p1"], "beta": s["sprt_beta"], "region": strategy.region,
                 "region_vocabulary": strategy.vocabulary, "reason_pattern": r"SPRT:",
                 "min_samples": s["sprt_min_samples"], "max_samples": strategy.max_samples},
    )


def _sprt_history(strategy: "RegionSamplingStrategy", report: StatisticalReport, task_count: int):
    d = report.diagnostics
    return {
        "task_count": task_count, "metric": report.metric, "verdict": d.get("verdict"),
        "sample_size": report.sample_count, "estimate": report.estimate,
        "log_likelihood_ratio": d.get("log_likelihood_ratio"), "lower_log_threshold": d.get("lower_log_threshold"),
        "upper_log_threshold": d.get("upper_log_threshold"), "p0": d.get("p0"), "p1": d.get("p1"),
        "alpha": d.get("alpha"), "beta": d.get("beta"),
    }


def _ebstop_request(strategy: "RegionSamplingStrategy", bounds: BoundsMap | None) -> StatisticalRequest:
    s = strategy.settings
    return StatisticalRequest(
        method="ebstop", metric=s["ebstop_target"], bounds=bounds, confidence=s["ebstop_confidence"],
        options={"epsilon": s["ebstop_epsilon"], "value_range": s["ebstop_value_range"], "region": strategy.region,
                 "region_vocabulary": strategy.vocabulary, "reason_pattern": r"EBSTOP:",
                 "max_samples": s["ebstop_max_samples"]},
    )


def _ebstop_history(strategy: "RegionSamplingStrategy", report: StatisticalReport, task_count: int):
    d = report.diagnostics
    return {
        "task_count": task_count, "metric": report.metric, "sample_size": report.sample_count,
        "estimate": report.estimate,
        "lower_bound": report.interval[0] if report.interval else None,
        "upper_bound": report.interval[1] if report.interval else None,
        "epsilon": d.get("epsilon"), "value_range": d.get("value_range"), "mean": d.get("mean"), "std": d.get("std"),
    }


_METHODS: dict[str, _Method] = {
    "binomial_ci": _Method(
        tag="BINOMIAL_CI", report_target="Binomial CI", request=_binomial_request, history=_binomial_history,
        history_method="append_binomial_ci_record", samples_method="save_binomial_ci_samples",
        complete=lambda st, r: f"Binomial CI Complete: {r.metric} CI width {r.interval_width:.5f}",
        exhausted=lambda st: f"Binomial CI reached max_samples={st.max_samples}",
        error="Binomial CI evaluation error",
    ),
    "sprt": _Method(
        tag="SPRT", report_target="SPRT", request=_sprt_request, history=_sprt_history,
        history_method="append_sprt_record", samples_method="save_sprt_samples",
        complete=lambda st, r: f"SPRT Complete: {r.metric} verdict={r.diagnostics.get('verdict')} (n={r.sample_count})",
        exhausted=lambda st: f"SPRT reached max_samples={st.max_samples} undecided",
        error="SPRT evaluation error",
    ),
    "ebstop": _Method(
        tag="EBSTOP", report_target="EBStop", request=_ebstop_request, history=_ebstop_history,
        history_method="append_ebstop_record", samples_method="save_ebstop_samples",
        complete=lambda st, r: f"EBStop Complete: {r.metric} estimate={r.estimate:.5f} (n={r.sample_count})",
        exhausted=lambda st: f"EBStop reached max_samples={st.settings['ebstop_max_samples']} unconverged",
        error="EBStop evaluation error",
    ),
}


def _setting(value: object, settings: Mapping[str, object], key: str, default: object, cast: Callable) -> object:
    return cast(value if value is not None else settings.get(key, default))


class RegionSamplingStrategy:
    """One statistical mode over a region of the input space (see the module docstring)."""

    # The orchestrator keeps queueing samples (statistical sampling).
    issues_many_cases = True

    def __init__(
        self,
        *,
        scenario_name: str,
        config: object,
        target: str,
        run_mode: str,
        dataset_repository: object,
        statistical_history_repository: object | None = None,
        statistical_samples_repository: object | None = None,
        statistical_region_policy: object | None = None,
        region_vocabulary: object | None = None,
        bounds: Mapping[str, tuple[float, float]] | None = None,
        region: str = "custom",
        pure_smc: bool = False,
        simultaneous: bool = False,
        max_samples: int | None = None,
        random_seed: int = 42,
        dkw_minimum_value: float | None = None,
        services: Mapping[str, object] | None = None,
        **method_options: object,
    ) -> None:
        if run_mode not in REGION_SAMPLING_MODES:
            raise ValueError(f"region sampling runs {', '.join(sorted(REGION_SAMPLING_MODES))}, not {run_mode!r}")
        self.target = str(target)
        self.scenario_name = str(scenario_name)
        self.config = config
        self.run_mode = str(run_mode)
        self.case_definition = _load_case_definition_from_config(config, scenario_name)
        strategy_settings = _load_strategy_settings_from_config(config)
        self.param_ranges = dict(self.case_definition["param_ranges"])
        self.param_names = list(self.param_ranges)
        self.dataset_repository = dataset_repository
        self.statistical_history_repository = statistical_history_repository
        self.statistical_samples_repository = statistical_samples_repository
        self.region_policy = _with_vocabulary(statistical_region_policy, region_vocabulary)
        self.vocabulary = getattr(self.region_policy, "vocabulary", None)
        self.region = str(region)
        check_region_name(self.region, self.vocabulary)
        self.pure_smc = bool(pure_smc)
        self.simultaneous = bool(simultaneous)
        self.max_samples = int(max_samples) if max_samples is not None else int(strategy_settings.get("max_samples", 2000))
        self.random_seed = int(random_seed)
        self.settings = self._method_settings(strategy_settings, method_options)
        services = dict(services or {})
        self.services = {
            "binomial_ci": services.get("binomial_ci") or BinomialCIService(),
            "sprt": services.get("sprt") or SPRTService(),
            "ebstop": services.get("ebstop") or EBStopService(),
        }
        self.dkw_target_metric = str(strategy_settings.get("dkw_target_metric", "min_ttc"))
        self.dkw_target_metrics = [str(m) for m in strategy_settings.get("dkw_target_metrics", ["min_ttc", "min_distance"])]
        self.dkw_mode = DKWModeRunner.from_runtime_config(
            param_names=self.param_names, target_metric=self.dkw_target_metric, target_metrics=self.dkw_target_metrics,
            total_delta=float(getattr(config, "DKW_TOTAL_DELTA", 0.05)),
            target_epsilon=float(getattr(config, "DKW_TARGET_EPSILON", 0.15)),
            base_samples=int(getattr(config, "DKW_BASE_SAMPLES", 50)),
            region=self.region, pure_smc=self.pure_smc, simultaneous=self.simultaneous,
            max_samples=self.max_samples,
            dkw_service=services.get("dkw") or DKWService(feature_names=list(self.param_names)),
            statistical_history_repository=statistical_history_repository,
            statistical_samples_repository=statistical_samples_repository,
            config=config, case_kind=self.scenario_name, config_module_name=getattr(config, "__name__", None),
            region_policy=self.region_policy, minimum_value=dkw_minimum_value,
        )

        # Ranges to sample (a named region takes them from the earlier results).
        initial = dataset_repository.load_dataset()
        given = {str(n): (float(b[0]), float(b[1])) for n, b in dict(bounds).items()} if bounds is not None else None
        self.bounds = region_bounds(initial, region=self.region, vocabulary=self.vocabulary,
                                    param_names=self.param_names, current=given)
        self.active_bounds = dict(self.param_ranges)
        if self.bounds:
            for name in self.param_names:
                if name in self.bounds:
                    self.active_bounds[name] = self.bounds[name]
        self.sampler = RegionSampler(ranges={n: self.active_bounds[n] for n in self.param_names},
                                     seed=self.random_seed, region=self.region, region_policy=self.region_policy)

        self.task_count = 0  # cases issued, never below the dataset's highest loop number
        self.draw = 0  # draws made by this run (kept or skipped)
        self.dkw_state = DKWModeState(bounds=self.bounds)
        self._latest_final_report: FinalReport | None = None
        # Set by the orchestrator: attempts per sample and the samples the queue gave up as lost.
        self.max_attempts = 1
        self.abandoned_sample_indexes: set[int] = set()
        self.latest_sample_summary: dict[str, object] | None = None
        self._own_samples: pd.DataFrame | None = None
        self._waited_at_prefix: int | None = None
        # Issued samples without a final result yet (they cut the ordered prefix).
        self.pending_samples = 0
        self.waiting_for_result = False
        self._final_payload: dict[str, object] | None = None
        self._init_numbering(initial)

    # --- settings -----------------------------------------------------------------

    @staticmethod
    def _method_settings(settings: Mapping[str, object], given: Mapping[str, object]) -> dict[str, object]:
        g = dict(given)
        return {
            "binomial_target": _setting(g.get("binomial_target"), settings, "binomial_ci_target", "c_collision", str),
            "binomial_method": _setting(g.get("binomial_method"), settings, "binomial_ci_method", "wilson", str),
            "binomial_confidence": _setting(g.get("binomial_confidence"), settings, "binomial_ci_confidence", 0.95, float),
            "binomial_target_width": _setting(g.get("binomial_target_width"), settings, "binomial_ci_target_width", 0.02, float),
            "binomial_min_samples": _setting(g.get("binomial_min_samples"), settings, "binomial_ci_min_samples", 100, int),
            "binomial_anytime_valid": _setting(g.get("binomial_anytime_valid"), settings, "binomial_ci_anytime_valid", False, bool),
            "sprt_target": _setting(g.get("sprt_target"), settings, "sprt_target", "c_collision", str),
            "sprt_p0": _setting(g.get("sprt_p0"), settings, "sprt_p0", 0.1, float),
            "sprt_p1": _setting(g.get("sprt_p1"), settings, "sprt_p1", 0.01, float),
            "sprt_beta": _setting(g.get("sprt_beta"), settings, "sprt_beta", 0.05, float),
            "sprt_confidence": _setting(g.get("sprt_confidence"), settings, "sprt_confidence", 0.95, float),
            "sprt_min_samples": _setting(g.get("sprt_min_samples"), settings, "sprt_min_samples", 0, int),
            "ebstop_target": _setting(g.get("ebstop_target"), settings, "ebstop_target", "min_ttc", str),
            "ebstop_epsilon": _setting(g.get("ebstop_epsilon"), settings, "ebstop_epsilon", 0.1, float),
            "ebstop_value_range": _setting(g.get("ebstop_value_range"), settings, "ebstop_value_range", (0.0, 10.0), tuple),
            "ebstop_confidence": _setting(g.get("ebstop_confidence"), settings, "ebstop_confidence", 0.95, float),
            "ebstop_max_samples": _setting(g.get("ebstop_max_samples"), settings, "ebstop_max_samples", 2000, int),
        }

    def statistical_metrics(self) -> list[str]:
        if self.run_mode in {"dkw", "dkw_fixed"}:
            return list(self.dkw_target_metrics) if self.simultaneous else [self.dkw_target_metric]
        return [self.settings[{"binomial_ci": "binomial_target", "sprt": "sprt_target",
                               "ebstop": "ebstop_target"}[self.run_mode]]]

    # --- numbering ----------------------------------------------------------------

    def _init_numbering(self, initial: pd.DataFrame | None) -> None:
        identity = {
            "target": self.target, "case_kind": self.scenario_name, "mode": self.run_mode,
            "seed": self.random_seed, "region": self.region,
            "bounds": {name: list(map(float, self.active_bounds[name])) for name in sorted(self.active_bounds)},
            "metrics": self.statistical_metrics(),
        }
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()
        self.experiment_id = f"{self.run_mode}-{digest[:12]}"
        self.issued_sample_indexes: set[int] = set()
        self._next_sample_index = 1
        self.draw_base = 0
        own = self._own_rows(initial)
        if own is not None and not own.empty:
            indexes = pd.to_numeric(own["sample_index"], errors="coerce").dropna().astype(int)
            self.issued_sample_indexes = set(indexes.tolist())
            self._next_sample_index = (max(self.issued_sample_indexes) + 1) if self.issued_sample_indexes else 1
            if "sampling_draw_index" in own.columns:
                draws = pd.to_numeric(own["sampling_draw_index"], errors="coerce").dropna()
                if not draws.empty:
                    self.draw_base = int(draws.max()) + 1
        self.dkw_state.draw_base = self.draw_base

    def _own_rows(self, frame: pd.DataFrame | None) -> pd.DataFrame | None:
        if frame is None or frame.empty or "experiment_id" not in frame.columns or "sample_index" not in frame.columns:
            return None
        return frame[frame["experiment_id"].astype(str).eq(str(self.experiment_id))]

    # --- the strategy -------------------------------------------------------------

    def next_test_case(self) -> TestCase | None:
        payload = self.decide_next_target()
        # A wait: every sample is issued and the last results are still coming.
        self.waiting_for_result = payload.get("system_command") == WAIT_COMMAND
        if payload.get("system_command") in {"stop", WAIT_COMMAND}:
            return None
        index = self._next_sample_index
        self._next_sample_index += 1
        self.issued_sample_indexes.add(index)
        payload = {**payload, "experiment_id": self.experiment_id, "sample_index": index,
                   "sampling_draw_index": self.draw_base + self._draws_made() - 1}
        task_input = dict(self.case_definition.get("fixed_params", {}))
        task_input.update({key: value for key, value in payload.items() if key != "reason"})
        task_input.setdefault("scenario_type", str(self.case_definition.get("scenario_type", self.scenario_name)))
        return TestCase(
            case_id=f"{self.scenario_name}_strategy_{max(self.task_count, 1)}",
            target=self.target,
            case_kind=self.scenario_name,
            input=task_input,
            reason=str(payload.get("reason", "")),
            meta={"source_module": SOURCE_MODULE, "run_mode": self.run_mode},
        )

    def _draws_made(self) -> int:
        return self.dkw_state.random_index if self.run_mode in {"dkw", "dkw_fixed"} else self.draw

    # --- the cap on issued samples ----------------------------------------------
    # At most max_samples samples are issued. Once all are out, the strategy waits for
    # the missing results (a lost sample is reissued and its result fills the gap)
    # and decides on the full sample; it evaluates again only when the ordered
    # prefix has grown, not at every poll of the orchestrator.

    def _at_issue_cap(self) -> bool:
        return self._next_sample_index - 1 >= self.max_samples

    def _prefix_size(self) -> int:
        return 0 if self._own_samples is None else len(self._own_samples)

    def _still_waiting(self) -> bool:
        return (self._at_issue_cap() and self.pending_samples > 0
                and self._waited_at_prefix == self._prefix_size())

    def _wait(self) -> dict[str, object]:
        self._waited_at_prefix = self._prefix_size()
        return {"system_command": WAIT_COMMAND,
                "reason": f"all {self.max_samples} samples issued; waiting for {self.pending_samples} result(s)"}

    def decide_next_target(self) -> dict[str, object]:
        """The next point to run (with its reason), a wait, or a stop command.

        A stop records the final report and is kept: later calls return it
        without evaluating again (the orchestrator keeps calling while the
        last results come in).
        """
        if self._final_payload is not None:
            return dict(self._final_payload)
        payload = self._decide()
        if payload.get("system_command") == "stop":
            self._capture_final_report(payload)
            self._final_payload = dict(payload)
        return payload

    def _decide(self) -> dict[str, object]:
        dataset = self.dataset_repository.load_dataset()
        self._sync_task_count(dataset)
        self._own_samples = None
        dataset, problem = self._prepare(dataset)
        if problem is not None:
            return {"system_command": "stop", "reason": problem}
        if self.run_mode == "dkw":
            return self._run_dkw(self.dkw_mode.handle_sequential, dataset)
        if self.run_mode == "dkw_fixed":
            return self._run_dkw(self.dkw_mode.handle_fixed, dataset)
        return self._run_method(_METHODS[self.run_mode], dataset)

    def _sync_task_count(self, dataset: pd.DataFrame | None) -> None:
        """The task count never falls below the dataset's highest loop number (earlier results count)."""
        if dataset is None or "loop_num" not in dataset.columns:
            return
        rows = dataset
        if self.run_mode == "dkw_fixed" and self.pure_smc:
            if "reason" not in dataset.columns:
                return
            rows = dataset[dataset["reason"].fillna("").astype(str).str.contains("SMC", na=False)]
        if rows.empty:
            return
        highest = pd.to_numeric(rows["loop_num"], errors="coerce").max()
        if pd.notna(highest) and self.task_count < int(highest):
            self.task_count = int(highest)

    def _prepare(self, dataset: pd.DataFrame | None) -> tuple[pd.DataFrame | None, str | None]:
        """This run's samples in order (contiguous prefix, one row each); other rows as they are.

        Returns an error message instead when a value does not fit the target's
        declared metric.
        """
        from evaluation.statistical_sample import prepare_statistical_sample
        from targets.statistical import target_metric_spec

        own = self._own_rows(dataset)
        if own is None or own.empty:
            self.pending_samples = len(self.issued_sample_indexes)
            return dataset, None
        prepared = prepare_statistical_sample(own, max_attempts=self.max_attempts,
                                              abandoned=self.abandoned_sample_indexes,
                                              issued=self.issued_sample_indexes)
        self.latest_sample_summary = prepared.summary
        self._own_samples = prepared.frame
        cut = prepared.summary.get("cut_at_sample_index")
        self.pending_samples = 0 if cut is None else sum(1 for index in self.issued_sample_indexes if index >= int(cut))
        others = dataset.drop(index=own.index)
        frame = pd.concat([others, prepared.frame]) if not others.empty else prepared.frame
        rows = prepared.frame
        if "status" in rows.columns:
            rows = rows[rows["status"].fillna("").astype(str).str.lower().eq("success")]
        for metric in self.statistical_metrics():
            spec = target_metric_spec(self.target, metric)
            if spec is None or metric not in rows.columns:
                continue
            bad = spec.violations(pd.to_numeric(rows[metric], errors="coerce").dropna())
            if bad:
                return frame, (
                    f"{bad} value(s) of {metric} do not fit its declaration ({spec.describe()}); "
                    "no interval is computed (check the target's result interpreter or its metric_specs)"
                )
        return frame, None

    def _run_method(self, method: _Method, dataset: pd.DataFrame | None) -> dict[str, object]:
        if self._still_waiting():
            return self._wait()
        report = self.services[self.run_mode].evaluate_request(dataset, method.request(self, self.bounds))
        self._append_history(method, report)
        if report.next_action == "stop":
            self._save_samples(method, report)
            return {"system_command": "stop", "reason": method.complete(self, report)}
        if report.next_action == "stop_max_samples":
            return {"system_command": "stop", "reason": method.exhausted(self)}
        if report.next_action == "error":
            return {"system_command": "stop", "reason": str(report.diagnostics.get("message", method.error))}
        if self._at_issue_cap():
            return self._wait() if self.pending_samples > 0 else {"system_command": "stop",
                                                                  "reason": method.exhausted(self)}
        point, next_draw = self.sampler.next_inside(self.draw_base + self.draw)
        self.draw = next_draw - self.draw_base
        self.task_count += 1
        return {**point, "reason": f"{method.tag}: Sampling ({report.sample_count + 1})"}

    def _run_dkw(self, handler, dataset: pd.DataFrame | None) -> dict[str, object]:
        state = self.dkw_state
        state.bounds = self.bounds
        state.dispatched_task_count = max(state.dispatched_task_count, self.task_count)
        # DKWModeRunner draws through the sampler and skips points outside the region itself.
        payload = handler(dataset, state=state, get_random_point=self.sampler.point, pending=self.pending_samples)
        self.bounds = state.bounds
        self.task_count = max(self.task_count, state.dispatched_task_count)
        return payload

    def _append_history(self, method: _Method, report: object) -> None:
        if not isinstance(report, StatisticalReport) or report.diagnostics.get("status") != "success":
            return
        repository = self.statistical_history_repository
        if repository is None or not hasattr(repository, method.history_method):
            return
        record = method.history(self, report, self.task_count)
        if record is not None:
            getattr(repository, method.history_method)(record)

    def _save_samples(self, method: _Method, report: StatisticalReport) -> None:
        repository = self.statistical_samples_repository
        if repository is None:
            return
        rows = report.diagnostics.get("filtered_df")
        if isinstance(rows, pd.DataFrame):
            getattr(repository, method.samples_method)(rows)

    # --- final report -------------------------------------------------------------

    @property
    def latest_final_report(self) -> FinalReport | None:
        return self._latest_final_report

    def _report_target(self) -> str:
        if self.run_mode == "dkw_fixed":
            return "DKW Fixed"
        if self.run_mode == "dkw":
            return "SMC (DKW Simultaneous)" if self.simultaneous else "SMC (DKW)"
        return _METHODS[self.run_mode].report_target

    def _capture_final_report(self, payload: Mapping[str, object]) -> None:
        reason = str(payload.get("reason", ""))
        if reason:
            self._latest_final_report = build_final_report(num_samples=self.task_count, target=self._report_target(),
                                                           reason=reason)


class ComposedRegionSamplingStrategy(RegionSamplingStrategy):
    """Region sampling whose stop decision comes from a ``--stopping-rule`` (evaluation/stopping_rules.py).

    The samples are drawn as in :class:`RegionSamplingStrategy`; the rule sees
    this run's samples only (the ordered contiguous prefix), as on the other
    statistical strategies with a rule. A uniform draw over fixed ranges (a
    named region's ranges are fixed before sampling) gives independent samples,
    so the rule's guarantee applies to the sample kind ``iid_uniform``.
    """

    def __init__(self, *, stopping_rule, stopping_metric: str, **kwargs: object) -> None:
        from contracts.experiment import SampleKind
        from orchestration.composed_strategy import StoppingRuleService

        super().__init__(**kwargs)
        self.rule = stopping_rule
        self.statistical_service = StoppingRuleService(stopping_rule, SampleKind.IID_UNIFORM)
        self.statistical_request = StatisticalRequest(
            method=stopping_rule.spec.method, metric=str(stopping_metric),
            options={"stopping_rule": stopping_rule.spec.name},
        )
        self.latest_statistical_report: StatisticalReport | None = None
        self.stop_reason = ""

    def _draws_made(self) -> int:
        return self.draw

    def _decide(self) -> dict[str, object]:
        dataset = self.dataset_repository.load_dataset()
        self._sync_task_count(dataset)
        self._own_samples = None
        _, problem = self._prepare(dataset)
        if problem is not None:
            self.stop_reason = problem
            return {"system_command": "stop", "reason": problem}
        if self._still_waiting():
            return self._wait()
        report = self._evaluate()
        if report.next_action in {"stop", "stop_max_samples", "error"}:
            self.stop_reason = _rule_stop_reason(report, self.max_samples)
            return {"system_command": "stop", "reason": self.stop_reason}
        if self._at_issue_cap():
            if self.pending_samples > 0:
                return self._wait()
            self.stop_reason = f"Statistical sampling reached max_samples={self.max_samples}"
            return {"system_command": "stop", "reason": self.stop_reason}
        point, next_draw = self.sampler.next_inside(self.draw_base + self.draw)
        self.draw = next_draw - self.draw_base
        self.task_count += 1
        return {**point, "reason": f"{self.rule.spec.name.upper()}: Sampling ({report.sample_count + 1})"}

    def _evaluate(self) -> StatisticalReport:
        report = self.statistical_service.evaluate_request(self._own_samples, self.statistical_request)
        self.latest_statistical_report = report
        return report

    def statistical_report_payload(self) -> dict[str, object] | None:
        from orchestration.fixed_parameter_sampling import unresolved_payload

        # Results that came in after the last decision count too.
        _, problem = self._prepare(self.dataset_repository.load_dataset())
        if problem is None:
            self._evaluate()
        report = self.latest_statistical_report
        if report is None:
            return None
        metric = report.metric

        def resolved_values():
            rows = self._own_samples
            if rows is None or metric not in rows.columns:
                return None
            if "status" in rows.columns:
                rows = rows[rows["status"].fillna("").astype(str).str.lower().eq("success")]
            return [float(value) for value in pd.to_numeric(rows[metric], errors="coerce").dropna()]

        return {
            "method": report.method, "metric": metric, "sample_count": report.sample_count,
            "estimate": report.estimate, "interval": report.interval, "sufficient": report.sufficient,
            "next_action": report.next_action,
            "diagnostics": {key: value for key, value in report.diagnostics.items() if key != "filtered_df"},
            "experiment_id": self.experiment_id, "region": self.region,
            "bounds": {name: list(self.active_bounds[name]) for name in self.param_names},
            "sample": self.latest_sample_summary,
            **unresolved_payload(self.latest_sample_summary, resolved_values,
                                 confidence=float(self.statistical_request.confidence or 0.95)),
        }


def _rule_stop_reason(report: StatisticalReport, max_samples: int) -> str:
    """The stop reasons of the other strategies with a rule (fixed_parameter_sampling.py)."""
    if report.next_action == "stop":
        width = report.interval_width
        rendered = "unknown" if width is None else f"{width:.6f}"
        return f"Statistical interval sufficient for {report.metric}: width={rendered}, samples={report.sample_count}"
    if report.next_action == "stop_max_samples":
        return f"Statistical sampling reached max_samples={max_samples}"
    return str(report.diagnostics.get("message", f"Statistical evaluation failed for {report.metric}"))


def build_composed_region_strategy(orchestrator, config) -> ComposedRegionSamplingStrategy:
    """Region sampling with the ``--stopping-rule`` of the config (registered as ``composed_region``)."""
    from orchestration.composed_strategy import _metric_for, build_stopping_rule
    from targets.statistical import load_statistical_target_profile

    if config.max_samples is None:
        raise ValueError(f"{config.target} statistical sampling requires max_samples")
    module = importlib.import_module(config.config_module)
    settings = RegionSamplingStrategy._method_settings(
        _load_strategy_settings_from_config(module),
        {"binomial_target": config.binomial_target, "sprt_target": config.sprt_target,
         "ebstop_target": config.ebstop_target},
    )
    metrics = {
        "binary": settings["binomial_target"], "sprt": settings["sprt_target"],
        "dkw": str(_load_strategy_settings_from_config(module).get("dkw_target_metric", "min_ttc")),
        "ebstop": settings["ebstop_target"],
        "minimum_dkw_value": load_statistical_target_profile(config.target).minimum_dkw_value,
    }
    rule = build_stopping_rule(config, metrics)
    return build_region_sampling_strategy(
        orchestrator, config, strategy_class=ComposedRegionSamplingStrategy,
        stopping_rule=rule, stopping_metric=_metric_for(rule, metrics),
    )


def build_region_sampling_strategy(orchestrator, config, *, region_vocabulary=None, strategy_class=None,
                                   **extra: object) -> RegionSamplingStrategy:
    """A statistical mode of a target with a settings module (PARAM_RANGES), from an OrchestratorConfig."""
    from runtime.repository.statistical_history import StatisticalHistoryRepository
    from runtime.repository.statistical_samples import StatisticalSamplesRepository
    from targets.statistical import build_statistical_region_policy, load_statistical_target_profile

    strategy_config = importlib.import_module(config.config_module)
    dataset_repository = orchestrator._build_dataset_repository(config)
    traces_dir = Path(dataset_repository.paths.traces_dir)
    return (strategy_class or RegionSamplingStrategy)(
        **extra,
        scenario_name=config.case_kind,
        config=strategy_config,
        target=config.target,
        run_mode=config.run_mode,
        dataset_repository=dataset_repository,
        statistical_history_repository=StatisticalHistoryRepository(scenario_name=config.case_kind,
                                                                    traces_dir=traces_dir),
        statistical_samples_repository=StatisticalSamplesRepository(scenario_name=config.case_kind,
                                                                    traces_dir=traces_dir),
        statistical_region_policy=build_statistical_region_policy(
            config.target, case_kind=config.case_kind, config_module_name=config.config_module),
        region_vocabulary=region_vocabulary,
        bounds=config.dkw_bounds,
        region=config.dkw_region,
        pure_smc=config.dkw_pure_smc,
        simultaneous=config.dkw_simultaneous,
        max_samples=config.max_samples,
        dkw_minimum_value=load_statistical_target_profile(config.target).minimum_dkw_value,
        binomial_target=config.binomial_target,
        binomial_method=config.binomial_method,
        binomial_confidence=config.binomial_confidence,
        binomial_target_width=config.binomial_target_width,
        binomial_min_samples=config.binomial_min_samples,
        binomial_anytime_valid=config.binomial_anytime_valid,
        sprt_target=config.sprt_target,
        sprt_p0=config.sprt_p0,
        sprt_p1=config.sprt_p1,
        sprt_beta=config.sprt_beta,
        sprt_confidence=config.sprt_confidence,
        sprt_min_samples=config.sprt_min_samples,
        ebstop_target=config.ebstop_target,
        ebstop_epsilon=config.ebstop_epsilon,
        ebstop_value_range=config.ebstop_value_range,
        ebstop_confidence=config.ebstop_confidence,
        ebstop_max_samples=config.ebstop_max_samples,
    )


__all__ = [
    "ComposedRegionSamplingStrategy",
    "REGION_SAMPLING_MODES",
    "RegionSampler",
    "RegionSamplingStrategy",
    "build_composed_region_strategy",
    "build_region_sampling_strategy",
    "region_bounds",
]
