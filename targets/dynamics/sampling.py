"""Input-space P/Q sampling for deterministic dynamics experiments.

The ODE/SDE model is deliberately not modified here.  P and Q describe only
how the three externally supplied U-turn parameters are sampled.  The first
supported proposal is a mixture retaining a positive P component, so Q covers
the complete declared support of P and an exact P/Q ratio is available.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from orchestration.importance.input_law import InputVariables, draw_proposal, draw_target, parse_input_laws
from orchestration.sampling_plan import SamplingPlan
from scenario_specs.uturn import PARAM_RANGES


_PARAMETERS = tuple(PARAM_RANGES)
# The input variables the U-turn declares to the shared input laws (v1 plans).
INPUT_VARIABLES = InputVariables(
    names=_PARAMETERS,
    label="dynamics",
    ranges=PARAM_RANGES,
    default_target={name: {"distribution": "uniform", "min": lower, "max": upper}
                    for name, (lower, upper) in PARAM_RANGES.items()},
)


@dataclass(frozen=True, slots=True)
class DynamicsInputSample:
    """One sampled input vector plus its shared sampling provenance."""

    values: dict[str, float]
    sampling_metadata: dict[str, object]


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
        proposal_definition = _mapping(plan.proposal_definition, "dynamics proposal.definition")
        self.laws = parse_input_laws(definition, proposal_definition, INPUT_VARIABLES)
        self.sde_noise_proposal = _parse_sde_noise_proposal(
            proposal_definition.get("noise_path_q", proposal_definition.get("noise_path")),
            execution=self.execution_inputs,
        )
        input_weight_upper_bound = self.laws.weight_upper_bound
        self.internal_proposal = _legacy_internal_proposal(self.sde_noise_proposal)
        self.importance_weight_upper_bound: float | None = (
            input_weight_upper_bound * (1.0 / float(self.sde_noise_proposal["base_weight"]))
            if self.sde_noise_proposal is not None and self.sde_noise_proposal["distribution"] == "defensive_gaussian_drift"
            else (input_weight_upper_bound if self.sde_noise_proposal is None else None)
        )

    def sample_target(self, *, seed: int | None) -> DynamicsInputSample:
        values = draw_target(self.laws, seed=seed)
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
        draw = draw_proposal(self.laws, seed=seed)
        values, log_p, log_q = draw.values, draw.log_p, draw.log_q
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


def is_dynamics_schema_v2(plan: SamplingPlan) -> bool:
    definition = plan.target_measure_definition
    return isinstance(definition, Mapping) and definition.get("dynamics_schema_version") == 2


class DynamicsModelSampler:
    """Interpret a ``dynamics_schema_version: 2`` plan for a registered model.

    The target definition names the model, its parameters, the initial law,
    the solver (step and horizon) and the event; the proposal changes only the
    Brownian path law. Supported in this version: ``initial_law`` of kind
    ``point_mass`` (so the input ratio is exactly 1); the noise proposal is
    ``noise`` (innovation laws of ``targets.dynamics.proposals``) or the legacy
    ``noise_path_q`` (``gaussian_drift`` / ``defensive_gaussian_drift``).
    """

    def __init__(self, plan: SamplingPlan):
        from targets.dynamics.models.registry import get_model_adapter
        from targets.dynamics.profile import noise_path_definition, parse_noise_path_p, parse_noise_path_q
        from targets.dynamics.proposals import parse_noise_proposal
        from targets.dynamics.runners.sde import as_path_proposal

        if plan.target != "dynamics":
            raise ValueError("sampling plan target must be 'dynamics'")
        if plan.sampling_mode != "manual_proposal":
            raise ValueError("dynamics importance sampling currently requires manual_proposal")
        definition = _mapping(plan.target_measure_definition, "dynamics target_measure.definition")
        if definition.get("dynamics_schema_version") != 2:
            raise ValueError("target_measure.definition.dynamics_schema_version must be 2")
        model_id = definition.get("model_id")
        if not isinstance(model_id, str):
            raise ValueError("target_measure.definition.model_id must be a string")
        self.adapter = get_model_adapter(model_id)
        self.plan = plan
        self.model_id = model_id
        self.case_kind = str(definition.get("case_kind", model_id))
        channels = self.adapter.noise_channel_names()

        initial_law = _mapping(definition.get("initial_law"), "target_measure.definition.initial_law")
        if initial_law.get("kind") != "point_mass":
            raise ValueError("initial_law.kind must be 'point_mass' in this version")
        names = self.adapter.initial_input_names()
        values = initial_law.get("value")
        if not isinstance(values, list) or len(values) != len(names):
            raise ValueError(f"initial_law.value must be a list of {len(names)} number(s) for {list(names)}")
        initial_inputs = {
            name: _finite_float(value, f"initial_law.value[{index}]")
            for index, (name, value) in enumerate(zip(names, values))
        }
        solver = _mapping(definition.get("solver"), "target_measure.definition.solver")
        if solver.get("kind") != "sde":
            raise ValueError("solver.kind must be 'sde'")
        self.numerics = {
            "solver_kind": "sde",
            "dt_sec": _finite_float(solver.get("dt_sec"), "solver.dt_sec"),
            "horizon_sec": _finite_float(solver.get("horizon_sec"), "solver.horizon_sec"),
        }
        noise_path_p = definition.get("noise_path_p")
        parse_noise_path_p(noise_path_p, channels=channels)
        self.execution_inputs: dict[str, object] = {
            # Marks v2 inputs, so models with a legacy path (the U-turn) run
            # through the registered-model path.
            "dynamics_schema_version": 2,
            "model_id": model_id,
            "model_params": dict(_mapping(definition.get("model_params"), "model_params")),
            "solver_kind": "sde",
            "sde_dt_sec": self.numerics["dt_sec"],
            "horizon_sec": self.numerics["horizon_sec"],
            "event": dict(_mapping(definition.get("event"), "event")),
            **initial_inputs,
            **({"noise_path_p": noise_path_p} if noise_path_p is not None else {}),
        }

        proposal_definition = _mapping(plan.proposal_definition, "dynamics proposal.definition")
        input_rule = _mapping(proposal_definition.get("input", {"kind": "same_as_target"}), "proposal.definition.input")
        if input_rule.get("kind") != "same_as_target":
            raise ValueError("proposal.definition.input.kind must be 'same_as_target' in this version")
        # Q on the Brownian path: ``noise`` (innovation laws, innovation units)
        # or the legacy ``noise_path_q`` (gaussian_drift, SDE drift units).
        if proposal_definition.get("noise") is not None and proposal_definition.get("noise_path_q") is not None:
            raise ValueError("proposal.definition must give noise or noise_path_q, not both")
        self.proposal_inputs: dict[str, object] = {}
        noise_bound: float | None = 1.0
        internal = None
        if proposal_definition.get("noise") is not None:
            path_proposal = parse_noise_proposal(
                proposal_definition["noise"],
                channels=channels,
                state_names=self.adapter.state_names(),
                context="proposal.definition.noise",
            )
            self.proposal_inputs["noise_q"] = dict(proposal_definition["noise"])
            noise_bound = path_proposal.noise_weight_bound()
            internal = path_proposal
        else:
            noise_q = parse_noise_path_q(proposal_definition.get("noise_path_q"), channels=channels)
            if noise_q is not None:
                noise_q.validate()
                definition_q = noise_path_definition(noise_q)
                self.proposal_inputs["noise_path_q"] = definition_q
                noise_bound = (
                    1.0 / float(definition_q["base_weight"])
                    if definition_q["distribution"] == "defensive_gaussian_drift"
                    else None
                )
                internal = as_path_proposal(noise_q)
        # The proposal over the Brownian path, as seen by the shared code.
        self.internal_proposal = None if internal is None else internal.internal_proposal()
        # The input ratio is exactly 1 (point mass), so the bound is the noise bound.
        self.importance_weight_upper_bound: float | None = noise_bound
        self.target_definition = dict(definition)
        self.proposal_definition = dict(proposal_definition)

    def target_metadata(self) -> dict[str, object]:
        return {
            "plan_id": self.plan.experiment_id,
            "target_measure_id": self.plan.target_measure_id,
            "sampling_mode": "uniform",
            "importance_weight_status": "not_applicable",
        }

    def proposal_metadata(self) -> dict[str, object]:
        # The initial law is a point mass under both P and Q, so the input
        # ratio is exactly 1; the backend adds the Brownian path ratio.
        metadata: dict[str, object] = {
            "plan_id": self.plan.experiment_id,
            "target_measure_id": self.plan.target_measure_id,
            "sampling_mode": "manual_proposal",
            "proposal_id": self.plan.proposal_id,
            "importance_weight_status": "available",
            "log_p": 0.0,
            "log_q": 0.0,
            "log_importance_weight": 0.0,
        }
        if self.importance_weight_upper_bound is not None:
            metadata["importance_weight_upper_bound"] = self.importance_weight_upper_bound
        return metadata


def _legacy_internal_proposal(definition: Mapping[str, object] | None):
    """The v1 Brownian-path proposal (``noise_path_q``) as seen by the shared code."""
    if definition is None:
        return None
    from targets.dynamics.profile import parse_noise_path_q
    from targets.dynamics.runners.sde import as_path_proposal

    return as_path_proposal(parse_noise_path_q(definition)).internal_proposal()


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


__all__ = ["DynamicsInputSample", "DynamicsInputSampler", "DynamicsModelSampler", "is_dynamics_schema_v2"]
