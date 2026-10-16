"""Fixed-Q input importance sampling for the U-turn dynamics target."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from contracts.execution import TestCase
from orchestration.fixed_importance_sampling import run_fixed_importance_sampling_comparison
from orchestration.sampling_plan import load_sampling_plan
from runtime.repository.importance_sampling_results import ImportanceSamplingResultsRepository
from targets.dynamics.sampling import DynamicsInputSampler


@dataclass(frozen=True, slots=True)
class DynamicsImportanceSamplingRunConfig:
    sampling_plan_path: str | Path
    output_root: str | Path
    max_samples: int
    metric: str = "c_collision"
    confidence: float = 0.95
    seed: int | None = None
    params: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.max_samples < 1:
            raise ValueError("max_samples must be positive")
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
        sampler = DynamicsInputSampler(plan)
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
                "source_module": "orchestration.dynamics_importance_sampling",
            },
        )


def _sample_seed(seed: int | None, index: int) -> int | None:
    """Give each outer replicate a disjoint deterministic seed block.

    Ordinary and proposal runs still use common random numbers for one outer
    seed, while different outer seeds do not silently reuse 99 of 100 samples.
    This matches the PRISM importance-sampling runner's seed contract.
    """

    if seed is None:
        return None
    if index < 1:
        raise ValueError("sample index must be positive")
    return int(seed) * 1_000_000 + int(index)


__all__ = ["DynamicsImportanceSamplingRunConfig", "DynamicsImportanceSamplingRunner"]
