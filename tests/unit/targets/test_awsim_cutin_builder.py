from targets.awsim.scenario_builders.cutin_builder import build_cutin_scenario


def test_build_cutin_scenario_maps_dynamic_and_fixed_params() -> None:
    captured: dict[str, object] = {}

    def fake_builder(**kwargs):
        captured.update(kwargs)
        return {"scenario": "cutin"}

    scenario = build_cutin_scenario(
        network="fake-network",
        dynamic_params={
            "dx0": 12.0,
            "ego_speed": 30.0,
            "npc_speed": 10.0,
        },
        fixed_params={
            "ego_init_lane": "111",
            "ego_init_offset": 0.0,
            "ego_goal_lane": "111",
            "ego_goal_offset": 150.0,
            "npc_init_lane": "112",
            "npc_init_offset": 80.0,
            "cutin_next_lane": "111",
            "cutin_vy": 1.4,
            "acceleration": 7.0,
        },
        scenario_profiles=None,
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builder=fake_builder,
    )

    assert scenario == {"scenario": "cutin"}
    assert captured["network"] == "fake-network"
    assert captured["ego_init_laneoffset"] == ("111", 0.0)
    assert captured["ego_goal_laneoffset"] == ("111", 150.0)
    assert captured["npc_init_laneoffset"] == ("112", 80.0)
    assert captured["cutin_next_lane"] == "111"
    assert captured["_ego_speed"] == 30.0 / 3.6
    assert captured["_npc_speed"] == 10.0 / 3.6
    assert captured["_cutin_vy"] == 1.4
    assert captured["dx0"] == 12.0
    assert captured["acceleration"] == 7.0


def test_build_cutin_scenario_forwards_optional_body_style() -> None:
    captured: dict[str, object] = {}

    build_cutin_scenario(
        network="fake-network",
        dynamic_params={
            "dx0": 12.0,
            "ego_speed": 30.0,
            "npc_speed": 10.0,
        },
        fixed_params={
            "ego_init_lane": "111",
            "ego_init_offset": 0.0,
            "ego_goal_lane": "111",
            "ego_goal_offset": 150.0,
            "npc_init_lane": "112",
            "npc_init_offset": 80.0,
            "cutin_next_lane": "111",
            "cutin_vy": 1.4,
            "acceleration": 7.0,
            "body_style": "small-car",
        },
        scenario_profiles=None,
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builder=lambda **kwargs: captured.update(kwargs) or kwargs,
    )

    assert captured["body_style"] == "small-car"


def test_build_cutin_scenario_rejects_missing_params() -> None:
    try:
        build_cutin_scenario(
            network="fake-network",
            dynamic_params={"dx0": 12.0, "ego_speed": 30.0},
            fixed_params={
                "ego_init_lane": "111",
                "ego_init_offset": 0.0,
                "ego_goal_lane": "111",
                "ego_goal_offset": 150.0,
                "npc_init_lane": "112",
                "npc_init_offset": 80.0,
                "cutin_next_lane": "111",
                "cutin_vy": 1.4,
            },
            scenario_profiles=None,
            lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
            scenario_builder=lambda **kwargs: kwargs,
        )
    except KeyError as exc:
        assert str(exc) == "'Missing dynamic params: npc_speed'"
    else:
        raise AssertionError("Expected KeyError for missing npc_speed")


def test_build_cutin_scenario_allows_dynamic_cutin_vy_override() -> None:
    captured: dict[str, object] = {}

    build_cutin_scenario(
        network="fake-network",
        dynamic_params={
            "dx0": 12.0,
            "ego_speed": 30.0,
            "npc_speed": 10.0,
            "cutin_vy": 1.6,
        },
        fixed_params={
            "ego_init_lane": "111",
            "ego_init_offset": 0.0,
            "ego_goal_lane": "111",
            "ego_goal_offset": 150.0,
            "npc_init_lane": "112",
            "npc_init_offset": 80.0,
            "cutin_next_lane": "111",
            "cutin_vy": 1.4,
            "acceleration": 7.0,
        },
        scenario_profiles=None,
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builder=lambda **kwargs: captured.update(kwargs) or kwargs,
    )

    assert captured["_cutin_vy"] == 1.6


def test_build_cutin_scenario_uses_speed_profile_offsets() -> None:
    captured: dict[str, object] = {}

    build_cutin_scenario(
        network="fake-network",
        dynamic_params={
            "dx0": 12.0,
            "ego_speed": 30.0,
            "npc_speed": 10.0,
        },
        fixed_params={
            "ego_init_lane": "fallback-lane",
            "ego_init_offset": -1.0,
            "ego_goal_lane": "fallback-lane",
            "ego_goal_offset": -1.0,
            "npc_init_lane": "fallback-lane",
            "npc_init_offset": -1.0,
            "cutin_next_lane": "fallback-lane",
            "cutin_vy": 1.4,
            "acceleration": 1.0,
        },
        scenario_profiles=[
            {
                "profile_id": "cutin_10",
                "npc_speed": 10.0,
                "npc_init_lane": "112",
                "cutin_next_lane": "111",
                "acceleration": 7.0,
                "ego_speed_bands": [
                    {
                        "max_ego_speed": 35.0,
                        "ego_init_lane": "111",
                        "ego_init_offset": 0.0,
                        "ego_goal_lane": "111",
                        "ego_goal_offset": 150.0,
                        "npc_init_offset": 80.0,
                    }
                ],
            }
        ],
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builder=lambda **kwargs: captured.update(kwargs) or kwargs,
    )

    assert captured["ego_init_laneoffset"] == ("111", 0.0)
    assert captured["ego_goal_laneoffset"] == ("111", 150.0)
    assert captured["npc_init_laneoffset"] == ("112", 80.0)
    assert captured["cutin_next_lane"] == "111"
    assert captured["acceleration"] == 7.0
