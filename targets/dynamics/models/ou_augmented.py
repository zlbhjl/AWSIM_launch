"""Augmented-state OU model (model B of docs/sde_importance_sampling_plan.md §12).

    dC = -theta_c C dt + sigma dW
    dX = (-theta_x X + coupling C) dt

Two states and one Brownian channel: X is driven by correlated (OU) noise
represented by the extra state C, and the diffusion G = [[sigma], [0]] is
rank-deficient. The Euler--Maruyama law of (C_K, X_K) is Gaussian and exactly
computable (:func:`targets.dynamics.models.exact.ou_augmented_terminal_moments`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from .registry import ResolvedModelRun
from .sde_common import finite, interpret_sde_model, mapping, resolve_sde_model

OU_AUGMENTED_NOISE_CHANNELS = frozenset({"w"})


class OUAugmentedSystem:
    state_names = ("c", "x")

    def __init__(self, *, theta_c: float, theta_x: float, coupling: float, sigma: float, c0: float, x0: float):
        self.theta_c, self.theta_x, self.coupling, self.sigma = theta_c, theta_x, coupling, sigma
        self.c0, self.x0 = c0, x0

    def initial_mode(self) -> str:
        return "run"

    def initial_state(self) -> np.ndarray:
        return np.array([self.c0, self.x0])

    def drift(self, _t, state, _mode, _entry) -> np.ndarray:
        c, x = state
        return np.array([-self.theta_c * c, -self.theta_x * x + self.coupling * c])

    def noise_channels(self, _mode) -> tuple[str, ...]:
        return ("w",) if self.has_noise else ()

    def diffusion(self, _t, _state, _mode, _entry) -> np.ndarray:
        return np.array([[self.sigma], [0.0]])

    def transitions(self, _mode):
        return ()

    def project(self, state: np.ndarray) -> np.ndarray:
        return state

    @property
    def has_noise(self) -> bool:
        return self.sigma != 0.0


@dataclass(frozen=True)
class OUAugmentedAdapter:
    model_id: str = "ou_augmented"
    legacy_execution: bool = False
    default_binary_metric: str = "c_event"
    default_continuous_metric: str | None = "x_terminal"

    def noise_channel_names(self) -> frozenset[str]:
        return OU_AUGMENTED_NOISE_CHANNELS

    def initial_input_names(self) -> tuple[str, ...]:
        return ("c0", "x0")

    def state_names(self) -> tuple[str, ...]:
        return OUAugmentedSystem.state_names

    def resolve(self, inputs: Mapping[str, object]) -> ResolvedModelRun:
        params = mapping(inputs.get("model_params"), "model_params")
        values = {
            "theta_c": finite(params.get("theta_c", 2.0), "model_params.theta_c"),
            "theta_x": finite(params.get("theta_x", 1.0), "model_params.theta_x"),
            "coupling": finite(params.get("coupling", 1.0), "model_params.coupling"),
            "sigma": finite(params.get("sigma", 1.0), "model_params.sigma"),
        }
        if values["sigma"] < 0.0:
            raise ValueError("model_params.sigma must be non-negative")
        c0 = finite(inputs.get("c0", 0.0), "c0")
        x0 = finite(inputs.get("x0", 0.0), "x0")
        return resolve_sde_model(
            model_id=self.model_id,
            system=OUAugmentedSystem(**values, c0=c0, x0=x0),
            inputs=inputs,
            channels=OU_AUGMENTED_NOISE_CHANNELS,
            model_meta={"model_params": values, "c0": c0, "x0": x0,
                        "initial_input_names": self.initial_input_names()},
        )

    def interpret(self, payload: Mapping[str, object], trace: Mapping[str, np.ndarray]) -> dict[str, object]:
        return interpret_sde_model(payload, trace, state_names=OUAugmentedSystem.state_names)


__all__ = ["OUAugmentedAdapter", "OUAugmentedSystem", "OU_AUGMENTED_NOISE_CHANNELS"]
