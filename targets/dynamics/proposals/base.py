"""Common contract of conditional innovation laws for the Euler--Maruyama runner.

Each step of the runner draws a standardised Brownian increment (innovation)
xi_k for the noise channels active in the current mode. An innovation law gives
the conditional distribution of xi_k given what is known just before the draw:

    sample(context, rng)       -> xi_k
    log_density(xi_k, context) -> log density of xi_k under this law

Both must describe the same normalised distribution, use only the read-only
``context`` (never future draws), and keep no state across steps or paths.
The importance weight of a path is the product over steps of P/Q densities of
the realised innovations, all evaluated on the same context
(docs/sde_importance_sampling_plan.md §3-§5).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class ProposalContext:
    """What is known just before drawing the innovation of one step."""

    time_sec: float
    dt_sec: float  # the actual step, shorter than h for the last step
    state: np.ndarray
    mode: str
    mode_entry_time_sec: float
    channels: tuple[str, ...]  # active noise channels, in the order of the innovation vector
    state_names: tuple[str, ...]
    initial_inputs: Mapping[str, object]

    def state_value(self, name: str) -> float:
        return float(self.state[self.state_names.index(name)])


class InnovationLaw(Protocol):
    def sample(self, context: ProposalContext, rng: np.random.Generator) -> np.ndarray: ...

    def log_density(self, value: np.ndarray, context: ProposalContext) -> float: ...

    def referenced_channels(self) -> frozenset[str]: ...

    def describe(self) -> dict[str, object]: ...


LOG_2PI = float(np.log(2.0 * np.pi))


def check_known(names: object, known: frozenset[str] | tuple[str, ...], *, what: str, context: str) -> None:
    unknown = sorted(set(names) - set(known))  # type: ignore[arg-type]
    if unknown:
        raise ValueError(f"{context} refers to unknown {what}: {unknown}")


__all__ = ["InnovationLaw", "LOG_2PI", "ProposalContext", "check_known"]
