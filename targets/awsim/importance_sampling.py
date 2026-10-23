"""Fixed-P/fixed-Q input importance sampling for AWSIM U-turn cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from contracts.execution import TestCase
from orchestration.fixed_importance_sampling import run_fixed_importance_sampling_comparison
from orchestration.sampling_plan import load_sampling_plan
from orchestration.seeding import legacy_sample_seed
from runtime.repository.importance_sampling_results import ImportanceSamplingResultsRepository
from targets.awsim.sampling import AWSIMInputSampler


@dataclass(frozen=True, slots=True)
class AWSIMImportanceSamplingRunConfig:
    sampling_plan_path: str | Path
    output_root: str | Path
    max_samples: int
    metric: str = "c_collision"
    confidence: float = 0.95
    seed: int | None = None
    scenario_profile: str | None = None
    params: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.max_samples < 1:
            raise ValueError("max_samples must be positive")
        object.__setattr__(self, "params", dict(self.params))


class AWSIMImportanceSamplingRunner:
    """Execute fixed-size AWSIM ordinary and fixed-Q input samples."""

    def __init__(self, *, backend: object, result_interpreter: object):
        self.backend = backend
        self.result_interpreter = result_interpreter

    def run_fixed_samples(self, config: AWSIMImportanceSamplingRunConfig) -> dict[str, object]:
        plan = load_sampling_plan(config.sampling_plan_path)
        sampler = AWSIMInputSampler(plan)
        repository = ImportanceSamplingResultsRepository(config.output_root)
        return run_fixed_importance_sampling_comparison(
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

    @staticmethod
    def _build_case(
        *,
        run_kind: str,
        index: int,
        sampler: AWSIMInputSampler,
        config: AWSIMImportanceSamplingRunConfig,
    ) -> TestCase:
        seed = _sample_seed(config.seed, index)
        if run_kind == "ordinary":
            sampled = sampler.sample_target(seed=seed)
        elif run_kind == "importance":
            sampled = sampler.sample_proposal(seed=seed)
        else:
            raise ValueError("run_kind must be 'ordinary' or 'importance'")
        return TestCase(
            case_id=f"{sampler.plan.experiment_id}-{run_kind}-{index:05d}",
            target="awsim",
            case_kind=sampler.case_kind,
            input={
                **sampler.fixed_inputs,
                **config.params,
                **sampled.values,
                "execution_kind": "sample_path",
                "record_kind": "sample",
                "sample_index": index,
                "sampling_seed": seed,
                "sampling_plan_path": str(Path(config.sampling_plan_path).resolve()),
            },
            reason=f"AWSIM_IMPORTANCE_{run_kind.upper()}: {sampler.plan.experiment_id} sample={index}",
            meta={
                "sampling": sampled.sampling_metadata,
                "source_module": "targets.awsim.importance_sampling",
                **(
                    {"scenario_profile": config.scenario_profile}
                    if config.scenario_profile is not None
                    else {}
                ),
            },
        )


def _sample_seed(seed: int | None, index: int) -> int | None:
    return legacy_sample_seed(seed, index)


def build_target_components(args):
    """The normal AWSIM component assembly (kept here so tests can replace it)."""
    from targets.registry import build_target_components as build

    return build(args)


class AWSIMImportanceSamplingSupport:
    """Importance-sampling hook of the AWSIM plugin (U-turn input P/Q)."""

    def add_arguments(self, parser) -> None:
        parser.add_argument("--container-profile", default="autoware171")
        parser.add_argument("--scenario-profile", default="autoware171")
        parser.add_argument("--headless", action="store_true")
        parser.add_argument(
            "--reuse-infra-between-samples",
            action="store_true",
            help=(
                "Reuse the AWSIM infrastructure between samples. Disabled by default "
                "so that each importance-sampling record starts from a clean world."
            ),
        )

    def run(self, args) -> dict[str, object]:
        from types import SimpleNamespace

        if args.max_samples is None:
            raise ValueError("--max-samples is required")
        # Reuse the normal AWSIM component assembly.  In particular this retains
        # managed infrastructure, readiness probes, and the verified profile
        # settings instead of creating a second container startup path here.
        components = build_target_components(
            SimpleNamespace(
                target="awsim",
                case_kind="uturn",
                headless=args.headless,
                container_profile=args.container_profile,
                scenario_profile=args.scenario_profile,
                config_module=None,
                ext_mode="cvm",
                # A raw trace may be produced even when its later semantic
                # interpretation rejects the case.  Do not retain that world for
                # the next importance-sampling record unless explicitly requested.
                fresh_infra_per_sample=not args.reuse_infra_between_samples,
            )
        )
        runner = AWSIMImportanceSamplingRunner(
            backend=components.backend,
            result_interpreter=components.result_interpreter,
        )
        try:
            return runner.run_fixed_samples(
                AWSIMImportanceSamplingRunConfig(
                    sampling_plan_path=args.sampling_plan,
                    output_root=args.output_root,
                    max_samples=args.max_samples,
                    metric=args.metric or "c_collision",
                    confidence=args.confidence,
                    seed=args.sampling_seed,
                    scenario_profile=args.scenario_profile,
                )
            )
        finally:
            close = getattr(components.backend, "close", None)
            if callable(close):
                close()


__all__ = [
    "AWSIMImportanceSamplingRunConfig",
    "AWSIMImportanceSamplingRunner",
    "AWSIMImportanceSamplingSupport",
]
