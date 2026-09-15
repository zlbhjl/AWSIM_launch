from pathlib import Path

from contracts.execution import RunStatus, TestCase
from runtime.container.profile import build_runtime_profile
from targets.awsim.backend import AWSIMBackend, AWSIMBackendConfig


class FakeClientProcess:
    def __init__(self, returncode: int | None = 0) -> None:
        self.returncode = returncode
        self.pid = 321

    def poll(self) -> int | None:
        return self.returncode


class FakeProcessManager:
    def __init__(self, trace_path: Path) -> None:
        self.trace_path = trace_path
        self.infra_calls: list[dict[str, object]] = []
        self.client_calls: list[dict[str, object]] = []
        self.stop_case_client_calls = 0
        self.stop_case_scoped_processes_calls = 0
        self.refresh_non_resident_infra_calls = 0
        self.shutdown_all_calls = 0

    def start_infra_processes(
        self,
        tasks,
        *,
        sim_num,
        output_dir,
        source_setup_script,
        env,
    ):
        self.infra_calls.append(
            {
                "task_names": [task.name for task in tasks],
                "task_commands": [task.command for task in tasks],
                "sim_num": sim_num,
                "output_dir": Path(output_dir),
                "source_setup_script": source_setup_script,
                "env": dict(env),
            }
        )
        return []

    def launch_client(
        self,
        command,
        *,
        work_dir,
        source_setup_script=None,
        output_dir=None,
        log_filename=None,
        env=None,
    ):
        self.client_calls.append(
            {
                "command": list(command) if isinstance(command, list) else command,
                "work_dir": Path(work_dir),
                "source_setup_script": source_setup_script,
                "output_dir": Path(output_dir) if output_dir is not None else None,
                "log_filename": log_filename,
                "env": dict(env or {}),
            }
        )
        self.trace_path.write_text("{}", encoding="utf-8")
        numbered_trace_path = self.trace_path.with_name(
            f"{self.trace_path.stem[:-1]}{len(self.client_calls)}{self.trace_path.suffix}"
        )
        numbered_trace_path.write_text("{}", encoding="utf-8")

        class ManagedClient:
            name = "Scenario Client"
            process = FakeClientProcess()

        return ManagedClient()

    def stop_case_client(self) -> None:
        self.stop_case_client_calls += 1

    def stop_case_scoped_processes(self) -> None:
        self.stop_case_scoped_processes_calls += 1

    def refresh_non_resident_infra(self) -> None:
        self.refresh_non_resident_infra_calls += 1

    def shutdown_all(self) -> None:
        self.shutdown_all_calls += 1


class NoTraceProcessManager(FakeProcessManager):
    def __init__(
        self,
        trace_path: Path,
        *,
        client_returncode: int | None = 0,
    ) -> None:
        super().__init__(trace_path)
        self.client_returncode = client_returncode

    def launch_client(
        self,
        command,
        *,
        work_dir,
        source_setup_script=None,
        output_dir=None,
        log_filename=None,
        env=None,
    ):
        self.client_calls.append(
            {
                "command": list(command) if isinstance(command, list) else command,
                "work_dir": Path(work_dir),
                "source_setup_script": source_setup_script,
                "output_dir": Path(output_dir) if output_dir is not None else None,
                "log_filename": log_filename,
                "env": dict(env or {}),
            }
        )

        class ManagedClient:
            name = "Scenario Client"
            process = FakeClientProcess(returncode=self.client_returncode)

        return ManagedClient()


class TimeoutTraceProcessManager(FakeProcessManager):
    def launch_client(
        self,
        command,
        *,
        work_dir,
        source_setup_script=None,
        output_dir=None,
        log_filename=None,
        env=None,
    ):
        self.client_calls.append(
            {
                "command": list(command) if isinstance(command, list) else command,
                "work_dir": Path(work_dir),
                "source_setup_script": source_setup_script,
                "output_dir": Path(output_dir) if output_dir is not None else None,
                "log_filename": log_filename,
                "env": dict(env or {}),
            }
        )
        self.trace_path.write_text("{}", encoding="utf-8")

        class ManagedClient:
            name = "Scenario Client"
            process = FakeClientProcess(returncode=124)

        return ManagedClient()


class TraceAfterTimeoutProcessManager(FakeProcessManager):
    def launch_client(
        self,
        command,
        *,
        work_dir,
        source_setup_script=None,
        output_dir=None,
        log_filename=None,
        env=None,
    ):
        self.client_calls.append({"command": list(command)})
        if len(self.client_calls) == 2:
            (Path(output_dir) / "uturn_test_sim2.json").write_text(
                "{}",
                encoding="utf-8",
            )

        class ManagedClient:
            name = "Scenario Client"
            process = FakeClientProcess()

        return ManagedClient()


