"""Anytime-valid sequential decisions for bounded importance weights.

This is intentionally separate from Wald SPRT.  Given an a priori bound W on
importance weights, each weighted binary contribution w*Y/W lies in [0, 1].
A union-bound Hoeffding confidence sequence is then used for a sequential
decision about the target probability under P.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite, log, sqrt

from contracts.evaluation import EvaluationRecord
from contracts.statistics import StatisticalReport, StatisticalRequest
from evaluation.empirical_bernstein import empirical_bernstein_radius
from evaluation.importance_samples import collect_available_importance_samples


def calculate_weighted_sequential_decision(
    records: Sequence[EvaluationRecord] | None,
    *,
    metric: str,
    null_upper: float,
    alternative_lower: float,
    confidence: float,
    max_importance_weight: float,
) -> dict[str, object] | None:
    """Return an anytime-valid confidence sequence decision for weighted data."""

    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must satisfy 0.0 < confidence < 1.0")
    if not 0.0 < max_importance_weight:
        raise ValueError("max_importance_weight must be positive")
    if not 0.0 <= null_upper < alternative_lower <= 1.0:
        raise ValueError("hypotheses must satisfy 0 <= null_upper < alternative_lower <= 1")

    contributions = _weighted_contributions(
        records,
        metric=metric,
        max_importance_weight=max_importance_weight,
    )
    if not contributions:
        return None
    count = len(contributions)
    delta = 1.0 - confidence
    # sum_{n>=1} delta/(n(n+1)) = delta, so the bound is simultaneous in n.
    alpha_at_count = delta / (count * (count + 1))
    interval = calculate_bounded_hoeffding_interval(
        contributions,
        confidence=confidence,
        upper_bound=max_importance_weight,
        alpha=alpha_at_count,
    )
    lower = float(interval["lower_bound"])
    upper = float(interval["upper_bound"])
    if upper <= null_upper:
        decision = "accept_null"
    elif lower >= alternative_lower:
        decision = "accept_alternative"
    else:
        decision = "continue"
    return {
        "metric": metric,
        "method": "weighted_hoeffding_confidence_sequence",
        "sample_count": count,
        "estimate": interval["estimate"],
        "lower_bound": lower,
        "upper_bound": upper,
        "interval_width": interval["interval_width"],
        "decision": decision,
        "null_upper": null_upper,
        "alternative_lower": alternative_lower,
        "max_importance_weight": max_importance_weight,
        "alpha_at_count": alpha_at_count,
    }


def calculate_fixed_weighted_hoeffding_interval(
    records: Sequence[EvaluationRecord] | None,
    *,
    metric: str,
    confidence: float,
    max_importance_weight: float,
) -> dict[str, object] | None:
    """Fixed-time Hoeffding interval for exact, bounded P/Q weights.

    This is the same bounded-contribution calculation used by the sequential
    service, but spends the full error probability at one preselected sample
    count.  It is suitable for a fair fixed-sample comparison with ordinary
    Monte Carlo; it is not an anytime stopping rule.
    """

    contributions = _weighted_contributions(
        records,
        metric=metric,
        max_importance_weight=max_importance_weight,
    )
    if not contributions:
        return None
    return {
        "metric": metric,
        "estimator": "fixed_weighted_hoeffding",
        "max_importance_weight": max_importance_weight,
        **calculate_bounded_hoeffding_interval(
            contributions,
            confidence=confidence,
            upper_bound=max_importance_weight,
        ),
    }


def calculate_bounded_hoeffding_interval(
    contributions: Sequence[float],
    *,
    confidence: float,
    upper_bound: float,
    alpha: float | None = None,
) -> dict[str, object]:
    """Bound the mean of observations known to lie in ``[0, upper_bound]``.

    ``alpha`` is optional so the sequential service can supply its spent
    error probability.  Fixed-sample callers use ``1 - confidence``.
    """

    if not contributions:
        raise ValueError("bounded Hoeffding interval requires at least one contribution")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must satisfy 0.0 < confidence < 1.0")
    if not isfinite(upper_bound) or upper_bound <= 0.0:
        raise ValueError("upper_bound must be a positive finite number")
    error_probability = 1.0 - confidence if alpha is None else float(alpha)
    if not 0.0 < error_probability < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    normalized = []
    for contribution in contributions:
        if not isfinite(contribution) or not 0.0 <= contribution <= upper_bound:
            raise ValueError("contributions must be finite values in [0, upper_bound]")
        normalized.append(contribution / upper_bound)
    count = len(normalized)
    normalized_mean = sum(normalized) / count
    normalized_radius = sqrt(log(2.0 / error_probability) / (2.0 * count))
    # The raw mean of the contributions; it can exceed 1 and is not clipped.
    # Only the interval is intersected with [0, 1].
    estimate = upper_bound * normalized_mean
    lower = max(0.0, upper_bound * (normalized_mean - normalized_radius))
    upper = min(1.0, upper_bound * (normalized_mean + normalized_radius))
    return {
        "sample_size": count,
        "estimate": estimate,
        "lower_bound": lower,
        "upper_bound": upper,
        "interval_width": upper - lower,
        "confidence": confidence,
        "alpha": error_probability,
    }


def calculate_fixed_weighted_empirical_bernstein_interval(
    records: Sequence[EvaluationRecord] | None,
    *,
    metric: str,
    confidence: float,
    max_importance_weight: float,
) -> dict[str, object] | None:
    """Fixed-sample empirical Bernstein interval for E_P[Y] from w*Y in [0, W].

    Like the Hoeffding interval it needs a bound W fixed before sampling, and
    it is valid only when N is fixed in advance. Unlike Hoeffding it adapts to
    the observed variance of the contributions.
    """

    contributions = _weighted_contributions(
        records,
        metric=metric,
        max_importance_weight=max_importance_weight,
    )
    if len(contributions) < 2:
        return None
    return {
        "metric": metric,
        "estimator": "fixed_weighted_empirical_bernstein",
        "max_importance_weight": max_importance_weight,
        **calculate_bounded_empirical_bernstein_interval(
            contributions,
            confidence=confidence,
            upper_bound=max_importance_weight,
        ),
    }


def calculate_bounded_empirical_bernstein_interval(
    contributions: Sequence[float],
    *,
    confidence: float,
    upper_bound: float,
) -> dict[str, object]:
    """Bound the mean of N >= 2 observations known to lie in ``[0, upper_bound]``.

    Uses :func:`evaluation.empirical_bernstein.empirical_bernstein_radius`
    with the empirical standard deviation (denominator N).
    """

    if len(contributions) < 2:
        raise ValueError("empirical Bernstein interval requires at least two contributions")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must satisfy 0.0 < confidence < 1.0")
    if not isfinite(upper_bound) or upper_bound <= 0.0:
        raise ValueError("upper_bound must be a positive finite number")
    for contribution in contributions:
        if not isfinite(contribution) or not 0.0 <= contribution <= upper_bound:
            raise ValueError("contributions must be finite values in [0, upper_bound]")
    count = len(contributions)
    mean = sum(contributions) / count
    std = sqrt(max(0.0, sum((value - mean) ** 2 for value in contributions) / count))
    error_probability = 1.0 - confidence
    radius = empirical_bernstein_radius(count, std, upper_bound, error_probability)
    lower = max(0.0, mean - radius)
    upper = min(1.0, mean + radius)
    return {
        "sample_size": count,
        "estimate": mean,
        "lower_bound": lower,
        "upper_bound": upper,
        "interval_width": upper - lower,
        "confidence": confidence,
        "alpha": error_probability,
    }


class WeightedSequentialService:
    """StatisticalRequest interface for the weighted confidence-sequence test."""

    def evaluate_request(
        self,
        records: Sequence[EvaluationRecord] | None,
        request: StatisticalRequest,
    ) -> StatisticalReport:
        try:
            null_upper = float(request.options["null_upper"])
            alternative_lower = float(request.options["alternative_lower"])
        except KeyError as exc:
            raise ValueError(
                "weighted sequential evaluation requires null_upper and alternative_lower"
            ) from exc
        max_weight = resolve_max_importance_weight(records, request.options)
        result = calculate_weighted_sequential_decision(
            records,
            metric=request.metric,
            null_upper=null_upper,
            alternative_lower=alternative_lower,
            confidence=request.confidence,
            max_importance_weight=max_weight,
        )
        if result is None:
            return StatisticalReport(
                method=request.method,
                metric=request.metric,
                sample_count=0,
                estimate=None,
                interval=None,
                sufficient=False,
                next_action="collect_more_samples",
                diagnostics={
                    "status": "pending",
                    "message": "No records with available importance weights are available yet.",
                },
            )
        decision = str(result["decision"])
        return StatisticalReport(
            method=request.method,
            metric=request.metric,
            sample_count=int(result["sample_count"]),
            estimate=float(result["estimate"]),
            interval=(float(result["lower_bound"]), float(result["upper_bound"])),
            sufficient=decision != "continue",
            next_action="stop" if decision != "continue" else "collect_more_samples",
            diagnostics={"status": "success", **result},
        )


def resolve_max_importance_weight(
    records: Sequence[EvaluationRecord] | None,
    options: Mapping[str, object],
) -> float:
    """Use an explicit bound, or the common bound recorded by a fixed-Q target."""
    if "max_importance_weight" in options:
        return float(options["max_importance_weight"])
    return resolve_recorded_importance_weight_bound(records)


def resolve_recorded_importance_weight_bound(
    records: Sequence[EvaluationRecord] | None,
) -> float:
    """Return the one fixed-Q upper bound recorded on all usable samples."""

    bounds: set[float] = set()
    for record in records or ():
        sampling = record.meta.get("sampling")
        if not isinstance(sampling, dict):
            continue
        if sampling.get("importance_weight_status") != "available":
            continue
        value = sampling.get("importance_weight_upper_bound")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                "weighted sequential evaluation requires max_importance_weight or "
                "importance_weight_upper_bound on every weighted sample"
            )
        bound = float(value)
        if not isfinite(bound) or bound <= 0.0:
            raise ValueError("importance_weight_upper_bound must be a positive finite number")
        bounds.add(bound)
    if not bounds:
        raise ValueError(
            "weighted sequential evaluation requires max_importance_weight or "
            "importance_weight_upper_bound on weighted samples"
        )
    if len(bounds) != 1:
        raise ValueError(
            "weighted sequential evaluation requires one common importance weight bound; "
            "use only one fixed proposal Q"
        )
    return bounds.pop()


def _weighted_contributions(
    records: Sequence[EvaluationRecord] | None,
    *,
    metric: str,
    max_importance_weight: float,
) -> list[float]:
    contributions: list[float] = []
    for sample in collect_available_importance_samples(records, metric=metric):
        tolerance = 1e-12 * max(1.0, max_importance_weight)
        if sample.weight > max_importance_weight + tolerance:
            raise ValueError(
                "observed importance weight exceeds max_importance_weight"
            )
        contributions.append(min(sample.weight, max_importance_weight) * sample.value)
    return contributions


def evaluate_weighted_sequential_request(
    records: Sequence[EvaluationRecord] | None,
    request: StatisticalRequest,
) -> StatisticalReport:
    return WeightedSequentialService().evaluate_request(records, request)


__all__ = [
    "WeightedSequentialService",
    "calculate_bounded_empirical_bernstein_interval",
    "calculate_bounded_hoeffding_interval",
    "calculate_fixed_weighted_empirical_bernstein_interval",
    "calculate_fixed_weighted_hoeffding_interval",
    "calculate_weighted_sequential_decision",
    "evaluate_weighted_sequential_request",
    "resolve_max_importance_weight",
    "resolve_recorded_importance_weight_bound",
]
