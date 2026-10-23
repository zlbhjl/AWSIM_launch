"""Conditional innovation laws (S3 of docs/sde_importance_sampling_plan.md)."""

from __future__ import annotations

from math import log, pi, sqrt

import numpy as np
import pytest
from scipy.stats import multivariate_normal

from sde_test_systems import Linear1D, TwoModes
from targets.dynamics.proposals import ProposalContext, parse_noise_proposal
from targets.dynamics.proposals.gaussian import ChannelMatrix
from targets.dynamics.runners import (
    GaussianDriftNoisePath,
    ODESolverSettings,
    SDESolverSettings,
    solve_hybrid_sde,
)


def _settings(proposal, *, seed=11, horizon=0.2, dt=0.1):
    return SDESolverSettings(
        horizon_sec=horizon, dt_sec=dt, seed=seed,
        ode_fallback=ODESolverSettings(horizon_sec=horizon, max_step_sec=dt),
        noise_proposal=proposal,
    )


def _normal_log(value, mean=0.0, variance=1.0):
    return -0.5 * ((value - mean) ** 2 / variance + log(2 * pi * variance))


def test_state_dependent_two_step_density_matches_a_reference_calculation() -> None:
    system = Linear1D()
    proposal = parse_noise_proposal(
        {"kind": "gaussian",
         "mean_rule": {"kind": "affine_state", "channels": ["w"], "states": ["x"], "offset": [0.4], "matrix": [[2.0]]},
         "covariance": {"channels": ["w"], "matrix": [[1.7]]}},
        channels=frozenset({"w"}), state_names=("x",),
    )

    path = solve_hybrid_sde(system, _settings(proposal))

    x = path.states[:, 0]
    dt = 0.1
    log_p = log_q = 0.0
    for k in range(2):
        # Recover the innovation from the realised step; the rule uses the state before it.
        xi = (x[k + 1] - x[k] + system.a * x[k] * dt) / (system.s * sqrt(dt))
        log_p += _normal_log(xi)
        log_q += _normal_log(xi, mean=0.4 + 2.0 * x[k], variance=1.7)
    assert len(x) == 3
    assert path.noise_log_p == pytest.approx(log_p, abs=1e-10)
    assert path.noise_log_q == pytest.approx(log_q, abs=1e-10)


def test_constant_innovation_mean_reproduces_the_legacy_gaussian_drift() -> None:
    system = Linear1D()
    drift_u = 1.3
    legacy = solve_hybrid_sde(system, _settings(GaussianDriftNoisePath(channel_drifts={"w": drift_u}), horizon=0.5))
    new = solve_hybrid_sde(system, _settings(
        parse_noise_proposal(
            {"kind": "gaussian", "mean_rule": {"kind": "constant", "values": {"w": drift_u * sqrt(0.1)}}},
            channels=frozenset({"w"}), state_names=("x",),
        ),
        horizon=0.5,
    ))

    assert np.allclose(new.states, legacy.states, rtol=0.0, atol=1e-12)
    assert new.noise_log_q == pytest.approx(legacy.noise_log_q, abs=1e-10)
    assert new.noise_log_p == pytest.approx(legacy.noise_log_p, abs=1e-10)


@pytest.mark.parametrize(
    ("rule", "x", "time", "expected"),
    [
        ({"kind": "constant", "values": {"w": 0.5}}, 0.0, 0.0, 0.5),
        ({"kind": "piecewise", "values": {"w": 0.1}, "pieces": [{"time": [0.5, None], "values": {"w": 0.9}}]}, 0.0, 0.2, 0.1),
        ({"kind": "piecewise", "values": {"w": 0.1}, "pieces": [{"time": [0.5, None], "values": {"w": 0.9}}]}, 0.0, 0.7, 0.9),
        ({"kind": "piecewise", "pieces": [{"time": [0.0, None], "mode": "other", "values": {"w": 0.9}}]}, 0.0, 0.7, 0.0),
        ({"kind": "state_threshold", "state": "x", "threshold": 1.0, "below": {"w": 0.2}, "above": {"w": -0.4}}, 0.5, 0.0, 0.2),
        ({"kind": "state_threshold", "state": "x", "threshold": 1.0, "below": {"w": 0.2}, "above": {"w": -0.4}}, 1.0, 0.0, -0.4),
        ({"kind": "affine_state", "channels": ["w"], "states": ["x"], "offset": [1.0], "matrix": [[-2.0]]}, 0.25, 0.0, 0.5),
    ],
)
def test_mean_rules(rule, x, time, expected) -> None:
    law = parse_noise_proposal({"kind": "gaussian", "mean_rule": rule}, channels=frozenset({"w"}), state_names=("x",))
    context = ProposalContext(time, 0.1, np.array([x]), "run", 0.0, ("w",), ("x",), {})

    assert law.components[0].mean_rule.mean(context)[0] == pytest.approx(expected)


