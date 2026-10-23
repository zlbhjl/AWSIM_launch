from __future__ import annotations

from math import log, sqrt

import numpy as np
import pytest
from scipy.stats import laplace

from sde_test_systems import Linear1D
from targets.dynamics.proposals import ProposalContext, parse_noise_proposal
from targets.dynamics.runners import ODESolverSettings, SDESolverSettings, solve_hybrid_sde


def _law(scale=None):
    spec = {"kind": "laplace", "location_rule": {"kind": "constant", "values": {"w": 0.4}}}
    if scale is not None:
        spec["scale"] = scale
    return parse_noise_proposal(spec, channels=frozenset({"w"}), state_names=("x",)).components[0]


def test_laplace_density_matches_scipy_and_has_unit_variance_by_default() -> None:
    context = ProposalContext(0.0, 0.1, np.zeros(1), "run", 0.0, ("w",), ("x",), {})
    law = _law({"w": 0.8})

    assert law.log_density(np.array([1.3]), context) == pytest.approx(laplace(loc=0.4, scale=0.8).logpdf(1.3))
    draws = np.array([_law().sample(context, np.random.default_rng(seed)) for seed in range(20000)])
    assert np.var(draws) == pytest.approx(1.0, rel=0.05)


def test_laplace_runs_as_a_proposal_and_its_path_density_matches() -> None:
    system = Linear1D()
    proposal = parse_noise_proposal({"kind": "laplace", "location_rule": {"kind": "constant", "values": {"w": 0.4}}},
                                    channels=frozenset({"w"}), state_names=("x",))
    path = solve_hybrid_sde(system, SDESolverSettings(
        horizon_sec=0.2, dt_sec=0.1, seed=2, ode_fallback=ODESolverSettings(horizon_sec=0.2, max_step_sec=0.1),
        noise_proposal=proposal))

    x = path.states[:, 0]
    xi = (np.diff(x) + system.a * x[:-1] * 0.1) / (system.s * sqrt(0.1))
    assert path.noise_log_q == pytest.approx(float(np.sum(laplace(loc=0.4, scale=1 / sqrt(2)).logpdf(xi))))
    assert path.noise_log_p == pytest.approx(float(np.sum(-0.5 * xi ** 2 - 0.5 * log(2 * np.pi))))


def test_laplace_rejects_bad_scales() -> None:
    with pytest.raises(ValueError, match="positive"):
        _law({"w": 0.0})
    with pytest.raises(ValueError, match="unknown noise channels"):
        _law({"v": 1.0})
