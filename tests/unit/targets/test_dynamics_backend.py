import json
import math
from pathlib import Path

import numpy as np
import pytest

from contracts.execution import RawRunResult, RunStatus, TestCase as ExecutionTestCase
from targets.dynamics.backend import DynamicsBackend, DynamicsBackendConfig
from targets.dynamics.calibration import (
    AUTOWARE171_UTURN_CONTROLLER_KIND,
    predict_autoware171_uturn_start_geometry,
)
from targets.dynamics.profile import (
    AWSIM_ALIGNMENT_MODE,
    build_execution_profile,
)
from targets.dynamics.result_interpreter import (
    DynamicsResultInterpreter,
    _cvm_obb_ttc,
    _obb_clearances,
    _safety_metrics,
)


def _test_case(output_root: Path) -> ExecutionTestCase:
    return ExecutionTestCase(
        case_id="dynamics_uturn_1",
        target="dynamics",
        case_kind="uturn",
        input={
            "dx0": 15.0,
            "ego_speed": 36.0,
            "npc_speed": 18.0,
            "initial_state": {
                "ego": {"x_m": 0.0, "y_m": 0.0, "heading_rad": 0.0, "speed_mps": 10.0},
                "npc": {"x_m": 30.0, "y_m": 0.0, "heading_rad": math.pi, "speed_mps": 5.0},
            },
            "turn_radius_m": 5.0,
            "horizon_sec": 5.0,
            "max_step_sec": 0.01,
            "output_root": str(output_root),
        },
    )


def test_dynamics_backend_and_interpreter_complete_single_uturn_case(tmp_path: Path) -> None:
    backend = DynamicsBackend(DynamicsBackendConfig(output_root=tmp_path / "default"))
    raw = backend.run(_test_case(tmp_path / "runs"))

    assert raw.status is RunStatus.SUCCESS
    raw_path = Path(raw.evidence["raw_result_json"])
    trace_path = Path(raw.evidence["trace_csv"])
    assert raw_path.exists()
    assert trace_path.exists()
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    assert payload["model_id"] == "uturn"
    assert payload["events"]["uturn_start_time_sec"] is not None

    record = DynamicsResultInterpreter().interpret_raw_run_result(raw)

    assert record.status is RunStatus.SUCCESS
    assert record.target == "dynamics"
    assert record.output["c_collision"] in {0, 1}
    assert record.output["min_ttc"] >= 0.0
    assert record.output["theory_zone_a"] in {"A", "B", "C", "D"}
    assert record.output["c_npc_stuck"] == 0
    assert record.meta["schema_version"] == 1
    assert record.meta["source_module"] == "targets.dynamics.result_interpreter"


def test_dynamics_backend_normalizes_invalid_sample_to_invalid_status(tmp_path: Path) -> None:
    result = DynamicsBackend().run(
        ExecutionTestCase(
            case_id="dynamics_invalid",
            target="dynamics",
            case_kind="uturn",
            input={
                "dx0": 9.0,
                "ego_speed": 35.0,
                "npc_speed": 15.0,
                "output_root": str(tmp_path),
            },
        )
    )

    assert result.status is RunStatus.INVALID
    assert "outside" in str(result.meta["error_message"])


def test_dynamics_backend_persists_sde_provenance(tmp_path: Path) -> None:
    raw = DynamicsBackend().run(
        ExecutionTestCase(
            case_id="dynamics_sde", target="dynamics", case_kind="uturn",
            input={
                "dx0": 15.0, "ego_speed": 36.0, "npc_speed": 18.0,
                "solver_kind": "sde", "sde_seed": 17, "sde_dt_sec": 0.01,
                "max_step_sec": 0.01,
                "sde_noise": {"npc_acceleration_std": 0.2},
                "output_root": str(tmp_path),
            },
        )
    )

    assert raw.status is RunStatus.SUCCESS
    payload = json.loads(Path(raw.evidence["raw_result_json"]).read_text(encoding="utf-8"))
    assert payload["execution"]["solver_kind"] == "sde"
    assert payload["execution"]["sde_seed"] == 17
    record = DynamicsResultInterpreter().interpret_raw_run_result(raw)
    assert record.status is RunStatus.SUCCESS
    assert record.meta["solver_kind"] == "sde"


