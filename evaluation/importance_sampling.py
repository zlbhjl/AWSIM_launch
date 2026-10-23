"""Fixed-sample estimators for records produced by importance sampling.

This module deliberately accepts only records whose provenance says that an
importance weight is available.  Its interval is a fixed-sample normal
approximation for the self-normalized importance-sampling estimator; it is not
an anytime-valid interval and must not be used as a replacement for SPRT.

Weights are aggregated in log space, so a large log weight does not overflow.
When the result itself cannot be represented as a float, ``numeric_status`` is
``"overflow"`` and the estimate is None; samples are never dropped or clipped
to hide this.  When no event was observed, or there is only one sample, the
normal approximation carries no information about the error, so the interval
is None and ``information`` is ``"insufficient"`` instead of a zero-width
interval.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import exp, log, sqrt

import pandas as pd
from scipy.stats import norm

from contracts.evaluation import EvaluationRecord
from contracts.statistics import StatisticalReport, StatisticalRequest
from evaluation.importance_samples import (
    LOG_FLOAT_MAX,
    ImportanceSample,
    collect_importance_records,
    log_sum_exp,
)


def calculate_importance_sampling_interval(
    records: Sequence[EvaluationRecord] | None,
    *,
    metric: str,
    confidence: float = 0.95,
) -> dict[str, object] | None:
    """Estimate a bounded metric using available importance-weighted records."""

    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must satisfy 0.0 < confidence < 1.0")
    collected = collect_importance_records(records, metric=metric)
    usable = collected.samples
    if not usable:
        return None

    result: dict[str, object] = {
        "metric": metric,
        "estimator": "self_normalized_importance_sampling",
        "confidence": confidence,
        "sample_size": len(usable),
        "excluded_records": collected.excluded,
        "max_log_importance_weight": _max_log_weight(usable),
    }
    positive = [sample.log_weight for sample in usable if sample.log_weight is not None]
    if not positive:
        # Every weight is exactly zero: the self-normalized ratio is undefined.
        return {
            **result,
            **_no_interval("all_weights_zero"),
            "effective_sample_size": 0.0,
            "estimate": None,
        }

    max_log_weight = max(positive)
    scaled_weights = [
        0.0 if sample.log_weight is None else exp(sample.log_weight - max_log_weight)
        for sample in usable
    ]
    values = [sample.value for sample in usable]
    weight_total = sum(scaled_weights)
    normalized_weights = [weight / weight_total for weight in scaled_weights]
    estimate = sum(weight * value for weight, value in zip(normalized_weights, values, strict=True))
    result["effective_sample_size"] = 1.0 / sum(weight * weight for weight in normalized_weights)
    result["estimate"] = estimate
    if estimate == 0.0 or len(usable) < 2:
        return {**result, **_no_interval("ok")}

    # Sandwich-style fixed-sample variance approximation for the ratio estimator.
    variance = sum(
        (weight * weight) * ((value - estimate) ** 2)
        for weight, value in zip(normalized_weights, values, strict=True)
    )
    return {**result, **_normal_interval(estimate, sqrt(variance), confidence)}


def calculate_standard_importance_sampling_interval(
    records: Sequence[EvaluationRecord] | None,
    *,
    metric: str,
    confidence: float = 0.95,
) -> dict[str, object] | None:
    """Estimate E_P[Y] by mean((P/Q) * Y) with a normal approximation.

    This estimator is appropriate only when the target measure P is already
    normalized and every sampled path has an exact likelihood ratio P/Q.  The
    point estimate is the raw mean; it can exceed 1 and is not clipped.
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must satisfy 0.0 < confidence < 1.0")
    collected = collect_importance_records(records, metric=metric)
    usable = collected.samples
    if not usable:
        return None
    count = len(usable)
    result: dict[str, object] = {
        "metric": metric,
        "estimator": "ordinary_importance_sampling",
        "confidence": confidence,
        "sample_size": count,
        "excluded_records": collected.excluded,
        "max_log_importance_weight": _max_log_weight(usable),
        "effective_sample_size": _effective_sample_size(usable),
    }

    log_contributions = [sample.log_contribution for sample in usable]
    log_total = log_sum_exp(log_contributions)
    if log_total == float("-inf"):
        # No event was observed: the estimate is 0 but its error is unknown.
        return {**result, "estimate": 0.0, **_no_interval("ok")}
    log_estimate = log_total - log(count)
    if log_estimate > LOG_FLOAT_MAX:
        return {**result, "estimate": None, **_no_interval("overflow")}
    estimate = exp(log_estimate)
    result["estimate"] = estimate
    if count < 2:
        return {**result, **_no_interval("ok")}

    # Sample standard deviation of the contributions, scaled by the largest
    # one so that no contribution is exponentiated on its own.
    peak = max(log_contributions)
    scaled = [exp(value - peak) for value in log_contributions]
    scaled_mean = sum(scaled) / count
    scaled_variance = sum((value - scaled_mean) ** 2 for value in scaled) / (count - 1)
    if scaled_variance == 0.0:
        standard_error = 0.0
    else:
        log_standard_error = peak + 0.5 * log(scaled_variance / count)
        if log_standard_error > LOG_FLOAT_MAX:
            return {**result, **_no_interval("overflow")}
        standard_error = exp(log_standard_error)
    return {**result, **_normal_interval(estimate, standard_error, confidence)}


