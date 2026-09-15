import signal
from pathlib import Path

import pytest

from runtime.container.cleanup import ContainerCleanup
from runtime.container.infra_tasks import InfraTask
from runtime.container.process_manager import ContainerProcessManager, ManagedRuntimeProcess


class FakeProcess:
    def __init__(self, pid: int, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


class FakeSupervisor:
    def __init__(self) -> None:
        self.spawn_calls: list[dict[str, object]] = []
        self.signal_calls: list[tuple[str, int]] = []
        self.release_calls: list[str] = []
        self.run_calls: list[dict[str, object]] = []

    def spawn(self, command, **kwargs):
        self.spawn_calls.append({"command": command, **kwargs})
        process = FakeProcess(pid=900 + len(self.spawn_calls))
        process.process_id = f"process-{len(self.spawn_calls)}"
        return process

    def signal(self, process_id, signal_number):
        self.signal_calls.append((process_id, signal_number))

    def release(self, process_id):
        self.release_calls.append(process_id)

    def run(self, command, **kwargs):
        self.run_calls.append({"command": command, **kwargs})
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()


def test_process_manager_starts_infra_and_tracks_resident_processes(tmp_path: Path) -> None:
    popen_calls: list[dict[str, object]] = []
    sleep_calls: list[float] = []

    def fake_popen(command, **kwargs):
        popen_calls.append({"command": command, "kwargs": kwargs})
        return FakeProcess(pid=100 + len(popen_calls))

    manager = ContainerProcessManager(
        popen_factory=fake_popen,
        sleeper=lambda seconds: sleep_calls.append(seconds),
        cleanup=ContainerCleanup(
            signal_sender=lambda *_args: None,
            sleeper=lambda _seconds: None,
            system_runner=lambda _command: 0,
        ),
    )
    tasks = [
        InfraTask(
            name="Resident",
            work_dir=tmp_path,
            command="python3 resident.py",
            resident=True,
            delay_sec=1.0,
            log_filename="resident.log",
        ),
        InfraTask(
            name="Autoware",
            work_dir=tmp_path,
            command="ros2 launch demo demo.launch.xml",
            delay_sec=2.0,
            source_setup=True,
            log_filename="autoware.log",
        ),
    ]

    started = manager.start_infra_processes(
        tasks,
        sim_num=7,
        output_dir=tmp_path / "logs",
        source_setup_script=tmp_path / "setup.bash",
        env={},
    )

    assert [process.name for process in manager.resident_processes] == ["Resident"]
    assert [process.name for process in manager.infra_processes] == ["Autoware"]
    assert [process.name for process in started] == ["Autoware"]
    assert sleep_calls == [1.0, 2.0]
    assert popen_calls[0]["command"] == [
        "/bin/bash",
        "-i",
        "-c",
        "python3 resident.py",
    ]
    assert popen_calls[0]["kwargs"]["env"] == {}
    assert popen_calls[1]["command"] == [
        "/bin/bash",
        "-i",
        "-c",
        f"source {Path(tmp_path / 'setup.bash').resolve()} && ros2 launch demo demo.launch.xml",
    ]
    assert (tmp_path / "logs" / "resident.log").exists()
    assert (tmp_path / "logs" / "autoware.log").exists()


def test_process_manager_reports_infrastructure_process_that_exits_during_startup(
    tmp_path: Path,
) -> None:
    from runtime.container.process_manager import InfrastructureProcessExited

    log_dir = tmp_path / "logs"

    def fake_popen(_command, **kwargs):
        kwargs["stdout"].write("NameError: AWSIMClientOpStateTrackerTopic is not defined\n")
        kwargs["stdout"].flush()
        return FakeProcess(pid=404, returncode=1)

    manager = ContainerProcessManager(
        popen_factory=fake_popen,
        sleeper=lambda _seconds: None,
    )

    with pytest.raises(InfrastructureProcessExited, match="AWSIMClientOpStateTrackerTopic"):
        manager.start_process(
            InfraTask(
                name="Runtime Monitor",
                work_dir=tmp_path,
                command="python3 main.py",
                delay_sec=5.0,
                log_filename="runtime_monitor.log",
            ),
            sim_num=1,
            output_dir=log_dir,
            source_setup_script=None,
            env={},
        )


def test_process_manager_uses_supervisor_for_spawn_and_cleanup(tmp_path: Path) -> None:
    supervisor = FakeSupervisor()
    manager = ContainerProcessManager(
        popen_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("worker must not fork subprocesses")
        ),
        sleeper=lambda _seconds: None,
        supervisor_client=supervisor,  # type: ignore[arg-type]
    )

    process = manager.start_process(
        InfraTask(
            name="Autoware",
            work_dir=tmp_path,
            command="ros2 launch demo demo.launch.xml",
            log_filename="autoware.log",
        ),
        sim_num=1,
        output_dir=tmp_path / "logs",
        source_setup_script=None,
        env={"ROS_DOMAIN_ID": "21"},
    )
    manager.infra_processes.append(process)
    manager.stop_infra(force_cleanup_os=False)

    assert supervisor.spawn_calls[0]["command"] == [
        "/bin/bash",
        "-i",
        "-c",
        "ros2 launch demo demo.launch.xml",
    ]
    assert supervisor.spawn_calls[0]["log_path"] == tmp_path / "logs" / "autoware.log"
    assert supervisor.signal_calls == [
        ("process-1", signal.SIGINT),
        ("process-1", signal.SIGKILL),
    ]
    assert supervisor.release_calls == ["process-1"]