def test_awsim_backend_manage_infra_starts_processes_and_runs_async_client(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "uturn_test_sim5.json"
    process_manager = FakeProcessManager(trace_path)
    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
                headless=True,
                launch_dir=tmp_path,
            ),
            output_dir=tmp_path,
            timeout_sec=0.1,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
            manage_infra=True,
            infra_home_dir=tmp_path,
        ),
        process_manager=process_manager,
    )

    result = backend.run(
        TestCase(
            case_id="backend_infra_1",
            target="awsim",
            case_kind="uturn",
            input={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
            meta={"global_loop_num": 5},
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert result.evidence["trace_json"].endswith("uturn_eval_sim5.json")
    assert process_manager.infra_calls[0]["task_names"] == [
        "AWSIM Labs",
        "Autoware",
        "Runtime Monitor",
        "AW Checker (Safety Evaluator)",
    ]
    assert process_manager.client_calls[0]["command"] == [
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
    assert process_manager.client_calls[0]["env"]["AW_OUTPUT_DIR"] == str(tmp_path)
    assert process_manager.shutdown_all_calls == 1


def test_awsim_backend_manage_infra_uses_trace_when_client_exits_124(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "uturn_test_sim1.json"
    process_manager = TimeoutTraceProcessManager(trace_path)
    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
                launch_dir=tmp_path,
            ),
            output_dir=tmp_path,
            timeout_sec=0.1,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
            manage_infra=True,
        ),
        process_manager=process_manager,
    )

    result = backend.run(
        TestCase(
            case_id="backend_infra_timeout_with_trace",
            target="awsim",
            case_kind="uturn",
            input={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
            meta={"global_loop_num": 6},
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert result.meta["returncode"] == 124
    assert result.meta["client_process_state"] == "exited"
    assert result.evidence["trace_json"].endswith("uturn_eval_sim6.json")


def test_awsim_backend_manage_infra_forwards_input_ext_mode_to_checker(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "uturn_test_sim2.json"
    process_manager = FakeProcessManager(trace_path)
    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
                launch_dir=tmp_path,
            ),
            output_dir=tmp_path,
            timeout_sec=0.1,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
            manage_infra=True,
            infra_ext_mode="cvm",
        ),
        process_manager=process_manager,
    )

    result = backend.run(
        TestCase(
            case_id="backend_infra_2",
            target="awsim",
            case_kind="uturn",
            input={
                "dx0": 15.0,
                "ego_speed": 35.0,
                "npc_speed": 14.0,
                "ext_mode": "maude",
            },
            meta={"global_loop_num": 2},
        )
    )

    assert result.status is RunStatus.SUCCESS
    checker_task_name = process_manager.infra_calls[0]["task_names"][-1]
    checker_task_command = process_manager.infra_calls[0]["task_commands"][-1]
    assert checker_task_name == "AW Checker (Safety Evaluator)"
    assert checker_task_command == "python3 awchecker.py --type uturn --ext_mode maude"


def test_awsim_backend_manage_infra_can_skip_awchecker(tmp_path: Path) -> None:
    trace_path = tmp_path / "uturn_test_sim3.json"
    process_manager = FakeProcessManager(trace_path)
    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
                launch_dir=tmp_path,
            ),
            output_dir=tmp_path,
            timeout_sec=0.1,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
            manage_infra=True,
            include_awchecker=False,
        ),
        process_manager=process_manager,
    )

    result = backend.run(
        TestCase(
            case_id="backend_infra_3",
            target="awsim",
            case_kind="uturn",
            input={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
            meta={"global_loop_num": 3},
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert process_manager.infra_calls[0]["task_names"] == [
        "AWSIM Labs",
        "Autoware",
        "Runtime Monitor",
    ]


def test_awsim_backend_manage_infra_uses_input_local_loop_num_for_sim_number(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "uturn_test_sim5.json"
    process_manager = FakeProcessManager(trace_path)
    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
                launch_dir=tmp_path,
            ),
            output_dir=tmp_path,
            timeout_sec=0.1,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
            manage_infra=True,
            include_awchecker=False,
        ),
        process_manager=process_manager,
    )

    result = backend.run(
        TestCase(
            case_id="backend_infra_local_loop_num",
            target="awsim",
            case_kind="uturn",
            input={
                "dx0": 15.0,
                "ego_speed": 35.0,
                "npc_speed": 14.0,
                "local_loop_num": 5,
                "expected_trace_path": str(tmp_path / "uturn_eval_sim5.json"),
            },
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert process_manager.infra_calls[0]["sim_num"] == 5


def test_awsim_backend_reuses_infra_between_runs_and_warms_up_once(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "uturn_test_sim1.json"
    process_manager = FakeProcessManager(trace_path)
    sleep_calls: list[float] = []
    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
                launch_dir=tmp_path,
            ),
            output_dir=tmp_path,
            timeout_sec=0.1,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
            manage_infra=True,
            reuse_infra_between_runs=True,
            initial_warmup_sec=40.0,
        ),
        process_manager=process_manager,
        sleeper=lambda seconds: sleep_calls.append(seconds),
    )

    first = backend.run(
        TestCase(
            case_id="backend_infra_reuse_1",
            target="awsim",
            case_kind="uturn",
            input={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
            meta={"global_loop_num": 1},
        )
    )
    second = backend.run(
        TestCase(
            case_id="backend_infra_reuse_2",
            target="awsim",
            case_kind="uturn",
            input={"dx0": 16.0, "ego_speed": 35.0, "npc_speed": 14.0},
            meta={"global_loop_num": 2},
        )
    )
    backend.close()

    assert first.status is RunStatus.SUCCESS
    assert second.status is RunStatus.SUCCESS
    assert len(process_manager.infra_calls) == 1
    assert len(process_manager.client_calls) == 2
    assert process_manager.stop_case_client_calls == 2
    assert process_manager.stop_case_scoped_processes_calls == 0
    assert process_manager.refresh_non_resident_infra_calls == 0
    assert process_manager.shutdown_all_calls == 1
    assert sleep_calls == [40.0]


def test_awsim_backend_reuse_mode_cleans_up_all_on_timeout(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "uturn_test_sim7.json"
    process_manager = NoTraceProcessManager(trace_path)
    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
                launch_dir=tmp_path,
            ),
            output_dir=tmp_path,
            timeout_sec=0.0,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
            manage_infra=True,
            reuse_infra_between_runs=True,
            initial_warmup_sec=40.0,
        ),
        process_manager=process_manager,
        sleeper=lambda _seconds: None,
    )

    result = backend.run(
        TestCase(
            case_id="backend_infra_timeout_1",
            target="awsim",
            case_kind="uturn",
            input={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
            meta={"global_loop_num": 7},
        )
    )

    assert result.status is RunStatus.TIMEOUT
    assert result.evidence["trace_json"].endswith("uturn_eval_sim7.json")
    assert process_manager.stop_case_scoped_processes_calls == 0
    assert process_manager.refresh_non_resident_infra_calls == 1
    assert process_manager.shutdown_all_calls == 0


def test_awsim_backend_keeps_local_sequence_aligned_after_timeout_refresh(
    tmp_path: Path,
) -> None:
    process_manager = TraceAfterTimeoutProcessManager(
        tmp_path / "uturn_test_unused.json"
    )
    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
                launch_dir=tmp_path,
            ),
            output_dir=tmp_path,
            timeout_sec=0.01,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
            post_timeout_grace_sec=0.0,
            manage_infra=True,
            reuse_infra_between_runs=True,
        ),
        process_manager=process_manager,
        sleeper=lambda _seconds: None,
    )

    first = backend.run(
        TestCase(
            case_id="backend_timeout_global_5224",
            target="awsim",
            case_kind="uturn",
            meta={"global_loop_num": 5224},
        )
    )
    second = backend.run(
        TestCase(
            case_id="backend_success_global_5228",
            target="awsim",
            case_kind="uturn",
            meta={"global_loop_num": 5228},
        )
    )

    assert first.status is RunStatus.TIMEOUT
    assert first.meta["local_loop_num"] == 1
    assert second.status is RunStatus.SUCCESS
    assert second.meta["local_loop_num"] == 2
    assert second.evidence["trace_json"].endswith("uturn_eval_sim5228.json")
    assert [call["sim_num"] for call in process_manager.infra_calls] == [1, 2]
    assert process_manager.refresh_non_resident_infra_calls == 1


