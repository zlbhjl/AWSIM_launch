from contracts.execution import RawRunResult, RunStatus, TestCase


def test_run_status_values_are_stable() -> None:
    assert RunStatus.SUCCESS.value == "success"
    assert RunStatus.TIMEOUT.value == "timeout"
    assert RunStatus.EXECUTION_ERROR.value == "execution_error"
    assert RunStatus.ANALYSIS_ERROR.value == "analysis_error"
    assert RunStatus.INVALID.value == "invalid"


def test_test_case_defaults_are_empty_collections() -> None:
    case = TestCase(case_id="case-1", target="awsim", case_kind="uturn")

    assert case.input == {}
    assert case.tags == []
    assert case.reason == ""
    assert case.meta == {}


def test_raw_run_result_defaults_are_empty_collections() -> None:
    result = RawRunResult(
        case_id="case-1",
        target="awsim",
        case_kind="uturn",
        status=RunStatus.SUCCESS,
    )

    assert result.evidence == {}
    assert result.meta == {}
