from types import SimpleNamespace

from strategist import ActiveLearningStrategist


def test_strategist_prefers_grouped_case_definition_and_strategy_settings() -> None:
    config = SimpleNamespace(
        get_case_definition=lambda: {
            "scenario_type": "uturn",
            "repeat_count": 10,
            "timeout_sec": 120.0,
            "target_npcs": ["npc1"],
            "param_ranges": {
                "dx0": (10.0, 20.0),
                "ego_speed": (30.0, 40.0),
            },
            "fixed_params": {"ego_init_lane": "514"},
        },
        get_strategy_settings=lambda: {
            "target_priorities": ["c_collision"],
            "initial_exploration_limit": 11,
            "min_samples": 22,
            "max_samples": 33,
            "stability_reference_points": 7,
            "stability_history_length": 8,
            "stability_hysteresis": (0.2, 0.8),
            "stability_shift_threshold": 0.05,
            "stability_required_streak": 4,
            "step2_max_exploration": 55,
            "margin_range": (0.25, 0.45),
            "margin_max_uncertainty": 0.07,
            "focus_noise": 0.15,
            "dkw_target_metric": "min_ttc",
            "dkw_target_metrics": ["min_ttc", "min_distance"],
            "binomial_ci_target": "c_collision",
            "binomial_ci_method": "wilson",
            "binomial_ci_confidence": 0.91,
            "binomial_ci_target_width": 0.03,
            "binomial_ci_min_samples": 44,
        },
    )

    strategist = ActiveLearningStrategist("uturn", config, num_candidates=5)

    assert strategist.param_ranges == {
        "dx0": (10.0, 20.0),
        "ego_speed": (30.0, 40.0),
    }
    assert strategist.param_names == ["dx0", "ego_speed"]
    assert strategist.target_priorities == ["c_collision"]
    assert strategist.INITIAL_EXPLORATION_LIMIT == 11
    assert strategist.MAX_SAMPLES == 33
    assert strategist.FOCUS_NOISE == 0.15
    assert strategist.binomial_confidence == 0.91