def test_awsim_backend_records_running_client_at_watch_deadline(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "uturn_test_sim_running.json"
    process_manager = NoTraceProcessManager(trace_path, client_returncode=None)
    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
                launch_dir=tmp_path,
            ),
            output_dir=tmp_path,
            timeout_sec=0.0,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
            manage_infra=True,
            reuse_infra_between_runs=True,
        ),
        process_manager=process_manager,
        sleeper=lambda _seconds: None,
    )

    result = backend.run(
        TestCase(
            case_id="backend_infra_running_at_deadline",
            target="awsim",
            case_kind="uturn",
            input={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
            meta={"global_loop_num": 9},
        )
    )

    assert result.status is RunStatus.TIMEOUT
    assert result.meta["returncode"] is None
    assert result.meta["client_process_state"] == "running_at_watch_deadline"


def test_awsim_backend_refresh_infra_delegates_to_non_resident_refresh(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "uturn_test_sim8.json"
    process_manager = FakeProcessManager(trace_path)
    backend = AWSIMBackend(
        config=AWSIMBackendConfig(
            runtime_profile=build_runtime_profile(
                case_kind="uturn",
                source_setup_script=None,
                launch_dir=tmp_path,
            ),
            output_dir=tmp_path,
            timeout_sec=0.1,
            poll_interval_sec=0.0,
            settle_time_sec=0.0,
            manage_infra=True,
            reuse_infra_between_runs=True,
        ),
        process_manager=process_manager,
    )

    backend.refresh_infra(reason="refresh_interval_reached")
    backend.close()

    assert process_manager.refresh_non_resident_infra_calls == 1
    assert process_manager.shutdown_all_calls == 1
