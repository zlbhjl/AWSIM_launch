"""Independent reference Monte Carlo for model C (no closed form).

This re-implements the Euler--Maruyama scheme of
:class:`targets.dynamics.models.nonlinear2d.Nonlinear2DSystem` with
vectorised NumPy, separately from the runner, so it can serve as an
independent check of the estimates. The grid matches the runner: steps of
``dt`` until the horizon, a shorter last step if needed, and the event is
judged at every grid time including t = 0.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import beta as beta_distribution


def nonlinear2d_event_counts(
    *,
    threshold: float,
    paths: int,
    seed: int,
    dt_sec: float = 0.01,
    horizon_sec: float = 1.0,
    side: str = "upper",
    drift_coupling: float = 0.5,
    modulation: float = 0.25,
    diffusion_scale: float = 0.5,
    correlation: float = 0.5,
    batch: int = 200_000,
) -> int:
    """Number of the ``paths`` simulated paths on which the max-exceedance event occurs."""
    rng = np.random.default_rng(seed)
    times = _grid(dt_sec, horizon_sec)
    hits = 0
    remaining = paths
    while remaining > 0:
        size = min(batch, remaining)
        remaining -= size
        x = np.zeros((size, 2))
        peak = _judged(x, side).max(axis=1)
        for step in np.diff(times):
            tanh = np.tanh(x)
            drift = np.column_stack([-x[:, 0] + drift_coupling * tanh[:, 1], -x[:, 1] + drift_coupling * tanh[:, 0]])
            a = 1.0 + modulation * tanh
            xi = rng.standard_normal((size, 2)) * math.sqrt(step)
            noise = diffusion_scale * np.column_stack([
                a[:, 0] * xi[:, 0],
                a[:, 1] * (correlation * xi[:, 0] + math.sqrt(1.0 - correlation ** 2) * xi[:, 1]),
            ])
            x = x + drift * step + noise
            peak = np.maximum(peak, _judged(x, side).max(axis=1))
        hits += int(np.count_nonzero(peak > threshold))
    return hits


def clopper_pearson(successes: int, trials: int, confidence: float = 0.95) -> tuple[float, float]:
    alpha = 1.0 - confidence
    lower = 0.0 if successes == 0 else float(beta_distribution.ppf(alpha / 2, successes, trials - successes + 1))
    upper = 1.0 if successes == trials else float(beta_distribution.ppf(1 - alpha / 2, successes + 1, trials - successes))
    return lower, upper


def _grid(dt_sec: float, horizon_sec: float) -> np.ndarray:
    times = [0.0]
    while times[-1] < horizon_sec - 1e-12:
        times.append(times[-1] + min(dt_sec, horizon_sec - times[-1]))
    return np.asarray(times)


def _judged(x: np.ndarray, side: str) -> np.ndarray:
    if side == "upper":
        return x
    if side == "two_sided":
        return np.abs(x)
    raise ValueError("side must be 'upper' or 'two_sided'")


__all__ = ["clopper_pearson", "nonlinear2d_event_counts"]
