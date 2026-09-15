import json
import sys
from pathlib import Path

import pytest

from apps.cli.worker_main import (
    build_direct_input,
    build_local_history,
    build_result_sink,
    build_simulation_input,
    build_task_source,
    build_test_case,
    merge_legacy_param_flags,
    normalize_args,
    parse_param_assignments,
    resolve_history_loop_num,
    run_worker,
    run_worker_cli,
    run_worker_with_summary,
    validate_args,
)
from contracts.evaluation import EvaluationRecord
from contracts.execution import RawRunResult, RunStatus, TestCase
from runtime.cluster.task_queue_gateway import TaskQueueGateway


class FakeTaskSource:
    def __init__(self, cases):
        self.cases = list(cases)
        self.status_updates = []
        self.completions = []
        self.last_stop_reason = ""

    def fetch_next(self):
        if not self.cases:
            return None
        return self.cases.pop(0)

    def update_worker_status(self, worker_id: str, status: str):
        self.status_updates.append((worker_id, status))

    def report_completion(self, loop_num: int, status: str):
        self.completions.append((loop_num, status))


def test_build_test_case_uses_fixture_stem_by_default() -> None:
    class Args:
        fixture = "tests/fixtures/awsim/timeout_trace.txt"
        fixture_raw_run_status = None
        case_id = None
        case_kind = "uturn"
        target = "awsim"
        tags = ["smoke"]
        reason = "manual"

    test_case = build_test_case(Args())

    assert test_case.case_id == "timeout_trace"
    assert test_case.input["fixture_path"].endswith("timeout_trace.txt")
    assert test_case.tags == ["smoke"]


def test_build_test_case_returns_none_without_fixture() -> None:
    class Args:
        fixture = None
        params = []

    assert build_test_case(Args()) is None


def test_build_test_case_includes_history_loop_num_when_given() -> None:
    class Args:
        fixture = "tests/fixtures/awsim/timeout_trace.txt"
        fixture_raw_run_status = None
        case_id = None
        case_kind = "uturn"
        target = "awsim"
        tags = []
        reason = "manual"
        history_loop_num = 12
        config_module = "targets.awsim.case_kinds.uturn"

    test_case = build_test_case(Args())

    assert test_case is not None
    assert test_case.meta["history_loop_num"] == 12


def test_build_test_case_includes_fixture_raw_run_status_when_given() -> None:
    class Args:
        fixture = "tests/fixtures/awsim/normal_trace_maude.json"
        fixture_raw_run_status = "timeout"
        case_id = None
        case_kind = "uturn"
        target = "awsim"
        tags = []
        reason = "manual"
        history_loop_num = None
        config_module = "targets.awsim.case_kinds.uturn"
        mode = "explore"
        ext_mode = "cvm"
        dkw_region = "custom"
        dkw_pure_smc = False
        dkw_simultaneous = False
        focus_points = None
        dkw_bounds = None

    test_case = build_test_case(Args())

    assert test_case is not None
    assert test_case.input["fixture_raw_run_status"] == "timeout"


def test_validate_args_requires_exactly_one_input_mode() -> None:
    class Args:
        fixture = None
        params = []
        queue_actor_name = None
        target = "awsim"
        headless = False

    with pytest.raises(ValueError):
        validate_args(Args())

    Args.fixture = "tests/fixtures/awsim/timeout_trace.txt"
    Args.queue_actor_name = "TaskQueueActor"
    with pytest.raises(ValueError):
        validate_args(Args())

    Args.fixture = None
    Args.params = ["dx0=10.0"]
    Args.queue_actor_name = None
    validate_args(Args())


def test_validate_args_allows_bbsl_param_mode() -> None:
    class Args:
        fixture = None
        params = ["dx0=10.0"]
        queue_actor_name = None
        target = "bbsl"
        headless = False
        scenario_type = None
        simulation_output_dir = None
        expected_trace_path = None
        local_loop_num = None

    validate_args(Args())


def test_validate_args_rejects_awsim_only_options_for_bbsl() -> None:
    class Args:
        fixture = None
        params = ["dx0=10.0"]
        queue_actor_name = None
        target = "bbsl"
        headless = False
        scenario_type = "uturn"
        simulation_output_dir = None
        expected_trace_path = None
        local_loop_num = None

    with pytest.raises(ValueError, match="AWSIM-only"):
        validate_args(Args())


def test_parse_param_assignments_parses_scalar_values() -> None:
    params = parse_param_assignments(
        ["dx0=15.0", "count=3", "enabled=true", "label=npc1"]
    )

    assert params == {
        "dx0": 15.0,
        "count": 3,
        "enabled": True,
        "label": "npc1",
    }


