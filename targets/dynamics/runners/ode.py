"""Event-segmented deterministic integration of a hybrid system.

Each mode is integrated separately with ``solve_ivp``; the mode's guards are
terminal events, so a control switch is located by root finding instead of
being hidden inside one discontinuous right-hand side.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.integrate import solve_ivp

from ..models.base import HybridSystem, HybridTrajectory

_MAX_MODE_SWITCHES = 1000


@dataclass(frozen=True)
class ODESolverSettings:
    horizon_sec: float
    max_step_sec: float
    rtol: float = 1e-7
    atol: float = 1e-9


def solve_hybrid_ode(system: HybridSystem, settings: ODESolverSettings) -> HybridTrajectory:
    mode = system.initial_mode()
    state = np.asarray(system.initial_state(), dtype=float)
    start_sec = 0.0
    entry_times: dict[str, float] = {mode: 0.0}
    times = np.array([start_sec])
    states = state.reshape(1, -1)
    modes: list[str] = [mode]

    for _ in range(_MAX_MODE_SWITCHES):
        transitions = list(system.transitions(mode))
        immediate = next(
            (item for item in transitions if item.is_satisfied(start_sec, state)), None
        )
        if immediate is not None:
            # A guard already satisfied at mode entry has no sign change for
            # the root finder to bracket; fire it at the entry time.
            fired = (immediate, start_sec, state)
        else:
            times_segment, states_segment, fired = _integrate_mode(
                system, mode, state, start_sec, settings, transitions, entry_times
            )
            times = np.concatenate((times, times_segment[1:]))
            states = np.concatenate((states, states_segment[1:]))
            modes.extend([mode] * (len(times_segment) - 1))
        if fired is None:
            break
        transition, event_time, event_state = fired
        state = np.array(event_state, dtype=float, copy=True)
        if transition.reset is not None:
            state = np.asarray(transition.reset(state), dtype=float)
        mode = transition.target_mode
        start_sec = event_time
        entry_times[mode] = event_time
    else:
        raise RuntimeError("hybrid ODE exceeded the maximum number of mode switches")

    return HybridTrajectory(
        times_sec=times,
        states=states,
        modes=tuple(modes),
        mode_entry_times=entry_times,
    )


def _integrate_mode(system, mode, state, start_sec, settings, transitions, entry_times):
    if settings.horizon_sec <= start_sec:
        return np.array([start_sec]), state.reshape(1, -1), None
    events = [_event(transition) for transition in transitions] or None
    solution = solve_ivp(
        lambda time_sec, value: system.drift(time_sec, value, mode, entry_times),
        (start_sec, settings.horizon_sec),
        state,
        events=events,
        max_step=settings.max_step_sec,
        rtol=settings.rtol,
        atol=settings.atol,
    )
    times = np.asarray(solution.t, dtype=float)
    states = np.asarray(solution.y.T, dtype=float)
    fired = None
    if events:
        candidates = [
            (float(t_events[0]), index)
            for index, t_events in enumerate(solution.t_events)
            if t_events.size
        ]
        if candidates:
            event_time, index = min(candidates)
            fired = (transitions[index], event_time, solution.y_events[index][0])
    return times, states, fired


def _event(transition):
    def event(time_sec: float, state: np.ndarray) -> float:
        return transition.guard(time_sec, state)

    event.terminal = True
    event.direction = transition.direction
    return event


__all__ = ["ODESolverSettings", "solve_hybrid_ode"]
