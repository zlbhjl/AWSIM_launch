import math

import pandas as pd
import pytest

from contracts.statistics import StatisticalRequest
from evaluation.sprt import (
    SPRTService,
    calculate_sprt_decision,
    evaluate_sprt_request,
    sprt_decide,
)


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "loop_num": [1, 2, 3, 4, 5],
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


def test_sprt_decide_thresholds_match_wald_formula() -> None:
    result = sprt_decide(samples=10, successes=5, p0=0.6, p1=0.4, alpha=0.04, beta=0.08)

    assert result["lower_log_threshold"] == pytest.approx(math.log(0.08 / 0.96))
    assert result["upper_log_threshold"] == pytest.approx(math.log(0.92 / 0.04))


def test_sprt_decide_accepts_h0_with_high_success_rate() -> None:
    result = sprt_decide(samples=10, successes=10, p0=0.9, p1=0.1, alpha=0.05, beta=0.05)

    assert result["verdict"] == "h0"


def test_sprt_decide_accepts_h1_with_low_success_rate() -> None:
    result = sprt_decide(samples=10, successes=0, p0=0.9, p1=0.1, alpha=0.05, beta=0.05)

    assert result["verdict"] == "h1"


def test_sprt_decide_continues_with_ambiguous_data() -> None:
    result = sprt_decide(samples=2, successes=1, p0=0.9, p1=0.1, alpha=0.05, beta=0.05)

    assert result["verdict"] == "continue"


def test_sprt_decide_rejects_equal_hypotheses() -> None:
    with pytest.raises(ValueError):
        sprt_decide(samples=10, successes=5, p0=0.5, p1=0.5, alpha=0.05, beta=0.05)


def test_sprt_decide_rejects_successes_greater_than_samples() -> None:
    with pytest.raises(ValueError):
        sprt_decide(samples=5, successes=6, p0=0.6, p1=0.4, alpha=0.05, beta=0.05)


def test_calculate_sprt_decision_filters_non_success_status() -> None:
    df = pd.DataFrame(
        {
            "c_collision": [1, 0, 1, 0],
            "status": ["success", "timeout", "analysis_error", "execution_error"],
        }
    )

    result = calculate_sprt_decision(
        df,
        target_column="c_collision",
        p0=0.9,
        p1=0.1,
        alpha=0.05,
        beta=0.05,
    )

    assert result is not None
    assert result["sample_size"] == 1
    assert result["success_count"] == 1


def test_calculate_sprt_decision_filters_reason_pattern() -> None:
    result = calculate_sprt_decision(
        _sample_df(),
        target_column="c_collision",
        p0=0.9,
        p1=0.1,
        alpha=0.05,
        beta=0.05,
        reason_pattern="manual_boundary",
    )

    assert result is not None
    assert result["sample_size"] == 3
    assert result["success_count"] == 1


def test_calculate_sprt_decision_returns_none_for_missing_column() -> None:
    result = calculate_sprt_decision(
        _sample_df(),
        target_column="does_not_exist",
        p0=0.9,
        p1=0.1,
        alpha=0.05,
        beta=0.05,
    )

    assert result is None


def test_evaluate_request_stops_when_decision_reached() -> None:
    df = pd.DataFrame(
        {
            "c_collision": [1] * 10,
            "status": ["success"] * 10,
        }
    )
    request = StatisticalRequest(
        method="sprt",
        metric="c_collision",
        confidence=0.95,
        options={"p0": 0.9, "p1": 0.1, "beta": 0.05},
    )

    report = evaluate_sprt_request(df, request)

    assert report.next_action == "stop"
    assert report.diagnostics["verdict"] == "h0"
    assert report.sample_count == 10


def test_evaluate_request_collects_when_no_samples_exist_yet() -> None:
    request = StatisticalRequest(
        method="sprt",
        metric="c_collision",
        confidence=0.95,
        options={"p0": 0.9, "p1": 0.1, "beta": 0.05},
    )

    report = evaluate_sprt_request(None, request)

    assert report.next_action == "collect_more_samples"
    assert report.sample_count == 0
    assert report.diagnostics["status"] == "pending"


def test_evaluate_request_continues_with_ambiguous_data() -> None:
    df = pd.DataFrame(
        {
            "c_collision": [1, 0],
            "status": ["success", "success"],
        }
    )
    request = StatisticalRequest(
        method="sprt",
        metric="c_collision",
        confidence=0.95,
        options={"p0": 0.9, "p1": 0.1, "beta": 0.05},
    )

    report = evaluate_sprt_request(df, request)

    assert report.next_action == "collect_more_samples"
    assert report.diagnostics["verdict"] == "continue"


def test_evaluate_request_stops_at_max_samples_without_decision() -> None:
    df = pd.DataFrame(
        {
            "c_collision": [1, 0],
            "status": ["success", "success"],
        }
    )
    request = StatisticalRequest(
        method="sprt",
        metric="c_collision",
        confidence=0.95,
        options={"p0": 0.9, "p1": 0.1, "beta": 0.05, "max_samples": 2},
    )

    report = evaluate_sprt_request(df, request)

    assert report.next_action == "stop_max_samples"


def test_evaluate_request_returns_error_report_for_missing_metric() -> None:
    df = pd.DataFrame({"other_metric": [1, 0], "status": ["success", "success"]})
    request = StatisticalRequest(
        method="sprt",
        metric="c_collision",
        confidence=0.95,
        options={"p0": 0.9, "p1": 0.1, "beta": 0.05},
    )

    report = evaluate_sprt_request(df, request)

    assert report.next_action == "error"
    assert report.diagnostics["status"] == "error"


def test_evaluate_request_two_sided_accepts_true_when_metric_is_frequent() -> None:
    # theta/delta define the tested property as "P(c_collision=1) is high"
    # (Younes 2006 sec. 5.1: both one-sided tests accept H0 -> verdict "true").
    df = pd.DataFrame(
        {
            "c_collision": [1] * 20,
            "status": ["success"] * 20,
        }
    )
    request = StatisticalRequest(
        method="sprt",
        metric="c_collision",
        confidence=0.96,
        options={"theta": 0.5, "delta": 0.1, "beta": 0.08, "gamma": 0.1},
    )

    report = SPRTService().evaluate_request(df, request)

    assert report.next_action == "stop"
    assert report.diagnostics["verdict"] == "true"


def test_evaluate_request_two_sided_accepts_false_when_metric_is_rare() -> None:
    # both one-sided tests accept H1 -> verdict "false" (property does not hold).
    df = pd.DataFrame(
        {
            "c_collision": [0] * 20,
            "status": ["success"] * 20,
        }
    )
    request = StatisticalRequest(
        method="sprt",
        metric="c_collision",
        confidence=0.96,
        options={"theta": 0.5, "delta": 0.1, "beta": 0.08, "gamma": 0.1},
    )

    report = SPRTService().evaluate_request(df, request)

    assert report.next_action == "stop"
    assert report.diagnostics["verdict"] == "false"
