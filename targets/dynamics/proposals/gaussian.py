"""Gaussian innovation laws.

``GaussianInnovationLaw``: mean from a rule, covariance fixed or per mode.
``DriftPathLaw``: the existing ``gaussian_drift`` path (unit covariance, mean
u * sqrt(dt) from SDE drift units); it draws and evaluates exactly as the
runner did before, so existing settings and seeds give the same paths.

A covariance names its channels. Channels it does not name are independent
with unit variance; for the channels active in a step the law uses the
corresponding sub-matrix (the marginal of the stated Gaussian). The matrix is
checked once with a Cholesky factorisation; it is never regularised (no jitter).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
from scipy.linalg import solve_triangular

from .base import LOG_2PI, ProposalContext, check_known
from .rules import MeanRule, parse_mean_rule


@dataclass(frozen=True)
class ChannelMatrix:
    """A symmetric positive-definite matrix over named channels."""

    channels: tuple[str, ...]
    matrix: np.ndarray
    _cache: dict = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        matrix = np.asarray(self.matrix, dtype=float)
        size = len(self.channels)
        if matrix.shape != (size, size):
            raise ValueError(f"covariance matrix must be {size}x{size} for channels {list(self.channels)}")
        if not np.all(np.isfinite(matrix)):
            raise ValueError("covariance matrix must be finite")
        scale = max(1.0, float(np.max(np.abs(matrix))))
        if not np.allclose(matrix, matrix.T, rtol=0.0, atol=1e-12 * scale):
            raise ValueError("covariance matrix must be symmetric")
        try:
            np.linalg.cholesky(matrix)
        except np.linalg.LinAlgError as exc:
            raise ValueError("covariance matrix must be positive definite") from exc
        object.__setattr__(self, "matrix", matrix)

    def factor(self, active: tuple[str, ...]) -> tuple[np.ndarray, float]:
        """Cholesky factor L and sum(log diag L) of the matrix over ``active``."""
        cached = self._cache.get(active)
        if cached is None:
            size = len(active)
            full = np.eye(size)
            index = {name: position for position, name in enumerate(self.channels)}
            for i, left in enumerate(active):
                for j, right in enumerate(active):
                    if left in index and right in index:
                        full[i, j] = self.matrix[index[left], index[right]]
            lower = np.linalg.cholesky(full)
            cached = (lower, float(np.sum(np.log(np.diag(lower)))))
            self._cache[active] = cached
        return cached

    def describe(self) -> dict[str, object]:
        return {"channels": list(self.channels), "matrix": self.matrix.tolist()}


@dataclass(frozen=True)
class CovarianceByMode:
    """Covariance per mode; modes not listed use ``default`` (None = identity)."""

    default: ChannelMatrix | None
    modes: Mapping[str, ChannelMatrix]

    def for_mode(self, mode: str) -> ChannelMatrix | None:
        return self.modes.get(mode, self.default)

    def channels(self) -> frozenset[str]:
        names = set(self.default.channels) if self.default is not None else set()
        for matrix in self.modes.values():
            names |= set(matrix.channels)
        return frozenset(names)

    def describe(self) -> dict[str, object]:
        return {
            "default": None if self.default is None else self.default.describe(),
            "by_mode": {mode: matrix.describe() for mode, matrix in self.modes.items()},
        }


def gaussian_log_density(value: np.ndarray, mean: np.ndarray, matrix: ChannelMatrix | None, channels) -> float:
    if matrix is None:
        deviation = value - mean
        return float(-0.5 * (deviation @ deviation + len(value) * LOG_2PI))
    lower, log_det_half = matrix.factor(tuple(channels))
    standardised = solve_triangular(lower, value - mean, lower=True)
    return float(-0.5 * standardised @ standardised - log_det_half - 0.5 * len(value) * LOG_2PI)


@dataclass(frozen=True)
class GaussianInnovationLaw:
    mean_rule: MeanRule
    covariance: CovarianceByMode

    def sample(self, context: ProposalContext, rng: np.random.Generator) -> np.ndarray:
        mean = self.mean_rule.mean(context)
        matrix = self.covariance.for_mode(context.mode)
        if matrix is None:
            return rng.normal(size=len(context.channels)) + mean
        lower, _ = matrix.factor(context.channels)
        return mean + lower @ rng.normal(size=len(context.channels))

    def log_density(self, value: np.ndarray, context: ProposalContext) -> float:
        return gaussian_log_density(
            value, self.mean_rule.mean(context), self.covariance.for_mode(context.mode), context.channels
        )

    def referenced_channels(self) -> frozenset[str]:
        return self.mean_rule.referenced_channels() | self.covariance.channels()

    def describe(self) -> dict[str, object]:
        return {
            "kind": "gaussian",
            "units": "innovation",
            "mean_rule": self.mean_rule.describe(),
            "covariance": self.covariance.describe(),
        }


@dataclass(frozen=True)
class DriftPathLaw:
    """The legacy ``gaussian_drift`` path: xi ~ N(u sqrt(dt), I)."""

    path: object  # runners.sde.GaussianDriftNoisePath (duck-typed to avoid a cycle)

    def mean(self, context: ProposalContext) -> np.ndarray:
        return self.path.innovation_mean(
            time_sec=context.time_sec, mode=context.mode, channels=context.channels, dt_sec=context.dt_sec
        )

    def sample(self, context: ProposalContext, rng: np.random.Generator) -> np.ndarray:
        return rng.normal(size=len(context.channels)) + self.mean(context)

    def log_density(self, value: np.ndarray, context: ProposalContext) -> float:
        return gaussian_log_density(value, self.mean(context), None, context.channels)

    def referenced_channels(self) -> frozenset[str]:
        names = set(self.path.channel_drifts)
        for entry in self.path.drift_schedule:
            names |= set(entry.channel_drifts)
        return frozenset(names)

    def describe(self) -> dict[str, object]:
        return {"kind": "gaussian_drift", "units": "sde_drift (innovation mean = u * sqrt(dt))"}


def parse_channel_matrix(spec: object, *, channels: frozenset[str], context: str) -> ChannelMatrix:
    if not isinstance(spec, Mapping):
        raise ValueError(f"{context} must be a mapping with channels and matrix")
    names = spec.get("channels")
    if not isinstance(names, list) or not names or not all(isinstance(name, str) for name in names):
        raise ValueError(f"{context}.channels must be a non-empty list of channel names")
    if len(set(names)) != len(names):
        raise ValueError(f"{context}.channels must not repeat names")
    check_known(names, channels, what="noise channels", context=context)
    try:
        matrix = np.asarray(spec.get("matrix"), dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{context}.matrix must be numeric") from exc
    try:
        return ChannelMatrix(tuple(names), matrix)
    except ValueError as exc:
        raise ValueError(f"{context}: {exc}") from exc


def parse_covariance(spec: object, *, channels: frozenset[str], context: str) -> CovarianceByMode:
    """``None`` = identity; ``{channels, matrix}``; or ``{default: ..., by_mode: {mode: ...}}``."""
    if spec is None:
        return CovarianceByMode(None, {})
    if not isinstance(spec, Mapping):
        raise ValueError(f"{context} must be a mapping")
    if "by_mode" in spec or "default" in spec:
        default = spec.get("default")
        by_mode = spec.get("by_mode", {})
        if not isinstance(by_mode, Mapping):
            raise ValueError(f"{context}.by_mode must be a mapping")
        return CovarianceByMode(
            None if default is None else parse_channel_matrix(default, channels=channels, context=f"{context}.default"),
            {
                str(mode): parse_channel_matrix(value, channels=channels, context=f"{context}.by_mode.{mode}")
                for mode, value in by_mode.items()
            },
        )
    return CovarianceByMode(parse_channel_matrix(spec, channels=channels, context=context), {})


def parse_gaussian_law(
    spec: Mapping[str, object],
    *,
    channels: frozenset[str],
    state_names: tuple[str, ...],
    context: str,
) -> GaussianInnovationLaw:
    return GaussianInnovationLaw(
        mean_rule=parse_mean_rule(
            spec.get("mean_rule"), channels=channels, state_names=state_names, context=f"{context}.mean_rule"
        ),
        covariance=parse_covariance(spec.get("covariance"), channels=channels, context=f"{context}.covariance"),
    )


__all__ = [
    "ChannelMatrix",
    "CovarianceByMode",
    "DriftPathLaw",
    "GaussianInnovationLaw",
    "gaussian_log_density",
    "parse_channel_matrix",
    "parse_covariance",
    "parse_gaussian_law",
]

