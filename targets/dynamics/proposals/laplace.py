"""Independent Laplace innovation law (added after the common API was fixed).

Each active channel is Laplace(location, b) independently; the location comes
from a mean rule and ``scale`` gives b per channel (default 1/sqrt(2), unit
variance). Added to measure how much a new law family needs
(docs/sde_importance_sampling_plan.md §13).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from .base import ProposalContext, check_known
from .rules import MeanRule, parse_mean_rule

UNIT_VARIANCE_SCALE = 1.0 / math.sqrt(2.0)


@dataclass(frozen=True)
class LaplaceInnovationLaw:
    location_rule: MeanRule
    scales: Mapping[str, float]

    def _scale(self, channels: tuple[str, ...]) -> np.ndarray:
        return np.asarray([self.scales.get(channel, UNIT_VARIANCE_SCALE) for channel in channels], dtype=float)

    def sample(self, context: ProposalContext, rng: np.random.Generator) -> np.ndarray:
        return rng.laplace(self.location_rule.mean(context), self._scale(context.channels))

    def log_density(self, value: np.ndarray, context: ProposalContext) -> float:
        scale = self._scale(context.channels)
        deviation = np.abs(value - self.location_rule.mean(context))
        return float(np.sum(-np.log(2.0 * scale) - deviation / scale))

    def referenced_channels(self) -> frozenset[str]:
        return self.location_rule.referenced_channels() | frozenset(self.scales)

    def describe(self) -> dict[str, object]:
        return {"kind": "laplace", "units": "innovation", "location_rule": self.location_rule.describe(),
                "scale": dict(self.scales)}


def parse_laplace_law(spec, *, channels: frozenset[str], state_names: tuple[str, ...], context: str):
    scales = spec.get("scale", {})
    if not isinstance(scales, Mapping):
        raise ValueError(f"{context}.scale must map channels to positive numbers")
    check_known(scales, channels, what="noise channels", context=f"{context}.scale")
    for name, value in scales.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{context}.scale.{name} must be a positive number")
    return LaplaceInnovationLaw(
        location_rule=parse_mean_rule(
            spec.get("location_rule"), channels=channels, state_names=state_names, context=f"{context}.location_rule"
        ),
        scales={str(name): float(value) for name, value in scales.items()},
    )


__all__ = ["LaplaceInnovationLaw", "parse_laplace_law"]
