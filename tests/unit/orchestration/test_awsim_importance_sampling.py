from __future__ import annotations

from pathlib import Path

from contracts.evaluation import EvaluationRecord
from contracts.execution import RawRunResult, RunStatus
from orchestration.awsim_importance_sampling import (
    AWSIMImportanceSamplingRunConfig,
    AWSIMImportanceSamplingRunner,
)


def test_awsim_fixed_runner_separates_runs_and_preserves_sampling_metadata(tmp_path: Path) -> None:
    plan = Path(__file__).resolve().parents[3] / "configs" / "awsim_importance_baseline.yaml"

    class Backend:
        def run(self, case):
            return RawRunResult(case.case_id, case.target, case.case_kind, RunStatus.SUCCESS)

    class Interpreter:
        def interpret_raw_run_result(self, raw):
            return EvaluationRecord(
                raw.case_id, raw.target, raw.case_kind, raw.status,
                output={"c_collision": 0}, meta=dict(raw.meta),
            )

    result = AWSIMImportanceSamplingRunner(
        backend=Backend(), result_interpreter=Interpreter(),
    ).run_fixed_samples(
        AWSIMImportanceSamplingRunConfig(
            sampling_plan_path=plan, output_root=tmp_path / "run", max_samples=2, seed=9,
        )
    )

    assert (tmp_path / "run" / "ordinary" / "records.jsonl").exists()
    assert (tmp_path / "run" / "importance" / "records.jsonl").exists()
    assert result["comparison"]["target"] == "awsim"
    assert result["comparison"]["target_measure_id"] == "synthetic_uturn_speed_histogram_p_v1"


def test_awsim_runner_marks_scenario_profile_in_case_metadata(tmp_path: Path) -> None:
    plan = Path(__file__).resolve().parents[3] / "configs" / "awsim_importance_baseline.yaml"
    captured = []

    class Backend:
        def run(self, case):
            captured.append(case)
            return RawRunResult(case.case_id, case.target, case.case_kind, RunStatus.SUCCESS)

    class Interpreter:
        def interpret_raw_run_result(self, raw):
            return EvaluationRecord(raw.case_id, raw.target, raw.case_kind, raw.status, output={"c_collision": 0})

    AWSIMImportanceSamplingRunner(backend=Backend(), result_interpreter=Interpreter()).run_fixed_samples(
        AWSIMImportanceSamplingRunConfig(
            sampling_plan_path=plan, output_root=tmp_path / "run", max_samples=1,
            scenario_profile="autoware171",
        )
    )
    assert {case.meta["scenario_profile"] for case in captured} == {"autoware171"}
