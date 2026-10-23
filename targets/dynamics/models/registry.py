"""Model registry for the dynamics target.

A model adapter turns ``TestCase.input`` into a ``HybridSystem`` with solver
settings, and judges the resulting trajectory. The shared backend runs any
registered adapter and writes the generic trajectory format (raw result schema
2), so adding a model does not change the backend.

The U-turn model predates this registry. It is registered for its metadata
(noise channels, default metrics) but still runs through its own profile and
writes raw result schema 1, unchanged; ``legacy_execution`` marks this.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from ..runners.ode import ODESolverSettings, solve_hybrid_ode
from ..runners.sde import (
    DefensiveGaussianNoiseProposal,
    GaussianDriftNoisePath,
    SDESolverSettings,
    solve_hybrid_sde,
)
from .base import HybridSystem, HybridTrajectory


@dataclass(frozen=True)
class ResolvedModelRun:
    """Everything needed to run one case of a registered model."""

    system: HybridSystem
    solver_kind: str  # "ode" or "sde"
    ode_settings: ODESolverSettings
    sde_settings: SDESolverSettings | None
    execution_meta: dict[str, object] = field(default_factory=dict)
    # Optional conversion of the raw trajectory into the columns and events the
    # model's judge reads (e.g. vehicle centres instead of reference points).
    # Default: the system's state names and states, and no extra events.
    trace_transform: Callable[[HybridTrajectory], tuple[tuple[str, ...], np.ndarray, dict[str, object]]] | None = None

    def trace(self, trajectory: HybridTrajectory) -> tuple[tuple[str, ...], np.ndarray, dict[str, object]]:
        if self.trace_transform is not None:
            return self.trace_transform(trajectory)
        return tuple(self.system.state_names), np.asarray(trajectory.states, dtype=float), {}

    @property
    def noise_proposal(self) -> GaussianDriftNoisePath | DefensiveGaussianNoiseProposal | None:
        return None if self.sde_settings is None else self.sde_settings.noise_proposal

    def solve(self) -> HybridTrajectory:
        if self.solver_kind == "sde":
            assert self.sde_settings is not None
            return solve_hybrid_sde(self.system, self.sde_settings)
        return solve_hybrid_ode(self.system, self.ode_settings)


class DynamicsModelAdapter(Protocol):
    model_id: str
    legacy_execution: bool
    default_binary_metric: str
    default_continuous_metric: str | None

    def noise_channel_names(self) -> frozenset[str]: ...

    def initial_input_names(self) -> tuple[str, ...]:
        """Input names of the initial state, in state order (for initial_law)."""
        ...

    def state_names(self) -> tuple[str, ...]:
        """State names of the model's HybridSystem (for state-dependent proposal rules)."""
        ...

    def resolve(self, inputs: Mapping[str, object]) -> ResolvedModelRun: ...

    def interpret(
        self,
        payload: Mapping[str, object],
        trace: Mapping[str, np.ndarray],
    ) -> dict[str, object]:
        """Judge one run. ``payload`` is the schema-2 raw result (input, execution,
        events); ``trace`` maps ``time_sec``, ``mode`` and every column to arrays."""
        ...


UTURN_EVENTS = {"collision": "c_collision", "screening": "c_screening_candidate"}
UTURN_TRACE_COLUMNS = (
    "ego_x_m", "ego_y_m", "ego_heading_rad", "ego_speed_mps",
    "npc_x_m", "npc_y_m", "npc_heading_rad", "npc_speed_mps", "turn_angle_rad",
)


