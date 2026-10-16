import math

import numpy as np
import pytest

from scenario_specs.uturn import JAMA_PROFILES
from targets.dynamics.models.uturn import (
    UTurnODEConfig,
    VehicleState,
    simulate_uturn,
)
from targets.dynamics.models.uturn_sde import (
    UTurnSDEConfig,
    UTurnSDENoise,
    simulate_uturn_sde,
)


def _config(**overrides) -> UTurnODEConfig:
    values = {
        "ego_initial": VehicleState(0.0, 0.0, 0.0, 10.0),
        "npc_initial": VehicleState(30.0, 0.0, math.pi, 10.0),
        "dx0_m": 15.0,
        "ego_target_speed_mps": 10.0,
        "npc_target_speed_mps": 10.0,
        "turn_radius_m": 10.0,
        "horizon_sec": 5.0,
        "max_step_sec": 0.01,
        "jama_profile": JAMA_PROFILES["ai_aeb"],
    }
    values.update(overrides)
    return UTurnODEConfig(**values)


def test_uturn_ode_matches_constant_velocity_lane_motion_before_trigger() -> None:
    result = simulate_uturn(
        _config(
            npc_initial=VehicleState(100.0, 0.0, math.pi, 10.0),
            dx0_m=15.0,
            horizon_sec=1.0,
        )
    )

    assert result.uturn_start_time_sec is None
    assert result.npc_start_time_sec == 0.0
    assert result.times_sec[-1] == pytest.approx(1.0)
    assert result.states[-1, 0] == pytest.approx(10.0, abs=1e-7)
    assert result.states[-1, 4] == pytest.approx(90.0, abs=1e-7)
    assert result.states[-1, 7] == pytest.approx(10.0, abs=1e-7)


def test_uturn_ode_switches_to_default_half_circle_and_applies_jama_braking() -> None:
    result = simulate_uturn(_config())

    assert result.uturn_start_time_sec == pytest.approx(0.75, abs=1e-6)
    assert result.uturn_end_time_sec == pytest.approx(0.75 + math.pi, abs=2e-3)
    assert result.states[-1, 6] == pytest.approx(0.0, abs=2e-3)
    assert result.states[-1, 7] == pytest.approx(10.0, abs=1e-6)
    assert result.states[-1, 3] == pytest.approx(0.0, abs=2e-3)
    assert result.ego_speeds_mps.min() >= 0.0
    assert np.all(np.diff(result.times_sec) > 0.0)


def test_uturn_ode_rejects_invalid_physical_configuration() -> None:
    with pytest.raises(ValueError, match="turn_radius_m"):
        simulate_uturn(_config(turn_radius_m=0.0))
    with pytest.raises(ValueError, match="turn_angle_rad"):
        simulate_uturn(_config(turn_angle_rad=0.0))
    with pytest.raises(ValueError, match="npc_turn_reference_offset_m"):
        simulate_uturn(_config(npc_turn_reference_offset_m=-1.0))


def test_uturn_ode_stops_turning_at_configured_angle() -> None:
    angle = math.radians(170.0)
    result = simulate_uturn(_config(turn_angle_rad=angle, horizon_sec=6.0))

    assert result.uturn_end_time_sec == pytest.approx(0.75 + angle, abs=2e-3)
    assert result.states[-1, 6] == pytest.approx(math.pi - angle, abs=2e-3)


def _turn_rows(result) -> np.ndarray:
    start, end = result.uturn_start_time_sec, result.uturn_end_time_sec
    return (result.times_sec >= start) & (result.times_sec <= end)


def test_uturn_ode_turns_reference_point_on_circle_when_center_is_offset() -> None:
    offset = 1.12
    radius = 3.4
    result = simulate_uturn(
        _config(
            turn_radius_m=radius,
            npc_turn_reference_offset_m=offset,
            npc_initial=VehicleState(30.0, 0.0, math.pi, 5.0),
            npc_target_speed_mps=5.0,
            horizon_sec=4.0,
        )
    )
    rows = _turn_rows(result)
    heading = result.states[rows, 6]
    reference = result.states[rows][:, [4, 5]] - offset * np.column_stack(
        (np.cos(heading), np.sin(heading))
    )
    # turn_direction=-1 from heading pi: the circle center lies at +y of the
    # reference point at the trigger.
    circle_center = reference[0] + np.array([0.0, radius])

    assert np.linalg.norm(reference - circle_center, axis=1) == pytest.approx(
        np.full(len(reference), radius), abs=1e-4
    )
    center_path = result.states[rows][:, [4, 5]]
    assert np.max(np.linalg.norm(center_path - circle_center, axis=1)) == pytest.approx(
        math.hypot(radius, offset), abs=1e-4
    )


