from targets.awsim.scenario_builders.swerve_builder import build_swerve_scenario


def test_build_swerve_scenario_maps_dynamic_and_fixed_params() -> None:
    captured: dict[str, object] = {}

    def fake_builder(**kwargs):
        captured.update(kwargs)
        return {"scenario": "swerve"}

    scenario = build_swerve_scenario(
        network="fake-network",
        dynamic_params={
            "dx0": 27.0,
            "ego_speed": 30.0,
            "npc_speed": 10.0,
        },
        fixed_params={
            "ego_init_lane": "355",
            "ego_init_offset": 10.0,
            "ego_goal_lane": "214",
            "ego_goal_offset": 10.0,
            "npc_init_lane": "205",
            "npc_init_offset": 60.0,
            "swerve_vy": 1.2,
            "swerve_ny": 1.8,
            "swerve_dis": 2.0,
            "swerve_right": True,
            "acceleration": 7.0,
        },
        scenario_profiles=None,
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builder=fake_builder,
    )

    assert scenario == {"scenario": "swerve"}
    assert captured["network"] == "fake-network"
    assert captured["ego_init_laneoffset"] == ("355", 10.0)
    assert captured["ego_goal_laneoffset"] == ("214", 10.0)
    assert captured["npc_init_laneoffset"] == ("205", 60.0)
    assert captured["_ego_speed"] == 30.0 / 3.6
    assert captured["_npc_speed"] == 10.0 / 3.6
    assert captured["swerve_vy"] == 1.2
    assert captured["dx0"] == 27.0
    assert captured["swerve_ny"] == 1.8
    assert captured["swerve_dis"] == 2.0
    assert captured["swerve_right"] is True
    assert captured["acceleration"] == 7.0
    assert captured["npc_start_speed_ratio"] == 1.0


def test_build_swerve_scenario_rejects_missing_params() -> None:
    try:
        build_swerve_scenario(
            network="fake-network",
            dynamic_params={"dx0": 27.0, "ego_speed": 30.0},
            fixed_params={
                "ego_init_lane": "355",
                "ego_init_offset": 10.0,
                "ego_goal_lane": "214",
                "ego_goal_offset": 10.0,
                "npc_init_lane": "205",
                "npc_init_offset": 60.0,
                "swerve_vy": 1.2,
                "swerve_ny": 1.8,
                "swerve_dis": 2.0,
                "swerve_right": True,
            },
            scenario_profiles=None,
            lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
            scenario_builder=lambda **kwargs: kwargs,
        )
    except KeyError as exc:
        assert str(exc) == "'Missing dynamic params: npc_speed'"
    else:
        raise AssertionError("Expected KeyError for missing npc_speed")


def test_build_swerve_scenario_uses_profiled_offsets_and_trigger_ratio() -> None:
    captured: dict[str, object] = {}

    build_swerve_scenario(
        network="fake-network",
        dynamic_params={
            "dx0": 35.0,
            "ego_speed": 39.0,
            "npc_speed": 14.7,
        },
        fixed_params={
            "ego_init_lane": "fallback-lane",
            "ego_init_offset": -1.0,
            "ego_goal_lane": "fallback-lane",
            "ego_goal_offset": -1.0,
            "npc_init_lane": "fallback-lane",
            "npc_init_offset": -1.0,
            "swerve_vy": 1.2,
            "swerve_ny": 1.8,
            "swerve_dis": 2.0,
            "swerve_right": False,
            "acceleration": 1.0,
        },
        scenario_profiles=[
            {
                "profile_id": "swerve_10",
                "npc_speed": 10.0,
                "npc_init_lane": "205",
                "acceleration": 7.0,
                "ego_speed_bands": [
                    {
                        "max_ego_speed": 35.0,
                        "ego_init_lane": "355",
                        "ego_init_offset": 10.0,
                        "ego_goal_lane": "214",
                        "ego_goal_offset": 10.0,
                        "npc_init_offset": 60.0,
                        "npc_start_speed_ratio": 0.898,
                    },
                    {
                        "max_ego_speed": float("inf"),
                        "ego_init_lane": "268",
                        "ego_init_offset": 0.0,
                        "ego_goal_lane": "214",
                        "ego_goal_offset": 26.0,
                        "npc_init_offset": 62.0,
                        "npc_start_speed_ratio": 0.916,
                    },
                ],
            },
            {
                "profile_id": "swerve_15",
                "npc_speed": 15.0,
                "npc_init_lane": "205",
                "acceleration": 7.0,
                "ego_speed_bands": [
                    {
                        "max_ego_speed": 35.0,
                        "ego_init_lane": "355",
                        "ego_init_offset": 10.0,
                        "ego_goal_lane": "214",
                        "ego_goal_offset": 10.0,
                        "npc_init_offset": 55.0,
                        "npc_start_speed_ratio": 0.898,
                    },
                    {
                        "max_ego_speed": float("inf"),
                        "ego_init_lane": "268",
                        "ego_init_offset": 0.0,
                        "ego_goal_lane": "214",
                        "ego_goal_offset": 26.0,
                        "npc_init_offset": 62.0,
                        "npc_start_speed_ratio": 0.916,
                    },
                ],
            },
        ],
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builder=lambda **kwargs: captured.update(kwargs) or kwargs,
    )

    assert captured["ego_init_laneoffset"] == ("268", 0.0)
    assert captured["ego_goal_laneoffset"] == ("214", 26.0)
    assert captured["npc_init_laneoffset"] == ("205", 62.0)
    assert captured["swerve_right"] is False
    assert captured["acceleration"] == 7.0
    assert captured["npc_start_speed_ratio"] == 0.916
