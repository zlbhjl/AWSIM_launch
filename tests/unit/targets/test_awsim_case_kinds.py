from targets.awsim.case_kinds import (
    load_case_definition,
    load_event_definitions,
    load_rule_spec,
    load_timeout_sec,
    resolve_case_kind_module,
)


def test_resolve_case_kind_module_loads_new_uturn_module() -> None:
    module = resolve_case_kind_module(case_kind="uturn")

    assert module.__name__ == "targets.awsim.case_kinds.uturn"
    assert module.SCENARIO_TYPE == "uturn"


def test_resolve_case_kind_module_loads_new_cutin_module() -> None:
    module = resolve_case_kind_module(case_kind="cutin")

    assert module.__name__ == "targets.awsim.case_kinds.cutin"
    assert module.SCENARIO_TYPE == "cutin"


def test_resolve_case_kind_module_loads_new_cutout_module() -> None:
    module = resolve_case_kind_module(case_kind="cutout")

    assert module.__name__ == "targets.awsim.case_kinds.cutout"
    assert module.SCENARIO_TYPE == "cutout"


def test_resolve_case_kind_module_loads_new_deceleration_module() -> None:
    module = resolve_case_kind_module(case_kind="deceleration")

    assert module.__name__ == "targets.awsim.case_kinds.deceleration"
    assert module.SCENARIO_TYPE == "deceleration"


def test_resolve_case_kind_module_loads_new_swerve_module() -> None:
    module = resolve_case_kind_module(case_kind="swerve")

    assert module.__name__ == "targets.awsim.case_kinds.swerve"
    assert module.SCENARIO_TYPE == "swerve"


def test_load_rule_spec_supports_new_and_legacy_module_names() -> None:
    new_result_labels, new_formulas, new_invalid_conditions = load_rule_spec(
        case_kind="uturn",
    )
    legacy_result_labels, legacy_formulas, legacy_invalid_conditions = load_rule_spec(
        case_kind="uturn",
        module_name="configs.uturn",
    )

    assert new_result_labels == legacy_result_labels
    assert new_formulas == legacy_formulas
    assert new_invalid_conditions == legacy_invalid_conditions


def test_load_event_definitions_uses_case_kind_module_mapping() -> None:
    event_definitions = load_event_definitions(case_kind="uturn")

    assert "c_collision" in event_definitions
    assert event_definitions["c_collision"] == {
        "dataset_filter": None,
        "error_filter": "output.c_collision",
        "target_column": "c_collision",
    }


def test_load_timeout_sec_reads_case_kind_module() -> None:
    assert load_timeout_sec(case_kind="uturn") == 200.0


def test_load_case_definition_reads_grouped_case_definition() -> None:
    definition = load_case_definition(case_kind="uturn")

    assert definition["scenario_type"] == "uturn"
    assert definition["timeout_sec"] == 200.0
    assert definition["fixed_params"]["ego_init_lane"] == "514"
    assert definition["param_ranges"]["dx0"] == (10.0, 25.0)


def test_load_case_definition_reads_cutin_case_definition() -> None:
    definition = load_case_definition(case_kind="cutin")

    assert definition["scenario_type"] == "cutin"
    assert definition["timeout_sec"] == 200.0
    assert definition["fixed_params"]["cutin_next_lane"] == "111"
    assert definition["fixed_params"]["cutin_vy"] == 1.4
    assert set(definition["param_ranges"].keys()) == {"dx0", "ego_speed", "npc_speed"}
    assert len(definition["scenario_profiles"]) == 3
    assert definition["scenario_profiles"][0]["ego_speed_bands"][0]["ego_goal_offset"] == 150.0


def test_load_case_definition_reads_cutout_case_definition() -> None:
    definition = load_case_definition(case_kind="cutout")

    assert definition["scenario_type"] == "cutout"
    assert definition["timeout_sec"] == 200.0
    assert definition["fixed_params"]["cutout_next_lane"] == "112"
    assert set(definition["param_ranges"].keys()) == {"ego_speed", "cutout_vy", "dx_f"}
    assert len(definition["scenario_profiles"]) == 1
    assert definition["scenario_profiles"][0]["ego_speed_bands"][0]["ego_goal_offset"] == 210.0
    assert definition["scenario_profiles"][0]["ego_speed_bands"][0]["spawn_trigger_speed_ratio"] < 1.0


def test_load_case_definition_reads_deceleration_case_definition() -> None:
    definition = load_case_definition(case_kind="deceleration")

    assert definition["scenario_type"] == "deceleration"
    assert definition["timeout_sec"] == 200.0
    assert definition["fixed_params"]["spawn_headway_sec"] == 2.0
    assert definition["fixed_params"]["npc_deceleration"] == 9.8
    assert set(definition["param_ranges"].keys()) == {"ego_speed"}
    assert len(definition["scenario_profiles"]) == 1
    assert definition["scenario_profiles"][0]["ego_speed_bands"][0]["ego_goal_offset"] == 210.0
    assert definition["scenario_profiles"][0]["ego_speed_bands"][0]["spawn_trigger_speed_ratio"] < 1.0
    assert definition["scenario_profiles"][0]["ego_speed_bands"][0]["decel_trigger_speed_ratio"] < 1.0


def test_load_case_definition_reads_swerve_case_definition() -> None:
    definition = load_case_definition(case_kind="swerve")

    assert definition["scenario_type"] == "swerve"
    assert definition["timeout_sec"] == 200.0
    assert definition["fixed_params"]["swerve_vy"] == 1.2
    assert definition["fixed_params"]["swerve_right"] is True
    assert set(definition["param_ranges"].keys()) == {"dx0", "ego_speed", "npc_speed"}
    assert len(definition["scenario_profiles"]) == 2
    assert definition["scenario_profiles"][0]["ego_speed_bands"][0]["npc_init_offset"] == 60.0
