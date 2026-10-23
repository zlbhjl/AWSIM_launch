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
    calculate_bounded_empirical_bernstein_interval,
    calculate_bounded_hoeffding_interval,
    calculate_fixed_weighted_empirical_bernstein_interval,
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

    The intervals have different roles (docs/sde_importance_sampling_plan.md
    §6.3).  ``diagnostic`` (normal approximation) is for comparing proposal
    candidates.  ``guarantee`` (Hoeffding and empirical Bernstein, which need a
    weight bound fixed before sampling) is only for checking that the
    interval contains the true value; its width is not used to rank proposals.
    ``comparable`` is kept for existing readers and equals
    ``guarantee.hoeffding``.
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

    result: dict[str, object] = {
        **context,
        "metric": metric,
        "confidence": confidence,
        "diagnostic": {
            "ordinary": ordinary,
            "importance": importance,
            "importance_estimator": str(importance["estimator"]),
            "intervals_overlap": _intervals_overlap(ordinary, importance),
        },
        "estimate_difference": (
            None
            if importance["estimate"] is None
            else float(importance["estimate"]) - float(ordinary["estimate"])
        ),
    }
    if importance_estimator == "ordinary":
        comparable_ordinary = _fixed_ordinary_hoeffding_interval(
            ordinary_records, metric=metric, confidence=confidence
        )
        max_weight = _recorded_importance_weight_bound_or_none(importance_records)
        if max_weight is None:
            unavailable = {
                "status": "unavailable",
                "reason": "proposal Q has no declared finite importance-weight bound",
            }
            result["comparable"] = unavailable
            result["guarantee"] = {**unavailable, "purpose": GUARANTEE_PURPOSE}
            return result
        comparable_importance = calculate_fixed_weighted_hoeffding_interval(
            importance_records,
            metric=metric,
            confidence=confidence,
            max_importance_weight=max_weight,
        )
        if comparable_ordinary is None or comparable_importance is None:
            raise ValueError("No valid samples are available for comparable intervals")
        result["comparable"] = {
            "method": "fixed_weighted_hoeffding",
            "ordinary": comparable_ordinary,
            "importance": comparable_importance,
            "intervals_overlap": _intervals_overlap(comparable_ordinary, comparable_importance),
            "width_ratio_importance_over_ordinary": (
                float(comparable_importance["interval_width"])
                / float(comparable_ordinary["interval_width"])
                if float(comparable_ordinary["interval_width"]) > 0.0
                else None
            ),
        }
        result["guarantee"] = {
            "purpose": GUARANTEE_PURPOSE,
            "hoeffding": result["comparable"],
            "empirical_bernstein": _empirical_bernstein_pair(
                ordinary_records,
                importance_records,
                metric=metric,
                confidence=confidence,
                max_weight=max_weight,
            ),
        }
    else:
        unavailable = {
            "status": "unavailable",
            "reason": "fixed_weighted_hoeffding requires ordinary importance sampling",
        }
        result["comparable"] = unavailable
        result["guarantee"] = {**unavailable, "purpose": GUARANTEE_PURPOSE}
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


GUARANTEE_PURPOSE = (
    "coverage_check_only: valid for a weight bound and N fixed before sampling; "
    "do not rank proposals by its width"
)


def _empirical_bernstein_pair(
    ordinary_records: Sequence[EvaluationRecord],
    importance_records: Sequence[EvaluationRecord],
    *,
    metric: str,
    confidence: float,
    max_weight: float,
) -> dict[str, object]:
    ordinary_values = _ordinary_values(ordinary_records, metric=metric)
    ordinary = (
        {
            "metric": metric,
            "estimator": "fixed_weighted_empirical_bernstein",
            "max_importance_weight": 1.0,
            **calculate_bounded_empirical_bernstein_interval(
                ordinary_values, confidence=confidence, upper_bound=1.0,
            ),
        }
        if len(ordinary_values) >= 2
        else None
    )
    importance = calculate_fixed_weighted_empirical_bernstein_interval(
        importance_records,
        metric=metric,
        confidence=confidence,
        max_importance_weight=max_weight,
    )
    if ordinary is None or importance is None:
        return {"status": "unavailable", "reason": "empirical Bernstein needs at least two samples"}
    return {
        "method": "fixed_weighted_empirical_bernstein",
        "ordinary": ordinary,
        "importance": importance,
        "intervals_overlap": _intervals_overlap(ordinary, importance),
    }


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
    excluded = {
        "not_success": sum(1 for record in records if record.status is not RunStatus.SUCCESS),
    }
    excluded["metric_missing"] = len(records) - count - excluded["not_success"]
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
        "excluded_records": {reason: number for reason, number in excluded.items() if number},
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
) -> dict[str, object]:
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
        **_fingerprint_context(ordinary_records, importance_records),
    }


def _fingerprint_context(
    ordinary_records: Sequence[EvaluationRecord],
    importance_records: Sequence[EvaluationRecord],
) -> dict[str, str]:
    """Check P and the fixed Q by fingerprint when the records carry one.

    A target_measure_id alone does not identify the numerical model: the step
    size or horizon can differ under the same id.  Records written with an
    experiment manifest carry ``target_fingerprint`` (P, numerical map, event)
    and ``proposal_fingerprint`` (Q).  Older records have neither; for them only
    the ids are compared, and the result says so.
    """

    ordinary_targets = _fingerprints(ordinary_records, "target_fingerprint")
    importance_targets = _fingerprints(importance_records, "target_fingerprint")
    proposals = _fingerprints(importance_records, "proposal_fingerprint")
    if not any(value is not None for value in ordinary_targets | importance_targets | proposals):
        return {"identity_check": "legacy_ids_only"}
    if None in ordinary_targets or None in importance_targets or None in proposals:
        raise ValueError(
            "comparison records must all carry target_fingerprint (and importance "
            "records proposal_fingerprint), or none of them"
        )
    if len(ordinary_targets | importance_targets) != 1:
        raise ValueError("ordinary and importance records must use the same target fingerprint")
    if len(proposals) != 1:
        raise ValueError("importance records must use one fixed proposal Q (one proposal_fingerprint)")
    return {
        "identity_check": "fingerprint",
        "target_fingerprint": next(iter(ordinary_targets)),
        "proposal_fingerprint": next(iter(proposals)),
    }


def _fingerprints(records: Sequence[EvaluationRecord], key: str) -> set[str | None]:
    found: set[str | None] = set()
    for record in records:
        sampling = record.meta.get("sampling")
        if isinstance(sampling, dict):
            value = sampling.get(key)
            found.add(value if isinstance(value, str) and value else None)
    return found


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
        if not _has_available_weight(records[:count]):
            continue
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


def _has_available_weight(records: Sequence[EvaluationRecord]) -> bool:
    return any(
        isinstance(record.meta.get("sampling"), dict)
        and record.meta["sampling"].get("importance_weight_status") == "available"
        for record in records
    )


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


def _intervals_overlap(left: dict[str, object], right: dict[str, object]) -> bool | None:
    """Whether two interval results overlap; None if either has no interval."""
    bounds = (left["lower_bound"], left["upper_bound"], right["lower_bound"], right["upper_bound"])
    if any(bound is None for bound in bounds):
        return None
    left_lower, left_upper, right_lower, right_upper = (float(bound) for bound in bounds)
    return max(left_lower, right_lower) <= min(left_upper, right_upper)


__all__ = ["compare_sampling_methods"]
