from __future__ import annotations

from pathlib import Path

from contracts.evaluation import EvaluationRecord
from contracts.execution import RawRunResult, RunStatus
from orchestration.dynamics_importance_sampling import (
    DynamicsImportanceSamplingRunConfig,
    DynamicsImportanceSamplingRunner,
    _sample_seed,
)


def test_dynamics_fixed_runner_separates_p_and_q_runs(tmp_path: Path) -> None:
    plan = tmp_path / "sampling_plan.yaml"
    plan.write_text(
        """schema_version: 1
experiment_id: dynamics_is_v1
target: dynamics
target_measure:
  id: uturn_uniform_p_v1
  definition:
    model_id: uturn
    parameters:
      dx0: {distribution: uniform, min: 10.0, max: 25.0}
      ego_speed: {distribution: uniform, min: 30.0, max: 40.0}
      npc_speed: {distribution: uniform, min: 10.0, max: 25.0}
proposal:
  id: dx0_focus_q1
  mode: manual_proposal
  definition:
    parameters:
      dx0:
        distribution: mixture_uniform_focus
        base_weight: 0.7
        focus: {min: 10.0, max: 14.0}
      ego_speed: {distribution: uniform, min: 30.0, max: 40.0}
      npc_speed: {distribution: uniform, min: 10.0, max: 25.0}
""",
        encoding="utf-8",
    )

    class Backend:
        def run(self, case):
            return RawRunResult(case.case_id, case.target, case.case_kind, RunStatus.SUCCESS)

    class Interpreter:
        def interpret_raw_run_result(self, raw):
            return EvaluationRecord(
                raw.case_id, raw.target, raw.case_kind, raw.status,
                output={"c_collision": 0}, meta=dict(raw.meta),
            )

    result = DynamicsImportanceSamplingRunner(
        backend=Backend(), result_interpreter=Interpreter(),
    ).run_fixed_samples(
        DynamicsImportanceSamplingRunConfig(
            sampling_plan_path=plan, output_root=tmp_path / "run", max_samples=2, seed=10,
        )
    )

    assert (tmp_path / "run" / "ordinary" / "records.jsonl").exists()
    assert (tmp_path / "run" / "importance" / "records.jsonl").exists()
    assert result["comparison"]["target"] == "dynamics"
    assert result["comparison"]["target_measure_id"] == "uturn_uniform_p_v1"


def test_outer_seed_uses_disjoint_sample_seed_blocks() -> None:
    first = {_sample_seed(0, index) for index in range(1, 101)}
    second = {_sample_seed(1, index) for index in range(1, 101)}

    assert first.isdisjoint(second)
