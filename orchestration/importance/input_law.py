"""Input laws P and Q of importance sampling, shared by every target.

A sampling plan may give a law over a target's inputs (for example the ego
and NPC speeds of an AWSIM U-turn). The shared code draws the inputs and
computes their log densities; the target only declares its input variables
and, optionally, the ranges they may take (docs/blackbox_importance_sampling_plan.md §5.1).

Plan syntax (in ``target_measure.definition`` and ``proposal.definition``):

- ``parameters``: one law per variable, drawn independently in the order of
  the target's variables. P: ``uniform`` {min, max} or ``histogram`` {bins}.
  Q: ``target`` (same as P), ``uniform`` (same support as P),
  ``mixture_uniform_focus`` {base_weight, focus {min, max}} for a uniform P,
  ``mixture_target_focus`` {base_weight, focus histogram} for a histogram P.
- ``input_density``: a ``joint_histogram`` over all variables (cells are
  mixture components: overlapping cells add their densities).
- A proposal without either uses P for the inputs.

Each law keeps its own density formula and random-draw order, so results are
identical to the target-specific samplers they replace.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import product


# --- univariate laws ---------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Uniform:
    lower: float
    upper: float

    def draw(self, generator: random.Random) -> float:
        return generator.uniform(self.lower, self.upper)

    def log_density(self, value: float) -> float:
        if not self.lower <= value <= self.upper:
            return float("-inf")
        return -math.log(self.upper - self.lower)


@dataclass(frozen=True, slots=True)
class MixtureUniformFocus:
    """beta * P + (1 - beta) * uniform focus, for a uniform P."""

    base: Uniform
    focus: Uniform
    base_weight: float

    def draw(self, generator: random.Random) -> float:
        distribution = self.base if generator.random() < self.base_weight else self.focus
        return distribution.draw(generator)

    def log_density(self, value: float) -> float:
        base_density = math.exp(self.base.log_density(value))
        focus_log_density = self.focus.log_density(value)
        focus_density = 0.0 if focus_log_density == float("-inf") else math.exp(focus_log_density)
        density = self.base_weight * base_density + (1.0 - self.base_weight) * focus_density
        return math.log(density)


@dataclass(frozen=True, slots=True)
class WeightedInterval:
    lower: float
    upper: float
    mass: float

    @property
    def density(self) -> float:
        return self.mass / (self.upper - self.lower)


@dataclass(frozen=True, slots=True)
class Histogram:
    intervals: tuple[WeightedInterval, ...]

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

    def log_density(self, value: float) -> float:
        density = self.density(value)
        return math.log(density) if density > 0.0 else float("-inf")

    def contains_support(self, other: "Histogram") -> bool:
        return all(
            any(container.lower <= interval.lower and interval.upper <= container.upper for container in self.intervals)
            for interval in other.intervals
        )


@dataclass(frozen=True, slots=True)
class MixtureTargetFocus:
    """beta * P + (1 - beta) * histogram focus, for a histogram P."""

    target: Histogram
    focus: Histogram
    base_weight: float

    def draw(self, generator: random.Random) -> float:
        return self.target.draw(generator) if generator.random() < self.base_weight else self.focus.draw(generator)

    def density(self, value: float) -> float:
        return self.base_weight * self.target.density(value) + (1.0 - self.base_weight) * self.focus.density(value)

    def log_density(self, value: float) -> float:
        density = self.density(value)
        return math.log(density) if density > 0.0 else float("-inf")


UnivariateLaw = Uniform | MixtureUniformFocus | Histogram | MixtureTargetFocus
_MIXTURES = (MixtureUniformFocus, MixtureTargetFocus)


# --- laws over all inputs ----------------------------------------------------

@dataclass(frozen=True, slots=True)
class IndependentLaw:
    """Independent variables, drawn and summed in the order of ``parameters``."""

    parameters: Mapping[str, UnivariateLaw]

    @property
    def variables(self) -> tuple[str, ...]:
        return tuple(self.parameters)

    def draw(self, generator: random.Random) -> dict[str, float]:
        return {name: law.draw(generator) for name, law in self.parameters.items()}

    def log_density(self, values: Mapping[str, float]) -> float:
        return sum(law.log_density(float(values[name])) for name, law in self.parameters.items())


@dataclass(frozen=True, slots=True)
class JointHistogramCell:
    bounds: Mapping[str, Uniform]
    weight: float

    def contains(self, values: Mapping[str, float]) -> bool:
        return all(interval.lower <= float(values[name]) <= interval.upper for name, interval in self.bounds.items())

    @property
    def density(self) -> float:
        volume = math.prod(interval.upper - interval.lower for interval in self.bounds.values())
        return self.weight / volume


@dataclass(frozen=True, slots=True)
class JointHistogram:
    """A finite mixture of uniform boxes over the complete input vector."""

    variables: tuple[str, ...]
    cells: tuple[JointHistogramCell, ...]

    def draw(self, generator: random.Random) -> dict[str, float]:
        total_weight = sum(cell.weight for cell in self.cells)
        selected = generator.random() * total_weight
        cumulative = 0.0
        cell = self.cells[-1]
        for candidate in self.cells:
            cumulative += candidate.weight
            if selected < cumulative:
                cell = candidate
                break
        return {name: generator.uniform(interval.lower, interval.upper) for name, interval in cell.bounds.items()}

    def log_density(self, values: Mapping[str, float]) -> float:
        density = sum(cell.density for cell in self.cells if cell.contains(values))
        return math.log(density) if density > 0.0 else float("-inf")


InputLaw = IndependentLaw | JointHistogram


@dataclass(frozen=True)
class InputVariables:
    """The input variables a target declares (``input_variables`` of its hooks).

    ``names`` fixes the variables and their draw order. ``ranges`` (optional)
    bounds the support of uniform laws; ``default_target`` (optional) is the
    P used when a plan gives none (otherwise P is required). ``label`` names
    the target in error messages.
    """

    names: tuple[str, ...]
    label: str
    ranges: Mapping[str, tuple[float, float]] | None = None
    default_target: Mapping[str, object] | None = None


@dataclass(frozen=True)
class InputLaws:
    target: InputLaw
    proposal: InputLaw
    # Exact sup p/q over the inputs.
    weight_upper_bound: float


@dataclass(frozen=True, slots=True)
class InputDraw:
    values: dict[str, float]
    log_p: float
    log_q: float


def parse_input_laws(
    target_definition: Mapping[str, object],
    proposal_definition: Mapping[str, object],
    variables: InputVariables,
) -> InputLaws:
    target = _parse_target(target_definition, variables)
    proposal = _parse_proposal(proposal_definition, target, variables)
    return InputLaws(target=target, proposal=proposal, weight_upper_bound=input_weight_upper_bound(target, proposal))


def draw_target(laws: InputLaws, *, seed: int | None) -> dict[str, float]:
    return laws.target.draw(random.Random(seed))


def draw_proposal(laws: InputLaws, *, seed: int | None) -> InputDraw:
    values = laws.proposal.draw(random.Random(seed))
    log_p = laws.target.log_density(values)
    log_q = laws.proposal.log_density(values)
    if not math.isfinite(log_p) or not math.isfinite(log_q):
        raise ValueError("proposal generated a value outside declared P/Q support")
    return InputDraw(values=values, log_p=log_p, log_q=log_q)


# --- parsing -----------------------------------------------------------------

def _parse_target(definition: Mapping[str, object], variables: InputVariables) -> InputLaw:
    raw_density = definition.get("input_density")
    if raw_density is not None:
        return _joint_histogram(raw_density, variables, context=f"{variables.label} target input_density")
    raw_parameters = definition.get("parameters")
    if raw_parameters is None:
        if variables.default_target is None:
            raise ValueError(f"{variables.label} target_measure.definition.parameters must be a mapping")
        raw_parameters = variables.default_target
    parameters = _mapping(raw_parameters, f"{variables.label} target_measure.definition.parameters")
    _require_names(parameters, variables, "target")
    parsed: dict[str, UnivariateLaw] = {}
    for name in variables.names:
        spec = _mapping(parameters[name], f"target parameter {name}")
        kind = spec.get("distribution")
        if kind == "uniform":
            parsed[name] = _uniform(spec, name=name, variables=variables, context="target parameter")
        elif kind == "histogram":
            parsed[name] = _histogram(spec, context=f"target parameter {name}")
        else:
            raise ValueError(f"target parameter {name} must use distribution 'uniform' or 'histogram'")
    return IndependentLaw(parsed)


def _parse_proposal(definition: Mapping[str, object], target: InputLaw, variables: InputVariables) -> InputLaw:
    raw_density = definition.get("input_density")
    if raw_density is not None:
        return _joint_histogram(raw_density, variables, context=f"{variables.label} proposal input_density")
    raw_parameters = definition.get("parameters")
    if raw_parameters is None:
        return target
    if not isinstance(target, IndependentLaw):
        raise ValueError("proposal.parameters cannot be combined with target input_density")
    parameters = _mapping(raw_parameters, f"{variables.label} proposal.definition.parameters")
    _require_names(parameters, variables, "proposal")
    parsed: dict[str, UnivariateLaw] = {}
    for name in variables.names:
        spec = _mapping(parameters[name], f"proposal parameter {name}")
        kind = spec.get("distribution")
        base = target.parameters[name]
        if kind == "target":
            parsed[name] = base
        elif kind == "uniform":
            proposal = _uniform(spec, name=name, variables=variables, context="proposal parameter")
            if proposal != base:
                raise ValueError(
                    f"uniform proposal parameter {name} must have the same support as P; "
                    "use mixture_uniform_focus to bias a subrange"
                )
            parsed[name] = proposal
        elif kind in {"mixture_uniform_focus", "mixture_target_focus"}:
            base_weight = _number(spec.get("base_weight"), f"proposal parameter {name}.base_weight")
            if not 0.0 < base_weight <= 1.0:
                raise ValueError(f"proposal parameter {name}.base_weight must be in (0, 1]")
            if kind == "mixture_uniform_focus":
                if not isinstance(base, Uniform):
                    raise ValueError(f"mixture_uniform_focus for {name} needs a uniform P")
                focus = _uniform(_mapping(spec.get("focus"), f"proposal parameter {name}.focus"),
                                 name=name, variables=variables, context="proposal focus")
                if not base.lower <= focus.lower < focus.upper <= base.upper:
                    raise ValueError(f"proposal focus for {name} must be inside P support")
                parsed[name] = MixtureUniformFocus(base=base, focus=focus, base_weight=base_weight)
            else:
                if not isinstance(base, Histogram):
                    raise ValueError(f"mixture_target_focus for {name} needs a histogram P")
                focus_histogram = _histogram(spec.get("focus"), context=f"proposal focus {name}")
                if not base.contains_support(focus_histogram):
                    raise ValueError(f"proposal focus for {name} must be inside P support")
                parsed[name] = MixtureTargetFocus(base, focus_histogram, base_weight)
        else:
            raise ValueError(
                f"proposal parameter {name}.distribution must be 'target', 'uniform', "
                "'mixture_uniform_focus' or 'mixture_target_focus'"
            )
    return IndependentLaw(parsed)


def _joint_histogram(raw: object, variables: InputVariables, *, context: str) -> JointHistogram:
    spec = _mapping(raw, context)
    if spec.get("distribution") != "joint_histogram":
        raise ValueError(f"{context}.distribution must be 'joint_histogram'")
    if tuple(spec.get("variables", ())) != variables.names:
        raise ValueError(f"{context}.variables must be {list(variables.names)}")
    raw_cells = spec.get("cells")
    if not isinstance(raw_cells, list) or not raw_cells:
        raise ValueError(f"{context}.cells must be a non-empty list")
    unnormalized: list[JointHistogramCell] = []
    for index, raw_cell in enumerate(raw_cells):
        cell = _mapping(raw_cell, f"{context}.cells[{index}]")
        raw_bounds = _mapping(cell.get("bounds"), f"{context}.cells[{index}].bounds")
        bounds = {
            name: _uniform(_mapping(raw_bounds.get(name), f"{context}.cells[{index}].bounds.{name}"),
                           name=name, variables=variables, context=f"{context}.cells[{index}].bounds")
            for name in variables.names
        }
        unnormalized.append(JointHistogramCell(bounds=bounds,
                                               weight=_number(cell.get("weight"), f"{context}.cells[{index}].weight")))
    total_weight = sum(cell.weight for cell in unnormalized)
    if total_weight <= 0.0:
        raise ValueError(f"{context}.cells must have positive total weight")
    return JointHistogram(
        variables.names,
        tuple(JointHistogramCell(bounds=cell.bounds, weight=cell.weight / total_weight) for cell in unnormalized),
    )


def _histogram(raw: object, *, context: str) -> Histogram:
    spec = _mapping(raw, context)
    if spec.get("distribution") != "histogram":
        raise ValueError(f"{context}.distribution must be 'histogram'")
    raw_bins = spec.get("bins")
    if not isinstance(raw_bins, list) or not raw_bins:
        raise ValueError(f"{context}.bins must be a non-empty list")
    intervals = tuple(
        WeightedInterval(
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
    return Histogram(ordered)


def _uniform(spec: Mapping[str, object], *, name: str, variables: InputVariables, context: str) -> Uniform:
    lower = _number(spec.get("min"), f"{context} {name}.min")
    upper = _number(spec.get("max"), f"{context} {name}.max")
    if not lower < upper:
        raise ValueError(f"{context} {name} must satisfy min < max")
    if variables.ranges is not None and name in variables.ranges:
        allowed_lower, allowed_upper = variables.ranges[name]
        if lower < allowed_lower or upper > allowed_upper:
            raise ValueError(
                f"{context} {name} support [{lower}, {upper}] is outside scenario range "
                f"[{allowed_lower}, {allowed_upper}]"
            )
    return Uniform(lower, upper)


def _require_names(parameters: Mapping[str, object], variables: InputVariables, role: str) -> None:
    if set(parameters) != set(variables.names):
        raise ValueError(
            f"{variables.label} {role} parameters must be exactly {sorted(variables.names)}, got {sorted(parameters)}"
        )


# --- weight bound ------------------------------------------------------------

def input_weight_upper_bound(target: InputLaw, proposal: InputLaw) -> float:
    """The exact sup p/q over the inputs for the supported laws."""
    if isinstance(target, IndependentLaw) and isinstance(proposal, IndependentLaw):
        return math.prod(
            1.0 / law.base_weight if isinstance(law, _MIXTURES) else 1.0
            for law in proposal.parameters.values()
        )
    variables = target.variables
    return _joint_ratio_bound(
        JointHistogram(variables, _cells(target, variables)),
        JointHistogram(variables, _cells(proposal, variables)),
        variables,
    )


def _cells(law: InputLaw, variables: Sequence[str]) -> tuple[JointHistogramCell, ...]:
    if isinstance(law, JointHistogram):
        return law.cells
    components: list[list[tuple[Uniform, float]]] = []
    for name in variables:
        distribution = law.parameters[name]
        if isinstance(distribution, Uniform):
            components.append([(distribution, 1.0)])
        elif isinstance(distribution, MixtureUniformFocus):
            components.append([(distribution.base, distribution.base_weight),
                               (distribution.focus, 1.0 - distribution.base_weight)])
        else:
            raise ValueError("a joint input density can only be compared with uniform laws")
    return tuple(
        JointHistogramCell(
            bounds={name: interval for name, (interval, _) in zip(variables, component, strict=True)},
            weight=math.prod(weight for _, weight in component),
        )
        for component in product(*components)
    )


def _joint_ratio_bound(target: JointHistogram, proposal: JointHistogram, variables: Sequence[str]) -> float:
    """Evaluate p/q on the finite grid induced by all box boundaries."""
    boundaries = {
        name: sorted({
            endpoint
            for density in (target, proposal)
            for cell in density.cells
            for endpoint in (cell.bounds[name].lower, cell.bounds[name].upper)
        })
        for name in variables
    }
    maximum = 0.0
    for coordinate in product(*[
        [(left + right) / 2.0 for left, right in zip(boundaries[name], boundaries[name][1:])]
        for name in variables
    ]):
        values = dict(zip(variables, coordinate, strict=True))
        log_p = target.log_density(values)
        if log_p == float("-inf"):
            continue
        log_q = proposal.log_density(values)
        if log_q == float("-inf"):
            raise ValueError("joint proposal Q has zero density in P support")
        maximum = max(maximum, math.exp(log_p - log_q))
    return maximum


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be a mapping")
    return value


def _number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{context} must be a finite number")
    return float(value)


__all__ = [
    "InputDraw",
    "InputLaws",
    "InputVariables",
    "draw_proposal",
    "draw_target",
    "input_weight_upper_bound",
    "parse_input_laws",
]
