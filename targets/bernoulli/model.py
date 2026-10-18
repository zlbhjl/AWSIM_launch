"""Outcome model of the bernoulli target and its exact statistics.

The framework draws ``u_failure``, ``u_time`` and ``u_score`` uniformly on
[0, 1] from the sampling seed; the model maps them to outcomes by inverse
transform sampling, so every outcome has a known distribution:

- ``c_failure``       ~ Bernoulli(p_failure)
- ``time_to_failure`` ~ Exponential(rate)
- ``score``           ~ Beta(score_alpha, score_beta)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from scipy.stats import beta as beta_distribution

INPUT_NAMES = ("u_failure", "u_time", "u_score")
DEFAULT_PARAMETERS = {
    "p_failure": 0.05,
    "rate": 1.0,
    "score_alpha": 2.0,
    "score_beta": 5.0,
}


@dataclass(frozen=True)
class BernoulliParameters:
    p_failure: float = DEFAULT_PARAMETERS["p_failure"]
    rate: float = DEFAULT_PARAMETERS["rate"]
    score_alpha: float = DEFAULT_PARAMETERS["score_alpha"]
    score_beta: float = DEFAULT_PARAMETERS["score_beta"]

    def __post_init__(self) -> None:
        if not 0.0 <= self.p_failure <= 1.0:
            raise ValueError("p_failure must be in [0, 1]")
        if self.rate <= 0.0:
            raise ValueError("rate must be positive")
        if self.score_alpha <= 0.0 or self.score_beta <= 0.0:
            raise ValueError("score_alpha and score_beta must be positive")

    @classmethod
    def from_input(cls, values: Mapping[str, object]) -> "BernoulliParameters":
        return cls(**{name: float(values.get(name, default)) for name, default in DEFAULT_PARAMETERS.items()})


def evaluate(parameters: BernoulliParameters, inputs: Mapping[str, object]) -> dict[str, float | int]:
    """Map uniform inputs to outcomes."""
    u_failure, u_time, u_score = (_unit_interval(inputs, name) for name in INPUT_NAMES)
    return {
        "c_failure": int(u_failure < parameters.p_failure),
        "time_to_failure": -math.log1p(-u_time) / parameters.rate,
        "score": float(beta_distribution.ppf(u_score, parameters.score_alpha, parameters.score_beta)),
    }


def failure_probability(parameters: BernoulliParameters) -> float:
    return parameters.p_failure


def time_to_failure_quantile(parameters: BernoulliParameters, q: float) -> float:
    """Exact q-quantile of Exponential(rate)."""
    return -math.log1p(-q) / parameters.rate


def score_mean(parameters: BernoulliParameters) -> float:
    return parameters.score_alpha / (parameters.score_alpha + parameters.score_beta)


def _unit_interval(inputs: Mapping[str, object], name: str) -> float:
    if name not in inputs:
        raise ValueError(f"missing input {name}")
    value = float(inputs[name])
    if not 0.0 <= value < 1.0:
        raise ValueError(f"{name} must be in [0, 1)")
    return value


__all__ = [
    "BernoulliParameters",
    "DEFAULT_PARAMETERS",
    "INPUT_NAMES",
    "evaluate",
    "failure_probability",
    "score_mean",
    "time_to_failure_quantile",
]
