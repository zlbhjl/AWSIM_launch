"""Models B (augmented OU), C (nonlinear) and D (U-turn) on the registered-model path."""

from __future__ import annotations

from math import sqrt
from pathlib import Path

import numpy as np
import pytest

from contracts.execution import RunStatus, TestCase
from targets.dynamics.backend import DynamicsBackend, DynamicsBackendConfig
from targets.dynamics.models.events import evaluate_event, parse_event
from targets.dynamics.models.exact import (
    ou_augmented_terminal_exceedance_probability,
    ou_augmented_terminal_moments,
)
from targets.dynamics.models.nonlinear2d import Nonlinear2DAdapter
from targets.dynamics.models.ou_augmented import OUAugmentedAdapter
from targets.dynamics.models.reference import nonlinear2d_event_counts
from targets.dynamics.result_interpreter import DynamicsResultInterpreter

COMMON = {"solver_kind": "sde", "sde_dt_sec": 0.01, "horizon_sec": 1.0, "sde_seed": 3}


def test_events_are_judged_on_the_grid() -> None:
    trace = {"x": np.array([0.0, 2.5, -3.0]), "y": np.array([0.0, 0.1, 0.2])}
    states = ("x", "y")

    assert evaluate_event(parse_event({"kind": "terminal_exceedance", "threshold": 2.0}, state_names=states), trace)["c_event"] == 0
    assert evaluate_event(parse_event({"kind": "terminal_exceedance", "threshold": 2.0, "side": "two_sided"}, state_names=states), trace)["c_event"] == 1
    assert evaluate_event(parse_event({"kind": "max_exceedance", "threshold": 2.0}, state_names=states), trace) == {"c_event": 1, "event_value": 2.5}
    assert evaluate_event(parse_event({"kind": "max_exceedance", "threshold": 2.0, "states": ["y"]}, state_names=states), trace)["c_event"] == 0
    with pytest.raises(ValueError, match="event.states"):
        parse_event({"kind": "max_exceedance", "threshold": 1.0, "states": ["z"]}, state_names=states)


def test_model_b_path_matches_its_euler_recursion_and_the_exact_moments() -> None:
    resolved = OUAugmentedAdapter().resolve(
        {**COMMON, "event": {"kind": "terminal_exceedance", "threshold": 0.5, "state": "x"}}
    )
    path = resolved.solve()

    rng = np.random.default_rng(3)
    c = x = 0.0
    for _ in range(100):
        xi = rng.normal(size=1)[0]
        c, x = c - 2.0 * c * 0.01 + sqrt(0.01) * xi, x + (-x + c) * 0.01
    assert path.states[-1] == pytest.approx([c, x], abs=1e-12)

    _, covariance = ou_augmented_terminal_moments(theta_c=2.0, theta_x=1.0, coupling=1.0, sigma=1.0, h=0.01, steps=100)
    transition = np.array([[0.98, 0.0], [0.01, 0.99]])
    direct = sum(
        np.linalg.matrix_power(transition, j) @ np.array([[0.01, 0.0], [0.0, 0.0]]) @ np.linalg.matrix_power(transition, j).T
        for j in range(100)
    )
    assert covariance == pytest.approx(direct, abs=1e-14)
    probability = ou_augmented_terminal_exceedance_probability(
        theta_c=2.0, theta_x=1.0, coupling=1.0, sigma=1.0, h=0.01, steps=100, threshold=3 * sqrt(covariance[1, 1])
    )
    assert probability == pytest.approx(1.3498980316e-3, rel=1e-8)


def test_model_c_runner_and_the_independent_reference_agree_path_by_path() -> None:
    adapter = Nonlinear2DAdapter()
    for seed in range(15):
        resolved = adapter.resolve({**COMMON, "sde_seed": seed, "event": {"kind": "max_exceedance", "threshold": 0.6}})
        peak = float(np.max(resolved.solve().states))
        expected = int(peak > 0.6)
        assert nonlinear2d_event_counts(threshold=0.6, paths=1, seed=seed, batch=1) == expected


def test_model_c_rejects_invalid_parameters() -> None:
    with pytest.raises(ValueError, match="correlation"):
        Nonlinear2DAdapter().resolve({**COMMON, "model_params": {"correlation": 1.0},
                                      "event": {"kind": "max_exceedance", "threshold": 1.0}})
    with pytest.raises(ValueError, match="unknown keys"):
        Nonlinear2DAdapter().resolve({**COMMON, "model_params": {"rate": 1.0},
                                      "event": {"kind": "max_exceedance", "threshold": 1.0}})


