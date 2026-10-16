"""Compare ordinary and importance-sampled records for one target measure."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from math import sqrt

from scipy.stats import norm

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from evaluation.importance_sampling import (
    calculate_importance_sampling_interval,
    calculate_standard_importance_sampling_interval,
)
from evaluation.weighted_sequential import (
    calculate_bounded_hoeffding_interval,
    calculate_fixed_weighted_hoeffding_interval,
    resolve_recorded_importance_weight_bound,
)

ImportanceIntervalCalculator = Callable[
    [Sequence[EvaluationRecord]],
    dict[str, object] | None,
]


def compare_sampling_methods(
    ordinary_records: Sequence[EvaluationRecord],
    importance_records: Sequence[EvaluationRecord],
    *,
    metric: str,
    confidence: float = 0.95,
    target_width: float | None = None,
    importance_estimator: str = "ordinary",
) -> dict[str, object]:
    """Compare estimates and separate diagnostic from comparable intervals.

    Ordinary records use the usual Wilson interval.  Importance records are
    passed to the selected fixed-sample estimator, which filters to available
    weights.  PRISM defaults to ordinary importance sampling because its
    normalized target measure and P/Q ratios are available exactly.
    Both collections must represent the same target, case kind, and declared
    target measure.  The estimator itself deliberately has no PRISM-specific
    knowledge: a target adapter is responsible for producing exact P/Q
    likelihood ratios when it selects the ordinary estimator.
    """

    ordinary = _ordinary_interval(ordinary_records, metric=metric, confidence=confidence)
    importance_calculator = _importance_calculator(importance_estimator)
    importance = importance_calculator(
        importance_records,
        metric=metric,
        confidence=confidence,
    )
    if ordinary is None:
        raise ValueError("No valid ordinary samples are available")
    if importance is None:
        raise ValueError("No valid importance-weighted samples are available")
    context = _comparison_context(ordinary_records, importance_records, metric=metric)

    ordinary_diagnostic_interval = (
        float(ordinary["lower_bound"]), float(ordinary["upper_bound"])
    )
    importance_diagnostic_interval = (
        float(importance["lower_bound"]),
        float(importance["upper_bound"]),
    )
    result: dict[str, object] = {
        **context,
        "metric": metric,
        "confidence": confidence,
        "diagnostic": {
            "ordinary": ordinary,
            "importance": importance,
            "importance_estimator": str(importance["estimator"]),
            "intervals_overlap": _intervals_overlap(
                ordinary_diagnostic_interval, importance_diagnostic_interval
            ),
        },
        "estimate_difference": float(importance["estimate"]) - float(ordinary["estimate"]),
    }
    if importance_estimator == "ordinary":
        comparable_ordinary = _fixed_ordinary_hoeffding_interval(
            ordinary_records, metric=metric, confidence=confidence
        )
        max_weight = _recorded_importance_weight_bound_or_none(importance_records)
        if max_weight is None:
            result["comparable"] = {
                "status": "unavailable",
                "reason": "proposal Q has no declared finite importance-weight bound",
            }
            return result
        comparable_importance = calculate_fixed_weighted_hoeffding_interval(
            importance_records,
            metric=metric,
            confidence=confidence,
            max_importance_weight=max_weight,
        )
        if comparable_ordinary is None or comparable_importance is None:
            raise ValueError("No valid samples are available for comparable intervals")
        ordinary_interval = (
            float(comparable_ordinary["lower_bound"]),
            float(comparable_ordinary["upper_bound"]),
        )
        importance_interval = (
            float(comparable_importance["lower_bound"]),
            float(comparable_importance["upper_bound"]),
        )
        result["comparable"] = {
            "method": "fixed_weighted_hoeffding",
            "ordinary": comparable_ordinary,
            "importance": comparable_importance,
            "intervals_overlap": _intervals_overlap(ordinary_interval, importance_interval),
            "width_ratio_importance_over_ordinary": (
                float(comparable_importance["interval_width"])
                / float(comparable_ordinary["interval_width"])
                if float(comparable_ordinary["interval_width"]) > 0.0
                else None
            ),
        }
    else:
        result["comparable"] = {
            "status": "unavailable",
            "reason": "fixed_weighted_hoeffding requires ordinary importance sampling",
        }
    if target_width is not None:
        if target_width < 0.0:
            raise ValueError("target_width must be non-negative")
        if importance_estimator != "ordinary":
            raise ValueError("target_width comparison requires ordinary importance sampling")
        result["target_width"] = float(target_width)
        result["ordinary_samples_to_comparable_target_width"] = _ordinary_samples_to_width(
            ordinary_records,
            metric=metric,
            confidence=confidence,
            target_width=target_width,
        )
        result["importance_samples_to_comparable_target_width"] = _importance_samples_to_width(
            importance_records,
            metric=metric,
            confidence=confidence,
            target_width=target_width,
        )
    return result


def _recorded_importance_weight_bound_or_none(
    records: Sequence[EvaluationRecord],
) -> float | None:
    """Leave a direct, unbounded Q usable for diagnostic IS estimates."""

    available = [
        record for record in records
        if isinstance(record.meta.get("sampling"), dict)
        and record.meta["sampling"].get("importance_weight_status") == "available"
    ]
    if available and any(
        "importance_weight_upper_bound" not in record.meta["sampling"]
        for record in available
    ):
        return None
    return resolve_recorded_importance_weight_bound(records)


def _ordinary_interval(
    records: Sequence[EvaluationRecord],
    *,
    metric: str,
    confidence: float,
) -> dict[str, object] | None:
    values = _ordinary_values(records, metric=metric)
    if not values:
        return None
    count = len(values)
    estimate = sum(values) / count
    z_value = float(norm.ppf(1.0 - (1.0 - confidence) / 2.0))
    denominator = 1.0 + (z_value * z_value) / count
    center = (estimate + (z_value * z_value) / (2.0 * count)) / denominator
    half_width = (
        z_value
        * sqrt((estimate * (1.0 - estimate) / count) + (z_value * z_value) / (4.0 * count * count))
        / denominator
    )
    lower = max(0.0, center - half_width)
    upper = min(1.0, center + half_width)
    return {
        "estimator": "ordinary_monte_carlo_wilson",
        "sample_size": count,
        "estimate": estimate,
        "lower_bound": lower,
        "upper_bound": upper,
        "interval_width": upper - lower,
    }


def _ordinary_values(records: Sequence[EvaluationRecord], *, metric: str) -> list[float]:
    values: list[float] = []
    for record in records:
        if record.status is not RunStatus.SUCCESS:
            continue
        value = record.output.get(metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        numeric_value = float(value)
        if not 0.0 <= numeric_value <= 1.0:
            raise ValueError(f"ordinary metric {metric!r} must be bounded in [0, 1]")
        values.append(numeric_value)
    return values


def _comparison_context(
    ordinary_records: Sequence[EvaluationRecord],
    importance_records: Sequence[EvaluationRecord],
    *,
    metric: str,
) -> dict[str, str]:
    """Return and validate provenance shared by the two record collections."""

    ordinary_contexts = _record_contexts(ordinary_records, metric=metric, weighted=False)
    importance_contexts = _record_contexts(importance_records, metric=metric, weighted=True)
    if len(ordinary_contexts) != 1:
        raise ValueError("ordinary records must have one target, case_kind, and target_measure_id")
    if len(importance_contexts) != 1:
        raise ValueError("importance records must have one target, case_kind, and target_measure_id")
    ordinary_context = next(iter(ordinary_contexts))
    importance_context = next(iter(importance_contexts))
    if ordinary_context != importance_context:
        raise ValueError("ordinary and importance records must use the same target measure")
    target, case_kind, target_measure_id = ordinary_context
    return {
        "target": target,
        "case_kind": case_kind,
        "target_measure_id": target_measure_id,
    }


def _record_contexts(
    records: Sequence[EvaluationRecord],
    *,
    metric: str,
    weighted: bool,
) -> set[tuple[str, str, str]]:
    contexts: set[tuple[str, str, str]] = set()
    for record in records:
        if record.status is not RunStatus.SUCCESS:
            continue
        value = record.output.get(metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        sampling = record.meta.get("sampling")
        if not isinstance(sampling, dict):
            raise ValueError("comparison records require meta.sampling provenance")
        target_measure_id = sampling.get("target_measure_id")
        if not isinstance(target_measure_id, str) or not target_measure_id:
            raise ValueError("comparison records require sampling.target_measure_id")
        if weighted and sampling.get("importance_weight_status") != "available":
            continue
        contexts.add((record.target, record.case_kind, target_measure_id))
    return contexts


def _ordinary_samples_to_width(
    records: Sequence[EvaluationRecord],
    *,
    metric: str,
    confidence: float,
    target_width: float,
) -> int | None:
    for count in range(1, len(records) + 1):
        result = _fixed_ordinary_hoeffding_interval(
            records[:count], metric=metric, confidence=confidence
        )
        if result is not None and float(result["interval_width"]) <= target_width:
            return int(result["sample_size"])
    return None


def _importance_samples_to_width(
    records: Sequence[EvaluationRecord],
    *,
    metric: str,
    confidence: float,
    target_width: float,
) -> int | None:
    for count in range(1, len(records) + 1):
        max_weight = resolve_recorded_importance_weight_bound(records[:count])
        result = calculate_fixed_weighted_hoeffding_interval(
            records[:count],
            metric=metric,
            confidence=confidence,
            max_importance_weight=max_weight,
        )
        if result is not None and float(result["interval_width"]) <= target_width:
            return int(result["sample_size"])
    return None


def _fixed_ordinary_hoeffding_interval(
    records: Sequence[EvaluationRecord],
    *,
    metric: str,
    confidence: float,
) -> dict[str, object] | None:
    values = _ordinary_values(records, metric=metric)
    if not values:
        return None
    return {
        "metric": metric,
        "estimator": "fixed_weighted_hoeffding",
        "max_importance_weight": 1.0,
        **calculate_bounded_hoeffding_interval(
            values,
            confidence=confidence,
            upper_bound=1.0,
        ),
    }


def _importance_calculator(
    estimator: str,
) -> Callable[..., dict[str, object] | None]:
    if estimator == "ordinary":
        return calculate_standard_importance_sampling_interval
    if estimator == "self_normalized":
        return calculate_importance_sampling_interval
    raise ValueError("importance_estimator must be 'ordinary' or 'self_normalized'")


def _intervals_overlap(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return max(left[0], right[0]) <= min(left[1], right[1])


__all__ = ["compare_sampling_methods"]
