"""Fixed-step Euler--Maruyama integration of a hybrid stochastic system.

    x_{k+1} = P( x_k + f_q(t_k, x_k) dt + G_q(t_k, x_k) sqrt(dt) xi_k ),

with xi_k ~ N(0, I_{m_q}) drawn from one seeded generator and ``P`` the
model's projection onto admissible states.  Guards are checked on the grid.
A system without noise is delegated to the ODE runner, so the zero-diffusion
limit is the deterministic solution exactly rather than an Euler
approximation of it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..models.base import HybridSystem, HybridTrajectory
from .ode import ODESolverSettings, solve_hybrid_ode


@dataclass(frozen=True)
class SDESolverSettings:
    horizon_sec: float
    dt_sec: float
    seed: int
    ode_fallback: ODESolverSettings


def solve_hybrid_sde(system: HybridSystem, settings: SDESolverSettings) -> HybridTrajectory:
    if not system.has_noise:
        return solve_hybrid_ode(system, settings.ode_fallback)

    rng = np.random.default_rng(int(settings.seed))
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
            increment = increment + diffusion @ (np.sqrt(dt) * rng.normal(size=len(channels)))
        state = np.asarray(system.project(state + increment), dtype=float)
        time_sec += dt
        mode = _apply_transitions(system, mode, state, time_sec, entry_times)
        times.append(time_sec)
        states.append(state.copy())
        modes.append(mode)
    return HybridTrajectory(
        times_sec=np.asarray(times, dtype=float),
        states=np.asarray(states, dtype=float),
        modes=tuple(modes),
        mode_entry_times=entry_times,
    )


def _apply_transitions(system, mode, state, time_sec, entry_times):
    """Fire at most one guard per grid point; ``state`` is reset in place."""
    for transition in system.transitions(mode):
        if transition.is_satisfied(time_sec, state):
            if transition.reset is not None:
                state[:] = transition.reset(state)
            entry_times[transition.target_mode] = time_sec
            return transition.target_mode
    return mode


__all__ = ["SDESolverSettings", "solve_hybrid_sde"]
