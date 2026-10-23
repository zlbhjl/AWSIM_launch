"""Exact probabilities of discretised linear SDE models.

The estimators are validated against the probability of the *numerical* model
(Euler--Maruyama with step h, horizon T = K h), not the continuous-time one.
For the scalar OU model dX = -theta X dt + sigma dW the scheme is

    X_{k+1} = a X_k + sigma sqrt(h) xi_k,   a = 1 - theta h,   xi_k ~ N(0, 1),

so X_K is Gaussian with mean a^K x0 and variance
sigma^2 h (1 - a^{2K}) / (1 - a^2)  (sigma^2 h K when a^2 = 1).
The augmented model B is linear too; its terminal covariance follows the
recursion below.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm


def ou_euler_terminal_mean(*, theta: float, h: float, steps: int, x0: float = 0.0) -> float:
    return (1.0 - theta * h) ** steps * x0


def ou_euler_terminal_variance(*, theta: float, sigma: float, h: float, steps: int) -> float:
    if h <= 0.0 or steps < 1:
        raise ValueError("h must be positive and steps at least 1")
    a = 1.0 - theta * h
    if math.isclose(a * a, 1.0):
        return sigma * sigma * h * steps
    return sigma * sigma * h * (1.0 - a ** (2 * steps)) / (1.0 - a * a)


def ou_terminal_exceedance_probability(
    *,
    theta: float,
    sigma: float,
    h: float,
    steps: int,
    threshold: float,
    side: str = "upper",
    x0: float = 0.0,
) -> float:
    """P(X_K > threshold) (``upper``) or P(|X_K| > threshold) (``two_sided``)."""
    mean = ou_euler_terminal_mean(theta=theta, h=h, steps=steps, x0=x0)
    std = math.sqrt(ou_euler_terminal_variance(theta=theta, sigma=sigma, h=h, steps=steps))
    upper = float(norm.sf((threshold - mean) / std))
    if side == "upper":
        return upper
    if side == "two_sided":
        return upper + float(norm.cdf((-threshold - mean) / std))
    raise ValueError("side must be 'upper' or 'two_sided'")


def ou_augmented_terminal_moments(
    *,
    theta_c: float,
    theta_x: float,
    coupling: float,
    sigma: float,
    h: float,
    steps: int,
    c0: float = 0.0,
    x0: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Mean and covariance of (C_K, X_K) for the Euler scheme of model B.

    z_{k+1} = A z_k + sqrt(h) b xi_k with A = [[1 - theta_c h, 0], [coupling h, 1 - theta_x h]]
    and b = (sigma, 0); Sigma_{k+1} = A Sigma_k A^T + h b b^T.
    """
    if h <= 0.0 or steps < 1:
        raise ValueError("h must be positive and steps at least 1")
    transition = np.array([[1.0 - theta_c * h, 0.0], [coupling * h, 1.0 - theta_x * h]])
    loading = np.array([[sigma], [0.0]])
    mean = np.array([c0, x0], dtype=float)
    covariance = np.zeros((2, 2))
    for _ in range(steps):
        mean = transition @ mean
        covariance = transition @ covariance @ transition.T + h * (loading @ loading.T)
    return mean, covariance


def ou_augmented_terminal_exceedance_probability(
    *,
    theta_c: float,
    theta_x: float,
    coupling: float,
    sigma: float,
    h: float,
    steps: int,
    threshold: float,
    side: str = "upper",
    state: str = "x",
    c0: float = 0.0,
    x0: float = 0.0,
) -> float:
    mean, covariance = ou_augmented_terminal_moments(
        theta_c=theta_c, theta_x=theta_x, coupling=coupling, sigma=sigma, h=h, steps=steps, c0=c0, x0=x0
    )
    index = {"c": 0, "x": 1}[state]
    std = math.sqrt(covariance[index, index])
    upper = float(norm.sf((threshold - mean[index]) / std))
    if side == "upper":
        return upper
    if side == "two_sided":
        return upper + float(norm.cdf((-threshold - mean[index]) / std))
    raise ValueError("side must be 'upper' or 'two_sided'")


__all__ = [
    "ou_augmented_terminal_exceedance_probability",
    "ou_augmented_terminal_moments",
    "ou_euler_terminal_mean",
    "ou_euler_terminal_variance",
    "ou_terminal_exceedance_probability",
]
