"""Fixed-sample estimators for records produced by importance sampling.

This module deliberately accepts only records whose provenance says that an
importance weight is available.  Its interval is a fixed-sample normal
approximation for the self-normalized importance-sampling estimator; it is not
an anytime-valid interval and must not be used as a replacement for SPRT.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import exp, sqrt

import pandas as pd
from scipy.stats import norm

from contracts.evaluation import EvaluationRecord
from contracts.statistics import StatisticalReport, StatisticalRequest
from evaluation.importance_samples import collect_available_importance_samples


def calculate_importance_sampling_interval(
    records: Sequence[EvaluationRecord] | None,
    *,
    metric: str,
    confidence: float = 0.95,
) -> dict[str, object] | None:
    """Estimate a bounded metric using available importance-weighted records."""

    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must satisfy 0.0 < confidence < 1.0")
    usable = collect_available_importance_samples(records, metric=metric)
    if not usable:
        return None

    values = [sample.value for sample in usable]
    log_weights = [sample.log_weight for sample in usable]
    max_log_weight = max(log_weights)
    scaled_weights = [exp(log_weight - max_log_weight) for log_weight in log_weights]
    weight_total = sum(scaled_weights)
    normalized_weights = [weight / weight_total for weight in scaled_weights]
    estimate = sum(weight * value for weight, value in zip(normalized_weights, values, strict=True))
    effective_sample_size = 1.0 / sum(weight * weight for weight in normalized_weights)

    # Sandwich-style fixed-sample variance approximation for the ratio estimator.
    variance = sum(
        (weight * weight) * ((value - estimate) ** 2)
        for weight, value in zip(normalized_weights, values, strict=True)
    )
    standard_error = sqrt(variance)
    z_value = float(norm.ppf(1.0 - (1.0 - confidence) / 2.0))
    lower = max(0.0, estimate - z_value * standard_error)
    upper = min(1.0, estimate + z_value * standard_error)
    return {
        "metric": metric,
        "estimator": "self_normalized_importance_sampling",
        "confidence": confidence,
        "sample_size": len(usable),
        "effective_sample_size": effective_sample_size,
        "max_log_importance_weight": max_log_weight,
        "estimate": estimate,
        "lower_bound": lower,
        "upper_bound": upper,
        "interval_width": upper - lower,
        "standard_error": standard_error,
    }


def calculate_standard_importance_sampling_interval(
    records: Sequence[EvaluationRecord] | None,
    *,
    metric: str,
    confidence: float = 0.95,
) -> dict[str, object] | None:
    """Estimate E_P[Y] by mean((P/Q) * Y) with a normal approximation.

    This estimator is appropriate only when the target measure P is already
    normalized and every sampled path has an exact likelihood ratio P/Q.
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must satisfy 0.0 < confidence < 1.0")
    usable = collect_available_importance_samples(records, metric=metric)
    if not usable:
        return None
    contributions = [sample.weight * sample.value for sample in usable]
    estimate = sum(contributions) / len(contributions)
    raw_weights = [sample.weight for sample in usable]
    normalized_weights = [weight / sum(raw_weights) for weight in raw_weights]
    effective_sample_size = 1.0 / sum(weight * weight for weight in normalized_weights)
    variance = (
        sum((value - estimate) ** 2 for value in contributions) / (len(contributions) - 1)
        if len(contributions) > 1
        else 0.0
    )
    standard_error = sqrt(variance / len(contributions))
    z_value = float(norm.ppf(1.0 - (1.0 - confidence) / 2.0))
    lower = max(0.0, estimate - z_value * standard_error)
    upper = min(1.0, estimate + z_value * standard_error)
    return {
        "metric": metric,
        "estimator": "ordinary_importance_sampling",
        "confidence": confidence,
        "sample_size": len(contributions),
        "estimate": estimate,
        "lower_bound": lower,
        "upper_bound": upper,
        "interval_width": upper - lower,
        "standard_error": standard_error,
        "max_log_importance_weight": max(sample.log_weight for sample in usable),
        "effective_sample_size": effective_sample_size,
    }


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
    interval = (float(result["lower_bound"]), float(result["upper_bound"]))
    sufficient = request.target_width is not None and float(result["interval_width"]) <= request.target_width
    diagnostics = {"status": "success", **result, "fixed_sample_only": fixed_sample_only}
    return StatisticalReport(
        method=request.method, metric=request.metric, sample_count=int(result["sample_size"]),
        estimate=float(result["estimate"]), interval=interval, sufficient=sufficient,
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
