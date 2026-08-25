from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Protocol


DEFAULT_PROCESS_PATTERNS = (
    "awsim_labs.x86_64",
    "run_scenario.py",
    "component_container",
    "rviz2",
    "autoware",
    "ros2",
)
DEFAULT_CLEANUP_COMMANDS = (
    "ros2 daemon stop > /dev/null 2>&1",
    "rm -f /dev/shm/ros2* > /dev/null 2>&1",
    "rm -f /dev/shm/fastrtps* > /dev/null 2>&1",
)


class ProcessLike(Protocol):
    pid: int

    def poll(self) -> int | None:
        ...


@dataclass(frozen=True)
class ManagedProcess:
    name: str
    process: ProcessLike


class ContainerCleanup:
    def __init__(
        self,
        *,
        signal_sender: Callable[[ProcessLike, str, int], None] | None = None,
        sleeper: Callable[[float], None] | None = None,
        system_runner: Callable[[str], int] | None = None,
    ):
        self.signal_sender = signal_sender or self._default_signal_sender
        self.sleeper = sleeper or time.sleep
        self.system_runner = system_runner or os.system

    def cleanup_processes(
        self,
        processes: Iterable[ManagedProcess],
        *,
        grace_period_sec: float = 3.0,
    ) -> None:
        managed_processes = list(processes)
        for managed_process in reversed(managed_processes):
            self.signal_sender(managed_process.process, managed_process.name, signal.SIGINT)

        if grace_period_sec > 0:
            self.sleeper(grace_period_sec)

        for managed_process in reversed(managed_processes):
            self.signal_sender(managed_process.process, managed_process.name, signal.SIGKILL)

    def force_cleanup_os(
        self,
        *,
        process_patterns: Iterable[str] = DEFAULT_PROCESS_PATTERNS,
        cleanup_commands: Iterable[str] = DEFAULT_CLEANUP_COMMANDS,
    ) -> None:
        patterns = list(process_patterns)
        for pattern in patterns:
            self.system_runner(f"pkill -15 -f {pattern} > /dev/null 2>&1")

        self.sleeper(1.0)

        for pattern in patterns:
            self.system_runner(f"pkill -9 -f {pattern} > /dev/null 2>&1")

        for command in cleanup_commands:
            self.system_runner(command)

    def cleanup_all(
        self,
        processes: Iterable[ManagedProcess],
        *,
        grace_period_sec: float = 3.0,
        process_patterns: Iterable[str] = DEFAULT_PROCESS_PATTERNS,
        cleanup_commands: Iterable[str] = DEFAULT_CLEANUP_COMMANDS,
    ) -> None:
        self.cleanup_processes(processes, grace_period_sec=grace_period_sec)
        self.force_cleanup_os(
            process_patterns=process_patterns,
            cleanup_commands=cleanup_commands,
        )

    @staticmethod
    def _default_signal_sender(process: ProcessLike, name: str, sig: int) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            pgid = os.getpgid(process.pid)
            os.killpg(pgid, sig)
        except Exception:
            return
