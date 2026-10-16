from __future__ import annotations

from math import log

import pytest

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from contracts.statistics import StatisticalRequest
from evaluation.importance_sampling import (
    ImportanceSamplingService,
    calculate_importance_sampling_interval,
    calculate_standard_importance_sampling_interval,
)


def _record(
    case_id: str,
    value: int,
    *,
    status: str = "available",
    log_weight: float = 0.0,
) -> EvaluationRecord:
    sampling = {
        "plan_id": "prism_is_v1",
        "target_measure_id": "original_model_v1",
        "sampling_mode": "manual_proposal",
        "proposal_id": "failure_biased_v1",
        "importance_weight_status": status,
    }
    if status == "available":
        sampling.update(
            {
                "log_p": log_weight,
                "log_q": 0.0,
                "log_importance_weight": log_weight,
            }
        )
    return EvaluationRecord(
        case_id=case_id,
        target="prism",
        case_kind="simple_reliability_dtmc",
        status=RunStatus.SUCCESS,
        output={"c_failure": value},
        meta={"sampling": sampling},
    )


def test_weighted_estimate_uses_only_records_with_available_weights() -> None:
    result = calculate_importance_sampling_interval(
        [
            _record("failure", 1, log_weight=log(2.0)),
            _record("safe", 0, log_weight=0.0),
            _record("unavailable", 1, status="unavailable"),
        ],
        metric="c_failure",
    )

    assert result is not None
    assert result["sample_size"] == 2
    assert result["estimate"] == pytest.approx(2.0 / 3.0)
    assert result["effective_sample_size"] == pytest.approx(1.8)
    assert result["max_log_importance_weight"] == pytest.approx(log(2.0))
    assert 0.0 <= result["lower_bound"] <= result["upper_bound"] <= 1.0


def test_weighted_estimate_returns_none_when_no_available_weight_exists() -> None:
    result = calculate_importance_sampling_interval(
        [_record("baseline", 0, status="not_applicable")],
        metric="c_failure",
    )

    assert result is None


def test_importance_sampling_service_reports_fixed_sample_interval() -> None:
    request = StatisticalRequest(
        method="importance_sampling",
        metric="c_failure",
        confidence=0.95,
        target_width=2.0,
    )

    report = ImportanceSamplingService().evaluate_request(
        [_record("failure", 1, log_weight=log(2.0)), _record("safe", 0)],
        request,
    )

    assert report.sufficient is True
    assert report.next_action == "stop"
    assert report.diagnostics["fixed_sample_only"] is True
    assert report.diagnostics["max_log_importance_weight"] == pytest.approx(log(2.0))


def test_importance_sampling_rejects_out_of_range_metric() -> None:
    invalid = _record("invalid", 2)

    with pytest.raises(ValueError, match=r"bounded in \[0, 1\]"):
        calculate_importance_sampling_interval([invalid], metric="c_failure")


def test_standard_importance_sampling_uses_mean_weighted_contribution() -> None:
    result = calculate_standard_importance_sampling_interval(
        [_record("failure", 1, log_weight=log(2.0)), _record("safe", 0)],
        metric="c_failure",
    )

    assert result is not None
    assert result["estimator"] == "ordinary_importance_sampling"
    assert result["estimate"] == pytest.approx(1.0)

