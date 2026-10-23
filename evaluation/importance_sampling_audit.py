"""Completion audit for a fixed-N importance-sampling evaluation.

A final estimate is complete only when every planned sample has been judged
and, for weighted runs, has a known weight (positive or exactly zero).  A failed,
unjudged or numerically broken sample leaves the evaluation incomplete; it is
retried as the same sample, never replaced by a new draw.  Records are matched
to the plan by ``meta.sampling.sample_id``; when a sample was attempted more
than once, the attempt with the largest ``meta.attempt_id`` decides.

See docs/sde_importance_sampling_plan.md §6.5.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from math import isfinite

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus


@dataclass(frozen=True)
class SampleAudit:
    """Counts and verdict of :func:`audit_fixed_sample_run`."""

    counts: dict[str, int]
    complete: bool
    unresolved_sample_ids: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "complete": self.complete,
            "counts": dict(self.counts),
            "unresolved_sample_ids": list(self.unresolved_sample_ids),
            "problems": list(self.problems),
        }


def audit_fixed_sample_run(
    records: Sequence[EvaluationRecord],
    *,
    planned_sample_ids: Sequence[str],
    metric: str,
    weighted: bool = True,
) -> SampleAudit:
    """Check that every planned sample has a judged result (and a weight)."""

    planned = list(planned_sample_ids)
    if len(set(planned)) != len(planned):
        raise ValueError("planned_sample_ids must be unique")
    planned_set = set(planned)
    problems: list[str] = []

    attempts: dict[str, list[tuple[int, int, EvaluationRecord]]] = {}
    without_id = 0
    for position, record in enumerate(records):
        sample_id = _sample_id(record)
        if sample_id is None:
            without_id += 1
            continue
        attempts.setdefault(sample_id, []).append((_attempt_id(record), position, record))
    if without_id:
        problems.append(f"{without_id} record(s) have no sample_id")
    unexpected = sorted(set(attempts) - planned_set)
    if unexpected:
        problems.append(f"{len(unexpected)} sample_id(s) are not in the plan")

    counts = {
        "planned": len(planned),
        "attempted": 0,
        "succeeded": 0,
        "judged": 0,
        "weighted": 0,
        "zero_weight": 0,
        "unresolved": 0,
        "duplicate_successes": 0,
        "retries": 0,
    }
    unresolved: list[str] = []
    for sample_id in planned:
        sample_attempts = sorted(attempts.get(sample_id, ()), key=lambda item: (item[0], item[1]))
        if not sample_attempts:
            counts["unresolved"] += 1
            unresolved.append(sample_id)
            continue
        counts["attempted"] += 1
        counts["retries"] += len(sample_attempts) - 1
        successes = sum(1 for _, _, record in sample_attempts if record.status is RunStatus.SUCCESS)
        if successes > 1:
            counts["duplicate_successes"] += 1
        latest = sample_attempts[-1][2]
        resolved, zero = _resolved(latest, metric=metric, weighted=weighted)
        if latest.status is RunStatus.SUCCESS:
            counts["succeeded"] += 1
        if resolved:
            counts["judged"] += 1
            if weighted:
                counts["weighted"] += 1
                counts["zero_weight"] += int(zero)
        else:
            counts["unresolved"] += 1
            unresolved.append(sample_id)
    if counts["duplicate_successes"]:
        problems.append(f"{counts['duplicate_successes']} sample(s) succeeded more than once")

    complete = not unresolved and not problems
    return SampleAudit(
        counts=counts,
        complete=complete,
        unresolved_sample_ids=unresolved,
        problems=problems,
    )


def _sample_id(record: EvaluationRecord) -> str | None:
    sampling = record.meta.get("sampling")
    if isinstance(sampling, dict):
        value = sampling.get("sample_id")
        if isinstance(value, str) and value:
            return value
    return None


def _attempt_id(record: EvaluationRecord) -> int:
    value = record.meta.get("attempt_id", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("meta.attempt_id must be an integer")
    return value


def _resolved(record: EvaluationRecord, *, metric: str, weighted: bool) -> tuple[bool, bool]:
    """Return (judged and weighted as required, weight is exactly zero)."""
    if record.status is not RunStatus.SUCCESS:
        return False, False
    value = record.output.get(metric)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, False
    if not isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
        return False, False
    if not weighted:
        return True, False
    sampling = record.meta.get("sampling")
    if not isinstance(sampling, dict) or sampling.get("importance_weight_status") != "available":
        return False, False
    if sampling.get("weight_kind") == "zero":
        return True, True
    log_weight = sampling.get("log_importance_weight")
    if isinstance(log_weight, bool) or not isinstance(log_weight, (int, float)):
        return False, False
    return isfinite(float(log_weight)), False


__all__ = ["SampleAudit", "audit_fixed_sample_run"]
