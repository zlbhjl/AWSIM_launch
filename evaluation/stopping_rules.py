"""Stopping rules with an explicit evaluation schedule and declared guarantee.

The legacy statistical modes re-evaluate whenever the orchestrator asks, with
the same confidence each time; whether their result carries a guarantee
depends on how often that happens. The rules here fix the schedule themselves:

- ``*_fixed_strict``: evaluate exactly once, on the first ``n`` samples. The
  usual fixed-sample guarantee holds.
- ``*_sequential_anytime``: evaluate after every new sample, spending the error
  budget as delta_k = 6 delta / (pi^2 k^2) on the k-th evaluation. Since
  sum_k delta_k <= delta, the result is valid at whatever time the rule stops.
- ``sprt_wald`` and ``ebstop_anytime`` wrap the existing SPRT and EBStop
  services, which are sequential by design.

All rules use only rows with ``status == success`` and a value for the metric,
in sample order.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.stats import beta as beta_distribution

from contracts.experiment import Guarantee, SampleKind, StopDecision, StoppingRuleSpec
from contracts.statistics import StatisticalReport, StatisticalRequest

IID_KINDS = frozenset({SampleKind.IID_UNIFORM, SampleKind.FIXED_POINT_IID})


def anytime_delta(delta: float, look: int) -> float:
    """Error budget of the ``look``-th evaluation (1-based); the budgets sum to at most ``delta``."""
    return 6.0 * delta / (math.pi**2 * look**2)


def clopper_pearson(successes: int, trials: int, delta: float) -> tuple[float, float]:
    lower = 0.0 if successes == 0 else float(beta_distribution.ppf(delta / 2, successes, trials - successes + 1))
    upper = 1.0 if successes == trials else float(beta_distribution.ppf(1 - delta / 2, successes + 1, trials - successes))
    return lower, upper


def dkw_quantile_interval(
    values: np.ndarray,
    q: float,
    delta: float,
    *,
    lower_support: float = -math.inf,
    upper_support: float = math.inf,
) -> tuple[float, float]:
    """Confidence interval for the q-quantile from the DKW band.

    With epsilon = sqrt(ln(2/delta) / (2n)), the band F_n - epsilon <= F <= F_n + epsilon
    holds with probability at least 1 - delta, so the q-quantile lies between the
    order statistics of ranks ceil(n(q - epsilon)) and ceil(n(q + epsilon)). When a
    rank falls outside 1..n, the bound is the support of the distribution.
    """
    ordered = np.sort(np.asarray(values, dtype=float))
    n = len(ordered)
    epsilon = math.sqrt(math.log(2.0 / delta) / (2.0 * n))
    lower_rank = math.ceil(n * (q - epsilon))
    upper_rank = math.ceil(n * (q + epsilon))
    lower = float(ordered[lower_rank - 1]) if lower_rank >= 1 else lower_support
    upper = float(ordered[upper_rank - 1]) if upper_rank <= n else upper_support
    return lower, upper


def _metric_values(data: pd.DataFrame | None, metric: str) -> np.ndarray:
    if data is None or data.empty or metric not in data.columns:
        return np.asarray([], dtype=float)
    frame = data
    if "status" in frame.columns:
        frame = frame[frame["status"].fillna("").astype(str).str.lower().eq("success")]
    if "sample_index" in frame.columns:
        frame = frame.assign(_order=pd.to_numeric(frame["sample_index"], errors="coerce")).sort_values("_order")
    values = pd.to_numeric(frame[metric], errors="coerce").dropna()
    return values.to_numpy(dtype=float)


def _report(
    spec: StoppingRuleSpec,
    metric: str,
    *,
    sample_count: int,
    estimate: float | None,
    interval: tuple[float, float] | None,
    sufficient: bool,
    next_action: str,
    diagnostics: Mapping[str, object],
) -> StatisticalReport:
    return StatisticalReport(
        method=spec.method,
        metric=metric,
        sample_count=sample_count,
        estimate=estimate,
        interval=interval,
        sufficient=sufficient,
        next_action=next_action,
        diagnostics={"status": "success", "stopping_rule": spec.name, "guarantee": spec.guarantee.value, **diagnostics},
    )


@dataclass
class _SequentialLooks:
    """Counts evaluations on new data, so repeated calls without new samples do not spend budget."""

    last_count: int = 0
    looks: int = 0

    def next_look(self, count: int) -> int | None:
        if count <= self.last_count:
            return None
        self.last_count = count
        self.looks += 1
        return self.looks


@dataclass
class BinomialFixedStrict:
    metric: str
    sample_size: int
    confidence: float = 0.95
    spec: StoppingRuleSpec = field(
        default_factory=lambda: StoppingRuleSpec(
            "binomial_ci_fixed_strict", "binomial_ci", Guarantee.FIXED_SAMPLE, IID_KINDS
        )
    )

    def decide(self, data: pd.DataFrame | None, *, issued: int) -> StopDecision:
        values = _metric_values(data, self.metric)
        valid = values[np.isin(values, (0.0, 1.0))]
        if len(valid) < self.sample_size:
            return StopDecision(False)
        sample = valid[: self.sample_size]
        successes = int(sample.sum())
        interval = clopper_pearson(successes, self.sample_size, 1.0 - self.confidence)
        report = _report(
            self.spec, self.metric, sample_count=self.sample_size, estimate=successes / self.sample_size,
            interval=interval, sufficient=True, next_action="stop",
            diagnostics={"confidence": self.confidence, "method": "clopper-pearson", "successes": successes},
        )
        return StopDecision(True, f"{self.spec.name}: evaluated once at n={self.sample_size}", report)


@dataclass
class BinomialSequentialAnytime:
    metric: str
    target_width: float
    max_samples: int
    confidence: float = 0.95
    spec: StoppingRuleSpec = field(
        default_factory=lambda: StoppingRuleSpec(
            "binomial_ci_sequential_anytime", "binomial_ci", Guarantee.ANYTIME, IID_KINDS
        )
    )
    _looks: _SequentialLooks = field(default_factory=_SequentialLooks)
    _last: StatisticalReport | None = None

    def decide(self, data: pd.DataFrame | None, *, issued: int) -> StopDecision:
        values = _metric_values(data, self.metric)
        valid = values[np.isin(values, (0.0, 1.0))]
        n = len(valid)
        look = self._looks.next_look(n) if n else None
        if look is not None:
            delta = anytime_delta(1.0 - self.confidence, look)
            successes = int(valid.sum())
            interval = clopper_pearson(successes, n, delta)
            width = interval[1] - interval[0]
            done = width <= self.target_width or n >= self.max_samples
            self._last = _report(
                self.spec, self.metric, sample_count=n, estimate=successes / n, interval=interval, sufficient=done,
                next_action="stop" if width <= self.target_width else ("stop_max_samples" if done else "collect_more_samples"),
                diagnostics={"confidence": self.confidence, "look": look, "look_delta": delta, "interval_width": width},
            )
        if self._last is not None and self._last.next_action in {"stop", "stop_max_samples"}:
            return StopDecision(True, f"{self.spec.name}: {self._last.next_action} at n={self._last.sample_count}", self._last)
        return StopDecision(False, report=self._last)


@dataclass
class DKWFixedStrict:
    metric: str
    sample_size: int
    q: float = 0.05
    confidence: float = 0.95
    lower_support: float = -math.inf
    spec: StoppingRuleSpec = field(
        default_factory=lambda: StoppingRuleSpec("dkw_fixed_strict", "dkw", Guarantee.FIXED_SAMPLE, IID_KINDS)
    )

    def decide(self, data: pd.DataFrame | None, *, issued: int) -> StopDecision:
        values = _metric_values(data, self.metric)
        if len(values) < self.sample_size:
            return StopDecision(False)
        sample = values[: self.sample_size]
        delta = 1.0 - self.confidence
        interval = dkw_quantile_interval(sample, self.q, delta, lower_support=self.lower_support)
        report = _report(
            self.spec, self.metric, sample_count=self.sample_size, estimate=float(np.quantile(sample, self.q)),
            interval=interval, sufficient=True, next_action="stop",
            diagnostics={"confidence": self.confidence, "q": self.q, "delta": delta},
        )
        return StopDecision(True, f"{self.spec.name}: evaluated once at n={self.sample_size}", report)


@dataclass
class DKWSequentialAnytime:
    metric: str
    target_width: float
    max_samples: int
    q: float = 0.05
    confidence: float = 0.95
    lower_support: float = -math.inf
    spec: StoppingRuleSpec = field(
        default_factory=lambda: StoppingRuleSpec("dkw_sequential_anytime", "dkw", Guarantee.ANYTIME, IID_KINDS)
    )
    _looks: _SequentialLooks = field(default_factory=_SequentialLooks)
    _last: StatisticalReport | None = None

    def decide(self, data: pd.DataFrame | None, *, issued: int) -> StopDecision:
        values = _metric_values(data, self.metric)
        n = len(values)
        look = self._looks.next_look(n) if n else None
        if look is not None:
            delta = anytime_delta(1.0 - self.confidence, look)
            interval = dkw_quantile_interval(values, self.q, delta, lower_support=self.lower_support)
            width = interval[1] - interval[0]
            done = width <= self.target_width or n >= self.max_samples
            self._last = _report(
                self.spec, self.metric, sample_count=n, estimate=float(np.quantile(values, self.q)), interval=interval,
                sufficient=done,
                next_action="stop" if width <= self.target_width else ("stop_max_samples" if done else "collect_more_samples"),
                diagnostics={"confidence": self.confidence, "q": self.q, "look": look, "look_delta": delta,
                             "interval_width": width},
            )
        if self._last is not None and self._last.next_action in {"stop", "stop_max_samples"}:
            return StopDecision(True, f"{self.spec.name}: {self._last.next_action} at n={self._last.sample_count}", self._last)
        return StopDecision(False, report=self._last)


@dataclass
class _ServiceRule:
    """Wraps an existing statistical service whose result is valid when checked repeatedly."""

    service: object
    request: StatisticalRequest
    spec: StoppingRuleSpec

    def decide(self, data: pd.DataFrame | None, *, issued: int) -> StopDecision:
        report = self.service.evaluate_request(data, self.request)
        report.diagnostics.update({"stopping_rule": self.spec.name, "guarantee": self.spec.guarantee.value})
        if report.next_action in {"stop", "stop_max_samples", "error"}:
            return StopDecision(True, f"{self.spec.name}: {report.next_action} at n={report.sample_count}", report)
        return StopDecision(False, report=report)


def sprt_wald(service: object, request: StatisticalRequest) -> _ServiceRule:
    return _ServiceRule(service, request, StoppingRuleSpec("sprt_wald", "sprt", Guarantee.ANYTIME, IID_KINDS))


def ebstop_anytime(service: object, request: StatisticalRequest) -> _ServiceRule:
    return _ServiceRule(service, request, StoppingRuleSpec("ebstop_anytime", "ebstop", Guarantee.ANYTIME, IID_KINDS))


STOPPING_RULE_NAMES = (
    "binomial_ci_fixed_strict",
    "binomial_ci_sequential_anytime",
    "dkw_fixed_strict",
    "dkw_sequential_anytime",
    "sprt_wald",
    "ebstop_anytime",
)

__all__ = [
    "BinomialFixedStrict",
    "BinomialSequentialAnytime",
    "DKWFixedStrict",
    "DKWSequentialAnytime",
    "STOPPING_RULE_NAMES",
    "anytime_delta",
    "clopper_pearson",
    "dkw_quantile_interval",
    "ebstop_anytime",
    "sprt_wald",
]
