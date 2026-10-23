"""Fixed-step Euler--Maruyama integration of a hybrid stochastic system.

    x_{k+1} = P( x_k + f_q(t_k, x_k) dt + G_q(t_k, x_k) sqrt(dt) xi_k ),

with the innovation xi_k drawn from one seeded generator and ``P`` the model's
projection onto admissible states.  Guards are checked on the grid.  A system
without noise is delegated to the ODE runner, so the zero-diffusion limit is
the deterministic solution exactly rather than an Euler approximation of it.

Under the target law the innovation is N(u^P sqrt(dt), I) (``noise_target``).
A proposal Q (``noise_proposal``) is a path-level mixture of conditional
innovation laws (``targets.dynamics.proposals``); the realised innovations
are evaluated under P and every component on the same step context, and the
path log densities are returned as ``noise_log_p`` / ``noise_log_q``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt
from typing import Mapping

import numpy as np

from ..models.base import HybridSystem, HybridTrajectory
from ..proposals.base import ProposalContext
from ..proposals.gaussian import DriftPathLaw
from ..proposals.path import TARGET, PathProposal
from .ode import ODESolverSettings, solve_hybrid_ode


@dataclass(frozen=True)
class SDESolverSettings:
    horizon_sec: float
    dt_sec: float
    seed: int
    ode_fallback: ODESolverSettings
    noise_target: "GaussianDriftNoisePath" = field(default_factory=lambda: GaussianDriftNoisePath())
    noise_proposal: "GaussianDriftNoisePath | DefensiveGaussianNoiseProposal | PathProposal | None" = None
    # Seed for the one per-path draw that picks a mixture component. None keeps
    # the legacy behaviour: the draw comes first from the innovation stream.
    mixture_seed: int | None = None
    # Initial inputs of the case, passed to proposal rules through the context.
    initial_inputs: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class GaussianDriftScheduleEntry:
    """One predictable, human-configured Girsanov control segment."""

    start_sec: float = 0.0
    end_sec: float = float("inf")
    mode: str | None = None
    channel_drifts: Mapping[str, float] = field(default_factory=dict)

    def matches(self, *, time_sec: float, mode: str) -> bool:
        return self.start_sec <= time_sec < self.end_sec and (self.mode is None or self.mode == mode)


@dataclass(frozen=True)
class GaussianDriftNoisePath:
    """Gaussian innovations with a predictable, time/mode-dependent mean.

    The configured controls are in SDE drift units.  Their innovation mean is
    ``u(t, mode) sqrt(dt)`` so the continuous-time interpretation retains the
    same diffusion matrix G under P and Q.
    """

    channel_drifts: Mapping[str, float] = field(default_factory=dict)
    drift_schedule: tuple[GaussianDriftScheduleEntry, ...] = ()

    def validate(self) -> None:
        for channel, drift in self.channel_drifts.items():
            if not isinstance(channel, str) or not channel or not np.isfinite(drift):
                raise ValueError("sde noise-path channel drifts must be finite and named")
        for entry in self.drift_schedule:
            if not np.isfinite(entry.start_sec) or not np.isfinite(entry.end_sec) and entry.end_sec != float("inf"):
                raise ValueError("sde noise-path schedule times must be finite or an open end")
            for channel, drift in entry.channel_drifts.items():
                if not isinstance(channel, str) or not channel or not np.isfinite(drift):
                    raise ValueError("sde noise-path schedule drifts must be finite and named")

    def innovation_mean(self, *, time_sec: float, mode: str, channels: tuple[str, ...], dt_sec: float) -> np.ndarray:
        controls = {str(channel): float(drift) for channel, drift in self.channel_drifts.items()}
        for entry in self.drift_schedule:
            if entry.matches(time_sec=time_sec, mode=mode):
                controls.update({str(channel): float(drift) for channel, drift in entry.channel_drifts.items()})
        return np.asarray([controls.get(channel, 0.0) * sqrt(dt_sec) for channel in channels], dtype=float)


@dataclass(frozen=True)
class DefensiveGaussianNoiseProposal:
    """A bounded defensive proposal for Euler--Maruyama Brownian innovations.

    At the start of a path, draw from ``b P + (1-b) R``.  ``P`` uses standard
    normal innovations and ``R`` gives channel ``c`` innovation mean
    ``u_c sqrt(dt)``.  The latter is the Euler form of a Girsanov drift
    control, rather than a step-size-dependent raw normal mean shift.
    """

    base_weight: float
    channel_drifts: Mapping[str, float] = field(default_factory=dict)
    drift_schedule: tuple[GaussianDriftScheduleEntry, ...] = ()

    def validate(self) -> None:
        if not np.isfinite(self.base_weight) or not 0.0 < self.base_weight <= 1.0:
            raise ValueError("sde noise proposal base_weight must be in (0, 1]")
        for channel, drift in self.channel_drifts.items():
            if not isinstance(channel, str) or not channel:
                raise ValueError("sde noise proposal channel names must be non-empty strings")
            if not np.isfinite(drift):
                raise ValueError("sde noise proposal channel drifts must be finite")
        GaussianDriftNoisePath(
            channel_drifts=self.channel_drifts,
            drift_schedule=self.drift_schedule,
        ).validate()

    def innovation_mean(self, *, time_sec: float, mode: str, channels: tuple[str, ...], dt_sec: float) -> np.ndarray:
        return GaussianDriftNoisePath(
            channel_drifts=self.channel_drifts,
            drift_schedule=self.drift_schedule,
        ).innovation_mean(time_sec=time_sec, mode=mode, channels=channels, dt_sec=dt_sec)


def solve_hybrid_sde(system: HybridSystem, settings: SDESolverSettings) -> HybridTrajectory:
    if not system.has_noise:
        return solve_hybrid_ode(system, settings.ode_fallback)

    target = settings.noise_target
    target.validate()
    if isinstance(settings.noise_proposal, (GaussianDriftNoisePath, DefensiveGaussianNoiseProposal)):
        settings.noise_proposal.validate()
    target_law = DriftPathLaw(target)
    proposal = as_path_proposal(settings.noise_proposal)
    rng = np.random.default_rng(int(settings.seed))
    choice_rng = rng if settings.mixture_seed is None else np.random.default_rng(int(settings.mixture_seed))
    # One draw per path picks the mixture component. No draw is consumed for
    # Q = P or a single direct component; this preserves the same-seed
    # regression invariant against the nominal SDE runner.
    if proposal is None:
        chosen = TARGET
    else:
        chosen = proposal.choose(choice_rng.random() if proposal.needs_choice else None)
    sampling_law = target_law if chosen is TARGET else chosen
    components = () if proposal is None else proposal.components

    log_p = 0.0
    log_r = [0.0] * len(components)
    noise_sample_count = 0
    time_sec = 0.0
    mode = system.initial_mode()
    state = np.asarray(system.initial_state(), dtype=float).copy()
    entry_times: dict[str, float] = {mode: 0.0}
    mode = _apply_transitions(system, mode, state, time_sec, entry_times)
    times = [time_sec]
    states = [state.copy()]
    modes = [mode]
    state_names = tuple(system.state_names)
    while time_sec < settings.horizon_sec - 1e-12:
        dt = min(float(settings.dt_sec), settings.horizon_sec - time_sec)
        drift = system.drift(time_sec, state, mode, entry_times)
        channels = system.noise_channels(mode)
        increment = drift * dt
        if channels:
            diffusion = system.diffusion(time_sec, state, mode, entry_times)
            context = ProposalContext(
                time_sec=time_sec,
                dt_sec=dt,
                state=state.copy(),
                mode=mode,
                mode_entry_time_sec=entry_times.get(mode, 0.0),
                channels=tuple(channels),
                state_names=state_names,
                initial_inputs=settings.initial_inputs,
            )
            innovation = np.asarray(sampling_law.sample(context, rng), dtype=float)
            step_log_p = target_law.log_density(innovation, context)
            log_p += step_log_p
            for index, component in enumerate(components):
                log_r[index] += (
                    step_log_p if component is TARGET else component.log_density(innovation, context)
                )
            noise_sample_count += len(channels)
            increment = increment + diffusion @ (sqrt(dt) * innovation)
        state = np.asarray(system.project(state + increment), dtype=float)
        time_sec += dt
        mode = _apply_transitions(system, mode, state, time_sec, entry_times)
        times.append(time_sec)
        states.append(state.copy())
        modes.append(mode)
    log_q = log_p if proposal is None else proposal.log_q(log_p, log_r)
    return HybridTrajectory(
        times_sec=np.asarray(times, dtype=float),
        states=np.asarray(states, dtype=float),
        modes=tuple(modes),
        mode_entry_times=entry_times,
        noise_log_p=log_p,
        noise_log_q=log_q,
        noise_sample_count=noise_sample_count,
    )


def as_path_proposal(proposal: object) -> PathProposal | None:
    """Express the legacy drift proposals as path proposals (same draws, same densities)."""
    if proposal is None or isinstance(proposal, PathProposal):
        return proposal
    if isinstance(proposal, DefensiveGaussianNoiseProposal):
        path = GaussianDriftNoisePath(channel_drifts=proposal.channel_drifts, drift_schedule=proposal.drift_schedule)
        return PathProposal((DriftPathLaw(path),), (1.0,), defensive_weight=proposal.base_weight)
    if isinstance(proposal, GaussianDriftNoisePath):
        return PathProposal((DriftPathLaw(proposal),), (1.0,))
    raise TypeError(f"unsupported SDE noise proposal {type(proposal).__name__}")


def _apply_transitions(system, mode, state, time_sec, entry_times):
    """Fire at most one guard per grid point; ``state`` is reset in place."""
    for transition in system.transitions(mode):
        if transition.is_satisfied(time_sec, state):
            if transition.reset is not None:
                state[:] = transition.reset(state)
            entry_times[transition.target_mode] = time_sec
            return transition.target_mode
    return mode


__all__ = [
    "DefensiveGaussianNoiseProposal",
    "GaussianDriftNoisePath",
    "GaussianDriftScheduleEntry",
    "SDESolverSettings",
    "solve_hybrid_sde",
]