def test_full_covariance_density_matches_scipy_and_uses_the_active_submatrix() -> None:
    proposal = parse_noise_proposal(
        {"kind": "gaussian",
         "mean_rule": {"kind": "constant", "values": {"a": 0.3, "b": -0.2}},
         "covariance": {"channels": ["a", "b"], "matrix": [[2.0, 0.6], [0.6, 1.5]]}},
        channels=frozenset({"a", "b"}), state_names=("x", "y"),
    )
    law = proposal.components[0]
    both = ProposalContext(0.0, 0.1, np.zeros(2), "first", 0.0, ("a", "b"), ("x", "y"), {})
    only_b = ProposalContext(0.0, 0.1, np.zeros(2), "second", 0.0, ("b",), ("x", "y"), {})
    value = np.array([0.7, -1.1])

    assert law.log_density(value, both) == pytest.approx(
        multivariate_normal(mean=[0.3, -0.2], cov=[[2.0, 0.6], [0.6, 1.5]]).logpdf(value)
    )
    assert law.log_density(np.array([-1.1]), only_b) == pytest.approx(_normal_log(-1.1, -0.2, 1.5))


def test_mode_switch_changes_channels_and_the_path_density_matches() -> None:
    proposal = parse_noise_proposal(
        {"kind": "gaussian",
         "mean_rule": {"kind": "constant", "values": {"a": 0.3, "b": -0.2}},
         "covariance": {"channels": ["a", "b"], "matrix": [[2.0, 0.6], [0.6, 1.5]]}},
        channels=frozenset({"a", "b"}), state_names=("x", "y"),
    )

    path = solve_hybrid_sde(TwoModes(), _settings(proposal, horizon=0.3, dt=0.1))

    assert path.modes == ("first", "first", "second", "second")
    assert path.noise_sample_count == 2 + 2 + 1
    increments = np.diff(path.states, axis=0) / sqrt(0.1)
    expected_q = 0.0
    expected_p = 0.0
    for k, mode in enumerate(path.modes[:-1]):
        if mode == "first":
            xi = increments[k]
            expected_q += multivariate_normal(mean=[0.3, -0.2], cov=[[2.0, 0.6], [0.6, 1.5]]).logpdf(xi)
            expected_p += multivariate_normal(mean=[0, 0], cov=np.eye(2)).logpdf(xi)
        else:
            xi = increments[k][1]
            expected_q += _normal_log(xi, -0.2, 1.5)
            expected_p += _normal_log(xi)
    assert path.noise_log_q == pytest.approx(expected_q, abs=1e-10)
    assert path.noise_log_p == pytest.approx(expected_p, abs=1e-10)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"kind": "gaussian", "covariance": {"channels": ["w"], "matrix": [[-1.0]]}}, "positive definite"),
        ({"kind": "gaussian", "covariance": {"channels": ["w", "v"], "matrix": [[1.0, 0.2], [0.3, 1.0]]}}, "symmetric"),
        ({"kind": "gaussian", "covariance": {"channels": ["w"], "matrix": [[1.0, 0.0]]}}, "1x1"),
        ({"kind": "gaussian", "covariance": {"channels": ["z"], "matrix": [[1.0]]}}, "unknown noise channels"),
        ({"kind": "gaussian", "mean_rule": {"kind": "constant", "values": {"z": 1.0}}}, "unknown noise channels"),
        ({"kind": "gaussian", "mean_rule": {"kind": "state_threshold", "state": "y", "threshold": 0.0}}, "unknown states"),
        ({"kind": "gaussian", "mean_rule": {"kind": "affine_state", "channels": ["w"], "states": ["x"], "matrix": [[1.0, 2.0]]}}, "1x1"),
        ({"kind": "gaussian", "mean_rule": {"kind": "quadratic"}}, "kind must be one of"),
        ({"kind": "laplace_unknown"}, "kind must be one of"),
    ],
)
def test_invalid_specifications_are_rejected(spec, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_noise_proposal(spec, channels=frozenset({"w", "v"}), state_names=("x",))


def test_covariance_is_not_regularised() -> None:
    with pytest.raises(ValueError, match="positive definite"):
        ChannelMatrix(("a", "b"), np.array([[1.0, 1.0], [1.0, 1.0]]))


def test_piecewise_rule_can_hold_an_affine_rule_per_window() -> None:
    law = parse_noise_proposal(
        {"kind": "gaussian", "mean_rule": {"kind": "piecewise", "values": {"w": 0.1}, "pieces": [
            {"time": [0.5, None], "rule": {"kind": "affine_state", "channels": ["w"], "states": ["x"],
                                           "offset": [1.0], "matrix": [[-2.0]]}}]}},
        channels=frozenset({"w"}), state_names=("x",),
    ).components[0]

    early = ProposalContext(0.2, 0.1, np.array([0.25]), "run", 0.0, ("w",), ("x",), {})
    late = ProposalContext(0.7, 0.1, np.array([0.25]), "run", 0.0, ("w",), ("x",), {})
    assert law.mean_rule.mean(early)[0] == pytest.approx(0.1)
    assert law.mean_rule.mean(late)[0] == pytest.approx(0.5)
    with pytest.raises(ValueError, match="not be piecewise"):
        parse_noise_proposal(
            {"kind": "gaussian", "mean_rule": {"kind": "piecewise", "pieces": [
                {"time": [0.0, None], "rule": {"kind": "piecewise"}}]}},
            channels=frozenset({"w"}), state_names=("x",),
        )
