"""Model-independent hybrid stochastic dynamics interface.

Every dynamics model is a hybrid (stochastic) differential equation

    dx = f_q(t, x) dt + G_q(t, x) dW_t,

with a discrete mode ``q``.  ``f_q`` is the drift, ``G_q`` (shape n x m_q)
the diffusion over the mode's ``m_q`` noise channels, and ``W_t`` a standard
m_q-dimensional Brownian motion.  ``G_q = 0`` for every mode is the ODE case.

A mode switch ``q -> q'`` happens when the guard ``g(t, x)`` crosses zero in
the declared direction; the reset map ``x+ = r(x-)`` is then applied.  The
runners in ``targets.dynamics.runners`` integrate any model implementing
``HybridSystem`` and never interpret model-specific state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, Sequence

import numpy as np


Guard = Callable[[float, np.ndarray], float]
Reset = Callable[[np.ndarray], np.ndarray]


@dataclass(frozen=True)
class Transition:
    """Mode switch fired when ``guard`` crosses zero in ``direction``.

    ``direction=-1`` fires when the guard falls to <= 0, ``+1`` when it rises
    to >= 0.  The ODE runner locates the crossing by root finding; the SDE
    runner checks the sign after each fixed step.
    """

    target_mode: str
    guard: Guard
    direction: float
    reset: Reset | None = None

    def is_satisfied(self, time_sec: float, state: np.ndarray) -> bool:
        """Whether the guard is already on the firing side (used at grid points
        and at mode entry, where a root at the start cannot be bracketed)."""
        value = self.guard(time_sec, state)
        return value <= 0.0 if self.direction < 0 else value >= 0.0


class HybridSystem(Protocol):
    """Minimum contract a dynamics model provides to the generic runners."""

    state_names: tuple[str, ...]

    def initial_mode(self) -> str: ...

    def initial_state(self) -> np.ndarray: ...

    def drift(
        self,
        time_sec: float,
        state: np.ndarray,
        mode: str,
        mode_entry_times: Mapping[str, float],
    ) -> np.ndarray:
        """f_q(t, x); ``mode_entry_times`` maps each entered mode to its entry time."""
        ...

    def noise_channels(self, mode: str) -> tuple[str, ...]:
        """Names of the m_q Brownian channels driving mode ``q``."""
        ...

    def diffusion(
        self,
        time_sec: float,
        state: np.ndarray,
        mode: str,
        mode_entry_times: Mapping[str, float],
    ) -> np.ndarray:
        """G_q(t, x) with shape (len(state_names), len(noise_channels(mode)))."""
        ...

    def transitions(self, mode: str) -> Sequence[Transition]: ...

    def project(self, state: np.ndarray) -> np.ndarray:
        """Map a stochastic step back onto the admissible set (e.g. speed >= 0)."""
        ...

    @property
    def has_noise(self) -> bool:
        """False when every diffusion term is identically zero (pure ODE)."""
        ...


@dataclass(frozen=True)
class HybridTrajectory:
    times_sec: np.ndarray
    states: np.ndarray
    modes: tuple[str, ...]
    mode_entry_times: dict[str, float]


__all__ = ["Guard", "HybridSystem", "HybridTrajectory", "Reset", "Transition"]
