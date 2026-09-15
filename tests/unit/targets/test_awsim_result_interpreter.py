from pathlib import Path

from contracts.execution import RawRunResult, RunStatus
from targets.awsim.result_interpreter import ResultInterpreter, interpret_fixture
from verifiers.maude.backend import MaudeRunResult


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "awsim"


def test_result_interpreter_returns_timeout_for_timeout_marker() -> None:
    record = interpret_fixture(FIXTURES / "timeout_trace.txt")

    assert record.status is RunStatus.TIMEOUT
    assert record.meta["verifier_name"] == "maude"
    assert "trace_json" in record.evidence
    assert record.output["min_ttc"] == -1
    assert record.output["min_distance"] == -1
    assert record.output["min_ttb"] == -1
    assert record.output["z_margin"] == -1
    assert record.output["c_collision"] == -1


def test_result_interpreter_returns_analysis_error_for_missing_vehicle_sizes() -> None:
    record = interpret_fixture(FIXTURES / "maude_failure_missing_vehicle_sizes.json")

    assert record.status is RunStatus.ANALYSIS_ERROR
    assert record.meta["error_message"] == "missing_vehicle_sizes"


def test_result_interpreter_returns_success_for_normal_fixture() -> None:
    record = interpret_fixture(FIXTURES / "normal_trace_maude.json")

    assert record.status is RunStatus.SUCCESS
    assert record.output["groundtruth_kinematic_count"] > 0
    assert record.output["vehicle_sizes_count"] > 0
    assert record.output["c_collision"] == 0
    assert record.output["c_ttc_1.5"] == 0
    assert record.meta["analysis_pipeline"] == [
        "fixture_decode",
        "structural_validation",
        "maude_backend",
        "maude_evaluator",
    ]


def test_result_interpreter_collects_optional_artifacts_next_to_trace(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "uturn_eval_sim1.json"
    trace_path.write_text(
        (FIXTURES / "normal_trace_maude.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "uturn_eval_sim1_footage.mp4").write_text("video", encoding="utf-8")
    (tmp_path / "uturn_eval_sim1_footage.meta.json").write_text("{}", encoding="utf-8")

    interpreter = ResultInterpreter()
    record = interpreter.interpret_path(trace_path)

    assert record.status is RunStatus.SUCCESS
    assert record.evidence["trace_json"] == str(trace_path)
    assert record.evidence["video"] == str(tmp_path / "uturn_eval_sim1_footage.mp4")
    assert record.evidence["video_meta_json"] == str(
        tmp_path / "uturn_eval_sim1_footage.meta.json"
    )


def test_result_interpreter_returns_analysis_error_when_maude_output_is_incomplete() -> None:
    def fake_runner(_: Path, __: list[str]) -> MaudeRunResult:
        return MaudeRunResult(
            command=["python3", "aw_checkerpy.py"],
            workdir="/tmp",
            returncode=0,
            stdout='Checking formula: [] ~ collision("ego", "npc1")\nModel checking result: True\n',
            stderr="",
        )

    interpreter = ResultInterpreter(maude_runner=fake_runner)
    record = interpreter.interpret_fixture(FIXTURES / "normal_trace_maude.json")

    assert record.status is RunStatus.ANALYSIS_ERROR
    assert record.meta["error_message"] == "maude_evaluation_error"
    assert "c_collision" in record.output


def test_result_interpreter_accepts_raw_run_result() -> None:
    interpreter = ResultInterpreter()
    raw_run_result = RawRunResult(
        case_id="worker_case_1",
        target="awsim",
        case_kind="uturn",
        status=RunStatus.SUCCESS,
        evidence={"trace_json": str(FIXTURES / "normal_trace_maude.json")},
        meta={"returncode": 0},
    )

    record = interpreter.interpret_raw_run_result(raw_run_result)

    assert record.case_id == "worker_case_1"
    assert record.status is RunStatus.SUCCESS
    assert record.meta["raw_run_status"] == "success"
    assert record.meta["raw_run_meta"] == {"returncode": 0}
    assert record.meta["execution_status"] == "success"
    assert record.meta["analysis_status"] == "success"


def test_result_interpreter_uses_checker_result_for_partial_timeout_trace() -> None:
    interpreter = ResultInterpreter()
    raw_run_result = RawRunResult(
        case_id="worker_case_timeout_trace",
        target="awsim",
        case_kind="uturn",
        status=RunStatus.TIMEOUT,
        evidence={"trace_json": str(FIXTURES / "normal_trace_maude.json")},
        meta={"returncode": 124},
    )

    record = interpreter.interpret_raw_run_result(raw_run_result)

    assert record.case_id == "worker_case_timeout_trace"
    assert record.status is RunStatus.SUCCESS
    assert record.meta["raw_run_status"] == "timeout"
    assert record.meta["execution_status"] == "timeout"
    assert record.meta["analysis_status"] == "success"
    assert record.meta["raw_run_meta"] == {"returncode": 124}
    assert record.meta["raw_timeout_reason"] == "scenario_goal_timeout"


def test_result_interpreter_keeps_late_artifact_as_raw_metadata() -> None:
    interpreter = ResultInterpreter()
    raw_run_result = RawRunResult(
        case_id="worker_case_late_trace",
        target="awsim",
        case_kind="uturn",
        status=RunStatus.TIMEOUT,
        evidence={"trace_json": str(FIXTURES / "normal_trace_maude.json")},
        meta={"returncode": 124, "artifact_timing": "late"},
    )

    record = interpreter.interpret_raw_run_result(raw_run_result)

    assert record.status is RunStatus.SUCCESS
    assert record.meta["raw_timeout_reason"] == "late_artifact"
