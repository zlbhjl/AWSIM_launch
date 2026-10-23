from __future__ import annotations

from math import log, sqrt

import pytest

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from evaluation.empirical_bernstein import empirical_bernstein_radius
from evaluation.weighted_sequential import (
    calculate_bounded_empirical_bernstein_interval,
    calculate_bounded_hoeffding_interval,
    calculate_fixed_weighted_empirical_bernstein_interval,
)


def test_radius_matches_the_audibert_munos_szepesvari_bound() -> None:
    expected = 0.3 * sqrt(2 * log(3 / 0.05) / 100) + 3 * 5.0 * log(3 / 0.05) / 100

    assert empirical_bernstein_radius(100, 0.3, 5.0, 0.05) == pytest.approx(expected)


def test_interval_matches_a_hand_calculation() -> None:
    contributions = [0.0, 0.0, 2.0, 0.0, 2.0, 0.0, 0.0, 0.0]
    mean = 0.5
    std = sqrt(sum((value - mean) ** 2 for value in contributions) / 8)
    radius = std * sqrt(2 * log(3 / 0.05) / 8) + 3 * 2.0 * log(3 / 0.05) / 8

    result = calculate_bounded_empirical_bernstein_interval(contributions, confidence=0.95, upper_bound=2.0)

    assert result["estimate"] == pytest.approx(mean)
    assert result["lower_bound"] == pytest.approx(max(0.0, mean - radius))
    assert result["upper_bound"] == pytest.approx(min(1.0, mean + radius))


def test_interval_is_narrower_than_hoeffding_for_low_variance_contributions() -> None:
    contributions = [0.0] * 9990 + [0.5] * 10

    bernstein = calculate_bounded_empirical_bernstein_interval(contributions, confidence=0.95, upper_bound=5.0)
    hoeffding = calculate_bounded_hoeffding_interval(contributions, confidence=0.95, upper_bound=5.0)

    assert bernstein["interval_width"] < hoeffding["interval_width"] / 5


def test_interval_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="at least two"):
        calculate_bounded_empirical_bernstein_interval([0.5], confidence=0.95, upper_bound=1.0)
    with pytest.raises(ValueError, match="in \\[0, upper_bound\\]"):
        calculate_bounded_empirical_bernstein_interval([0.5, 1.5], confidence=0.95, upper_bound=1.0)
    with pytest.raises(ValueError, match="confidence"):
        calculate_bounded_empirical_bernstein_interval([0.5, 0.5], confidence=1.0, upper_bound=1.0)


def _record(value: int, weight: float) -> EvaluationRecord:
    return EvaluationRecord(
        case_id="sample",
        target="prism",
        case_kind="simple_reliability_dtmc",
        status=RunStatus.SUCCESS,
        output={"c_failure": value},
        meta={"sampling": {"importance_weight_status": "available", "log_importance_weight": log(weight)}},
    )


def test_fixed_interval_from_records_needs_two_samples_and_respects_the_bound() -> None:
    assert calculate_fixed_weighted_empirical_bernstein_interval(
        [_record(1, 1.0)], metric="c_failure", confidence=0.95, max_importance_weight=2.0,
    ) is None
    with pytest.raises(ValueError, match="exceeds max_importance_weight"):
        calculate_fixed_weighted_empirical_bernstein_interval(
            [_record(1, 3.0), _record(0, 1.0)], metric="c_failure", confidence=0.95, max_importance_weight=2.0,
        )

    result = calculate_fixed_weighted_empirical_bernstein_interval(
        [_record(1, 2.0), _record(0, 1.0)], metric="c_failure", confidence=0.95, max_importance_weight=2.0,
    )

    assert result["estimator"] == "fixed_weighted_empirical_bernstein"
    assert result["estimate"] == pytest.approx(1.0)
