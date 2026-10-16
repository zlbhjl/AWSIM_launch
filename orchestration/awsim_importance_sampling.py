"""Fixed-P/fixed-Q input importance sampling for AWSIM U-turn cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from contracts.execution import TestCase
from orchestration.fixed_importance_sampling import run_fixed_importance_sampling_comparison
from orchestration.sampling_plan import load_sampling_plan
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
                "source_module": "orchestration.awsim_importance_sampling",
                **(
                    {"scenario_profile": config.scenario_profile}
                    if config.scenario_profile is not None
                    else {}
                ),
            },
        )


def _sample_seed(seed: int | None, index: int) -> int | None:
    if seed is None:
        return None
    if index < 1:
        raise ValueError("sample index must be positive")
    return int(seed) * 1_000_000 + int(index)


__all__ = ["AWSIMImportanceSamplingRunConfig", "AWSIMImportanceSamplingRunner"]
