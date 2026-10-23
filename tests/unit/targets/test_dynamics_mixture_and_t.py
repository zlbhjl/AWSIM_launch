"""Student-t laws and path-level mixtures (S4 of docs/sde_importance_sampling_plan.md)."""

from __future__ import annotations

from math import exp, log, pi, sqrt

import numpy as np
import pytest
from scipy.stats import multivariate_normal, multivariate_t, t as student_t

from sde_test_systems import Linear1D
from targets.dynamics.proposals import TARGET, ProposalContext, parse_noise_proposal
from targets.dynamics.runners import ODESolverSettings, SDESolverSettings, solve_hybrid_sde

CHANNELS = frozenset({"a", "b"})
STATES = ("x", "y")


def _context(channels=("a", "b"), mode="run"):
    return ProposalContext(0.0, 0.1, np.zeros(2), mode, 0.0, channels, STATES, {})


def test_student_t_density_matches_scipy() -> None:
    proposal = parse_noise_proposal(
        {"kind": "student_t", "dof": 5, "location_rule": {"kind": "constant", "values": {"a": 0.3, "b": -0.2}},
         "scale": {"channels": ["a", "b"], "matrix": [[2.0, 0.6], [0.6, 1.5]]}},
        channels=CHANNELS, state_names=STATES,
    )
    law = proposal.components[0]
    value = np.array([0.7, -1.1])

    assert law.log_density(value, _context()) == pytest.approx(
        multivariate_t(loc=[0.3, -0.2], shape=[[2.0, 0.6], [0.6, 1.5]], df=5).logpdf(value)
    )
    assert law.log_density(np.array([-1.1]), _context(("b",))) == pytest.approx(
        student_t(df=5, loc=-0.2, scale=sqrt(1.5)).logpdf(-1.1)
    )