def test_merge_legacy_param_flags_maps_unknown_double_dash_pairs_into_params() -> None:
    class Args:
        fixture = None
        queue_actor_name = None
        params = []

    normalized = merge_legacy_param_flags(
        Args(),
        ["--dx0", "15.0", "--ego_speed", "35.0", "--enable_feature"],
    )

    assert normalized.params == [
        "dx0=15.0",
        "ego_speed=35.0",
        "enable_feature=true",
    ]


def test_build_simulation_input_adds_backend_fields() -> None:
    class Args:
        params = ["dx0=15.0", "ego_speed=35.0", "npc_speed=14.0"]
        case_kind = "uturn"
        scenario_type = None
        ext_mode = "cvm"
        simulation_output_dir = "/tmp/awsim-output"
        expected_trace_path = "/tmp/awsim-output/uturn_eval_sim1.json"
        local_loop_num = 3

    payload = build_simulation_input(Args())

    assert payload["dx0"] == 15.0
    assert payload["scenario_type"] == "uturn"
    assert payload["ext_mode"] == "cvm"
    assert payload["output_dir"] == "/tmp/awsim-output"
    assert payload["expected_trace_path"] == "/tmp/awsim-output/uturn_eval_sim1.json"
    assert payload["local_loop_num"] == 3


def test_normalize_args_uses_legacy_type_for_awsim_case_kind() -> None:
    class Args:
        target = "awsim"
        legacy_type = "uturn"
        case_kind = "uturn"
        config_module = None
        scenario_profile = None
        container_profile = None
        scenario_type = None
        mode = "explore"
        focus_points = None

    normalized = normalize_args(Args(), argv=["--type", "uturn"])

    assert normalized.case_kind == "uturn"
    assert normalized.config_module == "targets.awsim.case_kinds.uturn"
    assert normalized.scenario_type == "uturn"


def test_normalize_args_loads_focus_points_from_case_kind_module() -> None:
    class Args:
        target = "awsim"
        legacy_type = None
        case_kind = "uturn"
        config_module = None
        scenario_profile = None
        container_profile = None
        scenario_type = None
        mode = "focus"
        focus_points = None

    normalized = normalize_args(Args(), argv=["--mode", "focus"])

    assert normalized.config_module == "targets.awsim.case_kinds.uturn"
    assert isinstance(normalized.focus_points, list)
    assert normalized.focus_points
    assert normalized.focus_points[0]["dx0"] == 10.09


def test_normalize_args_uses_scenario_profile_specific_case_kind_module() -> None:
    class Args:
        target = "awsim"
        legacy_type = None
        case_kind = "uturn"
        config_module = None
        scenario_profile = "autoware171"
        container_profile = None
        scenario_type = None
        mode = "explore"
        focus_points = None

    normalized = normalize_args(Args(), argv=["--scenario-profile", "autoware171"])

    assert normalized.config_module == "targets.awsim.case_kinds.autoware171.uturn"


def test_normalize_args_does_not_force_unsupported_scenario_profile_from_container_profile() -> None:
    class Args:
        target = "awsim"
        legacy_type = None
        case_kind = "uturn"
        config_module = None
        scenario_profile = None
        container_profile = "autoware180"
        scenario_type = None
        mode = "explore"
        focus_points = None

    normalized = normalize_args(Args(), argv=["--container-profile", "autoware180"])

    assert normalized.scenario_profile is None
    assert normalized.config_module == "targets.awsim.case_kinds.uturn"


def test_build_test_case_includes_profile_metadata() -> None:
    class Args:
        fixture = None
        params = ["dx0=15.0", "ego_speed=35.0", "npc_speed=14.0"]
        case_id = None
        case_kind = "uturn"
        target = "awsim"
        tags = []
        reason = "manual"
        config_module = "targets.awsim.case_kinds.autoware171.uturn"
        container_profile = "autoware171"
        scenario_profile = "autoware171"
        mode = "explore"
        ext_mode = "cvm"
        dkw_region = "custom"
        dkw_pure_smc = False
        dkw_simultaneous = False
        focus_points = None
        dkw_bounds = None
        history_loop_num = None
        scenario_type = None
        simulation_output_dir = None
        expected_trace_path = None
        local_loop_num = None

    test_case = build_test_case(Args())

    assert test_case is not None
    assert test_case.meta["container_profile"] == "autoware171"
    assert test_case.meta["scenario_profile"] == "autoware171"


