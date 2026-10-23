"""Multivariate Student-t innovation law.

    xi = location + L z sqrt(nu / g),   z ~ N(0, I),  g ~ chi^2_nu,  L L^T = Sigma

``Sigma`` is the *scale* matrix, not the covariance: for nu > 2 the covariance
is nu / (nu - 2) Sigma. A spec gives either ``scale`` or ``covariance`` (then
Sigma = (nu - 2) / nu * covariance, which needs nu > 2), never both. As for the
Gaussian law, matrices name their channels, unnamed channels get unit scale,
and a step uses the sub-matrix of its active channels (the marginal of a
multivariate t is a t with the same nu).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from scipy.linalg import solve_triangular
from scipy.special import gammaln

from .base import ProposalContext
from .gaussian import ChannelMatrix, CovarianceByMode, parse_covariance
from .rules import MeanRule, parse_mean_rule


@dataclass(frozen=True)
class StudentTInnovationLaw:
    location_rule: MeanRule
    scale: CovarianceByMode
    dof: float

    def _factor(self, context: ProposalContext) -> tuple[np.ndarray | None, float]:
        matrix = self.scale.for_mode(context.mode)
        if matrix is None:
            return None, 0.0
        return matrix.factor(context.channels)

    def sample(self, context: ProposalContext, rng: np.random.Generator) -> np.ndarray:
        location = self.location_rule.mean(context)
        lower, _ = self._factor(context)
        standard = rng.normal(size=len(context.channels))
        mixing = math.sqrt(self.dof / rng.chisquare(self.dof))
        spread = standard if lower is None else lower @ standard
        return location + spread * mixing

    def log_density(self, value: np.ndarray, context: ProposalContext) -> float:
        lower, log_det_half = self._factor(context)
        deviation = value - self.location_rule.mean(context)
        standardised = deviation if lower is None else solve_triangular(lower, deviation, lower=True)
        dimension = len(value)
        nu = self.dof
        return float(
            gammaln((nu + dimension) / 2.0)
            - gammaln(nu / 2.0)
            - 0.5 * dimension * math.log(nu * math.pi)
            - log_det_half
            - 0.5 * (nu + dimension) * math.log1p(float(standardised @ standardised) / nu)
        )

    def referenced_channels(self) -> frozenset[str]:
        return self.location_rule.referenced_channels() | self.scale.channels()

    def describe(self) -> dict[str, object]:
        return {
            "kind": "student_t",
            "units": "innovation",
            "dof": self.dof,
            "location_rule": self.location_rule.describe(),
            "scale": self.scale.describe(),
        }


def parse_student_t_law(
    spec: Mapping[str, object],
    *,
    channels: frozenset[str],
    state_names: tuple[str, ...],
    context: str,
) -> StudentTInnovationLaw:
    dof = spec.get("dof", 5.0)
    if isinstance(dof, bool) or not isinstance(dof, (int, float)) or not math.isfinite(float(dof)) or dof <= 0:
        raise ValueError(f"{context}.dof must be a positive finite number")
    dof = float(dof)
    if spec.get("scale") is not None and spec.get("covariance") is not None:
        raise ValueError(f"{context} must give scale or covariance, not both")
    if spec.get("covariance") is not None:
        if dof <= 2.0:
            raise ValueError(f"{context}: a covariance needs dof > 2 (the t covariance is infinite otherwise)")
        covariance = parse_covariance(spec["covariance"], channels=channels, context=f"{context}.covariance")
        # Unnamed channels would get unit *scale*, i.e. covariance nu/(nu-2), so
        # a covariance must name every channel of the model.
        matrices = ([covariance.default] if covariance.default is not None else []) + list(covariance.modes.values())
        if covariance.default is None or any(set(matrix.channels) != set(channels) for matrix in matrices):
            raise ValueError(
                f"{context}.covariance must give a default matrix, and every matrix must name all channels "
                f"{sorted(channels)}"
            )
        factor = (dof - 2.0) / dof
        scale = CovarianceByMode(
            _scaled(covariance.default, factor),
            {mode: _scaled(matrix, factor) for mode, matrix in covariance.modes.items()},
        )
    else:
        scale = parse_covariance(spec.get("scale"), channels=channels, context=f"{context}.scale")
    return StudentTInnovationLaw(
        location_rule=parse_mean_rule(
            spec.get("location_rule"), channels=channels, state_names=state_names, context=f"{context}.location_rule"
        ),
        scale=scale,
        dof=dof,
    )


def _scaled(matrix: ChannelMatrix, factor: float) -> ChannelMatrix:
    return ChannelMatrix(matrix.channels, matrix.matrix * factor)


__all__ = ["StudentTInnovationLaw", "parse_student_t_law"]