def _max_log_weight(samples: Sequence[ImportanceSample]) -> float | None:
    positive = [sample.log_weight for sample in samples if sample.log_weight is not None]
    return max(positive) if positive else None


def _effective_sample_size(samples: Sequence[ImportanceSample]) -> float:
    """Kish ESS, (sum w)^2 / sum w^2, computed from log weights."""
    positive = [sample.log_weight for sample in samples if sample.log_weight is not None]
    if not positive:
        return 0.0
    return exp(2.0 * log_sum_exp(positive) - log_sum_exp([2.0 * value for value in positive]))


def _no_interval(numeric_status: str) -> dict[str, object]:
    return {
        "numeric_status": numeric_status,
        "information": "insufficient",
        "lower_bound": None,
        "upper_bound": None,
        "interval_width": None,
        "standard_error": None,
    }


def _normal_interval(estimate: float, standard_error: float, confidence: float) -> dict[str, object]:
    z_value = float(norm.ppf(1.0 - (1.0 - confidence) / 2.0))
    raw_lower = estimate - z_value * standard_error
    raw_upper = estimate + z_value * standard_error
    lower = max(0.0, raw_lower)
    upper = min(1.0, raw_upper)
    interval: dict[str, object] = {
        "numeric_status": "ok",
        "information": "sufficient",
        "standard_error": standard_error,
        "raw_lower_bound": raw_lower,
        "raw_upper_bound": raw_upper,
    }
    if lower > upper:
        # The whole normal interval lies outside [0, 1] (raw estimate > 1).
        return {**interval, "information": "outside_probability_range",
                "lower_bound": None, "upper_bound": None, "interval_width": None}
    return {**interval, "lower_bound": lower, "upper_bound": upper, "interval_width": upper - lower}


class ImportanceSamplingService:
    """Expose fixed-sample weighted estimation through StatisticalRequest."""

    def evaluate_request(
        self,
        data: Sequence[EvaluationRecord] | pd.DataFrame | None,
        request: StatisticalRequest,
    ) -> StatisticalReport:
        if isinstance(data, pd.DataFrame):
            raise TypeError(
                "ImportanceSamplingService requires EvaluationRecord instances "
                "so it can verify meta.sampling provenance"
            )
        records = list(data or ())
        result = calculate_importance_sampling_interval(
            records,
            metric=request.metric,
            confidence=request.confidence,
        )
        return _report_from_result(request, result, fixed_sample_only=True)


class StandardImportanceSamplingService:
    """Expose ordinary (non-self-normalized) importance sampling."""

    def evaluate_request(
        self,
        data: Sequence[EvaluationRecord] | pd.DataFrame | None,
        request: StatisticalRequest,
    ) -> StatisticalReport:
        if isinstance(data, pd.DataFrame):
            raise TypeError("StandardImportanceSamplingService requires EvaluationRecord instances")
        result = calculate_standard_importance_sampling_interval(
            list(data or ()), metric=request.metric, confidence=request.confidence,
        )
        return _report_from_result(request, result, fixed_sample_only=True)


def _report_from_result(
    request: StatisticalRequest,
    result: dict[str, object] | None,
    *,
    fixed_sample_only: bool,
) -> StatisticalReport:
    if result is None:
        return StatisticalReport(
            method=request.method, metric=request.metric, sample_count=0, estimate=None,
            interval=None, sufficient=False, next_action="collect_more_samples",
            diagnostics={"status": "pending", "confidence": request.confidence},
        )
    diagnostics = {"status": "success", **result, "fixed_sample_only": fixed_sample_only}
    estimate = None if result["estimate"] is None else float(result["estimate"])
    if result["lower_bound"] is None or result["upper_bound"] is None:
        # No usable interval (no event, one sample, overflow): never "sufficient".
        return StatisticalReport(
            method=request.method, metric=request.metric, sample_count=int(result["sample_size"]),
            estimate=estimate, interval=None, sufficient=False,
            next_action="collect_more_samples", diagnostics=diagnostics,
        )
    interval = (float(result["lower_bound"]), float(result["upper_bound"]))
    sufficient = request.target_width is not None and float(result["interval_width"]) <= request.target_width
    return StatisticalReport(
        method=request.method, metric=request.metric, sample_count=int(result["sample_size"]),
        estimate=estimate, interval=interval, sufficient=sufficient,
        next_action="stop" if sufficient else "collect_more_samples", diagnostics=diagnostics,
    )


def evaluate_importance_sampling_request(
    data: Sequence[EvaluationRecord] | pd.DataFrame | None,
    request: StatisticalRequest,
) -> StatisticalReport:
    return ImportanceSamplingService().evaluate_request(data, request)


def evaluate_standard_importance_sampling_request(
    data: Sequence[EvaluationRecord] | pd.DataFrame | None,
    request: StatisticalRequest,
) -> StatisticalReport:
    return StandardImportanceSamplingService().evaluate_request(data, request)


__all__ = [
    "ImportanceSamplingService",
    "StandardImportanceSamplingService",
    "calculate_importance_sampling_interval",
    "calculate_standard_importance_sampling_interval",
    "evaluate_importance_sampling_request",
    "evaluate_standard_importance_sampling_request",
]
