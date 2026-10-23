"""Scalar OU model (model A) through the registered-model path of the dynamics target."""

from __future__ import annotations

import json
from math import log, sqrt
from pathlib import Path

import numpy as np
import pytest

from contracts.execution import RunStatus, TestCase
from targets.dynamics.backend import DynamicsBackend, DynamicsBackendConfig
from targets.dynamics.models.exact import (
    ou_euler_terminal_variance,
    ou_terminal_exceedance_probability,
)
from targets.dynamics.models.ou import OUScalarAdapter
from targets.dynamics.models.registry import get_model_adapter, model_ids
from targets.dynamics.result_interpreter import DynamicsResultInterpreter


def _inputs(**changes: object) -> dict[str, object]:
    inputs: dict[str, object] = {
        "model_id": "ou_scalar",
        "model_params": {"theta": 1.0, "sigma": 1.0},
        "x0": 0.0,
        "solver_kind": "sde",
        "sde_seed": 7,
        "sde_dt_sec": 0.01,
        "horizon_sec": 1.0,
        "event": {"kind": "terminal_exceedance", "threshold": 1.0, "side": "upper"},
    }
    inputs.update(changes)
    return inputs


def test_model_a_discrete_variance_differs_from_continuous_time() -> None:
    variance = ou_euler_terminal_variance(theta=1.0, sigma=1.0, h=0.01, steps=100)

    assert variance == pytest.approx(0.4351860930, rel=1e-9)
    assert variance != pytest.approx((1 - np.exp(-2.0)) / 2, rel=1e-4)


def test_exceedance_probabilities_of_model_a() -> None:
    std = sqrt(ou_euler_terminal_variance(theta=1.0, sigma=1.0, h=0.01, steps=100))

    upper = ou_terminal_exceedance_probability(theta=1.0, sigma=1.0, h=0.01, steps=100, threshold=3 * std)
    both = ou_terminal_exceedance_probability(
        theta=1.0, sigma=1.0, h=0.01, steps=100, threshold=3 * std, side="two_sided"
    )

    assert upper == pytest.approx(1.3498980316e-3, rel=1e-8)
    assert both == pytest.approx(2 * upper)


def test_registry_lists_the_models() -> None:
    assert model_ids() == ("nonlinear2d", "ou_augmented", "ou_scalar", "uturn")
    assert get_model_adapter("uturn").legacy_execution is True
    with pytest.raises(ValueError, match="unsupported dynamics model_id"):
        get_model_adapter("lane_change")


def test_euler_path_matches_a_hand_rolled_scheme() -> None:
    resolved = OUScalarAdapter().resolve(_inputs())
    trajectory = resolved.solve()

    rng = np.random.default_rng(7)
    x = 0.0
    for _ in range(100):
        x = x - 1.0 * x * 0.01 + 1.0 * sqrt(0.01) * rng.normal(size=1)[0]

    assert len(trajectory.times_sec) == 101
    assert trajectory.states[-1, 0] == pytest.approx(x, abs=1e-12)


def test_q_equal_to_p_has_zero_log_ratio() -> None:
    resolved = OUScalarAdapter().resolve(
        _inputs(noise_path_q={"distribution": "gaussian_drift", "channel_drifts": {"w": 0.0}})
    )
    trajectory = resolved.solve()

    assert trajectory.noise_log_p == pytest.approx(trajectory.noise_log_q)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"sde_dt_sec": None}, "sde_dt_sec"),
        ({"event": {"kind": "first_passage", "threshold": 1.0}}, "event.kind"),
        ({"event": {"kind": "terminal_exceedance", "threshold": 1.0, "side": "lower"}}, "event.side"),
        ({"noise_path_q": {"distribution": "gaussian_drift", "channel_drifts": {"npc_heading": 1.0}}}, "unsupported Brownian channel"),
        ({"solver_kind": "ode"}, "solver_kind"),
        ({"sde_seed": 1.5}, "sde_seed"),
    ],
)
def test_invalid_inputs_are_rejected(changes: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        OUScalarAdapter().resolve(_inputs(**changes))


def _sampling(log_weight: float = 0.0) -> dict[str, object]:
    return {
        "plan_id": "ou_a",
        "target_measure_id": "ou_p",
        "sampling_mode": "manual_proposal",
        "proposal_id": "q1",
        "importance_weight_status": "available",
        "log_p": log_weight,
        "log_q": 0.0,
        "log_importance_weight": log_weight,
    }


def test_backend_and_interpreter_judge_the_event_and_merge_the_path_weight(tmp_path: Path) -> None:
    case = TestCase(
        case_id="ou-1",
        target="dynamics",
        case_kind="ou_scalar",
        input=_inputs(noise_path_q={"distribution": "gaussian_drift", "channel_drifts": {"w": 1.5}}),
        meta={"sampling": _sampling(log(2.0))},
    )

    raw = DynamicsBackend(DynamicsBackendConfig(output_root=tmp_path)).run(case)
    record = DynamicsResultInterpreter().interpret_raw_run_result(raw)

    assert raw.status is RunStatus.SUCCESS
    payload = json.loads(Path(raw.evidence["raw_result_json"]).read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["state_names"] == ["x"]
    assert record.status is RunStatus.SUCCESS
    assert record.output["c_event"] == int(record.output["x_terminal"] > 1.0)
    assert record.output["step_count"] == 100
    sampling = record.meta["sampling"]
    noise_log_ratio = raw.meta["sde_path_log_p"] - raw.meta["sde_path_log_q"]
    assert sampling["log_importance_weight"] == pytest.approx(log(2.0) + noise_log_ratio)
    assert record.meta["model_id"] == "ou_scalar"


def test_backend_reports_invalid_inputs_and_unknown_models(tmp_path: Path) -> None:
    backend = DynamicsBackend(DynamicsBackendConfig(output_root=tmp_path))
    unknown = TestCase("x", "dynamics", "x", input={"model_id": "lane_change"})
    invalid = TestCase("y", "dynamics", "ou_scalar", input=_inputs(horizon_sec=-1.0))

    assert backend.run(unknown).status is RunStatus.INVALID
    assert backend.run(invalid).status is RunStatus.INVALID
