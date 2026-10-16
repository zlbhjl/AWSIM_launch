"""Input-space P/Q sampling for AWSIM U-turn experiments.

The initial adapter deliberately samples only the external ego/NPC speeds.
``dx0`` remains a human-selected scenario condition, rather than inventing a
probability law for an unobserved U-turn timing variable.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Mapping

from orchestration.sampling_plan import SamplingPlan


_SPEED_NAMES = ("ego_speed", "npc_speed")


@dataclass(frozen=True, slots=True)
class AWSIMInputSample:
    values: dict[str, float]
    sampling_metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class _WeightedInterval:
    lower: float
    upper: float
    mass: float

    @property
    def density(self) -> float:
        return self.mass / (self.upper - self.lower)


@dataclass(frozen=True, slots=True)
class _Histogram:
    intervals: tuple[_WeightedInterval, ...]

    def draw(self, generator: random.Random) -> float:
        selected = generator.random()
        cumulative = 0.0
        interval = self.intervals[-1]
        for candidate in self.intervals:
            cumulative += candidate.mass
            if selected < cumulative:
                interval = candidate
                break
        return generator.uniform(interval.lower, interval.upper)

    def density(self, value: float) -> float:
        for index, interval in enumerate(self.intervals):
            right_inclusive = index == len(self.intervals) - 1
            if interval.lower <= value < interval.upper or (right_inclusive and value == interval.upper):
                return interval.density
        return 0.0

    def contains_support(self, other: "_Histogram") -> bool:
        return all(
            any(
                container.lower <= interval.lower and interval.upper <= container.upper
                for container in self.intervals
            )
            for interval in other.intervals
        )


@dataclass(frozen=True, slots=True)
class _MixtureTargetFocus:
    target: _Histogram
    focus: _Histogram
    base_weight: float

    def draw(self, generator: random.Random) -> float:
        return (
            self.target.draw(generator)
            if generator.random() < self.base_weight
            else self.focus.draw(generator)
        )

    def density(self, value: float) -> float:
        return self.base_weight * self.target.density(value) + (1.0 - self.base_weight) * self.focus.density(value)


class AWSIMInputSampler:
    """Interpret AWSIM-specific speed distributions in a common SamplingPlan."""

    def __init__(self, plan: SamplingPlan):
        if plan.target != "awsim":
            raise ValueError("sampling plan target must be 'awsim'")
        if plan.sampling_mode != "manual_proposal":
            raise ValueError("AWSIM importance sampling currently requires manual_proposal")
        definition = _mapping(plan.target_measure_definition, "awsim target_measure.definition")
        self.plan = plan
        self.case_kind = _string(definition.get("case_kind", "uturn"), "case_kind")
        if self.case_kind != "uturn":
            raise ValueError("AWSIM importance sampling currently supports case_kind 'uturn'")
        fixed_inputs = dict(_mapping(definition.get("fixed_inputs"), "awsim fixed_inputs"))
        if "dx0" not in fixed_inputs:
            raise ValueError("awsim fixed_inputs.dx0 is required")
        self.fixed_inputs = fixed_inputs
        # Container and CLI execution options are deliberately not scenario
        # inputs.  They are selected by the runtime profile at the CLI edge.
        _mapping(definition.get("execution", {}), "awsim execution")
        parameters = _mapping(definition.get("parameters"), "awsim target_measure.definition.parameters")
        self.target = _parse_parameters(parameters, context="target")
        self.proposal = _parse_proposal(plan.proposal_definition, self.target)
        self.importance_weight_upper_bound = math.prod(
            1.0 / distribution.base_weight
            if isinstance(distribution, _MixtureTargetFocus)
            else 1.0
            for distribution in self.proposal.values()
        )

    def sample_target(self, *, seed: int | None) -> AWSIMInputSample:
        generator = random.Random(seed)
        return AWSIMInputSample(
            values={name: distribution.draw(generator) for name, distribution in self.target.items()},
            sampling_metadata={
                "plan_id": self.plan.experiment_id,
                "target_measure_id": self.plan.target_measure_id,
                "sampling_mode": "uniform",
                "importance_weight_status": "not_applicable",
            },
        )

    def sample_proposal(self, *, seed: int | None) -> AWSIMInputSample:
        generator = random.Random(seed)
        values = {name: distribution.draw(generator) for name, distribution in self.proposal.items()}
        log_p = sum(_log_density(self.target[name].density(value), name, "P") for name, value in values.items())
        log_q = sum(_log_density(self.proposal[name].density(value), name, "Q") for name, value in values.items())
        return AWSIMInputSample(
            values=values,
            sampling_metadata={
                "plan_id": self.plan.experiment_id,
                "target_measure_id": self.plan.target_measure_id,
                "sampling_mode": "manual_proposal",
                "proposal_id": self.plan.proposal_id,
                "importance_weight_status": "available",
                "log_p": log_p,
                "log_q": log_q,
                "log_importance_weight": log_p - log_q,
                "importance_weight_upper_bound": self.importance_weight_upper_bound,
            },
        )


def _parse_parameters(raw: Mapping[str, object], *, context: str) -> dict[str, _Histogram]:
    if set(raw) != set(_SPEED_NAMES):
        raise ValueError(f"AWSIM {context} parameters must be exactly {list(_SPEED_NAMES)}")
    return {name: _histogram(raw[name], context=f"{context} parameter {name}") for name in _SPEED_NAMES}


def _parse_proposal(raw: object, target: Mapping[str, _Histogram]) -> dict[str, _Histogram | _MixtureTargetFocus]:
    definition = _mapping(raw, "awsim proposal.definition")
    parameters = _mapping(definition.get("parameters"), "awsim proposal.definition.parameters")
    if set(parameters) != set(_SPEED_NAMES):
        raise ValueError(f"AWSIM proposal parameters must be exactly {list(_SPEED_NAMES)}")
    parsed: dict[str, _Histogram | _MixtureTargetFocus] = {}
    for name in _SPEED_NAMES:
        spec = _mapping(parameters[name], f"proposal parameter {name}")
        kind = spec.get("distribution")
        if kind == "target":
            parsed[name] = target[name]
        elif kind == "mixture_target_focus":
            base_weight = _number(spec.get("base_weight"), f"proposal parameter {name}.base_weight")
            if not 0.0 < base_weight <= 1.0:
                raise ValueError(f"proposal parameter {name}.base_weight must be in (0, 1]")
            focus = _histogram(spec.get("focus"), context=f"proposal focus {name}")
            if not target[name].contains_support(focus):
                raise ValueError(f"proposal focus for {name} must be inside P support")
            parsed[name] = _MixtureTargetFocus(target[name], focus, base_weight)
        else:
            raise ValueError(f"proposal parameter {name}.distribution must be 'target' or 'mixture_target_focus'")
    return parsed


def _histogram(raw: object, *, context: str) -> _Histogram:
    spec = _mapping(raw, context)
    if spec.get("distribution") != "histogram":
        raise ValueError(f"{context}.distribution must be 'histogram'")
    raw_bins = spec.get("bins")
    if not isinstance(raw_bins, list) or not raw_bins:
        raise ValueError(f"{context}.bins must be a non-empty list")
    intervals = tuple(
        _WeightedInterval(
            _number(_mapping(item, f"{context}.bins[{index}]").get("min"), f"{context}.bins[{index}].min"),
            _number(_mapping(item, f"{context}.bins[{index}]").get("max"), f"{context}.bins[{index}].max"),
            _number(_mapping(item, f"{context}.bins[{index}]").get("weight"), f"{context}.bins[{index}].weight"),
        )
        for index, item in enumerate(raw_bins)
    )
    if any(not interval.lower < interval.upper or interval.mass <= 0.0 for interval in intervals):
        raise ValueError(f"{context}.bins require min < max and positive weight")
    ordered = tuple(sorted(intervals, key=lambda interval: interval.lower))
    if any(left.upper > right.lower for left, right in zip(ordered, ordered[1:])):
        raise ValueError(f"{context}.bins must not overlap")
    total = sum(interval.mass for interval in ordered)
    if not math.isclose(total, 1.0, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(f"{context}.bins weights must sum to 1")
    return _Histogram(ordered)


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be a mapping")
    return value


def _number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{context} must be a finite number")
    return float(value)


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a non-empty string")
    return value


def _log_density(value: float, name: str, measure: str) -> float:
    if value <= 0.0 or not math.isfinite(value):
        raise ValueError(f"{measure} density for {name} must be positive and finite")
    return math.log(value)


__all__ = ["AWSIMInputSample", "AWSIMInputSampler"]
