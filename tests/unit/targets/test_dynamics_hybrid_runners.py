"""The generic runners integrate any HybridSystem, not only the U-turn model."""

import math

import numpy as np
import pytest

from targets.dynamics.models.base import Transition
from targets.dynamics.runners import (
    ODESolverSettings,
    SDESolverSettings,
    solve_hybrid_ode,
    solve_hybrid_sde,
)


class DecayThenHold:
    """x' = -k x in mode "decay"; switch to "hold" (x' = 0) when x reaches x_switch.

    Analytic switch time: t* = ln(x0 / x_switch) / k.
    Optional diffusion sigma dW in "decay" makes it an Ornstein-Uhlenbeck-like SDE.
    """

    state_names = ("x",)

    def __init__(self, *, x0=1.0, k=2.0, x_switch=0.25, sigma=0.0, reset_to=None):
        self.x0, self.k, self.x_switch, self.sigma = x0, k, x_switch, sigma
        reset = None if reset_to is None else (lambda state: np.array([reset_to]))
        self._transitions = {
            "decay": (Transition("hold", lambda _t, x: x[0] - x_switch, -1.0, reset),),
            "hold": (),
        }

    def initial_mode(self):
        return "decay"

    def initial_state(self):
        return np.array([self.x0])

    def drift(self, _t, x, mode, _entry):
        return np.array([-self.k * x[0]]) if mode == "decay" else np.zeros(1)

    def noise_channels(self, mode):
        return ("w",) if self.has_noise and mode == "decay" else ()

    def diffusion(self, _t, _x, mode, _entry):
        return np.array([[self.sigma]]) if mode == "decay" else np.zeros((1, 0))

    def transitions(self, mode):
        return self._transitions[mode]

    def project(self, state):
        return state

    @property
    def has_noise(self):
        return self.sigma != 0.0


def _ode(horizon=2.0):
    return ODESolverSettings(horizon_sec=horizon, max_step_sec=0.01)


def test_ode_runner_matches_analytic_solution_and_switch_time() -> None:
    system = DecayThenHold()

    trajectory = solve_hybrid_ode(system, _ode())

    switch = math.log(1.0 / 0.25) / 2.0
    assert trajectory.mode_entry_times["hold"] == pytest.approx(switch, abs=1e-9)
    before = trajectory.times_sec < switch - 1e-6
    assert trajectory.states[before, 0] == pytest.approx(
        np.exp(-2.0 * trajectory.times_sec[before]), rel=1e-6
    )
    assert trajectory.states[-1, 0] == pytest.approx(0.25, abs=1e-9)
    assert trajectory.modes[0] == "decay" and trajectory.modes[-1] == "hold"


def test_ode_runner_applies_reset_map() -> None:
    trajectory = solve_hybrid_ode(DecayThenHold(reset_to=5.0), _ode())

    assert trajectory.states[-1, 0] == pytest.approx(5.0)


def test_ode_runner_fires_guard_already_satisfied_at_entry() -> None:
    # A root exactly at t=0 cannot be bracketed by solve_ivp; it must still fire.
    trajectory = solve_hybrid_ode(DecayThenHold(x0=0.25), _ode())

    assert trajectory.mode_entry_times["hold"] == 0.0
    assert trajectory.states[-1, 0] == pytest.approx(0.25)


def test_sde_runner_is_euler_maruyama_with_seeded_noise() -> None:
    system = DecayThenHold(x_switch=-10.0, sigma=0.3)  # never switches
    dt, steps, seed = 0.01, 50, 11

    trajectory = solve_hybrid_sde(
        system,
        SDESolverSettings(horizon_sec=dt * steps, dt_sec=dt, seed=seed, ode_fallback=_ode()),
    )

    rng = np.random.default_rng(seed)
    x = 1.0
    for _ in range(steps):
        x = x - 2.0 * x * dt + 0.3 * math.sqrt(dt) * rng.normal(size=1)[0]
    assert trajectory.states[-1, 0] == pytest.approx(x, abs=1e-12)


def test_sde_runner_without_noise_is_the_ode_solution() -> None:
    settings = SDESolverSettings(horizon_sec=2.0, dt_sec=0.01, seed=1, ode_fallback=_ode())

    stochastic = solve_hybrid_sde(DecayThenHold(), settings)
    deterministic = solve_hybrid_ode(DecayThenHold(), _ode())

    assert np.array_equal(stochastic.times_sec, deterministic.times_sec)
    assert np.array_equal(stochastic.states, deterministic.states)
