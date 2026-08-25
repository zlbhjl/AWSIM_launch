import signal

from runtime.container.cleanup import ContainerCleanup, ManagedProcess


class FakeProcess:
    def __init__(self, pid: int, returncode: int | None = None):
        self.pid = pid
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


def test_container_cleanup_sends_sigint_then_sigkill_in_reverse_order() -> None:
    calls: list[tuple[str, int, int]] = []

    cleanup = ContainerCleanup(
        signal_sender=lambda process, name, sig: calls.append((name, process.pid, sig)),
        sleeper=lambda _: None,
        system_runner=lambda _: 0,
    )
    processes = [
        ManagedProcess("awsim", FakeProcess(101)),
        ManagedProcess("autoware", FakeProcess(202)),
    ]

    cleanup.cleanup_processes(processes, grace_period_sec=0.0)

    assert calls == [
        ("autoware", 202, signal.SIGINT),
        ("awsim", 101, signal.SIGINT),
        ("autoware", 202, signal.SIGKILL),
        ("awsim", 101, signal.SIGKILL),
    ]


def test_container_cleanup_force_cleanup_os_runs_expected_commands() -> None:
    commands: list[str] = []

    cleanup = ContainerCleanup(
        signal_sender=lambda *_: None,
        sleeper=lambda _: None,
        system_runner=lambda command: commands.append(command) or 0,
    )

    cleanup.force_cleanup_os(
        process_patterns=["proc_a", "proc_b"],
        cleanup_commands=["echo cleanup_a", "echo cleanup_b"],
    )

    assert commands == [
        "pkill -15 -f proc_a > /dev/null 2>&1",
        "pkill -15 -f proc_b > /dev/null 2>&1",
        "pkill -9 -f proc_a > /dev/null 2>&1",
        "pkill -9 -f proc_b > /dev/null 2>&1",
        "echo cleanup_a",
        "echo cleanup_b",
    ]


def test_container_cleanup_all_combines_process_and_os_cleanup() -> None:
    calls: list[str] = []

    cleanup = ContainerCleanup(
        signal_sender=lambda process, name, sig: calls.append(f"{name}:{sig}"),
        sleeper=lambda _: None,
        system_runner=lambda command: calls.append(command) or 0,
    )
    processes = [ManagedProcess("runner", FakeProcess(303))]

    cleanup.cleanup_all(
        processes,
        grace_period_sec=0.0,
        process_patterns=["runner_proc"],
        cleanup_commands=["echo cleanup"],
    )

    assert calls == [
        f"runner:{signal.SIGINT}",
        f"runner:{signal.SIGKILL}",
        "pkill -15 -f runner_proc > /dev/null 2>&1",
        "pkill -9 -f runner_proc > /dev/null 2>&1",
        "echo cleanup",
    ]
