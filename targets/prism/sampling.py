"""Importance-sampled paths for the supported PRISM reliability DTMC.

The original PRISM model defines P and is never rewritten.  This module
generates an auxiliary path under the human-specified proposal Q, then records
the likelihood ratio for that same path under P and Q.
"""

from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from orchestration.sampling_plan import SamplingPlan, load_sampling_plan

from .model_catalog import PrismModelDefinition


_STATE_NAMES = {0: "normal", 1: "degraded", 2: "failure"}
_STATE_VALUES = {name: value for value, name in _STATE_NAMES.items()}


@dataclass(frozen=True)
class PrismImportanceSampleArtifacts:
    trace_csv: Path
    sampling_metadata: dict[str, object]


def run_target_sample_path(
    definition: PrismModelDefinition,
    *,
    constants: Mapping[str, float],
    horizon: int,
    output_dir: Path,
    seed: int | None = None,
) -> PrismImportanceSampleArtifacts:
    """Generate a reproducible ordinary path under the target distribution P."""
    if definition.model_id != "simple_reliability_dtmc":
        raise ValueError("target-adapter sampling is currently supported only for simple_reliability_dtmc")
    trace_csv, _, _ = _sample_trace(
        transitions=_target_transitions(constants), horizon=horizon,
        output_dir=output_dir, seed=seed,
    )
    return PrismImportanceSampleArtifacts(trace_csv=trace_csv, sampling_metadata={})


def run_importance_sample_path(
    definition: PrismModelDefinition,
    *,
    constants: Mapping[str, float],
    horizon: int,
    output_dir: Path,
    sampling_plan_path: str | Path,
    seed: int | None = None,
    proposal_definition: object | None = None,
    proposal_id: str | None = None,
    proposal_version: int | None = None,
    sampling_mode: str | None = None,
) -> PrismImportanceSampleArtifacts:
    """Generate one reliability-DTMC path under a fixed manual proposal Q."""

    if definition.model_id != "simple_reliability_dtmc":
        raise ValueError(
            "importance sampling is currently supported only for "
            "simple_reliability_dtmc"
        )
    plan = load_sampling_plan(sampling_plan_path)
    _validate_plan(plan, definition)
    selected_definition = plan.proposal_definition if proposal_definition is None else proposal_definition
    q_transitions = _proposal_transitions(selected_definition)
    p_transitions = _target_transitions(constants)

    trace_csv, log_p, log_q = _sample_trace(
        transitions=q_transitions, horizon=horizon, output_dir=output_dir, seed=seed,
        target_transitions=p_transitions,
    )
    sampling_metadata: dict[str, object] = {
        "plan_id": plan.experiment_id,
        "target_measure_id": plan.target_measure_id,
        "sampling_mode": sampling_mode or plan.sampling_mode,
        "proposal_id": proposal_id or plan.proposal_id,
        "importance_weight_status": "available",
        "log_p": log_p,
        "log_q": log_q,
        "log_importance_weight": log_p - log_q,
        "importance_weight_upper_bound": _max_path_importance_weight(
            p_transitions, q_transitions, horizon=horizon
        ),
    }
    if proposal_version is not None:
        sampling_metadata["proposal_version"] = proposal_version
    return PrismImportanceSampleArtifacts(trace_csv=trace_csv, sampling_metadata=sampling_metadata)


def _sample_trace(*, transitions: Mapping[int, Mapping[int, float]], horizon: int,
                  output_dir: Path, seed: int | None,
                  target_transitions: Mapping[int, Mapping[int, float]] | None = None) -> tuple[Path, float, float]:
    generator = random.Random(seed)
    state = 0
    log_p = 0.0
    log_q = 0.0
    trace_rows: list[tuple[int, int]] = [(0, state)]
    for step in range(1, horizon + 1):
        q_row = transitions[state]
        p_row = target_transitions[state] if target_transitions is not None else q_row
        next_state = _draw_successor(q_row, generator)
        p_probability, q_probability = p_row[next_state], q_row[next_state]
        if p_probability <= 0.0 or q_probability <= 0.0:
            raise ValueError("proposal generated a transition outside P support")
        log_p += math.log(p_probability)
        log_q += math.log(q_probability)
        state = next_state
        trace_rows.append((step, state))

    output_dir.mkdir(parents=True, exist_ok=True)
    trace_csv = output_dir / "trace.csv"
    with trace_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("step", "state"))
        writer.writerows(trace_rows)

    return trace_csv, log_p, log_q