def test_zero_noise_sde_records_ode_reduction(tmp_path: Path) -> None:
    profile = build_execution_profile(
        {
            "dx0": 15.0,
            "ego_speed": 36.0,
            "npc_speed": 18.0,
            "solver_kind": "sde",
            "sde_seed": 9,
            "sde_noise": {},
        },
        default_output_root=tmp_path,
    )

    assert profile.to_meta()["solver_kind"] == "sde"
    assert profile.to_meta()["effective_solver_kind"] == "ode"
    assert profile.to_meta()["zero_noise_reduction"] is True


def test_dynamics_interpreter_normalizes_missing_artifact_to_analysis_error() -> None:
    record = DynamicsResultInterpreter().interpret_raw_run_result(
        RawRunResult(
            case_id="dynamics_missing_artifact",
            target="dynamics",
            case_kind="uturn",
            status=RunStatus.SUCCESS,
        )
    )

    assert record.status is RunStatus.ANALYSIS_ERROR
    assert record.meta["error_message"] == "missing_raw_result_json"


def test_default_profile_starts_at_the_awsim_uturn_trigger(tmp_path: Path) -> None:
    profile = build_execution_profile(
        {"dx0": 15.0, "ego_speed": 36.0, "npc_speed": 18.0},
        default_output_root=tmp_path,
    )

    config = profile.ode_config
    calibrated, _ = predict_autoware171_uturn_start_geometry(
        dx0=15.0,
        ego_speed=36.0,
        npc_speed=18.0,
    )
    assert profile.alignment_mode == AWSIM_ALIGNMENT_MODE
    assert config.npc_initial.x_m - config.ego_initial.x_m == pytest.approx(
        calibrated["longitudinal_center_m"]
    )
    assert config.longitudinal_clearance_offset_m == pytest.approx(
        calibrated["longitudinal_center_m"] - 15.0
    )
    assert config.npc_initial.y_m == pytest.approx(calibrated["lateral_center_m"])
    assert config.turn_radius_m == pytest.approx(calibrated["turn_radius_m"])
    assert config.turn_angle_rad == pytest.approx(calibrated["turn_angle_rad"])
    assert config.turn_angle_rad < math.pi
    assert config.npc_turn_reference_offset_m == pytest.approx(1.12)
    assert config.npc_start_speed_ratio < 1.0
    assert config.ego_initial.speed_mps == pytest.approx(calibrated["ego_speed_mps"])
    assert config.npc_initial.speed_mps == pytest.approx(calibrated["npc_speed_mps"])
    assert profile.collision_model == "obb"
    assert profile.controller_kind == "jama_ai_aeb"


def test_autoware_calibrated_controller_uses_trace_fitted_response(tmp_path: Path) -> None:
    profile = build_execution_profile(
        {
            "dx0": 15.0,
            "ego_speed": 36.0,
            "npc_speed": 18.0,
            "controller_kind": AUTOWARE171_UTURN_CONTROLLER_KIND,
        },
        default_output_root=tmp_path,
    )

    assert profile.ode_config.jama_profile == {
        "t_delay": 0.616189,
        "t_jerk": 0.095315,
        "a_max": 3.01339,
    }
    assert profile.controller_provenance["calibration_trace_count"] == 56
    assert profile.controller_provenance["validation_trace_count"] == 24


def _single_state_trace(*, npc_x: float, npc_y: float) -> dict[str, np.ndarray]:
    return {
        "ego_x_m": np.array([0.0]),
        "ego_y_m": np.array([0.0]),
        "ego_heading_rad": np.array([0.0]),
        "ego_speed_mps": np.array([0.0]),
        "npc_x_m": np.array([npc_x]),
        "npc_y_m": np.array([npc_y]),
        "npc_heading_rad": np.array([0.0]),
        "npc_speed_mps": np.array([0.0]),
    }


def test_obb_collision_uses_vehicle_length_instead_of_center_threshold() -> None:
    geometry = {
        "ego": {"length": 4.886, "width": 2.186},
        "npc": {"length": 4.64, "width": 1.94},
    }
    trace = _single_state_trace(npc_x=4.7, npc_y=0.0)

    obb = _safety_metrics(
        trace,
        collision_distance_m=4.5,
        collision_model="obb",
        vehicle_geometry_m=geometry,
    )
    center = _safety_metrics(trace, collision_distance_m=4.5)

    assert obb["c_collision"] == 1
    assert center["c_collision"] == 0


