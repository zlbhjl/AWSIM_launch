from __future__ import annotations

from math import log

import pytest

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from contracts.statistics import StatisticalRequest
from evaluation.weighted_sequential import (
    WeightedSequentialService,
    calculate_weighted_sequential_decision,
)


def _record(index: int, value: int, *, weight: float = 1.0, status: str = "available") -> EvaluationRecord:
    sampling = {"importance_weight_status": status}
    if status == "available":
        sampling["log_importance_weight"] = log(weight)
    return EvaluationRecord(
        case_id=f"sample-{index}",
        target="prism",
        case_kind="simple_reliability_dtmc",
        status=RunStatus.SUCCESS,
        output={"c_failure": value},
        meta={"sampling": sampling},
    )


def _record_with_bound(index: int, value: int, *, weight: float = 1.0) -> EvaluationRecord:
    record = _record(index, value, weight=weight)
    record.meta["sampling"]["importance_weight_upper_bound"] = 2.0
    return record


def test_weighted_sequential_accepts_null_when_upper_confidence_bound_is_small() -> None:
    result = calculate_weighted_sequential_decision(
        [_record(index, 0) for index in range(100)],
        metric="c_failure",
        null_upper=0.3,
        alternative_lower=0.7,
        confidence=0.95,
        max_importance_weight=1.0,
    )

    assert result is not None
    assert result["decision"] == "accept_null"
    assert result["upper_bound"] <= 0.3


def test_weighted_sequential_accepts_alternative_when_lower_confidence_bound_is_large() -> None:
    result = calculate_weighted_sequential_decision(
        [_record(index, 1) for index in range(100)],
        metric="c_failure",
        null_upper=0.3,
        alternative_lower=0.7,
        confidence=0.95,
        max_importance_weight=1.0,
    )

    assert result is not None
    assert result["decision"] == "accept_alternative"
    assert result["lower_bound"] >= 0.7


def test_weighted_sequential_ignores_unavailable_weights_and_enforces_bound() -> None:
    pending = calculate_weighted_sequential_decision(
        [_record(1, 1, status="unavailable")],
        metric="c_failure",
        null_upper=0.3,
        alternative_lower=0.7,
        confidence=0.95,
        max_importance_weight=1.0,
    )
    assert pending is None

    with pytest.raises(ValueError, match="exceeds max_importance_weight"):
        calculate_weighted_sequential_decision(
            [_record(2, 1, weight=2.0)],
            metric="c_failure",
            null_upper=0.3,
            alternative_lower=0.7,
            confidence=0.95,
            max_importance_weight=1.0,
        )


def test_weighted_sequential_service_returns_statistical_report() -> None:
    request = StatisticalRequest(
        method="weighted_sequential",
        metric="c_failure",
        options={
            "null_upper": 0.3,
            "alternative_lower": 0.7,
            "max_importance_weight": 1.0,
        },
    )
    report = WeightedSequentialService().evaluate_request(
        [_record(index, 0) for index in range(100)],
        request,
    )

    assert report.sufficient is True
    assert report.next_action == "stop"
    assert report.diagnostics["decision"] == "accept_null"


def test_weighted_sequential_service_uses_common_bound_recorded_by_target() -> None:
    request = StatisticalRequest(
        method="weighted_sequential",
        metric="c_failure",
        options={"null_upper": 0.3, "alternative_lower": 0.7},
    )

    report = WeightedSequentialService().evaluate_request(
        [_record_with_bound(index, 0) for index in range(100)], request,
    )

    assert report.diagnostics["max_importance_weight"] == 2.0
