"""Fixed-P/fixed-Q input importance sampling for AWSIM U-turn cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from contracts.execution import TestCase
from contracts.importance_sampling_plugin import CaseRequest, PreparedTarget
from orchestration.importance.runner import ImportanceRunConfig, ImportanceSamplingRunner
from orchestration.sampling_plan import SamplingPlan
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
    """Compatible entry point: the shared runner with the AWSIM hooks."""

    def __init__(self, *, backend: object, result_interpreter: object):
        self.backend = backend
        self.result_interpreter = result_interpreter

    def run_fixed_samples(self, config: AWSIMImportanceSamplingRunConfig) -> dict[str, object]:
        runner = ImportanceSamplingRunner(
            backend=self.backend, result_interpreter=self.result_interpreter, hooks=AWSIMImportanceSampling(),
        )
        return runner.run_fixed_samples(ImportanceRunConfig(
            sampling_plan_path=config.sampling_plan_path,
            output_root=config.output_root,
            max_samples=config.max_samples,
            metric=config.metric,
            confidence=config.confidence,
            seed=config.seed,
            params=config.params,
            options={"scenario_profile": config.scenario_profile},
        ))


def build_target_components(args):
    """The normal AWSIM component assembly (kept here so tests can replace it)."""
    from targets.registry import build_target_components as build

    return build(args)


class AWSIMImportanceSampling:
    """Importance-sampling hooks of the AWSIM plugin: input P/Q of a U-turn (a pure black box)."""

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

    def build_components(self, args):
        from types import SimpleNamespace

        # Reuse the normal AWSIM component assembly.  In particular this retains
        # managed infrastructure, readiness probes, and the verified profile
        # settings instead of creating a second container startup path here.
        return build_target_components(
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

    def cluster_launch_options(self, args) -> dict[str, object]:
        """How the cluster's AWSIM workers are started (apps/cli/importance_main.py --cluster)."""
        return {
            "case_kind": "uturn",
            "container_profile": args.container_profile,
            "scenario_profile": args.scenario_profile,
            "headless": args.headless,
            "fresh_infra_per_sample": not args.reuse_infra_between_samples,
        }

    def run_options(self, args) -> dict[str, object]:
        return {"options": {"scenario_profile": args.scenario_profile}}

    def prepare(self, plan: SamplingPlan, config) -> PreparedTarget:
        sampler = AWSIMInputSampler(plan)
        # Plans run before seed policies existed keep the legacy rule; new plans
        # (and every cluster run) use per-sample streams (``seed_policy: streams``).
        return PreparedTarget(default_metric="c_collision", seed_policy=plan.seed_policy or "legacy",
                              input_laws=sampler.laws, state=sampler)

    def build_case(self, request: CaseRequest) -> TestCase:
        sampler: AWSIMInputSampler = request.prepared.state
        seed = request.seeds["sample"] if "sample" in request.seeds else request.seeds["input"]
        scenario_profile = request.options.get("scenario_profile")
        return TestCase(
            case_id=f"{sampler.plan.experiment_id}-{request.run_kind}-{request.sample_index:05d}",
            target="awsim",
            case_kind=sampler.case_kind,
            input={
                **sampler.fixed_inputs,
                **request.params,
                **request.inputs,
                "execution_kind": "sample_path",
                "record_kind": "sample",
                "sample_index": request.sample_index,
                "sampling_seed": seed,
                "sampling_plan_path": request.sampling_plan_path,
            },
            reason=f"AWSIM_IMPORTANCE_{request.run_kind.upper()}: {sampler.plan.experiment_id} sample={request.sample_index}",
            meta={
                "sampling": dict(request.sampling or {}),
                "source_module": "targets.awsim.importance_sampling",
                **({"scenario_profile": scenario_profile} if scenario_profile is not None else {}),
            },
        )


__all__ = [
    "AWSIMImportanceSampling",
    "AWSIMImportanceSamplingRunConfig",
    "AWSIMImportanceSamplingRunner",
]