def test_build_direct_input_returns_raw_params_for_bbsl() -> None:
    class Args:
        target = "bbsl"
        params = ["target_repo=/tmp/bbsl", "mini=true", "max_images=8", "tree=basic"]

    payload = build_direct_input(Args())

    assert payload == {
        "target_repo": "/tmp/bbsl",
        "mini": True,
        "max_images": 8,
        "tree": "basic",
    }


def test_run_worker_builds_headless_backend_profile(tmp_path: Path) -> None:
    output_path = tmp_path / "records.jsonl"
    fixture_path = Path("tests/fixtures/awsim/timeout_trace.txt").resolve()
    captured: dict[str, object] = {}

    import apps.cli.worker_main as worker_main_module

    original_builder = worker_main_module.build_target_components
    try:
        def fake_build_target_components(args, *, backend=None, result_interpreter=None):
            captured["headless"] = args.headless

            def fake_backend(case):
                return RawRunResult(
                    case_id=case.case_id,
                    target=case.target,
                    case_kind=case.case_kind,
                    status=RunStatus.TIMEOUT,
                    evidence={"trace_json": str(fixture_path)},
                )

            def fake_interpreter(raw):
                return EvaluationRecord(
                    case_id=raw.case_id,
                    target=raw.target,
                    case_kind=raw.case_kind,
                    status=raw.status,
                    evidence=dict(raw.evidence),
                    meta={"verifier_name": "fake"},
                )

            class Components:
                backend = fake_backend
                result_interpreter = fake_interpreter

            return Components()

        worker_main_module.build_target_components = fake_build_target_components
        exit_code = run_worker(
            [
                "--fixture",
                str(fixture_path),
                "--output",
                str(output_path),
                "--headless",
            ]
        )
    finally:
        worker_main_module.build_target_components = original_builder

    assert exit_code == 0
    assert captured["headless"] is True


def test_build_test_case_builds_direct_simulation_case() -> None:
    class Args:
        fixture = None
        params = ["dx0=15.0", "ego_speed=35.0", "npc_speed=14.0"]
        case_id = None
        case_kind = "uturn"
        target = "awsim"
        tags = ["direct"]
        reason = "manual"
        history_loop_num = 12
        config_module = "targets.awsim.case_kinds.uturn"
        mode = "focus"
        ext_mode = "ctrv"
        focus_points = [{"dx0": 15.0}]
        dkw_region = "custom"
        dkw_pure_smc = False
        dkw_simultaneous = False
        dkw_bounds = None
        scenario_type = None
        simulation_output_dir = None
        expected_trace_path = None
        local_loop_num = None

    test_case = build_test_case(Args())

    assert test_case is not None
    assert test_case.case_id == "uturn_direct"
    assert test_case.input["dx0"] == 15.0
    assert test_case.input["scenario_type"] == "uturn"
    assert test_case.meta["config_module"] == "targets.awsim.case_kinds.uturn"
    assert test_case.meta["run_mode"] == "focus"
    assert test_case.meta["ext_mode"] == "ctrv"
    assert test_case.meta["focus_points"] == [{"dx0": 15.0}]


def test_build_test_case_builds_direct_bbsl_case_without_awsim_fields() -> None:
    class Args:
        fixture = None
        params = ["target_repo=/tmp/bbsl", "mini=true", "tree=basic"]
        case_id = None
        case_kind = "full_all"
        target = "bbsl"
        tags = ["direct"]
        reason = "manual"
        history_loop_num = None
        config_module = "targets.awsim.case_kinds.uturn"
        scenario_type = None
        simulation_output_dir = None
        expected_trace_path = None
        local_loop_num = None

    test_case = build_test_case(Args())

    assert test_case is not None
    assert test_case.case_id == "full_all_direct"
    assert test_case.input == {
        "target_repo": "/tmp/bbsl",
        "mini": True,
        "tree": "basic",
    }


def test_build_task_source_returns_none_for_direct_mode() -> None:
    class Args:
        queue_actor_name = None

    assert build_task_source(Args()) is None


def test_build_task_source_connects_to_ray_actor(monkeypatch) -> None:
    class Args:
        queue_actor_name = "TaskQueueActor"
        queue_namespace = "awsim_cluster"
        queue_address = "ray://127.0.0.1:10001"
        queue_connect_timeout = 12.0
        queue_connect_poll_interval = 0.5
        queue_connect_retries = 4
        queue_connect_retry_interval = 1.5
        target = "awsim"
        case_kind = "uturn"

    actor = object()
    captured: dict[str, object] = {}

    class FakeLocator:
        def connect_and_get_actor(self, actor_name, *, config):
            captured["actor_name"] = actor_name
            captured["config"] = config

            class FakeRay:
                @staticmethod
                def get(value):
                    return value

            return FakeRay(), actor

    gateway = build_task_source(Args(), actor_locator=FakeLocator())

    assert isinstance(gateway, TaskQueueGateway)
    assert gateway.actor is actor
    assert captured["actor_name"] == "TaskQueueActor"
    config = captured["config"]
    assert config.address == "ray://127.0.0.1:10001"
    assert config.namespace == "awsim_cluster"
    assert config.actor_lookup_timeout_sec == 12.0
    assert config.actor_lookup_poll_interval_sec == 0.5
    assert config.connect_retries == 4
    assert config.connect_retry_interval_sec == 1.5


