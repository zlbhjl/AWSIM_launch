"""Fixed-Q importance sampling for the dynamics target.

Two plan formats are supported:

- v1 (U-turn input P/Q): count-based resume, legacy seeds
  ``seed * 1_000_000 + index`` shared by the ordinary and importance runs.
- v2 (``dynamics_schema_version: 2``, registered models such as OU): an
  experiment manifest, per-sample streams from one recorded root, resume by
  sample id and a completion audit (docs/sde_importance_sampling_plan.md §7).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping

from contracts.execution import TestCase
from orchestration.fixed_importance_sampling import (
    run_fixed_importance_sampling_comparison,
    run_strict_fixed_importance_sampling_comparison,
)
from orchestration.sampling_plan import SamplingPlan, load_sampling_plan
from orchestration.seeding import (
    PAIRINGS,
    PHASES,
    RNG_POLICY_VERSION,
    legacy_sample_seed,
    new_root_entropy,
    stream_seed,
)
from runtime.repository.importance_experiment_manifest import (
    ExperimentManifest,
    load_manifest,
    save_or_verify_manifest,
)
from runtime.repository.importance_sampling_results import ImportanceSamplingResultsRepository
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

    def __post_init__(self) -> None:
        if self.max_samples < 1:
            raise ValueError("max_samples must be positive")
        if self.phase not in PHASES:
            raise ValueError(f"phase must be one of {sorted(PHASES)}")
        if self.pairing not in PAIRINGS:
            raise ValueError(f"pairing must be one of {list(PAIRINGS)}")
        object.__setattr__(self, "params", dict(self.params))


class DynamicsImportanceSamplingRunner:
    """Compare IID P inputs with a human-configured, fixed input proposal Q.

    This runner intentionally does not adjust Q, modify the dynamics model, or
    add a sequential stop rule.  Those concerns remain outside the initial
    input-distribution importance-sampling scope.
    """

    def __init__(self, *, backend: object, result_interpreter: object):
        self.backend = backend
        self.result_interpreter = result_interpreter

    def run_fixed_samples(self, config: DynamicsImportanceSamplingRunConfig) -> dict[str, object]:
        plan = load_sampling_plan(config.sampling_plan_path)
        if is_dynamics_schema_v2(plan):
            return self._run_v2(plan, config)
        if config.metric is None:
            config = replace(config, metric="c_collision")
        sampler = DynamicsInputSampler(plan)
        repository = ImportanceSamplingResultsRepository(config.output_root)
        seed_generated = config.seed is None
        if seed_generated:
            # Without an outer seed every case would keep the plan's fixed
            # sde_seed, so all SDE paths would share one noise realisation.
            config = replace(config, seed=new_root_entropy() % (1 << 31))
        result = run_fixed_importance_sampling_comparison(
            backend=self.backend,
            result_interpreter=self.result_interpreter,
            repository=repository,
            max_samples=config.max_samples,
            build_case=lambda run_kind, index: self._build_case(
                run_kind=run_kind, index=index, sampler=sampler, config=config,
            ),
            metric=config.metric,
            confidence=config.confidence,
            plan_id=plan.experiment_id,
        )
        result["comparison"].update({"sampling_seed": config.seed, "sampling_seed_generated": seed_generated})
        repository.save_comparison(result["comparison"])
        return result

    def _run_v2(self, plan: SamplingPlan, config: DynamicsImportanceSamplingRunConfig) -> dict[str, object]:
        sampler = DynamicsModelSampler(plan)
        metric = config.metric or sampler.adapter.default_binary_metric
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
                    "definition": sampler.target_definition,
                    "params_override": dict(config.params),
                },
                proposal_definition={
                    "proposal_id": plan.proposal_id,
                    "definition": sampler.proposal_definition,
                },
                numerics=sampler.numerics,
                rng={
                    "policy_version": RNG_POLICY_VERSION,
                    "root_entropy": str(root_entropy),
                    "phase": config.phase,
                    "pairing": config.pairing,
                },
                planned_samples=config.max_samples,
                metric=metric,
            ),
        )
        return run_strict_fixed_importance_sampling_comparison(
            backend=self.backend,
            result_interpreter=self.result_interpreter,
            repository=ImportanceSamplingResultsRepository(config.output_root),
            manifest=manifest,
            build_case=lambda run_kind, index: self._build_case_v2(
                run_kind=run_kind, index=index, sampler=sampler, config=config,
                root_entropy=root_entropy,
            ),
            metric=metric,
            confidence=config.confidence,
            retry_failed=config.retry_failed,
        )

    @staticmethod
    def _build_case_v2(
        *,
        run_kind: str,
        index: int,
        sampler: DynamicsModelSampler,
        config: DynamicsImportanceSamplingRunConfig,
        root_entropy: int,
    ) -> TestCase:
        if run_kind not in {"ordinary", "importance"}:
            raise ValueError("run_kind must be 'ordinary' or 'importance'")

        def seed_for(stream: str) -> int:
            return stream_seed(
                root_entropy, phase=config.phase, run_kind=run_kind, sample_index=index,
                stream=stream, pairing=config.pairing,
            )

        importance = run_kind == "importance"
        return TestCase(
            case_id=f"{sampler.plan.experiment_id}-{run_kind}-{index:06d}",
            target="dynamics",
            case_kind=sampler.case_kind,
            input={
                **sampler.execution_inputs,
                **config.params,
                **(sampler.proposal_inputs if importance else {}),
                "sde_seed": seed_for("innovation"),
                "sde_mixture_seed": seed_for("mixture_choice"),
                "execution_kind": "sample_path",
                "record_kind": "sample",
                "sample_index": index,
                "sampling_plan_path": str(Path(config.sampling_plan_path).resolve()),
            },
            reason=f"DYNAMICS_IMPORTANCE_{run_kind.upper()}: {sampler.plan.experiment_id} sample={index}",
            meta={
                "sampling": sampler.proposal_metadata() if importance else sampler.target_metadata(),
                "source_module": "targets.dynamics.importance_sampling",
            },
        )

    @staticmethod
    def _build_case(
        *,
        run_kind: str,
        index: int,
        sampler: DynamicsInputSampler,
        config: DynamicsImportanceSamplingRunConfig,
    ) -> TestCase:
        sample_seed = _sample_seed(config.seed, index)
        if run_kind == "ordinary":
            sampled = sampler.sample_target(seed=sample_seed)
        elif run_kind == "importance":
            sampled = sampler.sample_proposal(seed=sample_seed)
        else:
            raise ValueError("run_kind must be 'ordinary' or 'importance'")
        return TestCase(
            case_id=f"{sampler.plan.experiment_id}-{run_kind}-{index:05d}",
            target="dynamics",
            case_kind=sampler.case_kind,
            input={
                **sampler.execution_inputs,
                **config.params,
                **(
                    {"noise_path_q": sampler.sde_noise_proposal}
                    if run_kind == "importance" and sampler.sde_noise_proposal is not None
                    else {}
                ),
                **(
                    {"sde_seed": sample_seed}
                    if sampler.execution_inputs.get("solver_kind") == "sde" and sample_seed is not None
                    else {}
                ),
                "model_id": sampler.model_id,
                **sampled.values,
                "execution_kind": "sample_path",
                "record_kind": "sample",
                "sample_index": index,
                "sampling_seed": sample_seed,
                "sampling_plan_path": str(Path(config.sampling_plan_path).resolve()),
            },
            reason=f"DYNAMICS_IMPORTANCE_{run_kind.upper()}: {sampler.plan.experiment_id} sample={index}",
            meta={
                "sampling": sampled.sampling_metadata,
                "source_module": "targets.dynamics.importance_sampling",
            },
        )


def _sample_seed(seed: int | None, index: int) -> int | None:
    return legacy_sample_seed(seed, index)


class DynamicsImportanceSamplingSupport:
    """Importance-sampling hook of the dynamics plugin (v1 U-turn inputs, v2 registered models)."""

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--phase", choices=sorted(PHASES), default="final",
            help="v2: random-stream phase; pilot runs never share streams with final runs",
        )
        parser.add_argument(
            "--root-entropy", type=int, default=None,
            help="v2: 128-bit root of all random streams; default: reuse the manifest's, or draw a new one",
        )
        parser.add_argument(
            "--pairing", choices=list(PAIRINGS), default="independent",
            help="v2: 'paired' uses the same streams for the ordinary and importance runs",
        )
        parser.add_argument(
            "--artifacts", choices=["files", "summary"], default="files",
            help="registered models: 'summary' keeps trajectories in memory and stores only the judged records",
        )
        parser.add_argument(
            "--retry-failed", action="store_true",
            help="v2: retry samples whose latest attempt failed, with the same inputs and streams",
        )

    def run(self, args) -> dict[str, object]:
        from targets.dynamics.backend import DynamicsBackend, DynamicsBackendConfig
        from targets.dynamics.result_interpreter import DynamicsResultInterpreter

        if args.max_samples is None:
            raise ValueError("--max-samples is required")
        backend = DynamicsBackend(DynamicsBackendConfig(
            output_root=Path(args.output_root) / "artifacts", artifact_policy=args.artifacts,
        ))
        runner = DynamicsImportanceSamplingRunner(backend=backend, result_interpreter=DynamicsResultInterpreter())
        return runner.run_fixed_samples(
            DynamicsImportanceSamplingRunConfig(
                sampling_plan_path=args.sampling_plan,
                output_root=args.output_root,
                max_samples=args.max_samples,
                metric=args.metric,
                confidence=args.confidence,
                seed=args.sampling_seed,
                phase=args.phase,
                root_entropy=args.root_entropy,
                pairing=args.pairing,
                retry_failed=args.retry_failed,
            )
        )


__all__ = [
    "DynamicsImportanceSamplingRunConfig",
    "DynamicsImportanceSamplingRunner",
    "DynamicsImportanceSamplingSupport",
]
