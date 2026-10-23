"""Small hybrid systems shared by the SDE proposal tests."""

from __future__ import annotations

import numpy as np

from targets.dynamics.models.base import Transition


class Linear1D:
    """x' = -a x + s dW on one channel w (no modes)."""

    state_names = ("x",)

    def __init__(self, *, a=0.5, s=0.8, x0=0.3):
        self.a, self.s, self.x0 = a, s, x0

    def initial_mode(self):
        return "run"

    def initial_state(self):
        return np.array([self.x0])

    def drift(self, _t, x, _mode, _entry):
        return np.array([-self.a * x[0]])

    def noise_channels(self, _mode):
        return ("w",)

    def diffusion(self, _t, _x, _mode, _entry):
        return np.array([[self.s]])

    def transitions(self, _mode):
        return ()

    def project(self, state):
        return state

    has_noise = True


class TwoModes:
    """Two states, channels (a, b) in "first" and (b,) in "second" after t >= 0.15."""

    state_names = ("x", "y")

    def initial_mode(self):
        return "first"

    def initial_state(self):
        return np.zeros(2)

    def drift(self, _t, _x, _mode, _entry):
        return np.zeros(2)

    def noise_channels(self, mode):
        return ("a", "b") if mode == "first" else ("b",)

    def diffusion(self, _t, _x, mode, _entry):
        return np.eye(2) if mode == "first" else np.array([[0.0], [1.0]])

    def transitions(self, mode):
        return (Transition("second", lambda t, _x: t - 0.15, 1.0),) if mode == "first" else ()

    def project(self, state):
        return state

    has_noise = True