def test_student_t_covariance_option_sets_the_scale_from_the_covariance() -> None:
    proposal = parse_noise_proposal(
        {"kind": "student_t", "dof": 5, "covariance": {"channels": ["a", "b"], "matrix": [[1.0, 0.0], [0.0, 1.0]]}},
        channels=CHANNELS, state_names=STATES,
    )
    law = proposal.components[0]
    rng = np.random.default_rng(3)
    draws = np.array([law.sample(_context(), rng) for _ in range(40000)])

    assert law.scale.default.matrix[0, 0] == pytest.approx(3.0 / 5.0)
    assert np.var(draws[:, 0]) == pytest.approx(1.0, rel=0.06)
    assert np.mean(draws[:, 1]) == pytest.approx(0.0, abs=0.03)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"kind": "student_t", "dof": 0}, "dof"),
        ({"kind": "student_t", "dof": 2, "covariance": {"channels": ["a", "b"], "matrix": [[1, 0], [0, 1]]}}, "dof > 2"),
        ({"kind": "student_t", "covariance": {"channels": ["a"], "matrix": [[1.0]]}}, "name all channels"),
        ({"kind": "student_t", "scale": {"channels": ["a"], "matrix": [[1.0]]},
          "covariance": {"channels": ["a"], "matrix": [[1.0]]}}, "not both"),
        ({"kind": "mixture", "components": [{"weight": 0.5, "law": {"kind": "target"}}]}, "sum to 1"),
        ({"kind": "mixture", "components": [{"weight": 1.0, "law": {"kind": "mixture", "components": []}}]}, "not be a mixture"),
        ({"kind": "mixture", "defensive_weight": 1.5, "components": [{"weight": 1.0, "law": {"kind": "target"}}]}, "0 < beta"),
        ({"kind": "mixture", "components": [{"weight": -0.5, "law": {"kind": "target"}},
                                            {"weight": 1.5, "law": {"kind": "target"}}]}, "positive"),
    ],
)
def test_invalid_specifications_are_rejected(spec, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_noise_proposal(spec, channels=CHANNELS, state_names=STATES)


def _settings(proposal, *, seed, mixture_seed=None, horizon=0.2):
    return SDESolverSettings(
        horizon_sec=horizon, dt_sec=0.1, seed=seed,
        ode_fallback=ODESolverSettings(horizon_sec=horizon, max_step_sec=0.1),
        noise_proposal=proposal, mixture_seed=mixture_seed,
    )


MIXTURE = {
    "kind": "mixture",
    "defensive_weight": 0.3,
    "components": [
        {"weight": 0.6, "law": {"kind": "gaussian", "mean_rule": {"kind": "constant", "values": {"w": 1.0}}}},
        {"weight": 0.4, "law": {"kind": "student_t", "dof": 4,
                                "location_rule": {"kind": "affine_state", "channels": ["w"], "states": ["x"],
                                                  "offset": [-0.5], "matrix": [[1.0]]}}},
    ],
}


def test_mixture_denominator_includes_every_component_on_a_short_path() -> None:
    system = Linear1D()
    proposal = parse_noise_proposal(MIXTURE, channels=frozenset({"w"}), state_names=("x",))

    for mixture_seed in range(6):
        path = solve_hybrid_sde(system, _settings(proposal, seed=4, mixture_seed=mixture_seed))
        x = path.states[:, 0]
        p = r1 = r2 = 1.0
        for k in range(2):
            xi = (x[k + 1] - x[k] + system.a * x[k] * 0.1) / (system.s * sqrt(0.1))
            p *= exp(-0.5 * xi * xi) / sqrt(2 * pi)
            r1 *= exp(-0.5 * (xi - 1.0) ** 2) / sqrt(2 * pi)
            r2 *= student_t(df=4, loc=-0.5 + x[k]).pdf(xi)
        q = 0.3 * p + 0.7 * (0.6 * r1 + 0.4 * r2)
        assert path.noise_log_p == pytest.approx(log(p), abs=1e-9)
        assert path.noise_log_q == pytest.approx(log(q), abs=1e-9)


def test_defensive_weight_one_is_the_target_law() -> None:
    system = Linear1D()
    nominal = solve_hybrid_sde(system, _settings(None, seed=8, horizon=0.5))
    proposal = parse_noise_proposal({**MIXTURE, "defensive_weight": 1.0}, channels=frozenset({"w"}), state_names=("x",))

    path = solve_hybrid_sde(system, _settings(proposal, seed=8, mixture_seed=2, horizon=0.5))

    assert np.array_equal(path.states, nominal.states)
    assert path.noise_log_q == path.noise_log_p


def test_every_path_respects_the_defensive_bound() -> None:
    system = Linear1D()
    proposal = parse_noise_proposal(MIXTURE, channels=frozenset({"w"}), state_names=("x",))
    log_bound = -log(0.3)

    log_weights = [
        path.noise_log_p - path.noise_log_q
        for path in (
            solve_hybrid_sde(system, _settings(proposal, seed=seed, mixture_seed=seed + 1000, horizon=1.0))
            for seed in range(200)
        )
    ]

    assert proposal.noise_weight_bound() == pytest.approx(1 / 0.3)
    assert max(log_weights) <= log_bound + 1e-12


def test_each_component_is_chosen_with_its_weight() -> None:
    proposal = parse_noise_proposal(MIXTURE, channels=frozenset({"w"}), state_names=("x",))
    rng = np.random.default_rng(0)
    chosen = [proposal.choose(rng.random()) for _ in range(20000)]

    assert sum(component is TARGET for component in chosen) / 20000 == pytest.approx(0.3, abs=0.01)
    assert sum(component is proposal.components[0] for component in chosen) / 20000 == pytest.approx(0.42, abs=0.01)


def test_families_switch_without_changing_the_target_or_the_diffusion() -> None:
    system = Linear1D()
    specs = [
        {"kind": "gaussian", "mean_rule": {"kind": "constant", "values": {"w": 0.5}}},
        {"kind": "student_t", "dof": 5, "location_rule": {"kind": "constant", "values": {"w": 0.5}}},
        MIXTURE,
    ]
    nominal_p = []
    for spec in specs:
        path = solve_hybrid_sde(
            system, _settings(parse_noise_proposal(spec, channels=frozenset({"w"}), state_names=("x",)),
                              seed=5, mixture_seed=6),
        )
        x = path.states[:, 0]
        xi = (np.diff(x) + system.a * x[:-1] * 0.1) / (system.s * sqrt(0.1))
        nominal_p.append(float(np.sum(multivariate_normal(mean=[0.0], cov=[[1.0]]).logpdf(xi[:, None]))))
        assert path.noise_log_p == pytest.approx(nominal_p[-1], abs=1e-9)
