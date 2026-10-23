"""Nonlinear test model with state-dependent diffusion (model C, plan §12).

    f(x) = [-x1 + k tanh(x2), -x2 + k tanh(x1)]
    a_i  = 1 + m tanh(x_i)
    G(x) = s [[a1, 0], [rho a2, sqrt(1 - rho^2) a2]]

with k = 0.5, m = 0.25, s = 0.5, rho = 0.5 by default. The drift is globally
Lipschitz and the diffusion bounded and smooth. Two channels w1, w2; the noise
of the two coordinates is correlated through rho and modulated by the state.
There is no closed form; the reference value comes from an independent
vectorised Monte Carlo (:mod:`targets.dynamics.models.reference`).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from .registry import ResolvedModelRun
from .sde_common import finite, interpret_sde_model, mapping, resolve_sde_model

NONLINEAR2D_NOISE_CHANNELS = frozenset({"w1", "w2"})
DEFAULT_PARAMS = {"drift_coupling": 0.5, "modulation": 0.25, "diffusion_scale": 0.5, "correlation": 0.5}


class Nonlinear2DSystem:
    state_names = ("x1", "x2")

    def __init__(self, *, drift_coupling, modulation, diffusion_scale, correlation, x1_0=0.0, x2_0=0.0):
        self.k, self.m, self.s, self.rho = drift_coupling, modulation, diffusion_scale, correlation
        self.x0 = np.array([x1_0, x2_0])

    def initial_mode(self) -> str:
        return "run"

    def initial_state(self) -> np.ndarray:
        return self.x0.copy()

    def drift(self, _t, x, _mode, _entry) -> np.ndarray:
        return np.array([-x[0] + self.k * math.tanh(x[1]), -x[1] + self.k * math.tanh(x[0])])

    def noise_channels(self, _mode) -> tuple[str, ...]:
        return ("w1", "w2") if self.has_noise else ()

    def diffusion(self, _t, x, _mode, _entry) -> np.ndarray:
        a1 = 1.0 + self.m * math.tanh(x[0])
        a2 = 1.0 + self.m * math.tanh(x[1])
        return self.s * np.array([[a1, 0.0], [self.rho * a2, math.sqrt(1.0 - self.rho ** 2) * a2]])

    def transitions(self, _mode):
        return ()

    def project(self, state: np.ndarray) -> np.ndarray:
        return state

    @property
    def has_noise(self) -> bool:
        return self.s != 0.0


@dataclass(frozen=True)
class Nonlinear2DAdapter:
    model_id: str = "nonlinear2d"
    legacy_execution: bool = False
    default_binary_metric: str = "c_event"
    default_continuous_metric: str | None = "event_value"

    def noise_channel_names(self) -> frozenset[str]:
        return NONLINEAR2D_NOISE_CHANNELS

    def initial_input_names(self) -> tuple[str, ...]:
        return ("x1_0", "x2_0")

    def state_names(self) -> tuple[str, ...]:
        return Nonlinear2DSystem.state_names

    def resolve(self, inputs: Mapping[str, object]) -> ResolvedModelRun:
        params = mapping(inputs.get("model_params"), "model_params")
        unknown = sorted(set(params) - set(DEFAULT_PARAMS))
        if unknown:
            raise ValueError(f"model_params has unknown keys {unknown}")
        values = {name: finite(params.get(name, default), f"model_params.{name}") for name, default in DEFAULT_PARAMS.items()}
        if not -1.0 < values["correlation"] < 1.0:
            raise ValueError("model_params.correlation must be in (-1, 1)")
        if not 0.0 <= values["modulation"] < 1.0:
            raise ValueError("model_params.modulation must be in [0, 1) so the diffusion stays positive")
        initial = {name: finite(inputs.get(name, 0.0), name) for name in self.initial_input_names()}
        return resolve_sde_model(
            model_id=self.model_id,
            system=Nonlinear2DSystem(**values, **initial),
            inputs=inputs,
            channels=NONLINEAR2D_NOISE_CHANNELS,
            model_meta={"model_params": values, **initial, "initial_input_names": self.initial_input_names()},
        )

    def interpret(self, payload: Mapping[str, object], trace: Mapping[str, np.ndarray]) -> dict[str, object]:
        return interpret_sde_model(payload, trace, state_names=Nonlinear2DSystem.state_names)


__all__ = ["DEFAULT_PARAMS", "Nonlinear2DAdapter", "Nonlinear2DSystem", "NONLINEAR2D_NOISE_CHANNELS"]
