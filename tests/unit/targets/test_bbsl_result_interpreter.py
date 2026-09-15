from pathlib import Path

from contracts.execution import RawRunResult, RunStatus
from targets.bbsl.result_interpreter import ResultInterpreter


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "bbsl"


def test_interpreter_reads_normal_full_all_fixture() -> None:
    record = ResultInterpreter().interpret_fixture(
        FIXTURES / "experiment_all_raw_result_mini.json"
    )

    assert record.target == "bbsl"
    assert record.case_kind == "full_all"
    assert record.status == RunStatus.SUCCESS
    assert record.input["sigma_pb_mode"] == "delta-clean"
    assert record.output["universal_dataset_size"] == 32
    assert record.output["active_condition_count"] == 4
    assert record.output["condition_result_count"] == 4
    assert record.output["condition_results"]["clean"]["total_count"] == 7
    assert record.output["condition_results"]["clean"]["error_count"] == 0
    assert record.meta["verifier_name"] == "bbsl"
    assert "raw_result_json" in record.evidence


def test_interpreter_marks_missing_bbsl_results_as_analysis_error() -> None:
    record = ResultInterpreter().interpret_fixture(
        FIXTURES / "invalid_missing_bbsl_results.json"
    )

    assert record.case_kind == "full_all"
    assert record.status == RunStatus.ANALYSIS_ERROR
    assert record.meta["error_message"] == "missing_top_level_keys:bbsl_results"


def test_interpreter_marks_empty_result_set_as_invalid() -> None:
    record = ResultInterpreter().interpret_fixture(
        FIXTURES / "empty_experiment_all_raw_result.json"
    )

    assert record.case_kind == "full_all"
    assert record.status == RunStatus.INVALID
    assert record.meta["error_message"] == "empty_bbsl_results"


def test_interpreter_uses_mode_for_batch_case_kind() -> None:
    record = ResultInterpreter().interpret_fixture(FIXTURES / "clean_baseline.json")

    assert record.case_kind == "clean_baseline"
    assert record.status == RunStatus.SUCCESS
    assert record.output["condition_result_count"] == 1
    assert record.output["condition_results"]["clean"]["total_count"] == 51865


def test_interpreter_accepts_raw_run_result() -> None:
    interpreter = ResultInterpreter()
    raw_run_result = RawRunResult(
        case_id="bbsl_worker_case_1",
        target="bbsl",
        case_kind="full_all",
        status=RunStatus.SUCCESS,
        evidence={"raw_result_json": str(FIXTURES / "experiment_all_raw_result_mini.json")},
        meta={"returncode": 0},
    )

    record = interpreter.interpret_raw_run_result(raw_run_result)

    assert record.case_id == "bbsl_worker_case_1"
    assert record.status is RunStatus.SUCCESS
    assert record.meta["raw_run_status"] == "success"
    assert record.meta["raw_run_meta"] == {"returncode": 0}
    assert record.meta["execution_status"] == "success"
    assert record.meta["analysis_status"] == "success"