@dataclass(frozen=True)
class UTurnAdapter:
    """The U-turn model.

    v1 inputs (no ``dynamics_schema_version``) run through
    ``targets.dynamics.profile`` and raw result schema 1, unchanged
    (``legacy_execution``). v2 inputs run here like any registered model, so
    the U-turn accepts the same proposals (innovation laws, mixtures) as OU.
    """

    model_id: str = "uturn"
    legacy_execution: bool = True
    default_binary_metric: str = "c_collision"
    default_continuous_metric: str | None = "min_ttc"

    def noise_channel_names(self) -> frozenset[str]:
        from ..profile import UTURN_NOISE_CHANNELS

        return UTURN_NOISE_CHANNELS

    def initial_input_names(self) -> tuple[str, ...]:
        return ("dx0", "ego_speed", "npc_speed")

    def state_names(self) -> tuple[str, ...]:
        from .uturn import UTurnSystem

        return tuple(UTurnSystem.state_names)

    def resolve(self, inputs: Mapping[str, object]) -> ResolvedModelRun:
        """Build the U-turn system from v2 inputs.

        ``model_params`` passes U-turn profile options through (``sde_noise``,
        ``controller_kind``, ``collision_model``, ...); the proposal is
        ``noise_q`` or ``noise_path_q``. The event is ``{kind: collision}``
        (default) or ``{kind: screening}``.
        """
        from ..profile import build_execution_profile
        from .uturn import UTurnSystem

        params = inputs.get("model_params") or {}
        if not isinstance(params, Mapping):
            raise ValueError("model_params must be a mapping")
        reserved = {"model_id", "solver_kind", "sde_seed", "sde_dt_sec", "horizon_sec",
                    "noise_path_p", "noise_path_q", "sde_noise_proposal", *self.initial_input_names()}
        clashes = sorted(reserved & set(params))
        if clashes:
            raise ValueError(f"model_params must not set {clashes}; they come from the plan")
        dt_sec = inputs.get("sde_dt_sec")
        profile = build_execution_profile(
            {
                **params,
                "model_id": "uturn",
                **{name: inputs[name] for name in self.initial_input_names() if name in inputs},
                "solver_kind": "sde",
                "sde_seed": inputs.get("sde_seed"),
                "sde_dt_sec": dt_sec,
                "horizon_sec": inputs.get("horizon_sec"),
                "max_step_sec": params.get("max_step_sec", dt_sec),
                **({"noise_path_p": inputs["noise_path_p"]} if inputs.get("noise_path_p") is not None else {}),
            },
            default_output_root=".",
        )
        sde = profile.sde_config
        assert sde is not None
        system = UTurnSystem(profile.ode_config, noise=sde.noise)
        proposal, definition = resolve_noise_proposal(
            inputs, channels=self.noise_channel_names(), state_names=self.state_names()
        )
        if proposal is not None:
            stds = {
                "ego_brake_acceleration": sde.noise.ego_brake_acceleration_std,
                "npc_acceleration": sde.noise.npc_acceleration_std,
                "npc_heading": sde.noise.npc_heading_std_rad_per_sqrt_sec,
            }
            referenced = (
                proposal.referenced_channels()
                if hasattr(proposal, "referenced_channels")
                else frozenset(proposal.channel_drifts)
            )
            idle = sorted(channel for channel in referenced if float(stds[channel]) == 0.0)
            if idle:
                raise ValueError(f"the proposal changes channels without diffusion: {idle}")
        event = inputs.get("event") or {"kind": "collision"}
        if not isinstance(event, Mapping) or event.get("kind") not in UTURN_EVENTS:
            raise ValueError(f"event.kind must be one of {sorted(UTURN_EVENTS)} for the U-turn")
        mixture_seed = inputs.get("sde_mixture_seed")
        ode_settings = ODESolverSettings(
            horizon_sec=profile.ode_config.horizon_sec, max_step_sec=profile.ode_config.max_step_sec
        )

        def trace_transform(trajectory: HybridTrajectory):
            result = system.to_result(trajectory)
            return UTURN_TRACE_COLUMNS, np.asarray(result.states, dtype=float), {
                "npc_start_time_sec": result.npc_start_time_sec,
                "uturn_start_time_sec": result.uturn_start_time_sec,
                "uturn_end_time_sec": result.uturn_end_time_sec,
            }

        return ResolvedModelRun(
            system=system,
            solver_kind="sde",
            ode_settings=ode_settings,
            sde_settings=SDESolverSettings(
                horizon_sec=profile.ode_config.horizon_sec,
                dt_sec=sde.dt_sec,
                seed=sde.seed,
                ode_fallback=ode_settings,
                noise_target=sde.noise_target,
                noise_proposal=proposal,
                mixture_seed=None if mixture_seed is None else int(mixture_seed),
                initial_inputs={name: inputs[name] for name in self.initial_input_names() if name in inputs},
            ),
            execution_meta={
                **profile.to_meta(),
                "model_id": "uturn",
                "event": {"kind": event["kind"]},
                "sde_mixture_seed": mixture_seed,
                "noise_q": definition,
            },
            trace_transform=trace_transform,
        )

    def interpret(self, payload: Mapping[str, object], trace: Mapping[str, np.ndarray]) -> dict[str, object]:
        from ..result_interpreter import uturn_metrics

        execution = payload["execution"]
        metrics = uturn_metrics(
            trace, execution=execution, inputs=payload["input"], events=payload.get("events") or {}
        )
        metrics["c_event"] = metrics[UTURN_EVENTS[execution["event"]["kind"]]]
        return metrics


def resolve_noise_proposal(
    inputs: Mapping[str, object],
    *,
    channels: frozenset[str],
    state_names: tuple[str, ...],
):
    """Parse the proposal Q of a case: ``noise_q`` (new laws) or ``noise_path_q`` (legacy).

    Returns ``(proposal, definition)``; both are None when Q = P.
    """
    from ..profile import noise_path_definition, parse_noise_path_q
    from ..proposals import parse_noise_proposal

    if inputs.get("noise_q") is not None and inputs.get("noise_path_q") is not None:
        raise ValueError("give the proposal as noise_q or noise_path_q, not both")
    if inputs.get("noise_q") is not None:
        proposal = parse_noise_proposal(
            inputs["noise_q"], channels=channels, state_names=state_names, context="noise_q"
        )
        return proposal, proposal.describe()
    legacy = parse_noise_path_q(inputs.get("noise_path_q"), channels=channels)
    if legacy is None:
        return None, None
    legacy.validate()
    return legacy, noise_path_definition(legacy)


def _adapters() -> dict[str, DynamicsModelAdapter]:
    from .nonlinear2d import Nonlinear2DAdapter
    from .ou import OUScalarAdapter
    from .ou_augmented import OUAugmentedAdapter

    adapters: tuple[DynamicsModelAdapter, ...] = (
        UTurnAdapter(),
        OUScalarAdapter(),
        OUAugmentedAdapter(),
        Nonlinear2DAdapter(),
    )
    return {adapter.model_id: adapter for adapter in adapters}


def model_ids() -> tuple[str, ...]:
    return tuple(sorted(_adapters()))


def get_model_adapter(model_id: str) -> DynamicsModelAdapter:
    adapters = _adapters()
    if model_id not in adapters:
        raise ValueError(f"unsupported dynamics model_id {model_id!r}; known: {sorted(adapters)}")
    return adapters[model_id]


__all__ = [
    "DynamicsModelAdapter",
    "ResolvedModelRun",
    "UTurnAdapter",
    "get_model_adapter",
    "model_ids",
    "resolve_noise_proposal",
]