def test_process_manager_launch_client_replaces_previous_client(tmp_path: Path) -> None:
    popen_calls: list[dict[str, object]] = []
    cleanup_calls: list[list[str]] = []

    def fake_popen(command, **kwargs):
        popen_calls.append({"command": command, "kwargs": kwargs})
        return FakeProcess(pid=200 + len(popen_calls))

    cleanup = ContainerCleanup(
        signal_sender=lambda process, name, sig: cleanup_calls.append([name, str(process.pid), str(sig)]),
        sleeper=lambda _seconds: None,
        system_runner=lambda _command: 0,
    )
    manager = ContainerProcessManager(
        popen_factory=fake_popen,
        sleeper=lambda _seconds: None,
        cleanup=cleanup,
    )

    first = manager.launch_client(
        "python3 run_scenario.py --type uturn",
        work_dir=tmp_path,
    )
    second = manager.launch_client(
        "python3 run_scenario.py --type uturn --dx0 15.0",
        work_dir=tmp_path,
        source_setup_script=tmp_path / "setup.bash",
        output_dir=tmp_path / "logs",
        log_filename="client.log",
        env={"DISPLAY": ":199"},
    )

    assert first.process.pid == 201
    assert second.process.pid == 202
    assert cleanup_calls[:2] == [
        ["Scenario Client", "201", str(signal.SIGINT)],
        ["Scenario Client", "201", str(signal.SIGKILL)],
    ]
    assert popen_calls[1]["command"] == [
        "/bin/bash",
        "-i",
        "-c",
        f"source {Path(tmp_path / 'setup.bash').resolve()} && python3 run_scenario.py --type uturn --dx0 15.0",
    ]
    assert popen_calls[1]["kwargs"]["env"] == {"DISPLAY": ":199"}
    assert (tmp_path / "logs" / "client.log").exists()


def test_process_manager_stop_case_client_keeps_infra_processes(tmp_path: Path) -> None:
    cleanup_calls: list[list[str]] = []

    cleanup = ContainerCleanup(
        signal_sender=lambda process, name, sig: cleanup_calls.append([name, str(process.pid), str(sig)]),
        sleeper=lambda _seconds: None,
        system_runner=lambda _command: 0,
    )
    manager = ContainerProcessManager(
        popen_factory=lambda *_args, **_kwargs: FakeProcess(pid=888),
        sleeper=lambda _seconds: None,
        cleanup=cleanup,
    )
    manager.client_process = manager.launch_client(
        "python3 run_scenario.py --type uturn",
        work_dir=tmp_path,
        output_dir=tmp_path / "logs",
        log_filename="client.log",
    )
    manager.infra_processes = [
        ManagedRuntimeProcess(name="Autoware", process=FakeProcess(pid=101), resident=False),
    ]

    manager.stop_case_client()

    assert manager.client_process is None
    assert [process.name for process in manager.infra_processes] == ["Autoware"]
    assert cleanup_calls == [
        ["Scenario Client", "888", str(signal.SIGINT)],
        ["Scenario Client", "888", str(signal.SIGKILL)],
    ]


