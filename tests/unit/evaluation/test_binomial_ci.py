import pandas as pd

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from contracts.statistics import StatisticalRequest
from evaluation.binomial_ci import (
    BinomialCIService,
    calculate_binomial_confidence_interval,
    evaluate_and_summarize_binomial_ci,
    evaluate_binomial_request,
)


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "loop_num": [1, 2, 3, 4, 5],
            "dx0": [10.0, 10.5, 11.0, 11.5, 12.0],
            "c_collision": [0, 1, 1, 0, 1],
            "status": ["success", "success", "success", "success", "success"],
            "reason": [
                "manual_boundary",
                "manual_boundary",
                "random",
                "manual_boundary",
                "random",
            ],
        }
    )


def test_calculate_binomial_confidence_interval_wilson() -> None:
    result = calculate_binomial_confidence_interval(
        _sample_df(),
        target_column="c_collision",
        confidence_level=0.95,
        method="wilson",
    )

    assert result is not None
    assert result["sample_size"] == 5
    assert result["success_count"] == 3
    assert 0.0 <= result["lower_bound"] <= result["upper_bound"] <= 1.0


def test_calculate_binomial_confidence_interval_filters_reason_pattern() -> None:
    result = calculate_binomial_confidence_interval(
        _sample_df(),
        target_column="c_collision",
        reason_pattern="manual_boundary",
    )

    assert result is not None
    assert result["sample_size"] == 3
    assert result["success_count"] == 1


def test_calculate_binomial_confidence_interval_excludes_non_success_rows() -> None:
    df = pd.DataFrame(
        {
            "c_collision": [1, 0, 1, 0],
            "status": ["success", "timeout", "analysis_error", "execution_error"],
        }
    )

    result = calculate_binomial_confidence_interval(
        df,
        target_column="c_collision",
    )

    assert result is not None
    assert result["sample_size"] == 1
    assert result["success_count"] == 1


def test_evaluate_and_summarize_binomial_ci_returns_success_payload() -> None:
    result = evaluate_and_summarize_binomial_ci(
        _sample_df(),
        target_column="c_collision",
        method="clopper-pearson",
    )

    assert result["status"] == "success"
    assert result["method"] == "clopper-pearson"


def test_calculate_binomial_confidence_interval_rejects_unknown_method() -> None:
    try:
        calculate_binomial_confidence_interval(
            _sample_df(),
            target_column="c_collision",
            method="unknown",
        )
    except ValueError as exc:
        assert "Unsupported binomial CI method" in str(exc)
    else:
        raise AssertionError("ValueError was not raised")


def test_evaluate_binomial_request_returns_statistical_report() -> None:
    request = StatisticalRequest(
        method="binomial_ci",
        metric="c_collision",
        confidence=0.95,
        target_width=0.8,
        options={"method": "wilson", "reason_pattern": "manual_boundary"},
    )

    report = evaluate_binomial_request(_sample_df(), request)

    assert report.method == "binomial_ci"
    assert report.metric == "c_collision"
    assert report.sample_count == 3
    assert report.interval is not None
    assert report.diagnostics["status"] == "success"
    assert report.next_action == "stop"


def test_evaluate_binomial_request_collects_when_no_samples_exist_yet() -> None:
    request = StatisticalRequest(
        method="binomial_ci",
        metric="c_collision",
        confidence=0.95,
        target_width=0.02,
        options={"method": "wilson", "reason_pattern": "BINOMIAL_CI:"},
    )

    report = evaluate_binomial_request(None, request)

    assert report.next_action == "collect_more_samples"
    assert report.sample_count == 0
    assert report.diagnostics["status"] == "pending"


def test_evaluate_binomial_request_collects_when_reason_filter_has_no_samples_yet() -> None:
    request = StatisticalRequest(
        method="binomial_ci",
        metric="c_collision",
        confidence=0.95,
        target_width=0.02,
        options={"method": "wilson", "reason_pattern": "BINOMIAL_CI:"},
    )

    report = evaluate_binomial_request(_sample_df(), request)

    assert report.next_action == "collect_more_samples"
    assert report.sample_count == 0
    assert report.diagnostics["status"] == "pending"


def test_binomial_service_accepts_evaluation_record_sequences() -> None:
    records = [
        EvaluationRecord(
            case_id=f"case-{index}",
            target="awsim",
            case_kind="uturn",
            status=RunStatus.SUCCESS,
            input={"dx0": 10.0 + index},
            output={"c_collision": int(index in (1, 2, 4))},
            meta={"global_loop_num": index + 1, "task_reason": "manual_boundary"},
        )
        for index in range(5)
    ]
    service = BinomialCIService()
    request = StatisticalRequest(
        method="binomial_ci",
        metric="c_collision",
        options={"method": "wilson", "reason_pattern": "manual_boundary"},
    )

    report = service.evaluate_request(records, request)

    assert report.sample_count == 5
    assert report.estimate == 3 / 5


def test_evaluate_binomial_request_returns_error_report_for_missing_metric() -> None:
    request = StatisticalRequest(method="binomial_ci", metric="missing_metric")

    report = evaluate_binomial_request(_sample_df(), request)

    assert report.next_action == "error"
    assert report.diagnostics["status"] == "error"


def test_prism_metric_is_not_filtered_by_awsim_collision_sentinel() -> None:
    frame = pd.DataFrame(
        {
            "c_failure": [0, 1],
            "c_collision": [-1, -1],
            "status": ["success", "success"],
        }
    )

    result = calculate_binomial_confidence_interval(
        frame,
        target_column="c_failure",
    )

    assert result is not None
    assert result["sample_size"] == 2
    assert result["estimate"] == 0.5