def test_build_result_sink_returns_composite_when_dataset_csv_is_set(tmp_path: Path) -> None:
    class Args:
        output = str(tmp_path / "records.jsonl")
        path_root = None
        dataset_csv = str(tmp_path / "uturn_dataset.csv")

    sink = build_result_sink(Args())

    assert sink.__class__.__name__ == "CompositeResultSink"


def test_run_worker_summary_keeps_running_when_optional_dataset_sink_fails(tmp_path: Path) -> None:
    output_path = tmp_path / "records.jsonl"
    dataset_csv_path = tmp_path / "uturn_dataset.csv"

    import apps.cli.worker_main as worker_main_module

    original_shared_store_sink = worker_main_module.SharedStoreResultSink
    try:
        class FailingSharedStoreSink:
            @classmethod
            def from_dataset_csv(cls, _dataset_csv):
                class _Sink:
                    def save(self, _record):
                        raise RuntimeError("shared store unavailable")

                return _Sink()

        original_shared_store_sink = worker_main_module.SharedStoreResultSink
        worker_main_module.SharedStoreResultSink = FailingSharedStoreSink
        summary = run_worker_with_summary(
            [
                "--fixture",
                "tests/fixtures/awsim/timeout_trace.txt",
                "--output",
                str(output_path),
                "--dataset-csv",
                str(dataset_csv_path),
            ],
            backend=lambda case: RawRunResult(
                case_id=case.case_id,
                target=case.target,
                case_kind=case.case_kind,
                status=RunStatus.SUCCESS,
                evidence={"trace_json": case.input["fixture_path"]},
            ),
            result_interpreter=lambda raw: EvaluationRecord(
                case_id=raw.case_id,
                target=raw.target,
                case_kind=raw.case_kind,
                status=RunStatus.SUCCESS,
                output={"c_collision": 0},
                evidence=dict(raw.evidence),
            ),
        )
    finally:
        worker_main_module.SharedStoreResultSink = original_shared_store_sink

    assert summary["exit_code"] == 0
    assert output_path.exists()
    assert len(summary["sink_warnings"]) == 1
    assert "shared store unavailable" in summary["sink_warnings"][0]


def test_run_worker_summary_keeps_running_when_optional_shared_store_actor_sink_fails(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "records.jsonl"

    import apps.cli.worker_main as worker_main_module

    original_actor_sink = worker_main_module.RaySharedStoreResultSink
    try:
        class FailingActorSharedStoreSink:
            @classmethod
            def from_actor_name(cls, *args, **kwargs):
                class _Sink:
                    def save(self, _record):
                        raise RuntimeError("shared store actor unavailable")

                return _Sink()

        worker_main_module.RaySharedStoreResultSink = FailingActorSharedStoreSink
        summary = run_worker_with_summary(
            [
                "--fixture",
                "tests/fixtures/awsim/timeout_trace.txt",
                "--output",
                str(output_path),
                "--shared-store-actor-name",
                "SharedStoreActor",
            ],
            backend=lambda case: RawRunResult(
                case_id=case.case_id,
                target=case.target,
                case_kind=case.case_kind,
                status=RunStatus.SUCCESS,
                evidence={"trace_json": case.input["fixture_path"]},
            ),
            result_interpreter=lambda raw: EvaluationRecord(
                case_id=raw.case_id,
                target=raw.target,
                case_kind=raw.case_kind,
                status=RunStatus.SUCCESS,
                output={"c_collision": 0},
                evidence=dict(raw.evidence),
            ),
        )
    finally:
        worker_main_module.RaySharedStoreResultSink = original_actor_sink

    assert summary["exit_code"] == 0
    assert output_path.exists()
    assert len(summary["sink_warnings"]) == 1
    assert "shared store actor unavailable" in summary["sink_warnings"][0]


def test_run_worker_summary_stops_on_ray_control_plane_loss(tmp_path: Path) -> None:
    output_path = tmp_path / "records.jsonl"
    case = TestCase(
        case_id="queue_case_ray_lost",
        target="awsim",
        case_kind="uturn",
        meta={"global_loop_num": 1},
    )

    class RayLostTaskSource(FakeTaskSource):
        def __init__(self):
            super().__init__([case])
            self.update_count = 0

        def update_worker_status(self, worker_id: str, status: str):
            self.update_count += 1
            if self.update_count >= 3:
                raise RuntimeError("Ray Client is not connected")
            super().update_worker_status(worker_id, status)

    def backend(test_case):
        import time

        time.sleep(0.04)
        return RawRunResult(
            case_id=test_case.case_id,
            target=test_case.target,
            case_kind=test_case.case_kind,
            status=RunStatus.SUCCESS,
        )

    summary = run_worker_with_summary(
        [
            "--queue-actor-name",
            "TaskQueueActor",
            "--output",
            str(output_path),
            "--worker-id",
            "worker-queue",
            "--queue-heartbeat-interval",
            "0.01",
        ],
        backend=backend,
        result_interpreter=lambda raw: EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
        ),
        task_source=RayLostTaskSource(),
    )

    assert summary["exit_code"] == 1
    assert summary["status"] == "ray_control_plane_lost"
    assert summary["terminal_status"] == "ray_control_plane_lost"
    assert not output_path.exists()