def test_uturn_sde_keeps_offset_turn_geometry_without_heading_noise() -> None:
    ode = _config(
        turn_radius_m=3.4,
        npc_turn_reference_offset_m=1.12,
        npc_initial=VehicleState(30.0, 0.0, math.pi, 5.0),
        npc_target_speed_mps=5.0,
        horizon_sec=4.0,
    )
    deterministic = simulate_uturn(ode)
    stochastic = simulate_uturn_sde(
        UTurnSDEConfig(
            ode_config=ode,
            seed=3,
            dt_sec=0.001,
            noise=UTurnSDENoise(ego_brake_acceleration_std=0.1),
        )
    )

    expected_npc = deterministic.states[-1, [4, 5, 6]]
    assert stochastic.states[-1, [4, 5, 6]] == pytest.approx(expected_npc, abs=0.03)


def test_uturn_sde_is_reproducible_for_same_seed_and_varies_for_other_seed() -> None:
    config = UTurnSDEConfig(
        ode_config=_config(horizon_sec=2.0, max_step_sec=0.01),
        seed=7,
        dt_sec=0.01,
        noise=UTurnSDENoise(npc_acceleration_std=0.2),
    )

    first = simulate_uturn_sde(config)
    second = simulate_uturn_sde(config)
    other = simulate_uturn_sde(
        UTurnSDEConfig(config.ode_config, seed=8, dt_sec=0.01, noise=config.noise)
    )

    assert np.array_equal(first.times_sec, second.times_sec)
    assert np.array_equal(first.states, second.states)
    assert not np.array_equal(first.states, other.states)
    assert first.npc_speeds_mps.min() >= 0.0


def test_uturn_sde_rejects_step_larger_than_declared_max_step() -> None:
    with pytest.raises(ValueError, match="dt_sec"):
        simulate_uturn_sde(
            UTurnSDEConfig(
                ode_config=_config(max_step_sec=0.01), seed=1, dt_sec=0.02,
                noise=UTurnSDENoise(),
            )
        )


def test_zero_noise_sde_is_exactly_the_deterministic_ode() -> None:
    ode_config = _config(horizon_sec=5.0, max_step_sec=0.02)
    ode_result = simulate_uturn(ode_config)
    sde_result = simulate_uturn_sde(
        UTurnSDEConfig(
            ode_config=ode_config,
            seed=123,
            dt_sec=0.01,
            noise=UTurnSDENoise(),
        )
    )

    assert np.array_equal(sde_result.times_sec, ode_result.times_sec)
    assert np.array_equal(sde_result.states, ode_result.states)
    assert sde_result.uturn_start_time_sec == ode_result.uturn_start_time_sec
    assert sde_result.uturn_end_time_sec == ode_result.uturn_end_time_sec


def test_trigger_aligned_profile_starts_uturn_at_time_zero_despite_rounding() -> None:
    # In the AWSIM-aligned profile the trigger guard is 0 at t=0 up to rounding
    # (here -1.8e-15 after the reference-point conversion).  The U-turn must
    # still start at t=0 instead of never firing.
    from targets.dynamics.models.uturn import UTurnSystem
    from targets.dynamics.profile import build_execution_profile

    profile = build_execution_profile(
        {
            "dx0": 10.247914532927936,
            "ego_speed": 38.13270239200273,
            "npc_speed": 23.691333659165828,
            "controller_kind": "autoware171_uturn_calibrated",
        },
        default_output_root="unused",
    )
    system = UTurnSystem(profile.ode_config)
    assert abs(system._uturn_trigger_guard(0.0, system.initial_state())) < 1e-12

    result = simulate_uturn(profile.ode_config)

    assert result.uturn_start_time_sec == 0.0
    assert result.uturn_end_time_sec is not None
