"""Mixtures over internal randomness and the composition of importance weights.

A target with capability ``internal_density`` reports, for one sample, the log
density of its realised internal draw under P and under every component of
the proposal (report section ``internal``). The shared code here

- picks the component to draw from (one uniform draw per sample),
- turns the per-component densities into the mixture density
      q = beta * p + (1 - beta) * sum_j alpha_j r_j,
- and adds the internal log ratio to the input log ratio.

The arithmetic is the one the dynamics SDE path proposal used before it moved
here, so weights are bit-identical (docs/blackbox_importance_sampling_plan.md §5.2).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from contracts.evaluation import EvaluationRecord
from contracts.importance_sampling_plugin import InternalProposal
from contracts.sampling import (
    INTERNAL_REPORT_KEY,
    SAMPLING_META_KEY,
    TARGET_COMPONENT,
    normalize_internal_report,
)


def needs_choice(weights: Sequence[float], defensive_weight: float | None) -> bool:
    """Whether a per-sample component draw is needed (beta = 1 means P only)."""
    if defensive_weight is not None:
        return defensive_weight < 1.0
    return len(weights) > 1


def choose_component(weights: Sequence[float], defensive_weight: float | None, uniform: float | None) -> int | str:
    """Map one U(0, 1) draw to P (``"target"``) or a component index."""
    beta = defensive_weight
    if beta is not None and beta >= 1.0:
        return TARGET_COMPONENT
    if uniform is None:
        return 0
    if beta is not None:
        if uniform < beta:
            return TARGET_COMPONENT
        uniform = (uniform - beta) / (1.0 - beta)
    cumulative = 0.0
    for index, weight in enumerate(weights):
        cumulative += weight
        if uniform < cumulative:
            return index
    return len(weights) - 1


def mixture_log_q(
    weights: Sequence[float],
    defensive_weight: float | None,
    log_p: float,
    log_q_components: Sequence[float],
) -> float:
    """Log density of the mixture from log P and the log density of each component."""
    beta = defensive_weight
    if beta is not None and beta >= 1.0:
        return log_p
    component_scale = 1.0 if beta is None else 1.0 - beta
    terms = [math.log(component_scale * weight) + value for weight, value in zip(weights, log_q_components)]
    if beta is not None:
        terms.append(math.log(beta) + log_p)
    finite = [term for term in terms if term != -math.inf]
    if not finite:
        return -math.inf
    peak = max(finite)
    return peak + math.log(sum(math.exp(term - peak) for term in finite))


def mixture_weight_bound(
    components: Sequence[str],
    defensive_weight: float | None,
    component_bound: float | None = None,
) -> float | None:
    """Upper bound of p/q for the internal part, or None if unbounded.

    The defensive mixture bounds it by 1/beta; components that are all P give 1;
    otherwise the target's own bound (``component_bound``) applies, if any.
    """
    if defensive_weight is not None:
        return 1.0 / defensive_weight
    if all(name == TARGET_COMPONENT for name in components):
        return 1.0
    return component_bound


def internal_log_q(proposal: InternalProposal, report: Mapping[str, object]) -> float:
    return mixture_log_q(proposal.weights, proposal.defensive_weight, report["log_p"], report["log_q"])


def compose_internal_weight(record: EvaluationRecord, proposal: InternalProposal | None) -> EvaluationRecord:
    """Add the internal log ratio of the report to the record's input log ratio.

    Only importance samples with an available weight are changed; the report is
    kept in the sampling metadata for audit. Records without a report are
    returned as they are.
    """
    sampling = record.meta.get(SAMPLING_META_KEY)
    if not isinstance(sampling, dict) or INTERNAL_REPORT_KEY not in sampling:
        return record
    if sampling.get("importance_weight_status") != "available":
        return record
    if proposal is None:
        raise ValueError("an internal report needs the internal proposal to compose the weight")
    report = normalize_internal_report(sampling[INTERNAL_REPORT_KEY], components=len(proposal.components))
    if report["support"] == "zero":
        raise ValueError("an internal draw outside P's support is not supported yet")
    noise_log_q = internal_log_q(proposal, report)
    composed = dict(sampling)
    composed[INTERNAL_REPORT_KEY] = report
    if composed.get("weight_kind") == "zero":
        # p = 0 already (inputs); only q changes.
        composed["log_q"] = float(composed["log_q"]) + noise_log_q
    else:
        composed["log_p"] = float(composed["log_p"]) + report["log_p"]
        composed["log_q"] = float(composed["log_q"]) + noise_log_q
        composed["log_importance_weight"] = float(composed["log_p"]) - float(composed["log_q"])
    record.meta = {**record.meta, SAMPLING_META_KEY: composed}
    return record


__all__ = [
    "choose_component",
    "compose_internal_weight",
    "internal_log_q",
    "mixture_log_q",
    "mixture_weight_bound",
    "needs_choice",
]