def test_run_worker_summary_exits_before_fetch_when_gpu_is_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output_path = tmp_path / "records.jsonl"
    case = TestCase(case_id="must_not_run", target="awsim", case_kind="uturn")
    task_source = FakeTaskSource([case])

    class Unhealthy:
        healthy = False
        detail = "Failed to initialize NVML: Unknown Error"

    monkeypatch.setattr(
        "apps.cli.worker_main.probe_nvidia_smi",
        lambda **_kwargs: Unhealthy(),
    )
    summary = run_worker_with_summary(
        [
            "--queue-actor-name",
            "TaskQueueActor",
            "--output",
            str(output_path),
            "--worker-id",
            "worker_21",
            "--worker-gpu-health-check",
        ],
        backend=lambda _: (_ for _ in ()).throw(AssertionError("backend must not run")),
        result_interpreter=lambda _: None,
        task_source=task_source,
    )

    assert summary["exit_code"] == 75
    assert summary["status"] == "gpu_unavailable"
    assert task_source.cases == [case]
    assert task_source.status_updates == [("worker_21", "gpu_unavailable")]
    assert not output_path.exists()


def test_build_local_history_returns_none_without_history_path() -> None:
    class Args:
        history_path = None

    assert build_local_history(Args()) is None


def test_resolve_history_loop_num_prefers_global_loop_num() -> None:
    test_case = TestCase(
        case_id="history_case",
        target="awsim",
        case_kind="uturn",
        meta={"global_loop_num": 8, "history_loop_num": 12},
    )

    assert resolve_history_loop_num(test_case) == 8


def test_run_worker_saves_record_and_returns_zero(tmp_path: Path) -> None:
    output_path = tmp_path / "records.jsonl"

    def backend(case):
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.SUCCESS,
            evidence={"trace_json": case.input["fixture_path"]},
        )

    def interpreter(raw):
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
            output={"c_collision": 0},
            evidence=dict(raw.evidence),
            meta={"verifier_name": "fake"},
        )

    exit_code = run_worker(
        [
            "--fixture",
            "tests/fixtures/awsim/timeout_trace.txt",
            "--output",
            str(output_path),
            "--tag",
            "smoke",
        ],
        backend=backend,
        result_interpreter=interpreter,
    )

    assert exit_code == 0
    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["status"] == "success"
    assert payload["output"]["c_collision"] == 0


def test_run_worker_supports_bbsl_fixture_target(tmp_path: Path) -> None:
    output_path = tmp_path / "bbsl_records.jsonl"

    def backend(case):
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.SUCCESS,
            evidence={"raw_result_json": case.input["fixture_path"]},
        )

    def interpreter(raw):
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
            output={"condition_result_count": 4},
            evidence=dict(raw.evidence),
            meta={"verifier_name": "bbsl"},
        )

    exit_code = run_worker(
        [
            "--fixture",
            "tests/fixtures/bbsl/experiment_all_raw_result_mini.json",
            "--output",
            str(output_path),
            "--target",
            "bbsl",
            "--case-kind",
            "full_all",
        ],
        backend=backend,
        result_interpreter=interpreter,
    )

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert payload["target"] == "bbsl"
    assert payload["case_kind"] == "full_all"
    assert payload["output"]["condition_result_count"] == 4


