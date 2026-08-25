from types import SimpleNamespace

from targets.awsim.scenario_runner import (
    _install_scenario_goal_timeout,
    build_scenario,
    parse_dynamic_params,
    run_scenario_case,
)


def test_parse_dynamic_params_parses_float_pairs() -> None:
    params = parse_dynamic_params(["--dx0", "15.0", "--ego_speed", "35.0", "--npc_speed", "14.0"])

    assert params == {
        "dx0": 15.0,
        "ego_speed": 35.0,
        "npc_speed": 14.0,
    }


def test_parse_dynamic_params_rejects_odd_argument_count() -> None:
    try:
        parse_dynamic_params(["--dx0", "15.0", "--ego_speed"])
    except ValueError as exc:
        assert str(exc) == "Dynamic params must be given as --key value pairs"
    else:
        raise AssertionError("Expected ValueError for odd dynamic param count")


def test_build_scenario_creates_uturn_variant_for_right_15_profile() -> None:
    captured: dict[str, object] = {}

    class FakeManager:
        def __init__(self) -> None:
            self.network = "fake-network"

    def fake_lane_offset_factory(lane_id: str, offset: float) -> dict[str, object]:
        return {
            "lane_id": lane_id,
            "offset": offset,
        }

    def fake_uturn_builder(**kwargs):
        captured.update(kwargs)
        return {"scenario": "uturn"}

    manager, scenario = build_scenario(
        "uturn",
        {"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
        case_kind_module_name="targets.awsim.case_kinds.uturn",
        scenario_manager=FakeManager(),
        lane_offset_factory=fake_lane_offset_factory,
        scenario_builders={"uturn": fake_uturn_builder},
    )

    assert manager.network == "fake-network"
    assert scenario == {"scenario": "uturn"}
    assert captured["network"] == "fake-network"
    assert captured["ego_init_laneoffset"] == {"lane_id": "514", "offset": 17}
    assert captured["ego_goal_laneoffset"] == {"lane_id": "516", "offset": 20}
    assert captured["npc_init_laneoffset"] == {"lane_id": "521", "offset": 32}
    assert captured["uturn_next_lane"] == "511"
    assert captured["_ego_speed"] == 35.0 / 3.6
    assert captured["_npc_speed"] == 14.0 / 3.6
    assert captured["dx0"] == 15.0
    assert captured["acceleration"] == 7.0


def test_build_scenario_delegates_uturn_shape_to_builder_module() -> None:
    captured: dict[str, object] = {}

    def fake_uturn_builder(**kwargs):
        captured.update(kwargs)
        return {"scenario": "uturn"}

    _, scenario = build_scenario(
        "uturn",
        {"dx0": 15.0, "ego_speed": 31.0, "npc_speed": 14.0},
        case_kind_module_name="targets.awsim.case_kinds.uturn",
        scenario_manager=SimpleNamespace(network="fake-network"),
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builders={"uturn": fake_uturn_builder},
    )

    assert scenario == {"scenario": "uturn"}
    assert captured["ego_init_laneoffset"] == ("514", 38)


def test_build_scenario_switches_to_legacy_high_speed_lane_pair() -> None:
    captured: dict[str, object] = {}

    def fake_uturn_builder(**kwargs):
        captured.update(kwargs)
        return {"scenario": "uturn"}

    _, scenario = build_scenario(
        "uturn",
        {"dx0": 15.0, "ego_speed": 39.0, "npc_speed": 14.0},
        case_kind_module_name="targets.awsim.case_kinds.uturn",
        scenario_manager=SimpleNamespace(network="fake-network"),
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builders={"uturn": fake_uturn_builder},
    )

    assert scenario == {"scenario": "uturn"}
    assert captured["ego_init_laneoffset"] == ("282", 4.0)
    assert captured["ego_goal_laneoffset"] == ("124", 18.0)


def test_run_scenario_case_runs_manager_once_for_uturn() -> None:
    outputs: list[str] = []

    class FakeManager:
        def __init__(self) -> None:
            self.network = "fake-network"
            self.runs: list[list[object]] = []

        def run(self, scenarios: list[object]) -> None:
            self.runs.append(list(scenarios))

    fake_manager = FakeManager()

    exit_code = run_scenario_case(
        "uturn",
        {"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
        case_kind_module_name="targets.awsim.case_kinds.uturn",
        scenario_manager=fake_manager,
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builders={"uturn": lambda **kwargs: SimpleNamespace(kind="uturn", kwargs=kwargs)},
        printer=outputs.append,
    )

    assert exit_code == 0
    assert outputs == [">>> [Runner] Starting 'uturn' simulation..."]
    assert len(fake_manager.runs) == 1
    assert fake_manager.runs[0][0].kind == "uturn"


def test_build_scenario_creates_cutin_variant() -> None:
    captured: dict[str, object] = {}

    def fake_cutin_builder(**kwargs):
        captured.update(kwargs)
        return {"scenario": "cutin"}

    manager, scenario = build_scenario(
        "cutin",
        {"dx0": 12.0, "ego_speed": 30.0, "npc_speed": 10.0},
        case_kind_module_name="targets.awsim.case_kinds.cutin",
        scenario_manager=SimpleNamespace(network="fake-network"),
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builders={"cutin": fake_cutin_builder},
    )

    assert manager.network == "fake-network"
    assert scenario == {"scenario": "cutin"}
    assert captured["ego_init_laneoffset"] == ("111", 0.0)
    assert captured["ego_goal_laneoffset"] == ("111", 150.0)
    assert captured["npc_init_laneoffset"] == ("112", 80.0)
    assert captured["cutin_next_lane"] == "111"
    assert captured["_cutin_vy"] == 1.4


def test_build_scenario_creates_cutin_high_speed_profile_variant() -> None:
    captured: dict[str, object] = {}

    def fake_cutin_builder(**kwargs):
        captured.update(kwargs)
        return {"scenario": "cutin"}

    _, scenario = build_scenario(
        "cutin",
        {"dx0": 12.0, "ego_speed": 39.0, "npc_speed": 20.0},
        case_kind_module_name="targets.awsim.case_kinds.cutin",
        scenario_manager=SimpleNamespace(network="fake-network"),
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builders={"cutin": fake_cutin_builder},
    )

    assert scenario == {"scenario": "cutin"}
    assert captured["ego_goal_laneoffset"] == ("111", 210.0)
    assert captured["npc_init_laneoffset"] == ("112", 120.0)
    assert captured["acceleration"] == 7.0


def test_run_scenario_case_runs_manager_once_for_cutin() -> None:
    outputs: list[str] = []

    class FakeManager:
        def __init__(self) -> None:
            self.network = "fake-network"
            self.runs: list[list[object]] = []

        def run(self, scenarios: list[object]) -> None:
            self.runs.append(list(scenarios))

    fake_manager = FakeManager()

    exit_code = run_scenario_case(
        "cutin",
        {"dx0": 12.0, "ego_speed": 30.0, "npc_speed": 10.0},
        case_kind_module_name="targets.awsim.case_kinds.cutin",
        scenario_manager=fake_manager,
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builders={"cutin": lambda **kwargs: SimpleNamespace(kind="cutin", kwargs=kwargs)},
        printer=outputs.append,
    )

    assert exit_code == 0
    assert outputs == [">>> [Runner] Starting 'cutin' simulation..."]
    assert len(fake_manager.runs) == 1
    assert fake_manager.runs[0][0].kind == "cutin"


def test_build_scenario_creates_cutout_variant() -> None:
    captured: dict[str, object] = {}

    def fake_cutout_builder(**kwargs):
        captured.update(kwargs)
        return {"scenario": "cutout"}

    manager, scenario = build_scenario(
        "cutout",
        {"ego_speed": 30.0, "cutout_vy": 1.5, "dx_f": 10.0},
        case_kind_module_name="targets.awsim.case_kinds.cutout",
        scenario_manager=SimpleNamespace(network="fake-network"),
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builders={"cutout": fake_cutout_builder},
    )

    assert manager.network == "fake-network"
    assert scenario == {"scenario": "cutout"}
    assert captured["ego_init_laneoffset"] == ("111", 0.0)
    assert captured["ego_goal_laneoffset"] == ("111", 180.0)
    assert captured["cutout_next_lane"] == "112"
    assert captured["_speed"] == 30.0 / 3.6
    assert captured["vy"] == 1.5
    assert captured["dx_f"] == 10.0


def test_build_scenario_creates_cutout_high_speed_profile_variant() -> None:
    captured: dict[str, object] = {}

    def fake_cutout_builder(**kwargs):
        captured.update(kwargs)
        return {"scenario": "cutout"}

    _, scenario = build_scenario(
        "cutout",
        {"ego_speed": 39.0, "cutout_vy": 1.5, "dx_f": 10.0},
        case_kind_module_name="targets.awsim.case_kinds.cutout",
        scenario_manager=SimpleNamespace(network="fake-network"),
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builders={"cutout": fake_cutout_builder},
    )

    assert scenario == {"scenario": "cutout"}
    assert captured["ego_goal_laneoffset"] == ("111", 240.0)
    assert captured["cutout_next_lane"] == "112"


def test_run_scenario_case_runs_manager_once_for_cutout() -> None:
    outputs: list[str] = []

    class FakeManager:
        def __init__(self) -> None:
            self.network = "fake-network"
            self.runs: list[list[object]] = []

        def run(self, scenarios: list[object]) -> None:
            self.runs.append(list(scenarios))

    fake_manager = FakeManager()

    exit_code = run_scenario_case(
        "cutout",
        {"ego_speed": 30.0, "cutout_vy": 1.5, "dx_f": 10.0},
        case_kind_module_name="targets.awsim.case_kinds.cutout",
        scenario_manager=fake_manager,
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builders={"cutout": lambda **kwargs: SimpleNamespace(kind="cutout", kwargs=kwargs)},
        printer=outputs.append,
    )

    assert exit_code == 0
    assert outputs == [">>> [Runner] Starting 'cutout' simulation..."]
    assert len(fake_manager.runs) == 1
    assert fake_manager.runs[0][0].kind == "cutout"


def test_install_scenario_goal_timeout_marks_timeout_and_returns() -> None:
    warnings: list[str] = []
    spin_calls: list[float] = []

    class FakeLogger:
        def warning(self, message: str) -> None:
            warnings.append(message)

    scenario = SimpleNamespace(
        global_state={"ads_internal_status": 0},
        client_node=object(),
        logger=FakeLogger(),
        my_spin=lambda: None,
    )
    times = iter([0.0, 0.1, 1.2])

    _install_scenario_goal_timeout(
        scenario,
        timeout_sec=1.0,
        monotonic=lambda: next(times),
        spin_once=lambda _node, timeout_sec=None: spin_calls.append(timeout_sec),
        goal_arrived_value=5,
    )

    scenario.my_spin()

    assert spin_calls == [0.9]
    assert scenario._scenario_timeout_reached is True
    assert scenario.global_state["ads_internal_status"] > 0
    assert warnings == ["Scenario timed out after 1.0s before goal arrival."]


def test_run_scenario_case_returns_timeout_exit_code_when_goal_is_not_reached() -> None:
    outputs: list[str] = []

    class FakeLogger:
        def __init__(self) -> None:
            self.warnings: list[str] = []

        def warning(self, message: str) -> None:
            self.warnings.append(message)

    class FakeManager:
        def __init__(self) -> None:
            self.network = "fake-network"
            self.logger = FakeLogger()

        def run(self, scenarios: list[object]) -> None:
            scenarios[0].my_spin()

    fake_manager = FakeManager()
    fake_scenario = SimpleNamespace(
        kind="uturn",
        global_state={"ads_internal_status": 0},
        client_node=object(),
        logger=fake_manager.logger,
        my_spin=lambda: None,
    )
    times = iter([0.0, 0.2, 1.5])

    exit_code = run_scenario_case(
        "uturn",
        {"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
        case_kind_module_name="targets.awsim.case_kinds.uturn",
        scenario_manager=fake_manager,
        lane_offset_factory=lambda lane_id, offset: (lane_id, offset),
        scenario_builders={"uturn": lambda **kwargs: fake_scenario},
        printer=outputs.append,
        timeout_sec=1.0,
        timeout_monotonic=lambda: next(times),
        timeout_spin_once=lambda _node, timeout_sec=None: None,
        timeout_goal_arrived_value=5,
    )

    assert exit_code == 124
    assert outputs == [">>> [Runner] Starting 'uturn' simulation..."]
    assert fake_manager.logger.warnings == ["Scenario timed out after 1.0s before goal arrival."]
