from __future__ import annotations

from math import log

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from evaluation.importance_sampling_comparison import compare_sampling_methods


def _ordinary(case_id: str, value: int) -> EvaluationRecord:
    return EvaluationRecord(
        case_id=case_id,
        target="prism",
        case_kind="simple_reliability_dtmc",
        status=RunStatus.SUCCESS,
        output={"c_failure": value},
        meta={
            "sampling": {
                "plan_id": "prism_is_v1",
                "target_measure_id": "original_model_v1",
                "sampling_mode": "uniform",
                "importance_weight_status": "not_applicable",
            }
        },
    )


def _importance(case_id: str, value: int, weight: float) -> EvaluationRecord:
    return EvaluationRecord(
        case_id=case_id,
        target="prism",
        case_kind="simple_reliability_dtmc",
        status=RunStatus.SUCCESS,
        output={"c_failure": value},
        meta={
            "sampling": {
                "plan_id": "prism_is_v1",
                "target_measure_id": "original_model_v1",
                "sampling_mode": "manual_proposal",
                "proposal_id": "failure_biased_v1",
                "importance_weight_status": "available",
                "log_p": log(weight),
                "log_q": 0.0,
                "log_importance_weight": log(weight),
                "importance_weight_upper_bound": 2.0,
            }
        },
    )


def test_comparison_reports_estimates_intervals_and_width_target_progress() -> None:
    ordinary = [_ordinary("o1", 0), _ordinary("o2", 1), _ordinary("o3", 0), _ordinary("o4", 1)]
    importance = [
        _importance("q1", 0, 1.0),
        _importance("q2", 1, 1.0),
        _importance("q3", 0, 1.0),
        _importance("q4", 1, 1.0),
    ]

    result = compare_sampling_methods(
        ordinary,
        importance,
        metric="c_failure",
        target_width=2.0,
    )

    assert result["diagnostic"]["intervals_overlap"] is True
    assert result["estimate_difference"] == 0.0
    assert result["diagnostic"]["importance_estimator"] == "ordinary_importance_sampling"
    assert result["diagnostic"]["ordinary"]["sample_size"] == 4
    assert result["diagnostic"]["importance"]["sample_size"] == 4
    assert result["comparable"]["method"] == "fixed_weighted_hoeffding"
    assert result["ordinary_samples_to_comparable_target_width"] == 1
    assert result["importance_samples_to_comparable_target_width"] == 1


def test_comparison_can_explicitly_use_self_normalized_estimator() -> None:
    ordinary = [_ordinary("o1", 0), _ordinary("o2", 1)]
    importance = [_importance("q1", 1, 2.0), _importance("q2", 0, 1.0)]

    result = compare_sampling_methods(
        ordinary,
        importance,
        metric="c_failure",
        importance_estimator="self_normalized",
    )

    assert result["diagnostic"]["importance_estimator"] == "self_normalized_importance_sampling"
    assert result["diagnostic"]["importance"]["estimate"] == 2.0 / 3.0
    assert result["comparable"]["status"] == "unavailable"


def test_comparable_interval_matches_when_q_equals_p() -> None:
    ordinary = [_ordinary("o1", 0), _ordinary("o2", 1), _ordinary("o3", 0)]
    importance = [_importance("q1", 0, 1.0), _importance("q2", 1, 1.0), _importance("q3", 0, 1.0)]
    for record in importance:
        record.meta["sampling"]["importance_weight_upper_bound"] = 1.0

    result = compare_sampling_methods(ordinary, importance, metric="c_failure")

    assert result["comparable"]["ordinary"] == result["comparable"]["importance"]


def test_direct_q_without_a_weight_bound_keeps_diagnostic_estimate() -> None:
    ordinary = [_ordinary("o1", 0), _ordinary("o2", 1)]
    importance = [_importance("q1", 0, 1.0), _importance("q2", 1, 1.0)]
    for record in importance:
        record.meta["sampling"].pop("importance_weight_upper_bound")

    result = compare_sampling_methods(ordinary, importance, metric="c_failure")

    assert result["diagnostic"]["importance"]["estimate"] == 0.5
    assert result["comparable"]["status"] == "unavailable"


def test_comparison_rejects_missing_available_importance_weights() -> None:
    ordinary = [_ordinary("o1", 0)]
    unavailable = EvaluationRecord(
        case_id="q1",
        target="prism",
        case_kind="simple_reliability_dtmc",
        status=RunStatus.SUCCESS,
        output={"c_failure": 0},
        meta={"sampling": {"importance_weight_status": "unavailable"}},
    )

    try:
        compare_sampling_methods(ordinary, [unavailable], metric="c_failure")
    except ValueError as exc:
        assert "importance-weighted" in str(exc)
    else:
        raise AssertionError("ValueError was not raised")


def test_comparison_rejects_different_target_measures() -> None:
    ordinary = [_ordinary("o1", 0)]
    importance = [_importance("q1", 0, 1.0)]
    importance[0].meta["sampling"]["target_measure_id"] = "other_p"
    try:
        compare_sampling_methods(ordinary, importance, metric="c_failure")
    except ValueError as exc:
        assert "same target measure" in str(exc)
    else:
        raise AssertionError("ValueError was not raised")