def test_run_worker_can_consume_one_queue_case(tmp_path: Path) -> None:
    output_path = tmp_path / "queue_records.jsonl"
    dataset_csv_path = tmp_path / "queue_dataset.csv"
    task_source = FakeTaskSource(
        [
            TestCase(
                case_id="queue_case_8",
                target="awsim",
                case_kind="uturn",
                input={"fixture_path": str(Path("tests/fixtures/awsim/timeout_trace.txt").resolve())},
                meta={"global_loop_num": 8},
            )
        ]
    )

    def backend(case):
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.TIMEOUT,
            evidence={"trace_json": case.input["fixture_path"]},
        )

    def interpreter(raw):
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.TIMEOUT,
            evidence=dict(raw.evidence),
            meta={},
        )

    exit_code = run_worker(
        [
            "--queue-actor-name",
            "TaskQueueActor",
            "--output",
            str(output_path),
            "--worker-id",
            "worker-queue",
            "--dataset-csv",
            str(dataset_csv_path),
        ],
        backend=backend,
        result_interpreter=interpreter,
        task_source=task_source,
    )

    assert exit_code == 0
    assert task_source.status_updates == [
        ("worker-queue", "waiting"),
        ("worker-queue", "running"),
        ("worker-queue", "timeout"),
        ("worker-queue", "waiting"),
    ]
    assert task_source.completions == [(8, "timeout")]
    payload = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert payload["status"] == "timeout"
    assert "timeout" in dataset_csv_path.read_text(encoding="utf-8")


def test_run_worker_accepts_direct_simulation_input(tmp_path: Path) -> None:
    output_path = tmp_path / "simulation_records.jsonl"

    def backend(case):
        assert case.input["dx0"] == 15.0
        assert case.input["scenario_type"] == "uturn"
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.SUCCESS,
            evidence={"trace_json": "/tmp/uturn_eval_sim1.json"},
        )

    def interpreter(raw):
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
            output={"c_collision": 0},
            evidence=dict(raw.evidence),
            meta={"verifier_name": "fake"},
        )

    exit_code = run_worker(
        [
            "--param",
            "dx0=15.0",
            "--param",
            "ego_speed=35.0",
            "--param",
            "npc_speed=14.0",
            "--output",
            str(output_path),
        ],
        backend=backend,
        result_interpreter=interpreter,
    )

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert payload["case_id"] == "uturn_direct"
    assert payload["status"] == "success"


def test_run_worker_with_summary_preserves_explicit_config_module_from_sys_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_path = tmp_path / "simulation_records.jsonl"
    captured: dict[str, object] = {}

    def backend(case):
        captured["config_module"] = case.meta.get("config_module")
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.SUCCESS,
            evidence={"trace_json": "/tmp/uturn_eval_sim1.json"},
        )

    def interpreter(raw):
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
            output={"c_collision": 0},
            evidence=dict(raw.evidence),
            meta={"verifier_name": "fake"},
        )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_worker_v2.py",
            "--dx0",
            "15.0",
            "--ego_speed",
            "35.0",
            "--npc_speed",
            "14.0",
            "--config-module",
            "targets.awsim.case_kinds.uturn_timeout_smoke",
            "--output",
            str(output_path),
        ],
    )

    summary = run_worker_with_summary(
        backend=backend,
        result_interpreter=interpreter,
    )

    assert summary["exit_code"] == 0
    assert captured["config_module"] == "targets.awsim.case_kinds.uturn_timeout_smoke"
    payload = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert payload["meta"]["config_module"] == "targets.awsim.case_kinds.uturn_timeout_smoke"


def test_run_worker_accepts_legacy_double_dash_simulation_params(tmp_path: Path) -> None:
    output_path = tmp_path / "legacy_simulation_records.jsonl"

    def backend(case):
        assert case.input["dx0"] == 15.0
        assert case.input["ego_speed"] == 35.0
        assert case.input["npc_speed"] == 14.0
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.SUCCESS,
            evidence={"trace_json": "/tmp/uturn_eval_sim1.json"},
        )

    def interpreter(raw):
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
            output={"c_collision": 0},
            evidence=dict(raw.evidence),
            meta={"verifier_name": "fake"},
        )

    exit_code = run_worker(
        [
            "--dx0",
            "15.0",
            "--ego_speed",
            "35.0",
            "--npc_speed",
            "14.0",
            "--output",
            str(output_path),
        ],
        backend=backend,
        result_interpreter=interpreter,
    )

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert payload["status"] == "success"