def _uturn_inputs(**changes):
    inputs = {
        "model_id": "uturn", "dx0": 15.0, "ego_speed": 36.0, "npc_speed": 18.0,
        "solver_kind": "sde", "sde_seed": 5, "sde_dt_sec": 0.02, "horizon_sec": 10.0,
        "sde_noise": {"npc_acceleration_std": 0.2, "ego_brake_acceleration_std": 0.15},
    }
    inputs.update(changes)
    return inputs


def _run(backend, inputs, case_id="case"):
    raw = backend.run(TestCase(case_id, "dynamics", "uturn", input=inputs))
    return DynamicsResultInterpreter().interpret_raw_run_result(raw)


def test_uturn_v2_with_q_equal_to_p_reproduces_the_v1_result(tmp_path: Path) -> None:
    backend = DynamicsBackend(DynamicsBackendConfig(output_root=tmp_path))
    v1 = _run(backend, _uturn_inputs())
    noise = _uturn_inputs().pop("sde_noise")
    v2_inputs = {key: value for key, value in _uturn_inputs().items() if key != "sde_noise"}
    v2 = _run(backend, {**v2_inputs, "dynamics_schema_version": 2, "model_params": {"sde_noise": noise},
                        "event": {"kind": "collision"}})

    assert v1.status is RunStatus.SUCCESS and v2.status is RunStatus.SUCCESS
    for key in ("c_collision", "min_distance", "min_ttc", "min_clearance", "uturn_started", "c_screening_candidate"):
        assert v2.output[key] == pytest.approx(v1.output[key]), key
    assert v2.output["c_event"] == v1.output["c_collision"]


def test_uturn_v2_accepts_innovation_law_proposals_and_rejects_idle_channels(tmp_path: Path) -> None:
    backend = DynamicsBackend(DynamicsBackendConfig(output_root=tmp_path, artifact_policy="summary"))
    base = {key: value for key, value in _uturn_inputs().items() if key != "sde_noise"}
    params = {"sde_noise": {"npc_acceleration_std": 0.2}}
    proposal = {"kind": "mixture", "defensive_weight": 0.5, "components": [
        {"weight": 1.0, "law": {"kind": "gaussian", "mean_rule": {
            "kind": "piecewise", "pieces": [{"time": [0.0, 2.0], "mode": "turn", "values": {"npc_acceleration": -0.2}}]}}}]}

    record = _run(backend, {**base, "dynamics_schema_version": 2, "model_params": params, "noise_q": proposal})
    idle = backend.run(TestCase("idle", "dynamics", "uturn", input={
        **base, "dynamics_schema_version": 2, "model_params": params,
        "noise_q": {"kind": "gaussian", "mean_rule": {"kind": "constant", "values": {"npc_heading": 0.1}}}}))

    assert record.status is RunStatus.SUCCESS
    assert record.meta["execution"]["noise_q"]["defensive_weight"] == 0.5
    assert idle.status is RunStatus.INVALID
    assert "without diffusion" in idle.meta["error_message"]


@pytest.mark.parametrize("model", ["ou_augmented", "nonlinear2d"])
def test_summary_artifacts_give_the_same_record_as_files(tmp_path: Path, model: str) -> None:
    event = ({"kind": "terminal_exceedance", "threshold": 0.1, "state": "x"} if model == "ou_augmented"
             else {"kind": "max_exceedance", "threshold": 0.5})
    inputs = {**COMMON, "model_id": model, "event": event,
              "noise_q": {"kind": "gaussian", "mean_rule": {"kind": "constant", "values": {}}}}
    files = _run(DynamicsBackend(DynamicsBackendConfig(output_root=tmp_path / "f")), inputs)
    summary = _run(DynamicsBackend(DynamicsBackendConfig(output_root=tmp_path / "s", artifact_policy="summary")), inputs)

    assert files.output == summary.output
    assert "trace_csv" in files.evidence and summary.evidence == {}
    assert not (tmp_path / "s").exists()
    assert "_inline_result" not in summary.meta["raw_run_meta"]


def test_unknown_artifact_policy_is_rejected() -> None:
    with pytest.raises(ValueError, match="artifact_policy"):
        DynamicsBackendConfig(artifact_policy="none")
