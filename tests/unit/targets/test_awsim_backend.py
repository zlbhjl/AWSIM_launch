from pathlib import Path

from contracts.execution import RunStatus, TestCase
from runtime.container.profile import build_runtime_profile
from targets.awsim.backend import AWSIMBackend, AWSIMBackendConfig, CommandResult
from targets.awsim.readiness_probe import AWSIMReadinessResult


def test_awsim_backend_returns_success_for_normal_fixture() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "awsim"
        / "normal_trace_maude.json"
    )

    result = AWSIMBackend().run(
        TestCase(
            case_id="backend_1",
            target="awsim",
            case_kind="uturn",
            input={"fixture_path": str(fixture_path)},
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert result.evidence["trace_json"].endswith("normal_trace_maude.json")


def test_awsim_backend_returns_timeout_for_timeout_marker() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "awsim"
        / "timeout_trace.txt"
    )

    result = AWSIMBackend().run(
        TestCase(
            case_id="backend_2",
            target="awsim",
            case_kind="uturn",
            input={"fixture_path": str(fixture_path)},
        )
    )

    assert result.status is RunStatus.TIMEOUT


def test_awsim_backend_runs_simulation_command_and_promotes_local_trace(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def command_runner(command, *, cwd, env, source_setup_script):
        captured["command"] = list(command)
        captured["cwd"] = cwd
        captured["env"] = dict(env)
        local_trace = tmp_path / "uturn_test_sim5.json"
        local_trace.write_text("{}", encoding="utf-8")
        (tmp_path / "uturn_test_sim5_footage.mp4").write_text("video", encoding="utf-8")
        (tmp_path / "uturn_test_sim5_footage.meta.json").write_text("{}", encoding="utf-8")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
            ),
            output_dir=tmp_path,
            timeout_sec=0.1,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
        ),
        command_runner=command_runner,
    )
    result = backend.run(
        TestCase(
            case_id="backend_3",
            target="awsim",
            case_kind="uturn",
            input={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0, "ext_mode": "maude"},
            meta={"global_loop_num": 5},
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert result.evidence["trace_json"].endswith("uturn_eval_sim5.json")
    assert (tmp_path / "uturn_eval_sim5.json").exists()
    assert captured["command"] == [
        "python3",
        "run_scenario.py",
        "--type",
        "uturn",
        "--dx0",
        "15.0",
        "--ego_speed",
        "35.0",
        "--npc_speed",
        "14.0",
    ]
    assert "ext_mode" not in " ".join(captured["command"])
    assert result.evidence["video"].endswith("uturn_eval_sim5_footage.mp4")
    assert result.evidence["video_meta_json"].endswith("uturn_eval_sim5_footage.meta.json")
    assert (tmp_path / "uturn_eval_sim5_footage.mp4").exists()
    assert (tmp_path / "uturn_eval_sim5_footage.meta.json").exists()
    assert not (tmp_path / "uturn_test_sim5_footage.mp4").exists()
    assert not (tmp_path / "uturn_test_sim5_footage.meta.json").exists()


def test_awsim_backend_forwards_non_default_config_module_to_scenario_runner(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def command_runner(command, *, cwd, env, source_setup_script):
        captured["command"] = list(command)
        local_trace = tmp_path / "uturn_test_sim6.json"
        local_trace.write_text("{}", encoding="utf-8")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
            ),
            output_dir=tmp_path,
            timeout_sec=0.1,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
        ),
        command_runner=command_runner,
    )
    result = backend.run(
        TestCase(
            case_id="backend_custom_config",
            target="awsim",
            case_kind="uturn",
            input={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
            meta={
                "global_loop_num": 6,
                "config_module": "targets.awsim.case_kinds.uturn_timeout_smoke",
            },
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert captured["command"] == [
        "python3",
        "run_scenario.py",
        "--type",
        "uturn",
        "--config-module",
        "targets.awsim.case_kinds.uturn_timeout_smoke",
        "--dx0",
        "15.0",
        "--ego_speed",
        "35.0",
        "--npc_speed",
        "14.0",
    ]


def test_awsim_backend_promotes_local_trace_for_explicit_expected_trace_path(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "traces"
    output_dir.mkdir()

    def command_runner(command, *, cwd, env, source_setup_script):
        local_trace = output_dir / "uturn_test_sim3.json"
        local_trace.write_text("{}", encoding="utf-8")
        (output_dir / "uturn_test_sim3_footage.mp4").write_text("video", encoding="utf-8")
        (output_dir / "uturn_test_sim3_footage.meta.json").write_text("{}", encoding="utf-8")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
            ),
            output_dir=output_dir,
            timeout_sec=0.1,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
        ),
        command_runner=command_runner,
    )
    result = backend.run(
        TestCase(
            case_id="backend_explicit_trace",
            target="awsim",
            case_kind="uturn",
            input={
                "dx0": 15.0,
                "ego_speed": 35.0,
                "npc_speed": 14.0,
                "output_dir": str(output_dir),
                "expected_trace_path": str(output_dir / "uturn_eval_sim3.json"),
                "local_loop_num": 3,
            },
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert result.evidence["trace_json"].endswith("uturn_eval_sim3.json")
    assert (output_dir / "uturn_eval_sim3.json").exists()
    assert (output_dir / "uturn_eval_sim3_footage.mp4").exists()
    assert (output_dir / "uturn_eval_sim3_footage.meta.json").exists()
    assert not (output_dir / "uturn_test_sim3.json").exists()
    assert not (output_dir / "uturn_test_sim3_footage.mp4").exists()
    assert not (output_dir / "uturn_test_sim3_footage.meta.json").exists()


def test_awsim_backend_writes_timeout_marker_when_trace_never_appears(
    tmp_path: Path,
) -> None:
    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
            ),
            output_dir=tmp_path,
            timeout_sec=0.0,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
        ),
        command_runner=lambda *args, **kwargs: CommandResult(returncode=0),
    )
    result = backend.run(
        TestCase(
            case_id="backend_4",
            target="awsim",
            case_kind="uturn",
            input={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
            meta={"global_loop_num": 9},
        )
    )

    assert result.status is RunStatus.TIMEOUT
    timeout_path = tmp_path / "uturn_eval_sim9.json"
    assert timeout_path.exists()
    assert timeout_path.read_text(encoding="utf-8") == "TIMEOUT"


def test_awsim_backend_removes_stale_expected_and_local_artifacts_before_run(
    tmp_path: Path,
) -> None:
    expected_trace = tmp_path / "uturn_eval_sim4.json"
    local_trace = tmp_path / "uturn_test_sim4.json"
    expected_trace.write_text("stale", encoding="utf-8")
    local_trace.write_text("stale", encoding="utf-8")
    (tmp_path / "uturn_eval_sim4_footage.mp4").write_text("stale", encoding="utf-8")
    (tmp_path / "uturn_eval_sim4_footage.meta.json").write_text("stale", encoding="utf-8")
    (tmp_path / "uturn_test_sim4_footage.mp4").write_text("stale", encoding="utf-8")
    (tmp_path / "uturn_test_sim4_footage.meta.json").write_text("stale", encoding="utf-8")

    def command_runner(command, *, cwd, env, source_setup_script):
        assert not expected_trace.exists()
        assert not local_trace.exists()
        assert not (tmp_path / "uturn_eval_sim4_footage.mp4").exists()
        assert not (tmp_path / "uturn_eval_sim4_footage.meta.json").exists()
        assert not (tmp_path / "uturn_test_sim4_footage.mp4").exists()
        assert not (tmp_path / "uturn_test_sim4_footage.meta.json").exists()
        local_trace.write_text("{}", encoding="utf-8")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
            ),
            output_dir=tmp_path,
            timeout_sec=0.1,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
        ),
        command_runner=command_runner,
    )

    result = backend.run(
        TestCase(
            case_id="backend_6",
            target="awsim",
            case_kind="uturn",
            input={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
            meta={"global_loop_num": 4},
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert expected_trace.exists()


def test_awsim_backend_headless_mode_applies_xvfb_env_and_stops_session(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    calls: list[tuple[str, object]] = []

    class FakeXvfbController:
        def start(self, config):
            calls.append(("start", config.enabled))
            return object()

        def apply_environment(self, config, *, env):
            updated_env = dict(env)
            updated_env["DISPLAY"] = ":199"
            updated_env["VK_ICD_FILENAMES"] = "/tmp/fake_icd.json"
            return updated_env

        def stop(self, session):
            calls.append(("stop", session is not None))

    def command_runner(command, *, cwd, env, source_setup_script):
        captured["env"] = dict(env)
        local_trace = tmp_path / "uturn_test_sim7.json"
        local_trace.write_text("{}", encoding="utf-8")
        return CommandResult(returncode=0, stdout="ok", stderr="")

    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
                headless=True,
            ),
            output_dir=tmp_path,
            timeout_sec=0.1,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
        ),
        command_runner=command_runner,
        xvfb_controller=FakeXvfbController(),
    )

    result = backend.run(
        TestCase(
            case_id="backend_5",
            target="awsim",
            case_kind="uturn",
            input={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
            meta={"global_loop_num": 7},
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert captured["env"]["DISPLAY"] == ":199"
    assert captured["env"]["VK_ICD_FILENAMES"] == "/tmp/fake_icd.json"
    assert calls == [("start", True), ("stop", True)]


def test_awsim_backend_runs_startup_readiness_probe_before_launching_client(
    tmp_path: Path,
) -> None:
    class FakeProcessManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def start_infra_processes(self, tasks, *, sim_num, output_dir, source_setup_script, env):
            self.calls.append(("start_infra_processes", sim_num))

        def launch_client(self, command, *, work_dir, source_setup_script, output_dir, log_filename, env):
            self.calls.append(("launch_client", log_filename))
            (tmp_path / "uturn_test_sim1.json").write_text("{}", encoding="utf-8")
            return type("Managed", (), {"process": type("Proc", (), {"poll": lambda self: 0})()})()

        def stop_case_client(self):
            self.calls.append(("stop_case_client", None))

        def refresh_non_resident_infra(self):
            self.calls.append(("refresh_non_resident_infra", None))

        def shutdown_all(self):
            self.calls.append(("shutdown_all", None))

    class FakeReadinessProbe:
        def __init__(self) -> None:
            self.calls: list[object] = []

        def probe(self, *, cwd, env, source_setup_script, config):
            self.calls.append(config)
            return AWSIMReadinessResult(
                ready=True,
                attempts=2,
                checked_services=config.required_services,
                message="ready",
            )

    process_manager = FakeProcessManager()
    readiness_probe = FakeReadinessProbe()
    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
            ),
            output_dir=tmp_path,
            timeout_sec=0.1,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
            manage_infra=True,
            reuse_infra_between_runs=True,
            initial_warmup_sec=0.0,
            startup_probe_timeout_sec=30.0,
            refresh_probe_timeout_sec=45.0,
            startup_probe_stability_checks=2,
            refresh_probe_stability_checks=3,
        ),
        process_manager=process_manager,
        readiness_probe=readiness_probe,
    )

    result = backend.run(
        TestCase(
            case_id="backend_readiness_startup",
            target="awsim",
            case_kind="uturn",
            input={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
            meta={"global_loop_num": 1},
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert len(readiness_probe.calls) == 1
    assert readiness_probe.calls[0].probe_timeout_sec == 30.0
    assert readiness_probe.calls[0].stability_checks == 2
    assert process_manager.calls == [
        ("start_infra_processes", 1),
        ("launch_client", "scenario_runner.log"),
        ("stop_case_client", None),
    ]


def test_awsim_backend_retries_after_readiness_probe_failure(
    tmp_path: Path,
) -> None:
    class FakeProcessManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []
            self.launch_count = 0

        def start_infra_processes(self, tasks, *, sim_num, output_dir, source_setup_script, env):
            self.calls.append(("start_infra_processes", sim_num))

        def launch_client(self, command, *, work_dir, source_setup_script, output_dir, log_filename, env):
            self.launch_count += 1
            self.calls.append(("launch_client", self.launch_count))
            (tmp_path / "uturn_test_sim1.json").write_text("{}", encoding="utf-8")
            return type("Managed", (), {"process": type("Proc", (), {"poll": lambda self: 0})()})()

        def stop_case_client(self):
            self.calls.append(("stop_case_client", None))

        def refresh_non_resident_infra(self):
            self.calls.append(("refresh_non_resident_infra", None))

        def shutdown_all(self):
            self.calls.append(("shutdown_all", None))

    class FakeReadinessProbe:
        def __init__(self) -> None:
            self.calls: list[object] = []
            self.ready_states = [False, True]

        def probe(self, *, cwd, env, source_setup_script, config):
            self.calls.append(config)
            ready = self.ready_states.pop(0)
            return AWSIMReadinessResult(
                ready=ready,
                attempts=1,
                checked_services=config.required_services,
                message="ready" if ready else "execution_state_unavailable",
            )

    process_manager = FakeProcessManager()
    readiness_probe = FakeReadinessProbe()
    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
            ),
            output_dir=tmp_path,
            timeout_sec=0.1,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
            manage_infra=True,
            reuse_infra_between_runs=True,
            initial_warmup_sec=0.0,
            extra_refresh_warmup_sec=0.0,
            startup_probe_timeout_sec=30.0,
            refresh_probe_timeout_sec=45.0,
            startup_probe_stability_checks=2,
            refresh_probe_stability_checks=3,
            max_refresh_probe_retries=1,
        ),
        process_manager=process_manager,
        readiness_probe=readiness_probe,
    )

    result = backend.run(
        TestCase(
            case_id="backend_readiness_retry",
            target="awsim",
            case_kind="uturn",
            input={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
            meta={"global_loop_num": 1},
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert len(readiness_probe.calls) == 2
    assert readiness_probe.calls[0].probe_timeout_sec == 30.0
    assert readiness_probe.calls[1].probe_timeout_sec == 45.0
    assert readiness_probe.calls[1].stability_checks == 3
    assert process_manager.calls == [
        ("start_infra_processes", 1),
        ("refresh_non_resident_infra", None),
        ("start_infra_processes", 1),
        ("launch_client", 1),
        ("stop_case_client", None),
    ]


def test_awsim_backend_raises_after_exhausting_readiness_probe_retries(
    tmp_path: Path,
) -> None:
    class FakeProcessManager:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def start_infra_processes(self, tasks, *, sim_num, output_dir, source_setup_script, env):
            self.calls.append("start_infra_processes")

        def launch_client(self, command, *, work_dir, source_setup_script, output_dir, log_filename, env):
            self.calls.append("launch_client")
            raise AssertionError("launch_client should not be called when readiness never stabilizes")

        def stop_case_client(self):
            self.calls.append("stop_case_client")

        def refresh_non_resident_infra(self):
            self.calls.append("refresh_non_resident_infra")

        def shutdown_all(self):
            self.calls.append("shutdown_all")

    class FakeReadinessProbe:
        def __init__(self) -> None:
            self.calls = 0

        def probe(self, *, cwd, env, source_setup_script, config):
            self.calls += 1
            return AWSIMReadinessResult(
                ready=False,
                attempts=1,
                checked_services=config.required_services,
                message="execution_state_unavailable",
            )

    process_manager = FakeProcessManager()
    readiness_probe = FakeReadinessProbe()
    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
            ),
            output_dir=tmp_path,
            timeout_sec=0.1,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
            manage_infra=True,
            reuse_infra_between_runs=True,
            initial_warmup_sec=0.0,
            startup_probe_timeout_sec=30.0,
            refresh_probe_timeout_sec=45.0,
            max_refresh_probe_retries=1,
        ),
        process_manager=process_manager,
        readiness_probe=readiness_probe,
    )

    try:
        backend.run(
            TestCase(
                case_id="backend_readiness_failure",
                target="awsim",
                case_kind="uturn",
                input={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
                meta={"global_loop_num": 1},
            )
        )
    except RuntimeError as exc:
        assert "AWSIM readiness probe failed" in str(exc)
    else:
        raise AssertionError("Expected readiness probe failure to raise RuntimeError")

    assert readiness_probe.calls == 2
    assert process_manager.calls == [
        "start_infra_processes",
        "refresh_non_resident_infra",
        "start_infra_processes",
        "refresh_non_resident_infra",
    ]
