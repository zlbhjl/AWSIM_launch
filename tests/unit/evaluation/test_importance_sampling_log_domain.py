from __future__ import annotations

from math import log, sqrt

import pytest

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from contracts.statistics import StatisticalRequest
from evaluation.importance_samples import (
    ImportanceSample,
    collect_importance_records,
    log_sum_exp,
)
from evaluation.importance_sampling import (
    calculate_importance_sampling_interval,
    calculate_standard_importance_sampling_interval,
    evaluate_standard_importance_sampling_request,
)


def _record(value: int, log_weight: float | None, *, status: RunStatus = RunStatus.SUCCESS) -> EvaluationRecord:
    sampling: dict[str, object] = {"importance_weight_status": "available"}
    if log_weight is None:
        sampling.update({"weight_kind": "zero", "log_p": None, "log_q": 0.0, "log_importance_weight": None})
    else:
        sampling["log_importance_weight"] = log_weight
    return EvaluationRecord(
        case_id="sample",
        target="prism",
        case_kind="simple_reliability_dtmc",
        status=status,
        output={} if status is not RunStatus.SUCCESS else {"c_failure": value},
        meta={"sampling": sampling},
    )


def test_sample_weight_is_zero_inf_or_exp() -> None:
    assert ImportanceSample(1.0, None).weight == 0.0
    assert ImportanceSample(1.0, 1000.0).weight == float("inf")
    assert ImportanceSample(1.0, log(2.0)).weight == pytest.approx(2.0)
    assert ImportanceSample(0.0, 5.0).log_contribution == float("-inf")


def test_collect_counts_excluded_records_by_reason() -> None:
    records = [
        _record(1, 0.0),
        _record(0, None),
        _record(1, 0.0, status=RunStatus.EXECUTION_ERROR),
        _record(1, float("nan")),
    ]

    collected = collect_importance_records(records, metric="c_failure")

    assert [sample.log_weight for sample in collected.samples] == [0.0, None]
    assert collected.excluded == {"not_success": 1, "weight_not_finite": 1}


def test_log_sum_exp_handles_large_and_empty_inputs() -> None:
    assert log_sum_exp([1000.0, 1000.0]) == pytest.approx(1000.0 + log(2.0))
    assert log_sum_exp([]) == float("-inf")
    assert log_sum_exp([float("-inf")]) == float("-inf")


def test_zero_weights_count_toward_n() -> None:
    records = [_record(1, log(2.0)), _record(1, None), _record(0, 0.0), _record(0, None)]

    result = calculate_standard_importance_sampling_interval(records, metric="c_failure")

    assert result["sample_size"] == 4
    assert result["estimate"] == pytest.approx(2.0 / 4)
    contributions = [2.0, 0.0, 0.0, 0.0]
    mean = sum(contributions) / 4
    standard_error = sqrt(sum((c - mean) ** 2 for c in contributions) / 3 / 4)
    assert result["standard_error"] == pytest.approx(standard_error)


def test_large_log_weights_are_estimated_without_exponentiating_each_weight() -> None:
    records = [_record(1, 700.0), _record(1, 700.0), _record(0, 0.0)]

    result = calculate_standard_importance_sampling_interval(records, metric="c_failure")

    assert result["numeric_status"] == "ok"
    assert log(result["estimate"]) == pytest.approx(700.0 + log(2.0 / 3.0))


def test_raw_estimate_above_one_is_kept_and_the_interval_is_marked() -> None:
    records = [_record(1, log(4.0)), _record(1, log(4.0)), _record(1, log(4.0))]

    result = calculate_standard_importance_sampling_interval(records, metric="c_failure")

    assert result["estimate"] == pytest.approx(4.0)
    assert result["information"] == "outside_probability_range"
    assert result["lower_bound"] is None
    assert result["raw_lower_bound"] == pytest.approx(4.0)


def test_one_sample_has_no_normal_interval() -> None:
    result = calculate_standard_importance_sampling_interval([_record(1, 0.0)], metric="c_failure")

    assert result["estimate"] == 1.0
    assert result["information"] == "insufficient"
    assert result["interval_width"] is None


def test_effective_sample_size_matches_kish_from_log_weights() -> None:
    weights = [1.0, 2.0, 4.0]
    records = [_record(1, log(weight)) for weight in weights] + [_record(1, None)]

    result = calculate_standard_importance_sampling_interval(records, metric="c_failure")

    assert result["effective_sample_size"] == pytest.approx(sum(weights) ** 2 / sum(w * w for w in weights))


def test_all_zero_weights_have_zero_ess_and_no_interval() -> None:
    records = [_record(1, None), _record(0, None)]

    standard = calculate_standard_importance_sampling_interval(records, metric="c_failure")
    self_normalized = calculate_importance_sampling_interval(records, metric="c_failure")

    assert standard["estimate"] == 0.0
    assert standard["effective_sample_size"] == 0.0
    assert standard["lower_bound"] is None
    assert self_normalized["estimate"] is None
    assert self_normalized["numeric_status"] == "all_weights_zero"


def test_report_without_interval_is_never_sufficient() -> None:
    records = [_record(0, 0.0) for _ in range(10)]

    report = evaluate_standard_importance_sampling_request(
        records,
        StatisticalRequest(
            method="importance_sampling", metric="c_failure", confidence=0.95, target_width=0.5,
        ),
    )

    assert report.interval is None
    assert report.sufficient is False
    assert report.next_action == "collect_more_samples"
    assert report.estimate == 0.0