def test_obb_collision_rejects_side_gap_that_center_threshold_marks_collision() -> None:
    geometry = {
        "ego": {"length": 4.886, "width": 2.186},
        "npc": {"length": 4.64, "width": 1.94},
    }
    trace = _single_state_trace(npc_x=0.0, npc_y=2.1)

    obb = _safety_metrics(
        trace,
        collision_distance_m=4.5,
        collision_model="obb",
        vehicle_geometry_m=geometry,
    )
    center = _safety_metrics(trace, collision_distance_m=4.5)

    assert obb["c_collision"] == 0
    assert center["c_collision"] == 1


def test_cvm_obb_ttc_matches_awsim_discrete_projection_semantics() -> None:
    geometry = {
        "ego": {"length": 4.886, "width": 2.186},
        "npc": {"length": 4.64, "width": 1.94},
    }
    trace = _single_state_trace(npc_x=8.0, npc_y=0.0)
    trace["ego_speed_mps"] = np.array([1.0])

    ttc = _cvm_obb_ttc(trace, geometry)

    assert ttc[0] == pytest.approx(3.3)


def test_obb_clearance_is_body_gap_and_zero_on_overlap() -> None:
    geometry = {
        "ego": {"length": 4.0, "width": 2.0},
        "npc": {"length": 4.0, "width": 2.0},
    }
    trace = {
        "ego_x_m": np.array([0.0, 0.0, 0.0]),
        "ego_y_m": np.array([0.0, 0.0, 0.0]),
        "ego_heading_rad": np.array([0.0, 0.0, 0.0]),
        "npc_x_m": np.array([7.0, 1.0, 4.0]),
        "npc_y_m": np.array([0.0, 0.0, 3.0]),
        "npc_heading_rad": np.array([0.0, 0.0, math.pi / 2.0]),
    }

    clearance = _obb_clearances(trace, geometry)

    assert clearance == pytest.approx([3.0, 0.0, 1.0])


def test_obb_metrics_report_clearance_separately_from_center_distance() -> None:
    geometry = {
        "ego": {"length": 4.886, "width": 2.186},
        "npc": {"length": 4.64, "width": 1.94},
    }
    trace = _single_state_trace(npc_x=0.0, npc_y=2.9)

    metrics = _safety_metrics(
        trace,
        collision_distance_m=4.5,
        collision_model="obb",
        vehicle_geometry_m=geometry,
    )

    assert metrics["min_distance"] == pytest.approx(2.9)
    assert metrics["min_clearance"] == pytest.approx(2.9 - (2.186 + 1.94) / 2.0)


def test_screening_margin_is_calibrated_only_for_autoware_controller(tmp_path: Path) -> None:
    def profile(**extra):
        return build_execution_profile(
            {"dx0": 15.0, "ego_speed": 36.0, "npc_speed": 18.0, **extra},
            default_output_root=tmp_path,
        )

    calibrated = profile(controller_kind=AUTOWARE171_UTURN_CONTROLLER_KIND)
    jama = profile()
    override = profile(screening_clearance_margin_m=0.5)

    assert calibrated.screening_clearance_margin_m == pytest.approx(1.1)
    assert calibrated.screening_provenance["calibrated_for_current_controller"] is True
    assert jama.screening_provenance["calibrated_for_current_controller"] is False
    assert override.screening_clearance_margin_m == pytest.approx(0.5)
    assert override.screening_provenance == {"source": "input_override"}
    with pytest.raises(ValueError, match="screening_clearance_margin_m"):
        profile(screening_clearance_margin_m=-0.1)


def test_interpreter_reports_both_decision_modes(tmp_path: Path) -> None:
    case = _test_case(tmp_path)
    case = ExecutionTestCase(
        case_id=case.case_id,
        target=case.target,
        case_kind=case.case_kind,
        input={**case.input, "controller_kind": AUTOWARE171_UTURN_CONTROLLER_KIND},
    )
    raw = DynamicsBackend(DynamicsBackendConfig(output_root=tmp_path)).run(case)

    record = DynamicsResultInterpreter().interpret_raw_run_result(raw)

    assert record.status is RunStatus.SUCCESS
    expected = int(record.output["min_clearance"] < 1.1)
    assert record.output["c_screening_candidate"] == expected
    # Screening is never stricter than judgment: every overlap is a candidate.
    assert record.output["c_screening_candidate"] >= record.output["c_collision"]
    assert record.meta["screening_clearance_margin_m"] == pytest.approx(1.1)