def test_run_worker_accepts_legacy_dkw_flags_for_cli_compatibility(tmp_path: Path) -> None:
    output_path = tmp_path / "legacy_dkw_records.jsonl"

    def backend(case):
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.SUCCESS,
            evidence={"trace_json": "/tmp/uturn_eval_sim1.json"},
        )

    def interpreter(raw):
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
            output={"c_collision": 0},
            evidence=dict(raw.evidence),
            meta={"verifier_name": "fake"},
        )

    exit_code = run_worker(
        [
            "--dx0",
            "15.0",
            "--ego_speed",
            "35.0",
            "--npc_speed",
            "14.0",
            "--dkw-region",
            "intersect_safe",
            "--dkw-pure-smc",
            "--dkw-simultaneous",
            "--dkw-bounds",
            '{"dx0": [10.0, 20.0]}',
            "--output",
            str(output_path),
        ],
        backend=backend,
        result_interpreter=interpreter,
    )

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert payload["status"] == "success"


def test_run_worker_returns_zero_when_queue_has_no_task(tmp_path: Path) -> None:
    output_path = tmp_path / "queue_empty.jsonl"
    task_source = FakeTaskSource([])

    exit_code = run_worker(
        [
            "--queue-actor-name",
            "TaskQueueActor",
            "--output",
            str(output_path),
        ],
        backend=lambda _: None,
        result_interpreter=lambda _: None,
        task_source=task_source,
    )

    assert exit_code == 0
    assert not output_path.exists()


def test_run_worker_skips_duplicate_queue_case_when_history_contains_loop_num(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "queue_records.jsonl"
    history_path = tmp_path / "processed_loops_history.csv"
    history_path.write_text("8\n", encoding="utf-8")
    task_source = FakeTaskSource(
        [
            TestCase(
                case_id="queue_case_8",
                target="awsim",
                case_kind="uturn",
                input={"fixture_path": str(Path("tests/fixtures/awsim/timeout_trace.txt").resolve())},
                meta={"global_loop_num": 8},
            )
        ]
    )

    exit_code = run_worker(
        [
            "--queue-actor-name",
            "TaskQueueActor",
            "--output",
            str(output_path),
            "--worker-id",
            "worker-queue",
            "--history-path",
            str(history_path),
        ],
        backend=lambda _: None,
        result_interpreter=lambda _: None,
        task_source=task_source,
    )

    assert exit_code == 0
    assert task_source.status_updates == [
        ("worker-queue", "waiting"),
        ("worker-queue", "skipped_duplicate"),
        ("worker-queue", "waiting"),
    ]
    assert task_source.completions == [(8, "skipped_duplicate")]
    assert not output_path.exists()


def test_run_worker_drains_multiple_queue_cases_until_empty(tmp_path: Path) -> None:
    output_path = tmp_path / "queue_records.jsonl"
    task_source = FakeTaskSource(
        [
            TestCase(
                case_id="queue_case_1",
                target="awsim",
                case_kind="uturn",
                input={"fixture_path": str(Path("tests/fixtures/awsim/timeout_trace.txt").resolve())},
                meta={"global_loop_num": 1},
            ),
            TestCase(
                case_id="queue_case_2",
                target="awsim",
                case_kind="uturn",
                input={"fixture_path": str(Path("tests/fixtures/awsim/timeout_trace.txt").resolve())},
                meta={"global_loop_num": 2},
            ),
        ]
    )

    def backend(case):
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.TIMEOUT,
            evidence={"trace_json": case.input["fixture_path"]},
        )

    def interpreter(raw):
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.TIMEOUT,
            evidence=dict(raw.evidence),
        )

    exit_code = run_worker(
        [
            "--queue-actor-name",
            "TaskQueueActor",
            "--output",
            str(output_path),
            "--worker-id",
            "worker-queue",
        ],
        backend=backend,
        result_interpreter=interpreter,
        task_source=task_source,
    )

    assert exit_code == 0
    assert task_source.completions == [(1, "timeout"), (2, "timeout")]
    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_run_worker_requests_refresh_after_refresh_interval(tmp_path: Path) -> None:
    output_path = tmp_path / "queue_records.jsonl"
    task_source = FakeTaskSource(
        [
            TestCase(
                case_id="queue_case_1",
                target="awsim",
                case_kind="uturn",
                input={"fixture_path": str(Path("tests/fixtures/awsim/timeout_trace.txt").resolve())},
                meta={"global_loop_num": 1},
            ),
            TestCase(
                case_id="queue_case_2",
                target="awsim",
                case_kind="uturn",
                input={"fixture_path": str(Path("tests/fixtures/awsim/timeout_trace.txt").resolve())},
                meta={"global_loop_num": 2},
            ),
        ]
    )

    def backend(case):
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.TIMEOUT,
            evidence={"trace_json": case.input["fixture_path"]},
        )

    def interpreter(raw):
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.TIMEOUT,
            evidence=dict(raw.evidence),
        )

    exit_code = run_worker(
        [
            "--queue-actor-name",
            "TaskQueueActor",
            "--output",
            str(output_path),
            "--worker-id",
            "worker-queue",
            "--refresh-interval",
            "1",
        ],
        backend=backend,
        result_interpreter=interpreter,
        task_source=task_source,
    )

    assert exit_code == 0
    assert task_source.completions == [(1, "timeout")]
    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


