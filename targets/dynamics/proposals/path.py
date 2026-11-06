"""Path-level proposal Q over the Brownian innovations of one Euler--Maruyama path.

    q_path = beta * p_path + (1 - beta) * sum_j alpha_j r_{j,path}

A component is chosen once per path (never per step), and the realised path is
evaluated under P and under every component, so the denominator contains the
densities of the components that were not chosen. ``beta`` (defensive weight)
is optional; with it the noise part of the weight P/Q is at most 1/beta.
A single component without ``beta`` is a direct proposal.

The target P itself may appear as a component (``TARGET``).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from contracts.importance_sampling_plugin import InternalProposal
from contracts.sampling import TARGET_COMPONENT
from orchestration.importance.weights import choose_component, mixture_log_q, mixture_weight_bound, needs_choice

from .base import InnovationLaw


class _TargetComponent:
    """Marker for "the target law P" used as a mixture component."""

    def describe(self) -> dict[str, object]:
        return {"kind": "target"}

    def __repr__(self) -> str:
        return "TARGET"


TARGET = _TargetComponent()


@dataclass(frozen=True)
class PathProposal:
    components: tuple[InnovationLaw | _TargetComponent, ...]
    weights: tuple[float, ...]
    defensive_weight: float | None = None

    def __post_init__(self) -> None:
        if not self.components or len(self.components) != len(self.weights):
            raise ValueError("a path proposal needs one weight per component")
        if any(not math.isfinite(weight) or weight <= 0.0 for weight in self.weights):
            raise ValueError("mixture weights must be positive")
        if not math.isclose(sum(self.weights), 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("mixture weights must sum to 1")
        if self.defensive_weight is not None and not 0.0 < self.defensive_weight <= 1.0:
            raise ValueError("defensive_weight must satisfy 0 < beta <= 1")

    @property
    def needs_choice(self) -> bool:
        """Whether a per-path component draw is needed (beta = 1 means P only)."""
        return needs_choice(self.weights, self.defensive_weight)

    def choose_index(self, uniform: float | None) -> int | str:
        """Map one U(0, 1) draw to ``"target"`` (P) or a component index."""
        return choose_component(self.weights, self.defensive_weight, uniform)

    def choose(self, uniform: float | None) -> InnovationLaw | _TargetComponent:
        """Map one U(0, 1) draw to P (``TARGET``) or a component."""
        index = self.choose_index(uniform)
        return TARGET if index == TARGET_COMPONENT else self.components[index]

    def log_q(self, log_p: float, log_r: Sequence[float]) -> float:
        """Log path density of Q from log P and the log density of each component."""
        return mixture_log_q(self.weights, self.defensive_weight, log_p, log_r)

    def noise_weight_bound(self) -> float | None:
        """Upper bound of the noise part of P/Q, or None if unbounded."""
        return mixture_weight_bound(self.component_names(), self.defensive_weight)

    def component_names(self) -> tuple[str, ...]:
        return tuple(TARGET_COMPONENT if component is TARGET else f"component_{index}"
                     for index, component in enumerate(self.components))

    def internal_proposal(self) -> InternalProposal:
        """This proposal as seen by the shared importance-sampling code."""
        return InternalProposal(
            components=self.component_names(),
            weights=tuple(self.weights),
            definition=self,
            fingerprint_source=self.describe(),
            defensive_weight=self.defensive_weight,
        )

    def referenced_channels(self) -> frozenset[str]:
        names: set[str] = set()
        for component in self.components:
            if component is not TARGET:
                names |= set(component.referenced_channels())
        return frozenset(names)

    def describe(self) -> dict[str, object]:
        return {
            "kind": "path_mixture",
            "defensive_weight": self.defensive_weight,
            "components": [
                {"weight": weight, "law": component.describe()}
                for component, weight in zip(self.components, self.weights)
            ],
        }


__all__ = ["PathProposal", "TARGET"]
