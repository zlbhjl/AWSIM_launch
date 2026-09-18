import pandas as pd

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from contracts.statistics import StatisticalRequest
from evaluation.dkw import (
    DKWService,
    calculate_dkw_bounds,
    calculate_quantile_with_dkw,
    evaluate_and_summarize_dkw,
    evaluate_and_summarize_dkw_multiple,
    evaluate_statistical_request,
    evaluate_statistical_request_multiple,
    records_to_data_frame,
)


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "loop_num": [1, 2, 3, 4],
            "dx0": [10.0, 11.0, 12.0, 13.0],
            "ego_speed": [30.0, 31.0, 32.0, 33.0],
            "c_collision": [0, 0, 0, 0],
            "min_ttc": [1.0, 2.0, 3.0, 4.0],
            "min_distance": [2.0, 3.0, 4.0, 5.0],
        }
    )


def test_calculate_dkw_bounds_returns_ecdf_payload() -> None:
    result = calculate_dkw_bounds(_sample_df(), target_column="min_ttc", delta=0.1)

    assert result is not None
    assert list(result["x"]) == [1.0, 2.0, 3.0, 4.0]
    assert len(result["ecdf"]) == 4
    assert result["sample_size"] == 4.0


def test_calculate_quantile_with_dkw_returns_quantile_interval() -> None:
    result = calculate_quantile_with_dkw(
        _sample_df(),
        target_column="min_ttc",
        q=0.5,
        delta=0.1,
    )

    assert result is not None
    assert result["estimate"] == 2.0
    assert result["lower_bound"] <= result["upper_bound"]


def test_evaluate_and_summarize_dkw_multiple_returns_metric_map() -> None:
    result = evaluate_and_summarize_dkw_multiple(
        _sample_df(),
        target_columns=["min_ttc", "min_distance"],
        q=0.5,
        delta_total=0.1,
    )

    assert result["status"] == "success"
    assert set(result["metrics"]) == {"min_ttc", "min_distance"}


def test_evaluate_and_summarize_dkw_rejects_small_dataset() -> None:
    result = evaluate_and_summarize_dkw(
        pd.DataFrame({"loop_num": [1], "min_ttc": [1.0]}),
        target_column="min_ttc",
    )

    assert result["status"] == "error"


def test_records_to_data_frame_flattens_evaluation_records() -> None:
    record = EvaluationRecord(
        case_id="case-1",
        target="awsim",
        case_kind="uturn",
        status=RunStatus.SUCCESS,
        input={"dx0": 10.0, "ego_speed": 30.0},
        output={"c_collision": 0, "min_ttc": 1.5},
        meta={"global_loop_num": 12, "task_reason": "STEP1"},
    )

    df = records_to_data_frame([record])

    assert df is not None
    assert df.iloc[0]["dx0"] == 10.0
    assert df.iloc[0]["min_ttc"] == 1.5
    assert df.iloc[0]["loop_num"] == 12
    assert df.iloc[0]["reason"] == "STEP1"


def test_evaluate_statistical_request_returns_statistical_report() -> None:
    request = StatisticalRequest(
        method="dkw",
        metric="min_ttc",
        confidence=0.9,
        target_width=3.5,
        options={"q": 0.5},
    )

    report = evaluate_statistical_request(_sample_df(), request)

    assert report.method == "dkw"
    assert report.metric == "min_ttc"
    assert report.sample_count == 4
    assert report.interval is not None
    assert report.diagnostics["status"] == "success"
    assert report.next_action == "stop"


def test_evaluate_statistical_request_multiple_splits_error_budget() -> None:
    request = StatisticalRequest(
        method="dkw",
        metric="min_ttc",
        confidence=0.9,
        options={"q": 0.5},
    )

    reports = evaluate_statistical_request_multiple(
        _sample_df(),
        metrics=["min_ttc", "min_distance"],
        request=request,
    )

    assert set(reports) == {"min_ttc", "min_distance"}
    assert reports["min_ttc"].diagnostics["confidence"] == 0.95
    assert reports["min_distance"].diagnostics["confidence"] == 0.95


def test_dkw_service_handles_evaluation_record_sequences() -> None:
    records = [
        EvaluationRecord(
            case_id=f"case-{index}",
            target="awsim",
            case_kind="uturn",
            status=RunStatus.SUCCESS,
            input={"dx0": 10.0 + index, "ego_speed": 30.0 + index},
            output={"c_collision": 0, "min_ttc": float(index + 1)},
            meta={"global_loop_num": index + 1},
        )
        for index in range(4)
    ]
    service = DKWService()
    request = StatisticalRequest(method="dkw", metric="min_ttc", options={"q": 0.5})

    report = service.evaluate_request(records, request)

    assert report.sample_count == 4
    assert report.estimate == 2.0


def test_calculate_dkw_bounds_returns_none_for_a_single_sample() -> None:
    # n=1 is a degenerate case for the searchsorted-based quantile lookup: the
    # single-element array always makes lower_index/upper_index resolve to
    # the same element, reporting a spurious width-0 "converged" interval
    # regardless of the true DKW margin. Treat it as "not enough data yet".
    frame = pd.DataFrame({"steps_to_failure_capped": [5.0], "status": ["success"]})

    result = calculate_dkw_bounds(frame, target_column="steps_to_failure_capped")

    assert result is None


def test_calculate_quantile_with_dkw_computes_a_genuine_interval_at_two_samples() -> None:
    frame = pd.DataFrame(
        {"steps_to_failure_capped": [1.0, 20.0], "status": ["success", "success"]}
    )

    result = calculate_quantile_with_dkw(frame, target_column="steps_to_failure_capped")

    assert result is not None
    assert result["sample_size"] == 2
    assert result["upper_bound"] - result["lower_bound"] > 0.0


def test_evaluate_request_reports_collect_more_samples_for_a_single_sample() -> None:
    frame = pd.DataFrame({"steps_to_failure_capped": [5.0], "status": ["success"]})
    request = StatisticalRequest(
        method="dkw",
        metric="steps_to_failure_capped",
        confidence=0.95,
        target_width=0.15,
        options={"epsilon": 0.15},
    )

    report = DKWService().evaluate_request(frame, request)

    assert report.next_action == "collect_more_samples"
    assert report.diagnostics["status"] == "pending"
    assert report.sample_count == 0


def test_dkw_minimum_value_is_request_policy_not_metric_name() -> None:
    frame = pd.DataFrame(
        {
            "steps_to_failure_capped": [-1, 3, 5, 7],
            "status": ["success", "success", "timeout", "success"],
        }
    )
    request = StatisticalRequest(
        method="dkw",
        metric="steps_to_failure_capped",
        options={"q": 0.5, "minimum_value": 0.0},
    )

    report = DKWService().evaluate_request(frame, request)

    # status=timeout (5) is dropped first, then minimum_value=0.0 drops -1,
    # leaving [3, 7] as the only valid samples.
    assert report.sample_count == 2
    assert report.estimate == 3
    assert report.diagnostics["minimum_value"] == 0.0
