"""Fixed-Q importance sampling for the dynamics target.

Two plan formats are supported:

- v1 (U-turn input P/Q): count-based resume, legacy seeds
  ``seed * 1_000_000 + index`` shared by the ordinary and importance runs.
- v2 (``dynamics_schema_version: 2``, registered models such as OU): an
  experiment manifest, per-sample streams from one recorded root, resume by
  sample id and a completion audit (docs/sde_importance_sampling_plan.md §7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from contracts.execution import TestCase
from contracts.importance_sampling_plugin import CaseRequest, PreparedTarget
from contracts.target_plugin import TargetComponents
from orchestration.importance.runner import ImportanceRunConfig, ImportanceSamplingRunner
from orchestration.sampling_plan import SamplingPlan
from orchestration.seeding import PAIRINGS, PHASES
from targets.dynamics.sampling import DynamicsInputSampler, DynamicsModelSampler, is_dynamics_schema_v2


@dataclass(frozen=True, slots=True)
class DynamicsImportanceSamplingRunConfig:
    sampling_plan_path: str | Path
    output_root: str | Path
    max_samples: int
    # None: the model's binary metric (c_collision for the U-turn).
    metric: str | None = None
    confidence: float = 0.95
    seed: int | None = None
    params: Mapping[str, object] = field(default_factory=dict)
    # v2 plans only (ignored for v1).
    phase: str = "final"
    root_entropy: int | None = None
    pairing: str = "independent"
    retry_failed: bool = False
    # Attempts per sample in total: a failed sample is run again (same streams).
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if self.max_samples < 1:
            raise ValueError("max_samples must be positive")
        if self.phase not in PHASES:
            raise ValueError(f"phase must be one of {sorted(PHASES)}")
        if self.pairing not in PAIRINGS:
            raise ValueError(f"pairing must be one of {list(PAIRINGS)}")
        object.__setattr__(self, "params", dict(self.params))


class DynamicsImportanceSamplingRunner:
    """Compatible entry point: the shared runner with the dynamics hooks.

    v1 plans (U-turn inputs): legacy seeds ``seed * 1_000_000 + index``, shared
    by the ordinary and importance runs, count-based resume. v2 plans
    (registered models): manifest, per-sample streams, resume by sample id and
    a completion audit (docs/sde_importance_sampling_plan.md §7).
    """

    def __init__(self, *, backend: object, result_interpreter: object):
        self.backend = backend
        self.result_interpreter = result_interpreter

    def run_fixed_samples(self, config: DynamicsImportanceSamplingRunConfig) -> dict[str, object]:
        runner = ImportanceSamplingRunner(
            backend=self.backend, result_interpreter=self.result_interpreter, hooks=DynamicsImportanceSampling(),
        )
        return runner.run_fixed_samples(ImportanceRunConfig(
            sampling_plan_path=config.sampling_plan_path,
            output_root=config.output_root,
            max_samples=config.max_samples,
            metric=config.metric,
            confidence=config.confidence,
            seed=config.seed,
            params=config.params,
            phase=config.phase,
            root_entropy=config.root_entropy,
            pairing=config.pairing,
            retry_failed=config.retry_failed,
            max_attempts=config.max_attempts,
        ))


class DynamicsImportanceSampling:
    """Importance-sampling hooks of the dynamics plugin (contracts/importance_sampling_plugin.py)."""

    # The keys of proposal.definition that make up a Brownian-path proposal (pilot trials replace them).
    proposal_keys = ("noise", "noise_path_q")

    def exact_probability(self, plan: SamplingPlan) -> float:
        """The exact probability of the event of a v2 plan, for the models that have one (A and B)."""
        from targets.dynamics.models.exact import (
            ou_augmented_terminal_exceedance_probability,
            ou_terminal_exceedance_probability,
        )

        definition = plan.target_measure_definition
        solver, event, params = definition["solver"], definition["event"], definition["model_params"]
        steps = round(float(solver["horizon_sec"]) / float(solver["dt_sec"]))
        if definition["model_id"] == "ou_scalar":
            return ou_terminal_exceedance_probability(
                theta=params["theta"], sigma=params["sigma"], h=solver["dt_sec"], steps=steps,
                threshold=event["threshold"], side=event.get("side", "upper"),
                x0=float(definition["initial_law"]["value"][0]),
            )
        if definition["model_id"] == "ou_augmented":
            c0, x0 = (float(value) for value in definition["initial_law"]["value"])
            return ou_augmented_terminal_exceedance_probability(
                **params, h=solver["dt_sec"], steps=steps, threshold=event["threshold"],
                side=event.get("side", "upper"), state=event.get("state", "c"), c0=c0, x0=x0,
            )
        raise ValueError(f"no exact probability for model {definition['model_id']!r}")

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--artifacts", choices=["files", "summary"], default="files",
            help="registered models: 'summary' keeps trajectories in memory and stores only the judged records",
        )

    def build_components(self, args) -> TargetComponents:
        from targets.dynamics.backend import DynamicsBackend, DynamicsBackendConfig
        from targets.dynamics.result_interpreter import DynamicsResultInterpreter

        return TargetComponents(
            backend=DynamicsBackend(DynamicsBackendConfig(
                output_root=Path(args.output_root) / "artifacts", artifact_policy=args.artifacts,
            )),
            result_interpreter=DynamicsResultInterpreter(),
        )

    def prepare(self, plan: SamplingPlan, config) -> PreparedTarget:
        if is_dynamics_schema_v2(plan):
            model = DynamicsModelSampler(plan)
            return PreparedTarget(
                default_metric=model.adapter.default_binary_metric,
                seed_policy="streams",
                internal_proposal=model.internal_proposal,
                random_streams=("innovation", "mixture_choice"),
                target_definition=model.target_definition,
                proposal_definition=model.proposal_definition,
                numerics=model.numerics,
                state=model,
            )
        sampler = DynamicsInputSampler(plan)
        return PreparedTarget(
            default_metric="c_collision",
            seed_policy="legacy",
            input_laws=sampler.laws,
            internal_proposal=sampler.internal_proposal,
            draw_seed_when_missing=True,
            state=sampler,
        )

    def build_case(self, request: CaseRequest) -> TestCase:
        if isinstance(request.prepared.state, DynamicsModelSampler):
            return _build_case_v2(request, request.prepared.state)
        return _build_case_v1(request, request.prepared.state)


def _build_case_v2(request: CaseRequest, model: DynamicsModelSampler) -> TestCase:
    importance = request.run_kind == "importance"
    return TestCase(
        case_id=f"{model.plan.experiment_id}-{request.run_kind}-{request.sample_index:06d}",
        target="dynamics",
        case_kind=model.case_kind,
        input={
            **model.execution_inputs,
            **request.params,
            **(model.proposal_inputs if importance else {}),
            "sde_seed": request.seeds["innovation"],
            "sde_mixture_seed": request.seeds["mixture_choice"],
            # The proposal component chosen by the shared code.
            **({"sde_component": request.component} if request.component is not None else {}),
            "execution_kind": "sample_path",
            "record_kind": "sample",
            "sample_index": request.sample_index,
            "sampling_plan_path": request.sampling_plan_path,
        },
        reason=f"DYNAMICS_IMPORTANCE_{request.run_kind.upper()}: {model.plan.experiment_id} sample={request.sample_index}",
        meta={"sampling": dict(request.sampling or {}), "source_module": "targets.dynamics.importance_sampling"},
    )


def _build_case_v1(request: CaseRequest, sampler: DynamicsInputSampler) -> TestCase:
    sample_seed = request.seeds["sample"]
    importance = request.run_kind == "importance"
    return TestCase(
        case_id=f"{sampler.plan.experiment_id}-{request.run_kind}-{request.sample_index:05d}",
        target="dynamics",
        case_kind=sampler.case_kind,
        input={
            **sampler.execution_inputs,
            **request.params,
            **(
                {"noise_path_q": sampler.sde_noise_proposal}
                if importance and sampler.sde_noise_proposal is not None
                else {}
            ),
            **(
                {"sde_seed": sample_seed}
                if sampler.execution_inputs.get("solver_kind") == "sde" and sample_seed is not None
                else {}
            ),
            "model_id": sampler.model_id,
            **request.inputs,
            "execution_kind": "sample_path",
            "record_kind": "sample",
            "sample_index": request.sample_index,
            "sampling_seed": sample_seed,
            "sampling_plan_path": request.sampling_plan_path,
        },
        reason=f"DYNAMICS_IMPORTANCE_{request.run_kind.upper()}: {sampler.plan.experiment_id} sample={request.sample_index}",
        meta={"sampling": dict(request.sampling or {}), "source_module": "targets.dynamics.importance_sampling"},
    )


__all__ = [
    "DynamicsImportanceSampling",
    "DynamicsImportanceSamplingRunConfig",
    "DynamicsImportanceSamplingRunner",
]