def test_process_manager_stop_case_scoped_processes_keeps_resident_processes(tmp_path: Path) -> None:
    cleanup_calls: list[list[str]] = []

    cleanup = ContainerCleanup(
        signal_sender=lambda process, name, sig: cleanup_calls.append([name, str(process.pid), str(sig)]),
        sleeper=lambda _seconds: None,
        system_runner=lambda _command: 0,
    )
    manager = ContainerProcessManager(
        popen_factory=lambda *_args, **_kwargs: FakeProcess(pid=999),
        sleeper=lambda _seconds: None,
        cleanup=cleanup,
    )
    manager.client_process = manager.launch_client(
        "python3 run_scenario.py --type uturn",
        work_dir=tmp_path,
    )
    manager.infra_processes = [
        manager.start_process(
            InfraTask(name="AWSIM Labs", work_dir=tmp_path, command="./awsim_labs.x86_64"),
            sim_num=1,
            output_dir=tmp_path,
            source_setup_script=None,
            env={},
        ),
        manager.start_process(
            InfraTask(name="Autoware", work_dir=tmp_path, command="ros2 launch demo demo.launch.xml"),
            sim_num=1,
            output_dir=tmp_path,
            source_setup_script=None,
            env={},
        ),
    ]
    resident = manager.start_process(
        InfraTask(
            name="AW Checker (Safety Evaluator)",
            work_dir=tmp_path,
            command="python3 awchecker.py",
            resident=True,
        ),
        sim_num=1,
        output_dir=tmp_path,
        source_setup_script=None,
        env={},
    )
    manager.resident_processes = [resident]

    manager.stop_case_scoped_processes()

    assert manager.client_process is None
    assert manager.infra_processes == []
    assert [process.name for process in manager.resident_processes] == [
        "AW Checker (Safety Evaluator)"
    ]
    assert cleanup_calls == [
        ["Scenario Client", "999", str(signal.SIGINT)],
        ["Scenario Client", "999", str(signal.SIGKILL)],
        ["Autoware", "999", str(signal.SIGINT)],
        ["AWSIM Labs", "999", str(signal.SIGINT)],
        ["Autoware", "999", str(signal.SIGKILL)],
        ["AWSIM Labs", "999", str(signal.SIGKILL)],
    ]


def test_process_manager_refresh_non_resident_infra_keeps_resident_and_runs_os_cleanup(
    tmp_path: Path,
) -> None:
    os_cleanup_calls: list[str] = []

    cleanup = ContainerCleanup(
        signal_sender=lambda *_args: None,
        sleeper=lambda _seconds: None,
        system_runner=lambda command: os_cleanup_calls.append(command) or 0,
    )
    manager = ContainerProcessManager(
        popen_factory=lambda *_args, **_kwargs: FakeProcess(pid=777),
        sleeper=lambda _seconds: None,
        cleanup=cleanup,
    )
    manager.infra_processes = [
        ManagedRuntimeProcess(name="Autoware", process=FakeProcess(pid=101), resident=False),
    ]
    manager.resident_processes = [
        ManagedRuntimeProcess(
            name="AW Checker (Safety Evaluator)",
            process=FakeProcess(pid=201),
            resident=True,
        ),
    ]

    manager.refresh_non_resident_infra()

    assert manager.infra_processes == []
    assert [process.name for process in manager.resident_processes] == [
        "AW Checker (Safety Evaluator)"
    ]
    assert any("pkill -15 -f awsim_labs.x86_64" in command for command in os_cleanup_calls)


def test_process_manager_shutdown_all_clears_resident_processes(tmp_path: Path) -> None:
    cleanup_calls: list[list[str]] = []
    os_cleanup_calls: list[str] = []

    cleanup = ContainerCleanup(
        signal_sender=lambda process, name, sig: cleanup_calls.append([name, str(process.pid), str(sig)]),
        sleeper=lambda _seconds: None,
        system_runner=lambda command: os_cleanup_calls.append(command) or 0,
    )
    manager = ContainerProcessManager(
        popen_factory=lambda *_args, **_kwargs: FakeProcess(pid=555),
        sleeper=lambda _seconds: None,
        cleanup=cleanup,
    )
    manager.client_process = ManagedRuntimeProcess(
        name="Scenario Client",
        process=FakeProcess(pid=401),
        resident=False,
    )
    manager.infra_processes = [
        ManagedRuntimeProcess(name="Autoware", process=FakeProcess(pid=402), resident=False),
    ]
    manager.resident_processes = [
        ManagedRuntimeProcess(
            name="AW Checker (Safety Evaluator)",
            process=FakeProcess(pid=403),
            resident=True,
        ),
    ]

    manager.shutdown_all()

    assert manager.client_process is None
    assert manager.infra_processes == []
    assert manager.resident_processes == []
    assert cleanup_calls[:2] == [
        ["Scenario Client", "401", str(signal.SIGINT)],
        ["Scenario Client", "401", str(signal.SIGKILL)],
    ]
    assert any("pkill -9 -f autoware_launch" in command for command in os_cleanup_calls)
    assert not any("pkill -9 -f autoware " in command for command in os_cleanup_calls)
    assert not any("pkill -9 -f ros2 " in command for command in os_cleanup_calls)
