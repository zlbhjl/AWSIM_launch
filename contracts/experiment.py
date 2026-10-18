"""Contracts for strategies, sampling designs, and stopping rules.

A strategy issues test cases until it decides to stop. Every existing strategy
already follows ``Strategy``. New strategies compose two independent parts:

- a ``SamplingDesign`` decides the inputs of each sample (the experimental
  design), and declares what kind of samples it produces;
- a ``StoppingRule`` decides, from the results so far, whether to stop and what
  to report (the statistical algorithm), and declares which kinds of samples
  its guarantee needs.

Keeping them separate lets one stopping rule serve every target and design, and
lets the orchestrator state whether a combination carries a guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

import pandas as pd

from contracts.execution import TestCase
from contracts.statistics import StatisticalReport


class SampleKind(str, Enum):
    """How the samples of a design are drawn."""

    IID_UNIFORM = "iid_uniform"  # independent draws from the evaluated input distribution
    FIXED_POINT_IID = "fixed_point_iid"  # independent repetitions at one fixed input (e.g. PRISM paths)
    ADAPTIVE = "adaptive"  # inputs chosen from earlier results (e.g. GP exploration)
    WEIGHTED = "weighted"  # drawn from a proposal Q, each with a recorded P/Q weight


class Guarantee(str, Enum):
    """What a stopping rule promises about its final result."""

    FIXED_SAMPLE = "fixed_sample"  # valid for one evaluation at a sample size fixed in advance
    ANYTIME = "anytime"  # valid at whatever time the rule stops
    APPROXIMATE = "approximate"  # no formal guarantee
    NONE = "none"  # the samples do not meet the rule's requirements


@runtime_checkable
class Strategy(Protocol):
    """Issues test cases; ``None`` means the strategy has stopped."""

    def next_test_case(self) -> TestCase | None: ...


@runtime_checkable
class SamplingDesign(Protocol):
    name: str
    sample_kind: SampleKind

    def inputs(self, sample_index: int) -> dict[str, object]:
        """Inputs of sample ``sample_index`` (1-based); reproducible for the same index."""


@dataclass(frozen=True)
class StopDecision:
    stop: bool
    reason: str = ""
    report: StatisticalReport | None = None


@dataclass(frozen=True)
class StoppingRuleSpec:
    """Static facts about a stopping rule."""

    name: str
    method: str  # statistical method family: binomial_ci, sprt, dkw, ebstop
    guarantee: Guarantee
    required_sample_kinds: frozenset[SampleKind] = field(default_factory=lambda: frozenset({SampleKind.IID_UNIFORM}))


@runtime_checkable
class StoppingRule(Protocol):
    spec: StoppingRuleSpec

    def decide(self, data: pd.DataFrame | None, *, issued: int) -> StopDecision:
        """Decide from the completed samples whether to stop; ``issued`` counts samples issued so far."""


def combined_guarantee(rule: StoppingRuleSpec, design_kind: SampleKind) -> Guarantee:
    """The guarantee a rule keeps when fed samples of ``design_kind``."""
    if design_kind not in rule.required_sample_kinds:
        return Guarantee.NONE
    return rule.guarantee


__all__ = [
    "Guarantee",
    "SampleKind",
    "SamplingDesign",
    "StopDecision",
    "StoppingRule",
    "StoppingRuleSpec",
    "Strategy",
    "combined_guarantee",
]
