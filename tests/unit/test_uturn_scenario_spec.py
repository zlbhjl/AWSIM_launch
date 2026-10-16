import pytest

from scenario_specs import uturn
from targets.awsim.case_kinds import load_case_definition


def test_shared_uturn_spec_preserves_awsim_sample_ranges_and_jama_profiles() -> None:
    definition = load_case_definition(case_kind="uturn")

    assert uturn.SCENARIO_TYPE == "uturn"
    assert definition["scenario_type"] == uturn.SCENARIO_TYPE
    assert definition["param_ranges"] == {
        "dx0": (10.0, 25.0),
        "ego_speed": (30.0, 40.0),
        "npc_speed": (10.0, 25.0),
    }
    assert uturn.JAMA_PROFILES == {
        "human": {"t_delay": 0.75, "t_jerk": 0.6, "a_max": 7.58},
        "ai_aeb": {"t_delay": 0.1, "t_jerk": 0.1, "a_max": 8.33},
    }


@pytest.mark.parametrize(
    ("ego_speed_kmh", "expected_ratio"),
    [(30.0, 0.898), (35.0, 0.9083), (40.0, 0.916)],
)
def test_shared_npc_start_ratio_matches_awsim_uturn_profiles(
    ego_speed_kmh: float,
    expected_ratio: float,
) -> None:
    assert uturn.estimate_npc_start_speed_ratio(ego_speed=ego_speed_kmh) == expected_ratio


@pytest.mark.parametrize(
    ("sampled_speed_kmh", "expected_ratio"),
    [(30.0, 0.898), (32.5, 0.9083), (36.0, 0.9083), (37.5, 0.916), (40.0, 0.916)],
)
def test_shared_npc_start_ratio_uses_the_same_bands_as_awsim(
    sampled_speed_kmh: float,
    expected_ratio: float,
) -> None:
    assert (
        uturn.resolve_awsim_npc_start_speed_ratio(ego_speed=sampled_speed_kmh)
        == expected_ratio
    )


def test_shared_uturn_trigger_conditions_match_awsim_semantics() -> None:
    assert uturn.UTURN_START_CONDITION == "longitudinal_distance_to_ego <= dx0"
    assert uturn.should_start_uturn(
        longitudinal_distance_to_ego_m=15.0,
        dx0_m=15.0,
    )
    assert not uturn.should_start_uturn(
        longitudinal_distance_to_ego_m=15.0001,
        dx0_m=15.0,
    )

    target_speed_mps = 30.0 / 3.6
    threshold_mps = uturn.npc_start_speed_threshold_mps(
        ego_target_speed_mps=target_speed_mps,
        npc_start_speed_ratio=0.898,
    )
    assert uturn.should_start_npc(
        ego_speed_mps=threshold_mps,
        ego_target_speed_mps=target_speed_mps,
        npc_start_speed_ratio=0.898,
    )
    assert not uturn.should_start_npc(
        ego_speed_mps=threshold_mps - 1e-6,
        ego_target_speed_mps=target_speed_mps,
        npc_start_speed_ratio=0.898,
    )


def test_shared_uturn_spec_rejects_out_of_range_samples() -> None:
    uturn.validate_sampled_parameters(
        {"dx0": 10.0, "ego_speed": 35.0, "npc_speed": 25.0}
    )

    with pytest.raises(ValueError, match="dx0"):
        uturn.validate_sampled_parameters(
            {"dx0": 9.99, "ego_speed": 35.0, "npc_speed": 15.0}
        )
