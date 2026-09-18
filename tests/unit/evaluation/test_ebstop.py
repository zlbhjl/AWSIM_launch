import math

import numpy as np
import pandas as pd
import pytest

from contracts.statistics import StatisticalRequest
from evaluation.ebstop import (
    EBStopService,
    calculate_ebstop_decision,
    ebstop_update,
    evaluate_ebstop_request,
)


def test_ebstop_update_matches_paper_formula() -> None:
    result = ebstop_update(samples=50, mean=0.4, std=0.1, value_range=1.0, epsilon=0.1, delta_t=0.01)

    expected_c_t = 0.1 * math.sqrt(2 * math.log(3 / 0.01) / 50) + 3 * 1.0 * math.log(3 / 0.01) / 50
    assert result["c_t"] == pytest.approx(expected_c_t)
    assert result["lb_candidate"] == pytest.approx(0.4 - expected_c_t)
    assert result["ub_candidate"] == pytest.approx(0.4 + expected_c_t)


def test_ebstop_update_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        ebstop_update(samples=0, mean=0.1, std=0.1, value_range=1.0, epsilon=0.1, delta_t=0.01)
    with pytest.raises(ValueError):
        ebstop_update(samples=10, mean=0.1, std=-0.1, value_range=1.0, epsilon=0.1, delta_t=0.01)
    with pytest.raises(ValueError):
        ebstop_update(samples=10, mean=0.1, std=0.1, value_range=0.0, epsilon=0.1, delta_t=0.01)


def test_low_variance_data_stops_with_fewer_samples_than_high_variance() -> None:
    rng = np.random.default_rng(42)
    low_var = pd.DataFrame(
        {
            "min_ttc": 2.0 + rng.normal(0, 0.01, size=500),
            "status": ["success"] * 500,
        }
    )
    high_var = pd.DataFrame(
        {
            "min_ttc": 2.0 + rng.normal(0, 1.0, size=500),
            "status": ["success"] * 500,
        }
    )

    def first_stopping_n(df: pd.DataFrame) -> int | None:
        for n in range(2, len(df) + 1):
            result = calculate_ebstop_decision(
                df.iloc[:n],
                target_column="min_ttc",
                epsilon=0.1,
                delta=0.05,
                value_range=2.0,
            )
            if result is not None and result["stopped"]:
                return n
        return None

    low_var_stop_n = first_stopping_n(low_var)
    high_var_stop_n = first_stopping_n(high_var)

    assert low_var_stop_n is not None
    assert high_var_stop_n is None or low_var_stop_n < high_var_stop_n


def test_calculate_ebstop_decision_handles_signed_mean() -> None:
    df = pd.DataFrame(
        {
            "control_cost": [-5.0] * 200,
            "status": ["success"] * 200,
        }
    )

    result = calculate_ebstop_decision(
        df,
        target_column="control_cost",
        epsilon=0.1,
        delta=0.05,
        value_range=1.0,
    )

    assert result is not None
    assert result["stopped"] is True
    assert result["estimate"] < 0.0


def test_calculate_ebstop_decision_requires_at_least_two_samples() -> None:
    df = pd.DataFrame({"min_ttc": [2.0], "status": ["success"]})

    result = calculate_ebstop_decision(
        df, target_column="min_ttc", epsilon=0.05, delta=0.05, value_range=10.0
    )

    assert result is None


def test_calculate_ebstop_decision_accepts_value_range_as_bounds_tuple() -> None:
    df = pd.DataFrame({"min_ttc": [2.0] * 100, "status": ["success"] * 100})

    result = calculate_ebstop_decision(
        df,
        target_column="min_ttc",
        epsilon=0.05,
        delta=0.05,
        value_range=(0.0, 10.0),
    )

    assert result is not None
    assert result["value_range"] == pytest.approx(10.0)


def test_evaluate_request_stops_on_low_variance_metric() -> None:
    df = pd.DataFrame({"min_ttc": [2.0] * 100, "status": ["success"] * 100})
    request = StatisticalRequest(
        method="ebstop",
        metric="min_ttc",
        confidence=0.95,
        options={"epsilon": 0.1, "value_range": 0.5},
    )

    report = evaluate_ebstop_request(df, request)

    assert report.next_action == "stop"
    assert report.interval is not None
    assert report.diagnostics["status"] == "success"


def test_evaluate_request_collects_when_insufficient_samples() -> None:
    request = StatisticalRequest(
        method="ebstop",
        metric="min_ttc",
        confidence=0.95,
        options={"epsilon": 0.05, "value_range": 10.0},
    )

    report = evaluate_ebstop_request(None, request)

    assert report.next_action == "collect_more_samples"
    assert report.sample_count == 0
    assert report.diagnostics["status"] == "pending"


def test_evaluate_request_stops_at_max_samples_without_convergence() -> None:
    rng = np.random.default_rng(7)
    df = pd.DataFrame(
        {
            "min_ttc": 2.0 + rng.normal(0, 5.0, size=10),
            "status": ["success"] * 10,
        }
    )
    request = StatisticalRequest(
        method="ebstop",
        metric="min_ttc",
        confidence=0.95,
        options={"epsilon": 0.001, "value_range": 10.0, "max_samples": 10},
    )

    report = evaluate_ebstop_request(df, request)

    assert report.next_action == "stop_max_samples"


def test_evaluate_request_rejects_missing_epsilon() -> None:
    request = StatisticalRequest(
        method="ebstop",
        metric="min_ttc",
        confidence=0.95,
        options={"value_range": 10.0},
    )

    with pytest.raises(ValueError):
        EBStopService().evaluate_request(None, request)


def test_evaluate_request_returns_error_report_for_missing_metric() -> None:
    df = pd.DataFrame({"other_metric": [1.0, 2.0], "status": ["success", "success"]})
    request = StatisticalRequest(
        method="ebstop",
        metric="min_ttc",
        confidence=0.95,
        options={"epsilon": 0.05, "value_range": 10.0},
    )

    report = evaluate_ebstop_request(df, request)

    assert report.next_action == "error"
    assert report.diagnostics["status"] == "error"
