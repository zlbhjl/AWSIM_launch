from pathlib import Path

from contracts.execution import RawRunResult, RunStatus
from contracts.statistics import StatisticalRequest
from evaluation.binomial_ci import BinomialCIService
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


def test_binomial_service_accepts_only_successfully_interpreted_timeout_traces() -> None:
    interpreter = ResultInterpreter()
    normal_trace = str(FIXTURES / "normal_trace_maude.json")
    invalid_trace = str(FIXTURES / "maude_failure_missing_vehicle_sizes.json")
    raw_results = [
        RawRunResult(
            case_id="timeout_with_trace",
            target="awsim",
            case_kind="uturn",
            status=RunStatus.TIMEOUT,
            evidence={"trace_json": normal_trace},
            meta={"returncode": 124},
        ),
        RawRunResult(
            case_id="timeout_with_late_trace",
            target="awsim",
            case_kind="uturn",
            status=RunStatus.TIMEOUT,
            evidence={"trace_json": normal_trace},
            meta={"returncode": 124, "artifact_timing": "late"},
        ),
        RawRunResult(
            case_id="timeout_without_trace",
            target="awsim",
            case_kind="uturn",
            status=RunStatus.TIMEOUT,
            meta={"returncode": 124, "artifact_timing": "missing"},
        ),
        RawRunResult(
            case_id="timeout_with_invalid_trace",
            target="awsim",
            case_kind="uturn",
            status=RunStatus.TIMEOUT,
            evidence={"trace_json": invalid_trace},
            meta={"returncode": 124},
        ),
    ]

    records = [interpreter.interpret_raw_run_result(raw) for raw in raw_results]
    report = BinomialCIService().evaluate_request(
        records,
        StatisticalRequest(method="binomial_ci", metric="c_collision"),
    )

    assert [record.status for record in records] == [
        RunStatus.SUCCESS,
        RunStatus.SUCCESS,
        RunStatus.TIMEOUT,
        RunStatus.ANALYSIS_ERROR,
    ]
    assert all(record.meta["raw_run_status"] == "timeout" for record in records)
    assert report.sample_count == 2
    assert report.estimate == 0.0


def test_result_interpreter_records_kinematics_metrics_like_legacy_awchecker() -> None:
    from targets.awsim.kinematics_bridge import extract_kinematics_metrics

    fixture = FIXTURES / "normal_trace_maude.json"
    expected = extract_kinematics_metrics(fixture)

    record = ResultInterpreter().interpret_fixture(fixture)

    assert record.status is RunStatus.SUCCESS
    for key in ("min_ttc", "min_distance", "min_ttb", "z_margin"):
        assert record.output[key] == expected[key]
    # Maude stays the source of truth for the collision verdict.
    assert record.output["c_collision"] == 0


def test_result_interpreter_keeps_maude_result_when_kinematics_extraction_fails() -> None:
    def failing_extractor(_: Path) -> dict[str, object]:
        raise RuntimeError("broken trace")

    record = ResultInterpreter(kinematics_extractor=failing_extractor).interpret_fixture(
        FIXTURES / "normal_trace_maude.json"
    )

    assert record.status is RunStatus.SUCCESS
    assert record.output["min_ttc"] == ""
    assert record.output["c_collision"] == 0
    assert record.meta["kinematics_error"] == "RuntimeError: broken trace"


def test_result_interpreter_forces_zero_distance_on_collision() -> None:
    interpreter = ResultInterpreter(kinematics_extractor=lambda _: {"min_ttc": 0.4, "min_distance": 0.7})
    stdout = "".join(
        f"Checking formula: {spec.formula}\nModel checking result: "
        f"{'False' if spec.header == 'c_collision' else 'True'}\n"
        for spec in interpreter.formula_specs
    )
    interpreter.maude_runner = lambda _path, _formulas: MaudeRunResult(
        command=["python3", "aw_checkerpy.py"], workdir="/tmp", returncode=0, stdout=stdout, stderr=""
    )

    record = interpreter.interpret_fixture(FIXTURES / "normal_trace_maude.json")

    assert record.output["c_collision"] == 1
    assert record.output["min_distance"] == 0.0
    assert record.output["min_ttc"] == 0.4


def test_result_interpreter_invalidates_kinematics_on_maude_error() -> None:
    def fake_runner(_: Path, __: list[str]) -> MaudeRunResult:
        return MaudeRunResult(command=["python3"], workdir="/tmp", returncode=1, stdout="", stderr="boom")

    record = ResultInterpreter(
        maude_runner=fake_runner,
        kinematics_extractor=lambda _: {"min_ttc": 2.0, "min_distance": 3.0, "min_ttb": 1.0, "z_margin": 3.0},
    ).interpret_fixture(FIXTURES / "normal_trace_maude.json")

    assert record.status is RunStatus.ANALYSIS_ERROR
    assert {record.output[key] for key in ("min_ttc", "min_distance", "min_ttb", "z_margin")} == {-1}


def test_result_interpreter_flags_empty_required_trace_sections(tmp_path: Path) -> None:
    import json

    payload = json.loads((FIXTURES / "normal_trace_maude.json").read_text(encoding="utf-8"))
    payload["planning_trajectory"] = []
    trace_path = tmp_path / "uturn_eval_sim1.json"
    trace_path.write_text(json.dumps(payload), encoding="utf-8")

    record = ResultInterpreter().interpret_path(trace_path)

    assert record.status is RunStatus.SUCCESS
    assert record.meta["empty_trace_keys"] == "planning_trajectory"


def test_result_interpreter_does_not_flag_complete_trace() -> None:
    record = ResultInterpreter().interpret_fixture(FIXTURES / "normal_trace_maude.json")

    assert "empty_trace_keys" not in record.meta
