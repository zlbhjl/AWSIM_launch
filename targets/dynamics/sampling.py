"""Input-space P/Q sampling for deterministic dynamics experiments.

The ODE/SDE model is deliberately not modified here.  P and Q describe only
how the three externally supplied U-turn parameters are sampled.  The first
supported proposal is a mixture retaining a positive P component, so Q covers
the complete declared support of P and an exact P/Q ratio is available.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from itertools import product
from typing import Mapping

from orchestration.sampling_plan import SamplingPlan
from scenario_specs.uturn import PARAM_RANGES


_PARAMETERS = tuple(PARAM_RANGES)


@dataclass(frozen=True, slots=True)
class DynamicsInputSample:
    """One sampled input vector plus its shared sampling provenance."""

    values: dict[str, float]
    sampling_metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class _Uniform:
    lower: float
    upper: float

    def draw(self, generator: random.Random) -> float:
        return generator.uniform(self.lower, self.upper)

    def log_density(self, value: float) -> float:
        if not self.lower <= value <= self.upper:
            return float("-inf")
        return -math.log(self.upper - self.lower)


@dataclass(frozen=True, slots=True)
class _MixtureUniformFocus:
    base: _Uniform
    focus: _Uniform
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
class _IndependentDensity:
    """Legacy product density retained as the default U-turn input law."""

    parameters: Mapping[str, _Uniform | _MixtureUniformFocus]

    def draw(self, generator: random.Random) -> dict[str, float]:
        return {name: distribution.draw(generator) for name, distribution in self.parameters.items()}

    def log_density(self, values: Mapping[str, float]) -> float:
        return sum(self.parameters[name].log_density(float(values[name])) for name in _PARAMETERS)


@dataclass(frozen=True, slots=True)
class _JointHistogramCell:
    bounds: Mapping[str, _Uniform]
    weight: float

    def contains(self, values: Mapping[str, float]) -> bool:
        return all(
            interval.lower <= float(values[name]) <= interval.upper
            for name, interval in self.bounds.items()
        )

    @property
    def density(self) -> float:
        volume = math.prod(interval.upper - interval.lower for interval in self.bounds.values())
        return self.weight / volume


@dataclass(frozen=True, slots=True)
class _JointHistogramDensity:
    """A finite mixture of uniform boxes over the complete input vector."""

    cells: tuple[_JointHistogramCell, ...]

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
        return {
            name: generator.uniform(interval.lower, interval.upper)
            for name, interval in cell.bounds.items()
        }

    def log_density(self, values: Mapping[str, float]) -> float:
        density = sum(cell.density for cell in self.cells if cell.contains(values))
        return math.log(density) if density > 0.0 else float("-inf")


class DynamicsInputSampler:
    """Interpret the dynamics-specific P/Q definition in a :class:`SamplingPlan`."""

    def __init__(self, plan: SamplingPlan):
        if plan.target != "dynamics":
            raise ValueError("sampling plan target must be 'dynamics'")
        if plan.sampling_mode != "manual_proposal":
            raise ValueError("dynamics importance sampling currently requires manual_proposal")
        definition = _mapping(plan.target_measure_definition, "dynamics target_measure.definition")
        if definition.get("model_id") != "uturn":
            raise ValueError("dynamics target_measure.definition.model_id must be 'uturn'")
        self.plan = plan
        self.model_id = "uturn"
        self.case_kind = str(definition.get("case_kind", "uturn"))
        if self.case_kind != "uturn":
            raise ValueError("dynamics importance sampling currently supports case_kind 'uturn'")
        execution = definition.get("execution", {})
        self.execution_inputs = dict(_mapping(execution, "dynamics target_measure.definition.execution"))
        self.target = _parse_target_density(definition)
        proposal_definition = _mapping(plan.proposal_definition, "dynamics proposal.definition")
        self.proposal = _parse_proposal_density(proposal_definition, self.target)
        self.sde_noise_proposal = _parse_sde_noise_proposal(
            proposal_definition.get("noise_path_q", proposal_definition.get("noise_path")),
            execution=self.execution_inputs,
        )
        input_weight_upper_bound = _importance_weight_upper_bound(self.target, self.proposal)
        self.importance_weight_upper_bound: float | None = (
            input_weight_upper_bound * (1.0 / float(self.sde_noise_proposal["base_weight"]))
            if self.sde_noise_proposal is not None and self.sde_noise_proposal["distribution"] == "defensive_gaussian_drift"
            else (input_weight_upper_bound if self.sde_noise_proposal is None else None)
        )

    def sample_target(self, *, seed: int | None) -> DynamicsInputSample:
        generator = random.Random(seed)
        values = self.target.draw(generator)
        return DynamicsInputSample(
            values=values,
            sampling_metadata={
                "plan_id": self.plan.experiment_id,
                "target_measure_id": self.plan.target_measure_id,
                "sampling_mode": "uniform",
                "importance_weight_status": "not_applicable",
            },
        )

    def sample_proposal(self, *, seed: int | None) -> DynamicsInputSample:
        generator = random.Random(seed)
        values = self.proposal.draw(generator)
        log_p = self.target.log_density(values)
        log_q = self.proposal.log_density(values)
        if not math.isfinite(log_p) or not math.isfinite(log_q):
            raise ValueError("proposal generated a value outside declared P/Q support")
        metadata: dict[str, object] = {
            "plan_id": self.plan.experiment_id,
            "target_measure_id": self.plan.target_measure_id,
            "sampling_mode": "manual_proposal",
            "proposal_id": self.plan.proposal_id,
            "importance_weight_status": "available",
            "log_p": log_p,
            "log_q": log_q,
            "log_importance_weight": log_p - log_q,
        }
        if self.importance_weight_upper_bound is not None:
            metadata["importance_weight_upper_bound"] = self.importance_weight_upper_bound
        return DynamicsInputSample(
            values=values,
            sampling_metadata=metadata,
        )


def _parse_target_density(definition: Mapping[str, object]) -> _IndependentDensity | _JointHistogramDensity:
    raw_density = definition.get("input_density")
    if raw_density is not None:
        return _joint_histogram(raw_density, context="dynamics target input_density")
    raw_parameters = definition.get("parameters")
    if raw_parameters is None:
        return _IndependentDensity({
            name: _Uniform(lower, upper) for name, (lower, upper) in PARAM_RANGES.items()
        })
    return _IndependentDensity(_parse_target_parameters(raw_parameters))


def _parse_proposal_density(
    definition: Mapping[str, object],
    target: _IndependentDensity | _JointHistogramDensity,
) -> _IndependentDensity | _JointHistogramDensity:
    raw_density = definition.get("input_density")
    if raw_density is not None:
        return _joint_histogram(raw_density, context="dynamics proposal input_density")
    raw_parameters = definition.get("parameters")
    if raw_parameters is None:
        return target
    if not isinstance(target, _IndependentDensity):
        raise ValueError("legacy proposal.parameters cannot be combined with target input_density")
    return _IndependentDensity(_parse_proposal_parameters(raw_parameters, target.parameters))


def _parse_target_parameters(raw: object) -> dict[str, _Uniform]:
    parameters = _mapping(raw, "dynamics target_measure.definition.parameters")
    _require_parameter_names(parameters)
    parsed: dict[str, _Uniform] = {}
    for name in _PARAMETERS:
        spec = _mapping(parameters[name], f"target parameter {name}")
        if spec.get("distribution") != "uniform":
            raise ValueError(f"target parameter {name} must use distribution 'uniform'")
        parsed[name] = _uniform(spec, name=name, context="target parameter")
    return parsed


def _parse_proposal_parameters(
    raw: object,
    target: Mapping[str, _Uniform],
) -> dict[str, _Uniform | _MixtureUniformFocus]:
    parameters = _mapping(raw, "dynamics proposal.definition.parameters")
    _require_parameter_names(parameters)
    parsed: dict[str, _Uniform | _MixtureUniformFocus] = {}
    for name in _PARAMETERS:
        spec = _mapping(parameters[name], f"proposal parameter {name}")
        distribution = spec.get("distribution")
        if distribution == "uniform":
            proposal = _uniform(spec, name=name, context="proposal parameter")
            if proposal != target[name]:
                raise ValueError(
                    f"uniform proposal parameter {name} must have the same support as P; "
                    "use mixture_uniform_focus to bias a subrange"
                )
            parsed[name] = proposal
        elif distribution == "mixture_uniform_focus":
            base_weight = _finite_float(spec.get("base_weight"), f"proposal parameter {name}.base_weight")
            if not 0.0 < base_weight <= 1.0:
                raise ValueError(f"proposal parameter {name}.base_weight must be in (0, 1]")
            focus = _uniform(_mapping(spec.get("focus"), f"proposal parameter {name}.focus"), name=name, context="proposal focus")
            base = target[name]
            if not base.lower <= focus.lower < focus.upper <= base.upper:
                raise ValueError(f"proposal focus for {name} must be inside P support")
            parsed[name] = _MixtureUniformFocus(base=base, focus=focus, base_weight=base_weight)
        else:
            raise ValueError(
                f"proposal parameter {name} must use 'uniform' or 'mixture_uniform_focus'"
            )
    return parsed


def _parse_sde_noise_proposal(
    raw: object,
    *,
    execution: Mapping[str, object],
) -> dict[str, object] | None:
    """Parse a bounded proposal over Euler--Maruyama Brownian paths.

    The density is ``b P_noise + (1-b) R_noise`` where R gives each named
    Brownian channel a constant Girsanov drift.  The backend evaluates the
    realised path density, since its active channels depend on the path.
    """
    if raw is None:
        return None
    if execution.get("solver_kind") != "sde":
        raise ValueError("proposal noise_path requires execution.solver_kind='sde'")
    spec = _mapping(raw, "dynamics proposal.definition.noise_path")
    distribution = spec.get("distribution")
    if distribution not in {"gaussian_drift", "defensive_gaussian_drift"}:
        raise ValueError("proposal noise_path.distribution must be 'gaussian_drift' or 'defensive_gaussian_drift'")
    base_weight = (
        _finite_float(spec.get("base_weight"), "proposal noise_path.base_weight")
        if distribution == "defensive_gaussian_drift" else None
    )
    if base_weight is not None and not 0.0 < base_weight <= 1.0:
        raise ValueError("proposal noise_path.base_weight must be in (0, 1]")
    drifts = _mapping(spec.get("channel_drifts", {}), "proposal noise_path.channel_drifts")
    allowed = {"ego_brake_acceleration", "npc_acceleration", "npc_heading"}
    if not set(drifts) <= allowed:
        raise ValueError("proposal noise_path has an unsupported Brownian channel")
    normalized = {
        str(channel): _finite_float(value, f"proposal noise_path.channel_drifts.{channel}")
        for channel, value in drifts.items()
    }
    schedule = spec.get("drift_schedule", [])
    if not isinstance(schedule, list):
        raise ValueError("proposal noise_path.drift_schedule must be a list")
    return {
        "distribution": distribution,
        **({"base_weight": base_weight} if base_weight is not None else {}),
        "channel_drifts": normalized,
        "drift_schedule": schedule,
    }


def _joint_histogram(raw: object, *, context: str) -> _JointHistogramDensity:
    """Read a human-specified joint input density.

    Cells are mixture components rather than a partition: overlapping cells
    contribute both densities.  This keeps the density definition identical
    to the sampler without needing a separate overlap convention.
    """

    spec = _mapping(raw, context)
    if spec.get("distribution") != "joint_histogram":
        raise ValueError(f"{context}.distribution must be 'joint_histogram'")
    variables = tuple(spec.get("variables", ()))
    if variables != _PARAMETERS:
        raise ValueError(f"{context}.variables must be {list(_PARAMETERS)}")
    raw_cells = spec.get("cells")
    if not isinstance(raw_cells, list) or not raw_cells:
        raise ValueError(f"{context}.cells must be a non-empty list")

    unnormalized: list[_JointHistogramCell] = []
    for index, raw_cell in enumerate(raw_cells):
        cell = _mapping(raw_cell, f"{context}.cells[{index}]")
        raw_bounds = _mapping(cell.get("bounds"), f"{context}.cells[{index}].bounds")
        bounds = {
            name: _uniform(
                _mapping(raw_bounds.get(name), f"{context}.cells[{index}].bounds.{name}"),
                name=name,
                context=f"{context}.cells[{index}].bounds",
            )
            for name in _PARAMETERS
        }
        unnormalized.append(
            _JointHistogramCell(
                bounds=bounds,
                weight=_finite_float(cell.get("weight"), f"{context}.cells[{index}].weight"),
            )
        )

    total_weight = sum(cell.weight for cell in unnormalized)
    if total_weight <= 0.0:
        raise ValueError(f"{context}.cells must have positive total weight")
    return _JointHistogramDensity(
        tuple(
            _JointHistogramCell(bounds=cell.bounds, weight=cell.weight / total_weight)
            for cell in unnormalized
        )
    )


def _importance_weight_upper_bound(
    target: _IndependentDensity | _JointHistogramDensity,
    proposal: _IndependentDensity | _JointHistogramDensity,
) -> float:
    """Return the exact finite bound for the density models currently supported."""

    if isinstance(target, _IndependentDensity) and isinstance(proposal, _IndependentDensity):
        return math.prod(
            1.0 / distribution.base_weight
            if isinstance(distribution, _MixtureUniformFocus)
            else 1.0
            for distribution in proposal.parameters.values()
        )
    return _joint_histogram_ratio_bound(
        _JointHistogramDensity(_density_cells(target)),
        _JointHistogramDensity(_density_cells(proposal)),
    )


def _density_cells(density: _IndependentDensity | _JointHistogramDensity) -> tuple[_JointHistogramCell, ...]:
    if isinstance(density, _JointHistogramDensity):
        return density.cells

    components: list[list[tuple[_Uniform, float]]] = []
    for name in _PARAMETERS:
        distribution = density.parameters[name]
        if isinstance(distribution, _Uniform):
            components.append([(distribution, 1.0)])
        else:
            components.append([
                (distribution.base, distribution.base_weight),
                (distribution.focus, 1.0 - distribution.base_weight),
            ])
    return tuple(
        _JointHistogramCell(
            bounds={name: interval for name, (interval, _) in zip(_PARAMETERS, component, strict=True)},
            weight=math.prod(weight for _, weight in component),
        )
        for component in product(*components)
    )


def _joint_histogram_ratio_bound(
    target: _JointHistogramDensity,
    proposal: _JointHistogramDensity,
) -> float:
    """Evaluate p/q on the finite grid induced by all box boundaries."""

    boundaries = {
        name: sorted({
            endpoint
            for density in (target, proposal)
            for cell in density.cells
            for endpoint in (cell.bounds[name].lower, cell.bounds[name].upper)
        })
        for name in _PARAMETERS
    }
    maximum = 0.0
    for coordinate in product(*[
        [(left + right) / 2.0 for left, right in zip(boundaries[name], boundaries[name][1:])]
        for name in _PARAMETERS
    ]):
        values = dict(zip(_PARAMETERS, coordinate, strict=True))
        log_p = target.log_density(values)
        if log_p == float("-inf"):
            continue
        log_q = proposal.log_density(values)
        if log_q == float("-inf"):
            raise ValueError("joint proposal Q has zero density in P support")
        maximum = max(maximum, math.exp(log_p - log_q))
    return maximum


def _uniform(spec: Mapping[str, object], *, name: str, context: str) -> _Uniform:
    lower = _finite_float(spec.get("min"), f"{context} {name}.min")
    upper = _finite_float(spec.get("max"), f"{context} {name}.max")
    if not lower < upper:
        raise ValueError(f"{context} {name} must satisfy min < max")
    allowed_lower, allowed_upper = PARAM_RANGES[name]
    if lower < allowed_lower or upper > allowed_upper:
        raise ValueError(
            f"{context} {name} support [{lower}, {upper}] is outside scenario range "
            f"[{allowed_lower}, {allowed_upper}]"
        )
    return _Uniform(lower, upper)


def _require_parameter_names(parameters: Mapping[str, object]) -> None:
    actual = set(parameters)
    expected = set(_PARAMETERS)
    if actual != expected:
        raise ValueError(
            "dynamics sampling parameters must be exactly "
            f"{sorted(expected)}, got {sorted(actual)}"
        )


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be a mapping")
    return value


def _finite_float(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{context} must be a finite number")
    return number


__all__ = ["DynamicsInputSample", "DynamicsInputSampler"]
