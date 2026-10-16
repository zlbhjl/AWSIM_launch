"""Construct PRISM proposal-Q candidates within human-specified limits.

This module deliberately supports only the reliability DTMC currently handled
by :mod:`targets.prism.sampling`.  It never edits the PRISM model (P): it
builds complete proposal distributions Q from an initial Q and the explicitly
permitted changes in ``proposal_search``.
"""

from __future__ import annotations

from copy import deepcopy
from itertools import product
import math
from typing import Mapping, Sequence

from contracts.sampling import ProposalCandidate
from orchestration.sampling_plan import SamplingPlan

from .model_catalog import PrismModelDefinition, normalize_constants
from .sampling import _proposal_transitions, _target_transitions


_STATE_NAMES = {0: "normal", 1: "degraded", 2: "failure"}
_STATE_VALUES = {name: value for value, name in _STATE_NAMES.items()}


class PrismProposalSearchError(ValueError):
    """The human-specified PRISM proposal search is not safe or supported."""


def build_proposal_candidates(
    plan: SamplingPlan,
    definition: PrismModelDefinition,
    *,
    target_constants: Mapping[str, float],
    proposal_version: int = 1,
) -> tuple[ProposalCandidate, ...]:
    """Build candidate Qs without touching P or unspecified Q transitions.

    Each adjustable transition must state ``from``, ``to``, ``values``, and,
    when row sums are preserved, ``compensate_to``.  The latter absorbs exactly
    the change in that one row, making every Q modification explicit.
    """
    if definition.model_id != "simple_reliability_dtmc":
        raise PrismProposalSearchError(
            "proposal search is currently supported only for simple_reliability_dtmc"
        )
    if plan.target != "prism":
        raise PrismProposalSearchError("proposal search requires a prism sampling plan")
    search = _require_mapping(plan.proposal_search, "proposal_search")
    if search.get("enabled") is not True:
        raise PrismProposalSearchError("proposal_search.enabled must be true")

    constraints = _require_mapping(search.get("constraints", {}), "proposal_search.constraints")
    if constraints.get("preserve_row_sum") is not True:
        raise PrismProposalSearchError(
            "proposal_search.constraints.preserve_row_sum must be true"
        )
    if constraints.get("preserve_support") is not True:
        raise PrismProposalSearchError(
            "proposal_search.constraints.preserve_support must be true"
        )

    adjustments = _parse_adjustments(search.get("adjustable_transitions"))
    baseline = _proposal_transitions(plan.proposal_definition)
    target = _target_transitions(normalize_constants(definition, target_constants))
    candidates: list[ProposalCandidate] = []
    for ordinal, selected_values in enumerate(product(*(item.values for item in adjustments)), start=1):
        transitions = deepcopy(baseline)
        labels: list[str] = []
        for adjustment, value in zip(adjustments, selected_values, strict=True):
            row = transitions[adjustment.source]
            if adjustment.destination not in row or adjustment.compensate_destination not in row:
                raise PrismProposalSearchError(
                    "adjustable transition is outside P support for its source state"
                )
            previous = row[adjustment.destination]
            row[adjustment.destination] = value
            row[adjustment.compensate_destination] -= value - previous
            labels.append(
                f"{_STATE_NAMES[adjustment.source]}_{_STATE_NAMES[adjustment.destination]}={value:g}"
            )

        _validate_candidate_transitions(transitions, target)
        definition_mapping = _as_definition(transitions)
        candidates.append(
            ProposalCandidate(
                plan_id=plan.experiment_id,
                proposal_id=f"{plan.proposal_id}__candidate_{ordinal:03d}",
                proposal_version=proposal_version,
                definition=definition_mapping,
                reason="human_configured_proposal_search: " + ", ".join(labels),
                diagnostics={
                    "search_policy": search.get("policy", "suggest"),
                    "adjustments": labels,
                    "max_transition_likelihood_ratio": _max_transition_likelihood_ratio(
                        transitions, target
                    ),
                },
            )
        )
    return tuple(candidates)


class _Adjustment:
    def __init__(self, source: int, destination: int, compensate_destination: int, values: tuple[float, ...]):
        self.source = source
        self.destination = destination
        self.compensate_destination = compensate_destination
        self.values = values


def _parse_adjustments(raw: object) -> tuple[_Adjustment, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise PrismProposalSearchError("proposal_search.adjustable_transitions must be a non-empty list")
    adjustments: list[_Adjustment] = []
    sources: set[int] = set()
    for index, item in enumerate(raw):
        data = _require_mapping(item, f"proposal_search.adjustable_transitions[{index}]")
        source = _state_value(data.get("from"), "from")
        destination = _state_value(data.get("to"), "to")
        compensate = _state_value(data.get("compensate_to"), "compensate_to")
        if source in sources:
            raise PrismProposalSearchError(
                "only one adjustable transition per source state is supported"
            )
        if destination == compensate:
            raise PrismProposalSearchError("to and compensate_to must differ")
        values_raw = data.get("values")
        if not isinstance(values_raw, Sequence) or isinstance(values_raw, (str, bytes)) or not values_raw:
            raise PrismProposalSearchError("adjustable transition values must be a non-empty list")
        values = tuple(_probability(value, "adjustable transition value") for value in values_raw)
        sources.add(source)
        adjustments.append(_Adjustment(source, destination, compensate, values))
    return tuple(adjustments)


def _validate_candidate_transitions(
    proposal: Mapping[int, Mapping[int, float]], target: Mapping[int, Mapping[int, float]],
) -> None:
    for state, p_row in target.items():
        q_row = proposal[state]
        if not math.isclose(sum(q_row.values()), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise PrismProposalSearchError(f"candidate row {_STATE_NAMES[state]!r} does not sum to 1")
        for successor, q_probability in q_row.items():
            if not 0.0 <= q_probability <= 1.0:
                raise PrismProposalSearchError("candidate probability must be in [0, 1]")
            if p_row[successor] > 0.0 and q_probability <= 0.0:
                raise PrismProposalSearchError(
                    "candidate violates preserve_support: P > 0 requires Q > 0"
                )
            if p_row[successor] == 0.0 and q_probability != 0.0:
                raise PrismProposalSearchError(
                    "candidate introduces a transition outside P support"
                )


def _as_definition(transitions: Mapping[int, Mapping[int, float]]) -> dict[str, object]:
    return {
        "transition_probabilities": {
            _STATE_NAMES[state]: {
                _STATE_NAMES[successor]: probability
                for successor, probability in row.items()
            }
            for state, row in transitions.items()
        }
    }


def _max_transition_likelihood_ratio(
    proposal: Mapping[int, Mapping[int, float]], target: Mapping[int, Mapping[int, float]],
) -> float:
    return max(
        p_probability / proposal[state][successor]
        for state, row in target.items()
        for successor, p_probability in row.items()
        if p_probability > 0.0
    )


def _require_mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PrismProposalSearchError(f"{context} must be a mapping")
    return value


def _state_value(value: object, field_name: str) -> int:
    if not isinstance(value, str) or value not in _STATE_VALUES:
        raise PrismProposalSearchError(f"{field_name} must be one of {sorted(_STATE_VALUES)}")
    return _STATE_VALUES[value]


def _probability(value: object, context: str) -> float:
    try:
        probability = float(value)
    except (TypeError, ValueError) as exc:
        raise PrismProposalSearchError(f"{context} must be numeric") from exc
    if not 0.0 <= probability <= 1.0:
        raise PrismProposalSearchError(f"{context} must be in [0, 1]")
    return probability


__all__ = ["PrismProposalSearchError", "build_proposal_candidates"]