def _validate_plan(plan: SamplingPlan, definition: PrismModelDefinition) -> None:
    if plan.target != "prism":
        raise ValueError("sampling plan target must be 'prism'")
    if plan.sampling_mode != "manual_proposal":
        raise ValueError("PRISM importance sampling currently requires manual_proposal")
    target_definition = plan.target_measure_definition
    if not isinstance(target_definition, Mapping):
        raise ValueError("PRISM target_measure.definition must be a mapping")
    model_id = target_definition.get("model")
    if model_id != definition.model_id:
        raise ValueError(
            "sampling plan target_measure.definition.model must match the PRISM model"
        )


def _target_transitions(constants: Mapping[str, float]) -> dict[int, dict[int, float]]:
    normal_degrade = float(constants["P_NORMAL_DEGRADE"])
    normal_failure = float(constants["P_NORMAL_FAILURE"])
    degraded_normal = float(constants["P_DEGRADED_NORMAL"])
    degraded_failure = float(constants["P_DEGRADED_FAILURE"])
    return {
        0: {0: 1.0 - normal_degrade - normal_failure, 1: normal_degrade, 2: normal_failure},
        1: {0: degraded_normal, 1: 1.0 - degraded_normal - degraded_failure, 2: degraded_failure},
        2: {2: 1.0},
    }


def _proposal_transitions(definition: object) -> dict[int, dict[int, float]]:
    if not isinstance(definition, Mapping):
        raise ValueError("PRISM proposal.definition must be a mapping")
    raw_transitions = definition.get("transition_probabilities")
    if not isinstance(raw_transitions, Mapping):
        raise ValueError(
            "PRISM proposal.definition.transition_probabilities must be a mapping"
        )
    transitions: dict[int, dict[int, float]] = {}
    for state, successors in ((0, (0, 1, 2)), (1, (0, 1, 2)), (2, (2,))):
        raw_row = raw_transitions.get(_STATE_NAMES[state])
        if not isinstance(raw_row, Mapping):
            raise ValueError(
                f"proposal transition probabilities for {_STATE_NAMES[state]!r} are required"
            )
        row: dict[int, float] = {}
        for successor in successors:
            key = _STATE_NAMES[successor]
            if key not in raw_row:
                raise ValueError(
                    f"proposal probability {_STATE_NAMES[state]} -> {key} is required"
                )
            probability = float(raw_row[key])
            if probability < 0.0 or probability > 1.0:
                raise ValueError("proposal transition probabilities must be in [0, 1]")
            row[successor] = probability
        if not math.isclose(sum(row.values()), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError(
                f"proposal transition probabilities for {_STATE_NAMES[state]!r} must sum to 1"
            )
        transitions[state] = row
    return transitions


def _max_path_importance_weight(
    target_transitions: Mapping[int, Mapping[int, float]],
    proposal_transitions: Mapping[int, Mapping[int, float]],
    *,
    horizon: int,
) -> float:
    """Exact max P(path)/Q(path) for this finite-horizon DTMC adapter."""
    max_ratio_by_state = {state: 0.0 for state in target_transitions}
    max_ratio_by_state[0] = 1.0
    for _ in range(horizon):
        next_ratios = {state: 0.0 for state in target_transitions}
        for source, path_ratio in max_ratio_by_state.items():
            for destination, p_probability in target_transitions[source].items():
                if p_probability <= 0.0:
                    continue
                q_probability = proposal_transitions[source][destination]
                if q_probability <= 0.0:
                    raise ValueError("proposal must preserve target support")
                next_ratios[destination] = max(
                    next_ratios[destination], path_ratio * p_probability / q_probability
                )
        max_ratio_by_state = next_ratios
    return max(max_ratio_by_state.values())


def _draw_successor(probabilities: Mapping[int, float], generator: random.Random) -> int:
    threshold = generator.random()
    cumulative = 0.0
    last_state: int | None = None
    for state in sorted(probabilities):
        cumulative += probabilities[state]
        last_state = state
        if threshold < cumulative:
            return state
    assert last_state is not None
    return last_state


__all__ = ["PrismImportanceSampleArtifacts", "run_importance_sample_path", "run_target_sample_path"]
