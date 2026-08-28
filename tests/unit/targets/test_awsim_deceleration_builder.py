from targets.awsim.scenario_builders.deceleration_builder import build_deceleration_scenario


def test_build_deceleration_scenario_maps_dynamic_and_fixed_params() -> None:
    captured: dict[str, object] = {}

    def fake_builder(**kwargs):
        captured.update(kwargs)
        return {"scenario": "deceleration"}

    scenario = build_deceleration_scenario(
        network="fake-network",
        dynamic_params={"ego_speed": 30.0},
        fixed_params={
            "ego_init_lane": "111",
            "ego_init_offset": 0.0,
            "ego_goal_lane": "111",
            "ego_goal_offset": 210.0,
            "spawn_headway_sec": 2.0,
            "spawn_trigger_speed_ratio": 1.0,
            "npc_cruise_acceleration": 500.0,
            "npc_deceleration": 9.8,
            "decel_trigger_speed_ratio": 1.0,
        },
        scenario_profiles=None,
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builder=fake_builder,
    )

    assert scenario == {"scenario": "deceleration"}
    assert captured["network"] == "fake-network"
    assert captured["ego_init_laneoffset"] == ("111", 0.0)
    assert captured["ego_goal_laneoffset"] == ("111", 210.0)
    assert captured["_speed"] == 30.0 / 3.6
    assert captured["spawn_headway_sec"] == 2.0
    assert captured["spawn_trigger_speed_ratio"] == 1.0
    assert captured["npc_cruise_acceleration"] == 500.0
    assert captured["deceleration"] == 9.8
    assert captured["decel_trigger_speed_ratio"] == 1.0


def test_build_deceleration_scenario_rejects_missing_params() -> None:
    try:
        build_deceleration_scenario(
            network="fake-network",
            dynamic_params={},
            fixed_params={
                "ego_init_lane": "111",
                "ego_init_offset": 0.0,
                "ego_goal_lane": "111",
                "ego_goal_offset": 210.0,
                "spawn_headway_sec": 2.0,
                "spawn_trigger_speed_ratio": 1.0,
                "npc_cruise_acceleration": 500.0,
                "npc_deceleration": 9.8,
                "decel_trigger_speed_ratio": 1.0,
            },
            scenario_profiles=None,
            lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
            scenario_builder=lambda **kwargs: kwargs,
        )
    except KeyError as exc:
        assert str(exc) == "'Missing dynamic params: ego_speed'"
    else:
        raise AssertionError("Expected KeyError for missing ego_speed")


def test_build_deceleration_scenario_uses_speed_profile_goal_offset() -> None:
    captured: dict[str, object] = {}

    build_deceleration_scenario(
        network="fake-network",
        dynamic_params={"ego_speed": 39.0},
        fixed_params={
            "ego_init_lane": "fallback-lane",
            "ego_init_offset": -1.0,
            "ego_goal_lane": "fallback-lane",
            "ego_goal_offset": -1.0,
            "spawn_headway_sec": -1.0,
            "spawn_trigger_speed_ratio": -1.0,
            "npc_cruise_acceleration": 1.0,
            "npc_deceleration": 1.0,
            "decel_trigger_speed_ratio": -1.0,
        },
        scenario_profiles=[
            {
                "profile_id": "default",
                "ego_speed_bands": [
                    {
                        "max_ego_speed": 32.5,
                        "ego_init_lane": "111",
                        "ego_init_offset": 0.0,
                        "ego_goal_lane": "111",
                        "ego_goal_offset": 210.0,
                        "spawn_headway_sec": 2.0,
                        "spawn_trigger_speed_ratio": 1.0,
                        "npc_cruise_acceleration": 500.0,
                        "npc_deceleration": 9.8,
                        "decel_trigger_speed_ratio": 1.0,
                    },
                    {
                        "max_ego_speed": 37.5,
                        "ego_init_lane": "111",
                        "ego_init_offset": 0.0,
                        "ego_goal_lane": "111",
                        "ego_goal_offset": 240.0,
                        "spawn_headway_sec": 2.0,
                        "spawn_trigger_speed_ratio": 1.0,
                        "npc_cruise_acceleration": 500.0,
                        "npc_deceleration": 9.8,
                        "decel_trigger_speed_ratio": 1.0,
                    },
                    {
                        "max_ego_speed": float("inf"),
                        "ego_init_lane": "111",
                        "ego_init_offset": 0.0,
                        "ego_goal_lane": "111",
                        "ego_goal_offset": 280.0,
                        "spawn_headway_sec": 2.0,
                        "spawn_trigger_speed_ratio": 0.98,
                        "npc_cruise_acceleration": 400.0,
                        "npc_deceleration": 8.5,
                        "decel_trigger_speed_ratio": 0.99,
                    },
                ],
            }
        ],
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builder=lambda **kwargs: captured.update(kwargs) or kwargs,
    )

    assert captured["ego_init_laneoffset"] == ("111", 0.0)
    assert captured["ego_goal_laneoffset"] == ("111", 280.0)
    assert captured["spawn_headway_sec"] == 2.0
    assert captured["spawn_trigger_speed_ratio"] == 0.98
    assert captured["npc_cruise_acceleration"] == 400.0
    assert captured["deceleration"] == 8.5
    assert captured["decel_trigger_speed_ratio"] == 0.99
