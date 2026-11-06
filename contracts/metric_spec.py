"""What kind of values a target's metric takes, and which statistical methods fit it.

A target declares its metrics in ``StatisticalTargetProfile.metric_specs``; the
shared code refuses a method that does not fit the metric before anything runs,
and stops with an error (no interval) when a value outside the declaration
arrives (docs/statistical_modes_hardening_plan.md §3.2).

| kind      | values            | methods                        |
|-----------|-------------------|--------------------------------|
| binary    | 0 or 1            | binomial_ci, sprt, dkw         |
| bounded   | in [lower, upper] | ebstop, dkw                    |
| real      | any real (or inf) | dkw                            |
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

METHOD_KINDS: dict[str, frozenset[str]] = {
    "binomial_ci": frozenset({"binary"}),
    "sprt": frozenset({"binary"}),
    "ebstop": frozenset({"bounded"}),
    "dkw": frozenset({"binary", "bounded", "real"}),
}


@dataclass(frozen=True)
class MetricSpec:
    kind: str
    lower: float | None = None
    upper: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"binary", "bounded", "real"}:
            raise ValueError("metric kind must be 'binary', 'bounded' or 'real'")
        if self.kind == "bounded" and not (
            self.lower is not None and self.upper is not None and self.lower < self.upper
        ):
            raise ValueError("a bounded metric needs lower < upper")

    @property
    def value_range(self) -> tuple[float, float] | None:
        if self.kind == "binary":
            return (0.0, 1.0)
        if self.kind == "bounded":
            return (float(self.lower), float(self.upper))
        return None

    def violations(self, values: Iterable[float]) -> int:
        """How many values do not fit the declaration."""
        count = 0
        for value in values:
            number = float(value)
            if self.kind == "binary":
                count += number not in (0.0, 1.0)
            elif self.kind == "bounded":
                count += not (math.isfinite(number) and self.lower <= number <= self.upper)
            elif math.isnan(number):
                count += 1
        return count

    def describe(self) -> str:
        return {"binary": "0/1", "real": "real (no fixed range)"}.get(self.kind, f"in [{self.lower}, {self.upper}]")


def binary() -> MetricSpec:
    return MetricSpec("binary")


def bounded(lower: float, upper: float) -> MetricSpec:
    return MetricSpec("bounded", float(lower), float(upper))


def real() -> MetricSpec:
    return MetricSpec("real")


def check_method_fits(target: str, metric: str, method: str, spec: MetricSpec | None) -> None:
    """Refuse a method whose requirement the declared metric does not meet."""
    allowed = METHOD_KINDS.get(method)
    if spec is None or allowed is None or spec.kind in allowed:
        return
    hint = (
        " It has no fixed range (it can be infinite or arbitrarily large), and EBStop's guarantee needs every "
        "value in a fixed range. Use --mode dkw / dkw_fixed for its distribution, or a 0/1 metric with "
        "--mode binomial_ci (docs/statistical_method_selection.md, EBStop)."
        if method == "ebstop" and spec.kind == "real" else ""
    )
    raise ValueError(
        f"{method} cannot be used on {target}'s {metric}: {method} needs a "
        f"{' or '.join(sorted(allowed))} metric, and {metric} is declared {spec.describe()}.{hint}"
    )


__all__ = ["METHOD_KINDS", "MetricSpec", "binary", "bounded", "check_method_fits", "real"]
