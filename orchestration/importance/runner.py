"""The one importance-sampling runner for every target.

The target prepares the plan and builds each case (contracts/importance_sampling_plugin.py);
this runner draws the inputs (input laws), chooses the proposal component of
the internal randomness, seeds the random streams, builds the sampling
metadata, runs and saves the samples (composing each weight from the target's
``internal`` report), and evaluates them:

- ``fixed_samples``: N ordinary and N importance samples, compared.
  Seed policy "streams": manifest, sample ids, resume by id and a completion
  audit. Seed policy "legacy": the outer-seed rule and count-based resume.
- ``importance_width`` / ``weighted_sequential``: importance samples are added
  until the stop rule fires (or ``max_samples``); a fixed, human-chosen Q only.

docs/blackbox_importance_sampling_plan.md §5.3.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from contracts.capabilities import INTERNAL_DENSITY, require_capabilities
from contracts.execution import TestCase
from contracts.importance_sampling_plugin import (
    CaseRequest,
    ImportanceSamplingHooks,
    PreparedTarget,
    importance_sampling_hooks,
)
from contracts.statistics import StatisticalRequest
from evaluation.importance_sampling import evaluate_standard_importance_sampling_request
from evaluation.weighted_sequential import evaluate_weighted_sequential_request
from orchestration.fixed_importance_sampling import (
    importance_record_sink,
    run_fixed_importance_sampling_comparison,
    run_strict_fixed_importance_sampling_comparison,
)
from orchestration.importance.input_law import draw_proposal, draw_target
from orchestration.importance.weights import choose_component, mixture_weight_bound, needs_choice
from orchestration.sampling_plan import SamplingPlan, load_sampling_plan
from orchestration.seeding import (
    PAIRINGS,
    PHASES,
    RNG_POLICY_VERSION,
    legacy_sample_seed,
    new_root_entropy,
    stream_seed,
)
from orchestration.importance.executors import CaseExecutor, LocalExecutor
from runtime.repository.importance_experiment_manifest import (
    ExperimentManifest,
    load_manifest,
    save_or_verify_manifest,
)
from runtime.repository.importance_sampling_results import ImportanceSamplingResultsRepository

STOP_MODES = ("fixed_samples", "importance_width", "weighted_sequential")
MIXTURE_CHOICE_STREAM = "mixture_choice"


@dataclass(frozen=True, slots=True)
class ImportanceRunConfig:
    sampling_plan_path: str | Path
    output_root: str | Path
    max_samples: int | None
    # None: the target's default metric.
    metric: str | None = None
    confidence: float = 0.95
    # Seed policy "legacy": the outer seed (None: unseeded, or drawn if the target asks).
    seed: int | None = None
    params: Mapping[str, object] = field(default_factory=dict)
    # Seed policy "streams".
    phase: str = "final"
    root_entropy: int | None = None
    pairing: str = "independent"
    retry_failed: bool = False
    # Attempts per sample in total (streams policy): a failed sample is run again.
    max_attempts: int = 3
    # Stopping.
    stop_mode: str = "fixed_samples"
    target_width: float | None = None
    sequential_options: Mapping[str, object] = field(default_factory=dict)
    # Target-specific values (for example AWSIM's scenario profile).
    options: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.max_samples is not None and self.max_samples < 1:
            raise ValueError("max_samples must be positive")
        if self.phase not in PHASES:
            raise ValueError(f"phase must be one of {sorted(PHASES)}")
        if self.pairing not in PAIRINGS:
            raise ValueError(f"pairing must be one of {list(PAIRINGS)}")
        if self.stop_mode not in STOP_MODES:
            raise ValueError("stop_mode must be fixed_samples, importance_width, or weighted_sequential")
        if self.stop_mode == "importance_width" and self.target_width is None:
            raise ValueError("target_width is required for importance_width")
        object.__setattr__(self, "params", dict(self.params))
        object.__setattr__(self, "sequential_options", dict(self.sequential_options))
        object.__setattr__(self, "options", dict(self.options))


def hooks_for_target(target: str) -> ImportanceSamplingHooks:
    from targets.registry import get_target_plugin

    hooks = importance_sampling_hooks(get_target_plugin(target))
    if hooks is None:
        raise ValueError(f"target {target!r} does not support importance sampling (its plugin has no importance_sampling)")
    return hooks


class ImportanceSamplingRunner:
    """Run a fixed-Q importance-sampling experiment of any target."""

    def __init__(
        self,
        *,
        backend: object = None,
        result_interpreter: object = None,
        hooks: ImportanceSamplingHooks | None = None,
        executor: CaseExecutor | None = None,
    ):
        self.backend = backend
        self.result_interpreter = result_interpreter
        self.hooks = hooks
        # Default: run the cases here, one after the other.
        self.executor = executor or LocalExecutor(backend=backend, result_interpreter=result_interpreter)

    # --- entry points ---------------------------------------------------------

    def run(self, config: ImportanceRunConfig) -> dict[str, object]:
        if config.stop_mode == "fixed_samples":
            return self.run_fixed_samples(config)
        return self.run_until_stop(config)

    def run_fixed_samples(self, config: ImportanceRunConfig) -> dict[str, object]:
        if config.stop_mode != "fixed_samples":
            raise ValueError("run_fixed_samples requires stop_mode='fixed_samples'")
        if config.max_samples is None:
            raise ValueError("max_samples is required for fixed_samples")
        plan, prepared = self._prepare(config)
        self._check_executor(prepared)
        if prepared.seed_policy == "streams":
            return self._run_streams(plan, prepared, config)
        return self._run_legacy(plan, prepared, config)

    def run_until_stop(self, config: ImportanceRunConfig) -> dict[str, object]:
        """Append fixed-Q importance samples until a configured stop rule fires.

        Only the importance run is extended. An ordinary baseline is a separate
        fixed-sample comparison, so its cost cannot affect a sequential decision.
        """
        if config.max_samples is None:
            raise ValueError("max_samples is required as a safety cap for stop modes")
        if config.stop_mode not in {"importance_width", "weighted_sequential"}:
            raise ValueError("run_until_stop requires a non-fixed stop mode")
        plan, prepared = self._prepare(config)
        self._check_executor(prepared)
        if plan.sampling_mode != "manual_proposal":
            raise ValueError(
                "sequential stop modes require a human-selected fixed Q "
                "(proposal.mode='manual_proposal')"
            )
        root_entropy = None
        if prepared.seed_policy == "streams":
            # Sample i always uses the streams of index i, so a resumed run must
            # be given the same root.
            if config.root_entropy is None:
                raise ValueError("--root-entropy is required for a sequential stop mode with random streams")
            root_entropy = config.root_entropy
        repository = ImportanceSamplingResultsRepository(config.output_root)
        metric = config.metric or prepared.default_metric
        state: dict[str, object] = {"records": repository.load_records("importance"), "report": None}
        if state["records"]:
            state["report"] = self._stop_report(state["records"], config, plan, metric)

        def should_stop() -> bool:
            state["records"] = repository.load_records("importance")
            state["report"] = self._stop_report(state["records"], config, plan, metric)
            return bool(state["report"].sufficient)

        if state["report"] is None or not state["report"].sufficient:
            self.executor.run_sequential(
                build_case=lambda index: self.build_case(plan, prepared, config, "importance", index,
                                                         root_entropy=root_entropy),
                sink=importance_record_sink(repository, "importance", prepared.internal_proposal),
                first_index=len(state["records"]) + 1,
                max_index=config.max_samples,
                should_stop=should_stop,
            )
        records, report = state["records"], state["report"]
        if report is None:
            raise RuntimeError("stop-mode run did not produce an evaluation report")
        stopped_by_rule = report.sufficient
        result = {
            "plan_id": plan.experiment_id,
            "stop_mode": config.stop_mode,
            "sample_count": len(records),
            "max_samples": config.max_samples,
            "stopped_by_rule": stopped_by_rule,
            "stop_reason": report.next_action if stopped_by_rule else "max_samples_reached",
            "statistical_report": report_payload(report),
            "importance_records_path": str(repository.records_path("importance")),
            **({"root_entropy": str(root_entropy)} if root_entropy is not None else {}),
        }
        comparison_path = repository.save_comparison(result)
        result["comparison_path"] = str(comparison_path)
        return result

    # --- plan and cases -------------------------------------------------------

    def _check_executor(self, prepared: PreparedTarget) -> None:
        if getattr(self.executor, "requires_stream_seeds", False) and prepared.seed_policy != "streams":
            raise ValueError(
                "running on the cluster needs the 'streams' seed policy (sample ids, per-sample streams); "
                "this plan uses the legacy seed policy"
            )

    def _prepare(self, config: ImportanceRunConfig) -> tuple[SamplingPlan, PreparedTarget]:
        plan = load_sampling_plan(config.sampling_plan_path)
        if self.hooks is None:
            self.hooks = hooks_for_target(plan.target)
        prepared = self.hooks.prepare(plan, config)
        if prepared.internal_proposal is not None:
            from targets.registry import get_target_plugin

            try:
                plugin = get_target_plugin(plan.target)
            except ValueError:
                plugin = None
            require_capabilities(plan.target, plugin, {INTERNAL_DENSITY},
                                 algorithm="importance sampling of its internal randomness")
        return plan, prepared

    def build_case(
        self,
        plan: SamplingPlan,
        prepared: PreparedTarget,
        config: ImportanceRunConfig,
        run_kind: str,
        index: int,
        *,
        root_entropy: int | None = None,
    ) -> TestCase:
        if run_kind not in {"ordinary", "importance"}:
            raise ValueError("run_kind must be 'ordinary' or 'importance'")
        importance = run_kind == "importance"
        if prepared.seed_policy == "streams":
            if root_entropy is None:
                raise ValueError("the streams seed policy needs the root entropy")
            streams = set(prepared.random_streams)
            if prepared.input_laws is not None:
                streams.add("input")
            if prepared.internal_proposal is not None:
                streams.add(MIXTURE_CHOICE_STREAM)
            seeds: dict[str, int | None] = {
                stream: stream_seed(root_entropy, phase=config.phase, run_kind=run_kind, sample_index=index,
                                    stream=stream, pairing=config.pairing)
                for stream in sorted(streams)
            }
            input_seed = seeds.get("input")
        else:
            seeds = {"sample": legacy_sample_seed(config.seed, index)}
            input_seed = seeds["sample"]

        inputs: dict[str, float] = {}
        log_p = log_q = 0.0
        if prepared.input_laws is not None:
            if importance:
                draw = draw_proposal(prepared.input_laws, seed=input_seed)
                inputs, log_p, log_q = draw.values, draw.log_p, draw.log_q
            else:
                inputs = draw_target(prepared.input_laws, seed=input_seed)

        component: int | str | None = None
        internal = prepared.internal_proposal
        if importance and internal is not None and prepared.seed_policy == "streams":
            uniform = None
            if needs_choice(internal.weights, internal.defensive_weight):
                uniform = float(np.random.default_rng(int(seeds[MIXTURE_CHOICE_STREAM])).random())
            component = choose_component(internal.weights, internal.defensive_weight, uniform)

        return self.hooks.build_case(CaseRequest(
            run_kind=run_kind,
            sample_index=index,
            plan=plan,
            prepared=prepared,
            inputs=inputs,
            seeds=seeds,
            sampling=sampling_metadata(plan, prepared, importance=importance, log_p=log_p, log_q=log_q),
            component=component,
            params=config.params,
            options=config.options,
            sampling_plan_path=str(Path(config.sampling_plan_path).resolve()),
        ))

    # --- fixed N ----------------------------------------------------------------

    def _run_legacy(self, plan: SamplingPlan, prepared: PreparedTarget, config: ImportanceRunConfig) -> dict[str, object]:
        seed_generated = False
        if config.seed is None and prepared.draw_seed_when_missing:
            # Without an outer seed every case would keep a fixed internal seed,
            # so all paths would share one noise realisation.
            config = _replace(config, seed=new_root_entropy() % (1 << 31))
            seed_generated = True
        repository = ImportanceSamplingResultsRepository(config.output_root)
        result = run_fixed_importance_sampling_comparison(
            backend=self.backend,
            result_interpreter=self.result_interpreter,
            repository=repository,
            max_samples=int(config.max_samples),
            build_case=lambda run_kind, index: self.build_case(plan, prepared, config, run_kind, index),
            metric=config.metric or prepared.default_metric,
            confidence=config.confidence,
            target_width=config.target_width,
            plan_id=plan.experiment_id,
            internal_proposal=prepared.internal_proposal,
            executor=self.executor,
        )
        if prepared.draw_seed_when_missing:
            result["comparison"].update({"sampling_seed": config.seed, "sampling_seed_generated": seed_generated})
            repository.save_comparison(result["comparison"])
        return result

    def _run_streams(self, plan: SamplingPlan, prepared: PreparedTarget, config: ImportanceRunConfig) -> dict[str, object]:
        metric = config.metric or prepared.default_metric
        root_entropy = config.root_entropy
        if root_entropy is None:
            stored = load_manifest(config.output_root)
            root_entropy = int(stored.rng["root_entropy"]) if stored is not None else new_root_entropy()
        manifest = save_or_verify_manifest(
            config.output_root,
            ExperimentManifest(
                experiment_id=plan.experiment_id,
                target_definition={
                    "target_measure_id": plan.target_measure_id,
                    "definition": dict(prepared.target_definition or plan.target_measure_definition),
                    "params_override": dict(config.params),
                },
                proposal_definition={
                    "proposal_id": plan.proposal_id,
                    "definition": dict(prepared.proposal_definition or plan.proposal_definition),
                },
                numerics=dict(prepared.numerics),
                rng={
                    "policy_version": RNG_POLICY_VERSION,
                    "root_entropy": str(root_entropy),
                    "phase": config.phase,
                    "pairing": config.pairing,
                },
                planned_samples=int(config.max_samples),
                metric=metric,
            ),
        )
        return run_strict_fixed_importance_sampling_comparison(
            backend=self.backend,
            result_interpreter=self.result_interpreter,
            repository=ImportanceSamplingResultsRepository(config.output_root),
            manifest=manifest,
            build_case=lambda run_kind, index: self.build_case(
                plan, prepared, config, run_kind, index, root_entropy=root_entropy,
            ),
            metric=metric,
            confidence=config.confidence,
            retry_failed=config.retry_failed,
            internal_proposal=prepared.internal_proposal,
            executor=self.executor,
            max_attempts=config.max_attempts,
        )

    @staticmethod
    def _stop_report(records, config: ImportanceRunConfig, plan: SamplingPlan, metric: str):
        if config.stop_mode == "importance_width":
            return evaluate_standard_importance_sampling_request(
                records,
                StatisticalRequest(
                    method="ordinary_importance_sampling", metric=metric,
                    confidence=config.confidence, target_width=config.target_width,
                ),
            )
        configured_options = plan.sequential_decision
        if configured_options is None:
            configured_options = {}
        if not isinstance(configured_options, Mapping):
            raise ValueError("sequential_decision must be a mapping when weighted_sequential is used")
        return evaluate_weighted_sequential_request(
            records,
            StatisticalRequest(
                method="weighted_sequential", metric=metric, confidence=config.confidence,
                options={**dict(configured_options), **dict(config.sequential_options)},
            ),
        )


def sampling_metadata(
    plan: SamplingPlan,
    prepared: PreparedTarget,
    *,
    importance: bool,
    log_p: float = 0.0,
    log_q: float = 0.0,
) -> dict[str, object]:
    """The sampling provenance of one record before the target reports its internal draw."""
    if not importance:
        return {
            "plan_id": plan.experiment_id,
            "target_measure_id": plan.target_measure_id,
            "sampling_mode": "uniform",
            "importance_weight_status": "not_applicable",
        }
    metadata: dict[str, object] = {
        "plan_id": plan.experiment_id,
        "target_measure_id": plan.target_measure_id,
        "sampling_mode": "manual_proposal",
        "proposal_id": plan.proposal_id,
        "importance_weight_status": "available",
        "log_p": log_p,
        "log_q": log_q,
        "log_importance_weight": log_p - log_q,
    }
    bound = weight_upper_bound(prepared)
    if bound is not None:
        metadata["importance_weight_upper_bound"] = bound
    return metadata


def weight_upper_bound(prepared: PreparedTarget) -> float | None:
    """sup p/q: the input bound times the internal bound (None if either is unbounded)."""
    internal = prepared.internal_proposal
    input_bound = None if prepared.input_laws is None else prepared.input_laws.weight_upper_bound
    if internal is None:
        return 1.0 if input_bound is None else input_bound
    internal_bound = mixture_weight_bound(internal.components, internal.defensive_weight, internal.component_bound)
    if internal_bound is None:
        return None
    return internal_bound if input_bound is None else input_bound * internal_bound


def report_payload(report) -> dict[str, object]:
    return {
        "method": report.method,
        "metric": report.metric,
        "sample_count": report.sample_count,
        "estimate": report.estimate,
        "interval": list(report.interval) if report.interval is not None else None,
        "sufficient": report.sufficient,
        "next_action": report.next_action,
        "diagnostics": dict(report.diagnostics),
    }


def _replace(config: ImportanceRunConfig, **changes: object) -> ImportanceRunConfig:
    from dataclasses import replace

    return replace(config, **changes)


__all__ = [
    "ImportanceRunConfig",
    "ImportanceSamplingRunner",
    "STOP_MODES",
    "hooks_for_target",
    "report_payload",
    "sampling_metadata",
    "weight_upper_bound",
]
