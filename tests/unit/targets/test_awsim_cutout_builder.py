from targets.awsim.scenario_builders.cutout_builder import build_cutout_scenario


def test_build_cutout_scenario_maps_dynamic_and_fixed_params() -> None:
    captured: dict[str, object] = {}

    def fake_builder(**kwargs):
        captured.update(kwargs)
        return {"scenario": "cutout"}

    scenario = build_cutout_scenario(
        network="fake-network",
        dynamic_params={
            "ego_speed": 30.0,
            "cutout_vy": 1.5,
            "dx_f": 10.0,
        },
        fixed_params={
            "ego_init_lane": "111",
            "ego_init_offset": 0.0,
            "ego_goal_lane": "111",
            "ego_goal_offset": 210.0,
            "cutout_next_lane": "112",
        },
        scenario_profiles=None,
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builder=fake_builder,
    )

    assert scenario == {"scenario": "cutout"}
    assert captured["network"] == "fake-network"
    assert captured["ego_init_laneoffset"] == ("111", 0.0)
    assert captured["ego_goal_laneoffset"] == ("111", 210.0)
    assert captured["cutout_next_lane"] == "112"
    assert captured["_speed"] == 30.0 / 3.6
    assert captured["vy"] == 1.5
    assert captured["dx_f"] == 10.0
    assert captured["spawn_trigger_speed_ratio"] == 1.0


def test_build_cutout_scenario_forwards_optional_body_style() -> None:
    captured: dict[str, object] = {}

    build_cutout_scenario(
        network="fake-network",
        dynamic_params={
            "ego_speed": 35.0,
            "cutout_vy": 1.6,
            "dx_f": 8.0,
        },
        fixed_params={
            "ego_init_lane": "111",
            "ego_init_offset": 0.0,
            "ego_goal_lane": "111",
            "ego_goal_offset": 210.0,
            "cutout_next_lane": "112",
            "body_style": "small-car",
        },
        scenario_profiles=None,
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builder=lambda **kwargs: captured.update(kwargs) or kwargs,
    )

    assert captured["body_style"] == "small-car"


def test_build_cutout_scenario_rejects_missing_params() -> None:
    try:
        build_cutout_scenario(
            network="fake-network",
            dynamic_params={"ego_speed": 30.0, "cutout_vy": 1.5},
            fixed_params={
                "ego_init_lane": "111",
                "ego_init_offset": 0.0,
                "ego_goal_lane": "111",
                "ego_goal_offset": 210.0,
                "cutout_next_lane": "112",
            },
            scenario_profiles=None,
            lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
            scenario_builder=lambda **kwargs: kwargs,
        )
    except KeyError as exc:
        assert str(exc) == "'Missing dynamic params: dx_f'"
    else:
        raise AssertionError("Expected KeyError for missing dx_f")


def test_build_cutout_scenario_uses_speed_profile_goal_offset() -> None:
    captured: dict[str, object] = {}

    build_cutout_scenario(
        network="fake-network",
        dynamic_params={
            "ego_speed": 39.0,
            "cutout_vy": 1.5,
            "dx_f": 10.0,
        },
        fixed_params={
            "ego_init_lane": "fallback-lane",
            "ego_init_offset": -1.0,
            "ego_goal_lane": "fallback-lane",
            "ego_goal_offset": -1.0,
            "cutout_next_lane": "fallback-lane",
        },
        scenario_profiles=[
            {
                "profile_id": "default",
                "cutout_next_lane": "112",
                "ego_speed_bands": [
                    {
                        "max_ego_speed": 32.5,
                        "ego_init_lane": "111",
                        "ego_init_offset": 0.0,
                        "ego_goal_lane": "111",
                        "ego_goal_offset": 180.0,
                        "spawn_trigger_speed_ratio": 0.94,
                    },
                    {
                        "max_ego_speed": 37.5,
                        "ego_init_lane": "111",
                        "ego_init_offset": 0.0,
                        "ego_goal_lane": "111",
                        "ego_goal_offset": 210.0,
                        "spawn_trigger_speed_ratio": 0.93,
                    },
                    {
                        "max_ego_speed": float("inf"),
                        "ego_init_lane": "111",
                        "ego_init_offset": 0.0,
                        "ego_goal_lane": "111",
                        "ego_goal_offset": 240.0,
                        "spawn_trigger_speed_ratio": 0.92,
                    },
                ],
            }
        ],
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builder=lambda **kwargs: captured.update(kwargs) or kwargs,
    )

    assert captured["ego_init_laneoffset"] == ("111", 0.0)
    assert captured["ego_goal_laneoffset"] == ("111", 240.0)
    assert captured["cutout_next_lane"] == "112"
    assert captured["spawn_trigger_speed_ratio"] == 0.92