def test_run_worker_with_summary_reports_refresh_and_dataset_updates(tmp_path: Path) -> None:
    output_path = tmp_path / "queue_records.jsonl"
    dataset_csv_path = tmp_path / "queue_dataset.csv"
    task_source = FakeTaskSource(
        [
            TestCase(
                case_id="queue_case_1",
                target="awsim",
                case_kind="uturn",
                input={"fixture_path": str(Path("tests/fixtures/awsim/timeout_trace.txt").resolve())},
                meta={"global_loop_num": 1},
            ),
            TestCase(
                case_id="queue_case_2",
                target="awsim",
                case_kind="uturn",
                input={"fixture_path": str(Path("tests/fixtures/awsim/timeout_trace.txt").resolve())},
                meta={"global_loop_num": 2},
            ),
        ]
    )

    def backend(case):
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.TIMEOUT,
            evidence={"trace_json": case.input["fixture_path"]},
        )

    def interpreter(raw):
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.TIMEOUT,
            evidence=dict(raw.evidence),
        )

    summary = run_worker_with_summary(
        [
            "--queue-actor-name",
            "TaskQueueActor",
            "--output",
            str(output_path),
            "--worker-id",
            "worker-queue",
            "--dataset-csv",
            str(dataset_csv_path),
            "--refresh-interval",
            "1",
        ],
        backend=backend,
        result_interpreter=interpreter,
        task_source=task_source,
    )

    assert summary["exit_code"] == 0
    assert summary["status"] == "timeout"
    assert summary["terminal_status"] == "refresh_requested"
    assert summary["processed_count"] == 1
    assert summary["skipped_count"] == 0
    assert summary["sink_warnings"] == []
    assert task_source.completions == [(1, "timeout")]
    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    dataset_csv = dataset_csv_path.read_text(encoding="utf-8")
    assert "queue_case_1" in dataset_csv
    assert "[ERROR: TIMEOUT]" in dataset_csv


def test_run_worker_cli_restarts_on_refresh_for_queue_mode(tmp_path: Path) -> None:
    output_path = tmp_path / "queue_records.jsonl"
    task_source = FakeTaskSource(
        [
            TestCase(
                case_id="queue_case_1",
                target="awsim",
                case_kind="uturn",
                input={"fixture_path": str(Path("tests/fixtures/awsim/timeout_trace.txt").resolve())},
                meta={"global_loop_num": 1},
            ),
            TestCase(
                case_id="queue_case_2",
                target="awsim",
                case_kind="uturn",
                input={"fixture_path": str(Path("tests/fixtures/awsim/timeout_trace.txt").resolve())},
                meta={"global_loop_num": 2},
            ),
        ]
    )

    def backend(case):
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.TIMEOUT,
            evidence={"trace_json": case.input["fixture_path"]},
        )

    def interpreter(raw):
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.TIMEOUT,
            evidence=dict(raw.evidence),
        )

    exit_code = run_worker_cli(
        [
            "--queue-actor-name",
            "TaskQueueActor",
            "--output",
            str(output_path),
            "--worker-id",
            "worker-queue",
            "--refresh-interval",
            "1",
            "--restart-on-refresh",
        ],
        backend=backend,
        result_interpreter=interpreter,
        task_source=task_source,
    )

    assert exit_code == 0
    assert task_source.completions == [(1, "timeout"), (2, "timeout")]
    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_run_worker_appends_history_after_successful_direct_run(tmp_path: Path) -> None:
    output_path = tmp_path / "records.jsonl"
    history_path = tmp_path / "processed_loops_history.csv"

    def backend(case):
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.SUCCESS,
            evidence={"trace_json": case.input["fixture_path"]},
        )

    def interpreter(raw):
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
            output={"c_collision": 0},
            evidence=dict(raw.evidence),
            meta={"verifier_name": "fake"},
        )

    exit_code = run_worker(
        [
            "--fixture",
            "tests/fixtures/awsim/timeout_trace.txt",
            "--output",
            str(output_path),
            "--history-path",
            str(history_path),
            "--history-loop-num",
            "21",
        ],
        backend=backend,
        result_interpreter=interpreter,
    )

    assert exit_code == 0
    assert history_path.read_text(encoding="utf-8").strip() == "21"
