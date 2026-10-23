"""Scalar Ornstein-Uhlenbeck model: dX = -theta X dt + sigma dW.

This is the first non-vehicle model of the dynamics target (model A in
docs/sde_importance_sampling_plan.md §12). Its Euler--Maruyama discretisation
has an exactly known terminal law (see :mod:`targets.dynamics.models.exact`),
so importance-sampling estimates can be checked against the truth.

Inputs read from ``TestCase.input``:

- ``model_params``: ``{theta, sigma}`` (defaults 1.0, 1.0)
- ``x0``: initial state (default 0.0)
- ``solver_kind``: ``"sde"``; ``sde_seed``; optional ``sde_mixture_seed``
- ``sde_dt_sec`` and ``horizon_sec``
- ``event``: see :mod:`targets.dynamics.models.events`
- ``noise_path_p`` (P) and the proposal ``noise_q`` (innovation laws) or
  ``noise_path_q`` (legacy drift), on the single channel ``"w"``
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from .registry import ResolvedModelRun
from .sde_common import finite, interpret_sde_model, mapping, resolve_sde_model

OU_NOISE_CHANNELS = frozenset({"w"})


class OUSystem:
    """``HybridSystem`` with one mode ``"run"`` and one Brownian channel ``"w"``."""

    state_names = ("x",)

    def __init__(self, *, theta: float, sigma: float, x0: float) -> None:
        self.theta, self.sigma, self.x0 = theta, sigma, x0

    def initial_mode(self) -> str:
        return "run"

    def initial_state(self) -> np.ndarray:
        return np.array([self.x0])

    def drift(self, _t, x, _mode, _entry) -> np.ndarray:
        return np.array([-self.theta * x[0]])

    def noise_channels(self, _mode) -> tuple[str, ...]:
        return ("w",) if self.has_noise else ()

    def diffusion(self, _t, _x, _mode, _entry) -> np.ndarray:
        return np.array([[self.sigma]])

    def transitions(self, _mode):
        return ()

    def project(self, state: np.ndarray) -> np.ndarray:
        return state

    @property
    def has_noise(self) -> bool:
        return self.sigma != 0.0


@dataclass(frozen=True)
class OUScalarAdapter:
    model_id: str = "ou_scalar"
    legacy_execution: bool = False
    default_binary_metric: str = "c_event"
    default_continuous_metric: str | None = "x_terminal"

    def noise_channel_names(self) -> frozenset[str]:
        return OU_NOISE_CHANNELS

    def initial_input_names(self) -> tuple[str, ...]:
        return ("x0",)

    def state_names(self) -> tuple[str, ...]:
        return OUSystem.state_names

    def resolve(self, inputs: Mapping[str, object]) -> ResolvedModelRun:
        params = mapping(inputs.get("model_params"), "model_params")
        theta = finite(params.get("theta", 1.0), "model_params.theta")
        sigma = finite(params.get("sigma", 1.0), "model_params.sigma")
        if sigma < 0.0:
            raise ValueError("model_params.sigma must be non-negative")
        x0 = finite(inputs.get("x0", 0.0), "x0")
        return resolve_sde_model(
            model_id=self.model_id,
            system=OUSystem(theta=theta, sigma=sigma, x0=x0),
            inputs=inputs,
            channels=OU_NOISE_CHANNELS,
            model_meta={"model_params": {"theta": theta, "sigma": sigma}, "x0": x0,
                        "initial_input_names": self.initial_input_names()},
        )

    def interpret(
        self,
        payload: Mapping[str, object],
        trace: Mapping[str, np.ndarray],
    ) -> dict[str, object]:
        return interpret_sde_model(payload, trace, state_names=OUSystem.state_names)


__all__ = ["OUScalarAdapter", "OUSystem", "OU_NOISE_CHANNELS"]
