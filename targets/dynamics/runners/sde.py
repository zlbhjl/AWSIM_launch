"""Fixed-step Euler--Maruyama integration of a hybrid stochastic system.

    x_{k+1} = P( x_k + f_q(t_k, x_k) dt + G_q(t_k, x_k) sqrt(dt) xi_k ),

with xi_k ~ N(0, I_{m_q}) drawn from one seeded generator and ``P`` the
model's projection onto admissible states.  Guards are checked on the grid.
A system without noise is delegated to the ODE runner, so the zero-diffusion
limit is the deterministic solution exactly rather than an Euler
approximation of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import log, sqrt
from typing import Mapping

import numpy as np

from ..models.base import HybridSystem, HybridTrajectory
from .ode import ODESolverSettings, solve_hybrid_ode


@dataclass(frozen=True)
class SDESolverSettings:
    horizon_sec: float
    dt_sec: float
    seed: int
    ode_fallback: ODESolverSettings
    noise_target: "GaussianDriftNoisePath" = field(default_factory=lambda: GaussianDriftNoisePath())
    noise_proposal: "GaussianDriftNoisePath | DefensiveGaussianNoiseProposal | None" = None


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
    proposal = settings.noise_proposal
    target.validate()
    if proposal is not None:
        proposal.validate()
    rng = np.random.default_rng(int(settings.seed))
    # Do not consume a random draw for Q=P; this preserves the same-seed
    # regression invariant against the nominal SDE runner.
    use_tilt = (
        proposal is not None
        and isinstance(proposal, DefensiveGaussianNoiseProposal)
        and proposal.base_weight < 1.0
        and rng.random() >= proposal.base_weight
    )
    log_p = 0.0
    log_r = 0.0
    noise_sample_count = 0
    time_sec = 0.0
    mode = system.initial_mode()
    state = np.asarray(system.initial_state(), dtype=float).copy()
    entry_times: dict[str, float] = {mode: 0.0}
    mode = _apply_transitions(system, mode, state, time_sec, entry_times)
    times = [time_sec]
    states = [state.copy()]
    modes = [mode]
    while time_sec < settings.horizon_sec - 1e-12:
        dt = min(float(settings.dt_sec), settings.horizon_sec - time_sec)
        drift = system.drift(time_sec, state, mode, entry_times)
        channels = system.noise_channels(mode)
        increment = drift * dt
        if channels:
            diffusion = system.diffusion(time_sec, state, mode, entry_times)
            p_mean = target.innovation_mean(
                time_sec=time_sec, mode=mode, channels=channels, dt_sec=dt
            )
            q_mean = (
                proposal.innovation_mean(time_sec=time_sec, mode=mode, channels=channels, dt_sec=dt)
                if proposal is not None else p_mean
            )
            innovation = rng.normal(size=len(channels)) + (q_mean if use_tilt or isinstance(proposal, GaussianDriftNoisePath) else p_mean)
            log_p += _normal_log_density(innovation, p_mean)
            log_r += _normal_log_density(innovation, q_mean)
            noise_sample_count += len(channels)
            increment = increment + diffusion @ (sqrt(dt) * innovation)
        state = np.asarray(system.project(state + increment), dtype=float)
        time_sec += dt
        mode = _apply_transitions(system, mode, state, time_sec, entry_times)
        times.append(time_sec)
        states.append(state.copy())
        modes.append(mode)
    if proposal is None:
        log_q = log_p
    elif isinstance(proposal, DefensiveGaussianNoiseProposal):
        # q(path)=b p(path)+(1-b) r(path), evaluated stably in log space.
        log_q = _logaddexp(
            log(proposal.base_weight) + log_p,
            log(1.0 - proposal.base_weight) + log_r
        ) if proposal.base_weight < 1.0 else log_p
    else:
        log_q = log_r
    return HybridTrajectory(
        times_sec=np.asarray(times, dtype=float),
        states=np.asarray(states, dtype=float),
        modes=tuple(modes),
        mode_entry_times=entry_times,
        noise_log_p=log_p,
        noise_log_q=log_q,
        noise_sample_count=noise_sample_count,
    )


def _logaddexp(left: float, right: float) -> float:
    maximum = max(left, right)
    return maximum + log(np.exp(left - maximum) + np.exp(right - maximum))


def _normal_log_density(value: np.ndarray, mean: np.ndarray) -> float:
    return float(-0.5 * np.sum((value - mean) ** 2 + log(2.0 * np.pi)))


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
