"""Shared validation for records used by importance-sampling evaluators."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import exp, isfinite

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus


@dataclass(frozen=True, slots=True)
class ImportanceSample:
    """One bounded observation with an available P/Q likelihood ratio."""

    value: float
    log_weight: float
    weight: float


def collect_available_importance_samples(
    records: Sequence[EvaluationRecord] | None,
    *,
    metric: str,
) -> list[ImportanceSample]:
    """Extract successful, bounded observations with usable importance weights.

    Records without a usable weight are intentionally ignored.  A present but
    out-of-range metric is a data-contract error rather than an absent sample.
    """
    samples: list[ImportanceSample] = []
    for record in records or ():
        if record.status is not RunStatus.SUCCESS:
            continue
        sampling = record.meta.get("sampling")
        if not isinstance(sampling, dict):
            continue
        if sampling.get("importance_weight_status") != "available":
            continue
        value = record.output.get(metric)
        log_weight = sampling.get("log_importance_weight")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if isinstance(log_weight, bool) or not isinstance(log_weight, (int, float)):
            continue
        numeric_value = float(value)
        numeric_log_weight = float(log_weight)
        if not (isfinite(numeric_value) and isfinite(numeric_log_weight)):
            continue
        if not 0.0 <= numeric_value <= 1.0:
            raise ValueError(f"importance sampling metric {metric!r} must be bounded in [0, 1]")
        weight = exp(numeric_log_weight)
        if not isfinite(weight):
            raise ValueError("importance sampling weight must be finite")
        samples.append(
            ImportanceSample(
                value=numeric_value,
                log_weight=numeric_log_weight,
                weight=weight,
            )
        )
    return samples


__all__ = ["ImportanceSample", "collect_available_importance_samples"]
