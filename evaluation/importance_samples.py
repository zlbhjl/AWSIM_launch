"""Shared validation for records used by importance-sampling evaluators."""

from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from math import exp, inf, isfinite, log

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus

# exp(x) overflows a float above this value.
LOG_FLOAT_MAX = log(sys.float_info.max)


@dataclass(frozen=True, slots=True)
class ImportanceSample:
    """One bounded observation with an available P/Q likelihood ratio.

    ``log_weight`` is None for an exact zero weight (Q produced a value outside
    the support of P). Such a sample still counts toward N.
    """

    value: float
    log_weight: float | None

    @property
    def weight(self) -> float:
        """The weight as a float; ``inf`` if it is too large to represent."""
        if self.log_weight is None:
            return 0.0
        if self.log_weight > LOG_FLOAT_MAX:
            return inf
        return exp(self.log_weight)

    @property
    def log_contribution(self) -> float:
        """log(weight * value); ``-inf`` when the contribution is exactly zero."""
        if self.log_weight is None or self.value == 0.0:
            return -inf
        return self.log_weight + log(self.value)


@dataclass(frozen=True, slots=True)
class ImportanceRecordSet:
    """Usable samples and the number of records left out, by reason."""

    samples: list[ImportanceSample]
    excluded: dict[str, int] = field(default_factory=dict)


def collect_importance_records(
    records: Sequence[EvaluationRecord] | None,
    *,
    metric: str,
) -> ImportanceRecordSet:
    """Extract successful, bounded observations with usable importance weights.

    Records that cannot be used are counted in ``excluded`` by reason instead
    of being dropped without a trace. A present but out-of-range metric is a
    data-contract error rather than an absent sample.
    """
    samples: list[ImportanceSample] = []
    excluded: Counter[str] = Counter()
    for record in records or ():
        if record.status is not RunStatus.SUCCESS:
            excluded["not_success"] += 1
            continue
        sampling = record.meta.get("sampling")
        if not isinstance(sampling, dict):
            excluded["no_sampling_metadata"] += 1
            continue
        if sampling.get("importance_weight_status") != "available":
            excluded["weight_not_available"] += 1
            continue
        value = record.output.get(metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            excluded["metric_missing"] += 1
            continue
        numeric_value = float(value)
        if not isfinite(numeric_value):
            excluded["metric_not_finite"] += 1
            continue
        if not 0.0 <= numeric_value <= 1.0:
            raise ValueError(f"importance sampling metric {metric!r} must be bounded in [0, 1]")
        if sampling.get("weight_kind") == "zero":
            samples.append(ImportanceSample(value=numeric_value, log_weight=None))
            continue
        log_weight = sampling.get("log_importance_weight")
        if isinstance(log_weight, bool) or not isinstance(log_weight, (int, float)):
            excluded["weight_missing"] += 1
            continue
        numeric_log_weight = float(log_weight)
        if not isfinite(numeric_log_weight):
            excluded["weight_not_finite"] += 1
            continue
        samples.append(ImportanceSample(value=numeric_value, log_weight=numeric_log_weight))
    return ImportanceRecordSet(samples=samples, excluded=dict(excluded))


def collect_available_importance_samples(
    records: Sequence[EvaluationRecord] | None,
    *,
    metric: str,
) -> list[ImportanceSample]:
    """Return the usable samples of :func:`collect_importance_records`."""
    return collect_importance_records(records, metric=metric).samples


def log_sum_exp(values: Sequence[float]) -> float:
    """log(sum(exp(v))) without overflow; ``-inf`` for an empty or all ``-inf`` input."""
    finite = [value for value in values if value != -inf]
    if not finite:
        return -inf
    peak = max(finite)
    return peak + log(sum(exp(value - peak) for value in finite))


__all__ = [
    "LOG_FLOAT_MAX",
    "ImportanceRecordSet",
    "ImportanceSample",
    "collect_available_importance_samples",
    "collect_importance_records",
    "log_sum_exp",
]
