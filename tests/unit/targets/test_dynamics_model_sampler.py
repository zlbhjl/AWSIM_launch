from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from orchestration.sampling_plan import load_sampling_plan, sampling_plan_from_mapping
from targets.dynamics.sampling import DynamicsModelSampler, is_dynamics_schema_v2

GAUSSIAN_Q1 = Path("configs/dynamics_importance_ou_gaussian_q1.yaml")


def _plan(change=None):
    raw = yaml.safe_load(GAUSSIAN_Q1.read_text(encoding="utf-8"))
    if change is not None:
        change(raw)
    return sampling_plan_from_mapping(copy.deepcopy(raw))


def test_v2_plan_is_recognised_and_resolved_into_model_inputs() -> None:
    plan = load_sampling_plan(GAUSSIAN_Q1)
    sampler = DynamicsModelSampler(plan)

    assert is_dynamics_schema_v2(plan)
    assert sampler.case_kind == "ou_scalar"
    assert sampler.execution_inputs["x0"] == 0.0
    assert sampler.execution_inputs["sde_dt_sec"] == 0.01
    assert sampler.numerics == {"solver_kind": "sde", "dt_sec": 0.01, "horizon_sec": 1.0}
    assert sampler.proposal_inputs["noise_path_q"]["channel_drifts"] == {"w": 3.0}
    assert sampler.importance_weight_upper_bound is None
    assert sampler.proposal_metadata()["log_importance_weight"] == 0.0


def test_v1_plans_are_not_v2() -> None:
    assert not is_dynamics_schema_v2(load_sampling_plan("configs/dynamics_importance_q4.yaml"))


def test_defensive_proposal_records_its_weight_bound() -> None:
    def defensive(raw):
        raw["proposal"]["definition"]["noise_path_q"] = {
            "distribution": "defensive_gaussian_drift", "base_weight": 0.2, "channel_drifts": {"w": 3.0},
        }

    sampler = DynamicsModelSampler(_plan(defensive))

    assert sampler.importance_weight_upper_bound == pytest.approx(5.0)
    assert sampler.proposal_metadata()["importance_weight_upper_bound"] == pytest.approx(5.0)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda raw: raw["target_measure"]["definition"].update(initial_law={"kind": "uniform"}), "point_mass"),
        (lambda raw: raw["target_measure"]["definition"].update(initial_law={"kind": "point_mass", "value": [0.0, 1.0]}), "list of 1"),
        (lambda raw: raw["target_measure"]["definition"].update(model_id="uturn"), "list of 3"),
        (lambda raw: raw["target_measure"]["definition"].update(model_id="lane_change"), "unsupported dynamics model_id"),
        (lambda raw: raw["target_measure"]["definition"].update(solver={"kind": "ode"}), "solver.kind"),
        (lambda raw: raw["proposal"]["definition"].update(input={"kind": "shifted"}), "same_as_target"),
        (lambda raw: raw["proposal"]["definition"]["noise_path_q"].update(channel_drifts={"v": 1.0}), "unsupported Brownian channel"),
    ],
)
def test_invalid_v2_plans_are_rejected(change, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        DynamicsModelSampler(_plan(change))


@pytest.mark.parametrize(
    ("noise", "bound"),
    [
        ({"kind": "mixture", "defensive_weight": 0.25, "components": [
            {"weight": 1.0, "law": {"kind": "gaussian", "mean_rule": {"kind": "constant", "values": {"w": 0.3}}}}]}, 4.0),
        ({"kind": "mixture", "components": [
            {"weight": 0.5, "law": {"kind": "student_t", "dof": 5}},
            {"weight": 0.5, "law": {"kind": "target"}}]}, None),
        ({"kind": "gaussian"}, None),
    ],
)
def test_noise_laws_record_the_weight_bound(noise, bound) -> None:
    def use(raw):
        del raw["proposal"]["definition"]["noise_path_q"]
        raw["proposal"]["definition"]["noise"] = noise

    sampler = DynamicsModelSampler(_plan(use))

    assert sampler.importance_weight_upper_bound == (None if bound is None else pytest.approx(bound))
    assert sampler.proposal_inputs == {"noise_q": noise}


def test_noise_and_noise_path_q_together_are_rejected() -> None:
    def both(raw):
        raw["proposal"]["definition"]["noise"] = {"kind": "gaussian"}

    with pytest.raises(ValueError, match="not both"):
        DynamicsModelSampler(_plan(both))


def test_legacy_schedule_with_an_open_end_survives_the_round_trip(tmp_path) -> None:
    from targets.dynamics.models.ou import OUScalarAdapter

    def schedule(raw):
        raw["proposal"]["definition"]["noise_path_q"] = {"distribution": "gaussian_drift", "drift_schedule": [
            {"time": [0.0, 0.5], "channel_drifts": {"w": 1.0}}, {"time": [0.5, None], "channel_drifts": {"w": 2.0}}]}

    sampler = DynamicsModelSampler(_plan(schedule))
    definition = sampler.proposal_inputs["noise_path_q"]

    assert definition["drift_schedule"][1]["time"] == [0.5, None]
    resolved = OUScalarAdapter().resolve({**sampler.execution_inputs, **sampler.proposal_inputs, "sde_seed": 1})
    assert resolved.noise_proposal.drift_schedule[1].end_sec == float("inf")
