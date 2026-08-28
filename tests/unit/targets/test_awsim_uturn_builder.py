from targets.awsim.scenario_builders.uturn_builder import build_uturn_scenario


def test_build_uturn_scenario_uses_nearest_legacy_profile_for_npc_speed() -> None:
    captured: dict[str, object] = {}

    def fake_lane_offset(lane_id: str, offset: float) -> dict[str, object]:
        return {"lane_id": lane_id, "offset": offset}

    def fake_builder(**kwargs):
        captured.update(kwargs)
        return {"scenario": "uturn"}

    scenario = build_uturn_scenario(
        network="fake-network",
        dynamic_params={"dx0": 15.0, "ego_speed": 31.0, "npc_speed": 10.2},
        fixed_params={
            "ego_init_lane": "514",
            "ego_goal_lane": "516",
            "ego_goal_offset": 20,
            "npc_init_lane": "521",
            "npc_init_offset": 32,
            "uturn_next_lane": "511",
            "acceleration": 7.0,
        },
        scenario_profiles=[
            {
                "profile_id": "right_10",
                "npc_speed": 10.0,
                "npc_init_lane": "521",
                "npc_init_offset": 32.0,
                "uturn_next_lane": "511",
                "acceleration": 7.0,
                "ego_speed_bands": [
                    {
                        "max_ego_speed": 32.5,
                        "ego_init_lane": "514",
                        "ego_init_offset": 30.0,
                        "ego_goal_lane": "516",
                        "ego_goal_offset": 20.0,
                        "npc_start_speed_ratio": 0.898,
                    }
                ],
            },
            {
                "profile_id": "right_15",
                "npc_speed": 15.0,
                "npc_init_lane": "521",
                "npc_init_offset": 32.0,
                "uturn_next_lane": "511",
                "acceleration": 7.0,
                "ego_speed_bands": [
                    {
                        "max_ego_speed": 32.5,
                        "ego_init_lane": "514",
                        "ego_init_offset": 38.0,
                        "ego_goal_lane": "516",
                        "ego_goal_offset": 20.0,
                        "npc_start_speed_ratio": 0.898,
                    }
                ],
            },
        ],
        lane_offset_factory=fake_lane_offset,
        scenario_builder=fake_builder,
    )

    assert scenario == {"scenario": "uturn"}
    assert captured["ego_init_laneoffset"] == {"lane_id": "514", "offset": 30.0}
    assert captured["_ego_speed"] == 31.0 / 3.6
    assert captured["_npc_speed"] == 10.2 / 3.6
    assert captured["npc_start_speed_ratio"] == 0.898


def test_build_uturn_scenario_uses_high_speed_lane_switch_for_legacy_profile() -> None:
    captured: dict[str, object] = {}

    def fake_builder(**kwargs):
        captured.update(kwargs)
        return kwargs

    scenario = build_uturn_scenario(
        network="fake-network",
        dynamic_params={"dx0": 15.0, "ego_speed": 39.0, "npc_speed": 14.0},
        fixed_params={
            "ego_init_lane": "514",
            "ego_goal_lane": "516",
            "ego_goal_offset": 20,
            "npc_init_lane": "521",
            "npc_init_offset": 32,
            "uturn_next_lane": "511",
            "acceleration": 7.0,
        },
        scenario_profiles=[
            {
                "profile_id": "right_15",
                "npc_speed": 15.0,
                "npc_init_lane": "521",
                "npc_init_offset": 32.0,
                "uturn_next_lane": "511",
                "acceleration": 7.0,
                "ego_speed_bands": [
                    {
                        "max_ego_speed": 32.5,
                        "ego_init_lane": "514",
                        "ego_init_offset": 38.0,
                        "ego_goal_lane": "516",
                        "ego_goal_offset": 20.0,
                        "npc_start_speed_ratio": 0.898,
                    },
                    {
                        "max_ego_speed": 37.5,
                        "ego_init_lane": "514",
                        "ego_init_offset": 17.0,
                        "ego_goal_lane": "516",
                        "ego_goal_offset": 20.0,
                        "npc_start_speed_ratio": 0.9083,
                    },
                    {
                        "max_ego_speed": float("inf"),
                        "ego_init_lane": "282",
                        "ego_init_offset": 4.0,
                        "ego_goal_lane": "124",
                        "ego_goal_offset": 18.0,
                        "npc_start_speed_ratio": 0.916,
                    },
                ],
            }
        ],
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builder=fake_builder,
    )

    assert scenario["ego_init_laneoffset"] == ("282", 4.0)
    assert captured["ego_goal_laneoffset"] == ("124", 18.0)
    assert captured["npc_init_laneoffset"] == ("521", 32.0)
    assert captured["acceleration"] == 7.0
    assert captured["npc_start_speed_ratio"] == 0.916


def test_build_uturn_scenario_rejects_missing_params() -> None:
    try:
        build_uturn_scenario(
            network="fake-network",
            dynamic_params={"dx0": 15.0, "ego_speed": 39.0},
            fixed_params={
                "ego_init_lane": "514",
                "ego_goal_lane": "516",
                "ego_goal_offset": 20,
                "npc_init_lane": "521",
                "npc_init_offset": 32,
                "uturn_next_lane": "511",
                "acceleration": 7.0,
            },
            lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
            scenario_builder=lambda **kwargs: kwargs,
        )
    except KeyError as exc:
        assert str(exc) == "'Missing dynamic params: npc_speed'"
    else:
        raise AssertionError("Expected KeyError for missing npc_speed")
