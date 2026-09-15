from __future__ import annotations

import os
import signal
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Callable, Mapping

from .cleanup import (
    DEFAULT_CLEANUP_COMMANDS,
    DEFAULT_PROCESS_PATTERNS,
    ContainerCleanup,
    ManagedProcess,
)
from .infra_tasks import InfraTask
from .supervised_process import SupervisorClient, supervisor_client_from_environment


@dataclass
class ManagedRuntimeProcess:
    name: str
    process: subprocess.Popen[bytes] | object
    resident: bool = False


class InfrastructureProcessExited(RuntimeError):
    pass


class ContainerProcessManager:
    def __init__(
        self,
        *,
        popen_factory: Callable[..., object] | None = None,
        sleeper: Callable[[float], None] | None = None,
        cleanup: ContainerCleanup | None = None,
        bash_executable: str = "/bin/bash",
        process_group_factory=None,
        supervisor_client: SupervisorClient | None = None,
    ) -> None:
        import time

        self.popen_factory = popen_factory or subprocess.Popen
        self.sleeper = sleeper or time.sleep
        self.cleanup = cleanup or ContainerCleanup()
        self.bash_executable = bash_executable
        self.process_group_factory = process_group_factory or os.setsid
        self.supervisor_client = (
            supervisor_client
            if supervisor_client is not None
            else supervisor_client_from_environment()
        )
        self.infra_processes: list[ManagedRuntimeProcess] = []
        self.resident_processes: list[ManagedRuntimeProcess] = []
        self.client_process: ManagedRuntimeProcess | None = None
        self._log_handles: list[IO[str] | IO[bytes]] = []

    def build_command(
        self,
        task: InfraTask,
        *,
        sim_num: int,
        source_setup_script: str | Path | None,
    ) -> str:
        command = task.command.replace("{sim_num}", str(sim_num))
        if task.source_setup and source_setup_script is not None:
            resolved_script = Path(source_setup_script).expanduser().resolve()
            return f"source {resolved_script} && {command}"
        return command

    def start_process(
        self,
        task: InfraTask,
        *,
        sim_num: int,
        output_dir: str | Path,
        source_setup_script: str | Path | None,
        env: Mapping[str, str] | None = None,
    ) -> ManagedRuntimeProcess:
        resolved_output_dir = Path(output_dir).expanduser().resolve()
        resolved_output_dir.mkdir(parents=True, exist_ok=True)
        log_path = (
            resolved_output_dir / task.log_filename
            if task.log_filename is not None
            else None
        )
        full_command = self.build_command(
            task,
            sim_num=sim_num,
            source_setup_script=source_setup_script,
        )
        if self.supervisor_client is not None:
            process = self.supervisor_client.spawn(
                [self.bash_executable, "-i", "-c", full_command],
                cwd=task.work_dir,
                env=dict(env) if env is not None else os.environ.copy(),
                name=task.name,
                log_path=log_path,
            )
        else:
            stdout_target = self._resolve_output_target(
                task,
                output_dir=resolved_output_dir,
            )
            process = self.popen_factory(
                [self.bash_executable, "-i", "-c", full_command],
                cwd=task.work_dir,
                env=dict(env) if env is not None else None,
                preexec_fn=self.process_group_factory,
                stdout=stdout_target,
                stderr=stdout_target,
            )
        managed_process = ManagedRuntimeProcess(
            name=task.name,
            process=process,
            resident=task.resident,
        )
        if task.delay_sec > 0:
            self.sleeper(task.delay_sec)
        poll = getattr(process, "poll", None)
        returncode = poll() if callable(poll) else None
        if returncode is not None:
            log_tail = self._read_log_tail(
                resolved_output_dir / task.log_filename
                if task.log_filename is not None
                else None
            )
            detail = f"; log tail:\n{log_tail}" if log_tail else ""
            if self.supervisor_client is not None:
                process_id = getattr(process, "process_id", None)
                if process_id is not None:
                    self.supervisor_client.release(process_id)
            raise InfrastructureProcessExited(
                f"Infrastructure process {task.name!r} exited during startup "
                f"with return code {returncode}{detail}"
            )
        return managed_process

    def ensure_resident_processes(
        self,
        tasks: list[InfraTask],
        *,
        sim_num: int,
        output_dir: str | Path,
        source_setup_script: str | Path | None,
        env: Mapping[str, str] | None = None,
    ) -> list[ManagedRuntimeProcess]:
        started: list[ManagedRuntimeProcess] = []
        existing_names = {process.name for process in self.resident_processes}
        for task in tasks:
            if not task.resident or task.name in existing_names:
                continue
            managed_process = self.start_process(
                task,
                sim_num=sim_num,
                output_dir=output_dir,
                source_setup_script=source_setup_script,
                env=env,
            )
            self.resident_processes.append(managed_process)
            started.append(managed_process)
        return started

    def start_infra_processes(
        self,
        tasks: list[InfraTask],
        *,
        sim_num: int,
        output_dir: str | Path,
        source_setup_script: str | Path | None,
        env: Mapping[str, str] | None = None,
    ) -> list[ManagedRuntimeProcess]:
        started: list[ManagedRuntimeProcess] = []
        self.ensure_resident_processes(
            tasks,
            sim_num=sim_num,
            output_dir=output_dir,
            source_setup_script=source_setup_script,
            env=env,
        )
        for task in tasks:
            if task.resident:
                continue
            managed_process = self.start_process(
                task,
                sim_num=sim_num,
                output_dir=output_dir,
                source_setup_script=source_setup_script,
                env=env,
            )
            self.infra_processes.append(managed_process)
            started.append(managed_process)
        return started

    def launch_client(
        self,
        command: str | list[str],
        *,
        work_dir: str | Path,
        source_setup_script: str | Path | None = None,
        output_dir: str | Path | None = None,
        log_filename: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ManagedRuntimeProcess:
        self.stop_client()
        resolved_command = self._normalize_shell_command(command)
        if source_setup_script is not None:
            resolved_script = Path(source_setup_script).expanduser().resolve()
            resolved_command = f"source {resolved_script} && {resolved_command}"

        log_path: Path | None = None
        if output_dir is not None and log_filename is not None:
            log_path = Path(output_dir).expanduser().resolve() / log_filename

        resolved_work_dir = Path(work_dir).expanduser().resolve()
        if self.supervisor_client is not None:
            process = self.supervisor_client.spawn(
                [self.bash_executable, "-i", "-c", resolved_command],
                cwd=resolved_work_dir,
                env=dict(env) if env is not None else os.environ.copy(),
                name="Scenario Client",
                log_path=log_path,
            )
        else:
            stdout_target = (
                self._open_log(log_path)
                if log_path is not None
                else subprocess.DEVNULL
            )
            process = self.popen_factory(
                [self.bash_executable, "-i", "-c", resolved_command],
                cwd=resolved_work_dir,
                env=dict(env) if env is not None else None,
                preexec_fn=self.process_group_factory,
                stdout=stdout_target,
                stderr=stdout_target,
            )
        self.client_process = ManagedRuntimeProcess(
            name="Scenario Client",
            process=process,
            resident=False,
        )
        return self.client_process

    def stop_client(self) -> None:
        if self.client_process is None:
            return
        self._cleanup_processes([self.client_process], grace_period_sec=0.0)
        self.client_process = None

    def stop_case_client(self) -> None:
        self.stop_client()
        self._close_logs()

    def stop_case_scoped_processes(self) -> None:
        self.stop_case_client()
        if not self.infra_processes:
            return
        self._cleanup_processes(self.infra_processes, grace_period_sec=3.0)
        self.infra_processes = []
        self._close_logs()

    def refresh_non_resident_infra(self) -> None:
        self.stop_case_scoped_processes()
        self._force_cleanup_os()

    def shutdown_all(self) -> None:
        self.stop_client()
        processes = self.infra_processes + self.resident_processes
        self._cleanup_processes(processes, grace_period_sec=3.0)
        self._force_cleanup_os()
        self.infra_processes = []
        self.resident_processes = []
        self._close_logs()

    def stop_infra(self, *, include_resident: bool = False, force_cleanup_os: bool = True) -> None:
        processes = list(self.infra_processes)
        if include_resident:
            processes.extend(self.resident_processes)
        self._cleanup_processes(processes, grace_period_sec=3.0)
        if force_cleanup_os:
            self._force_cleanup_os()
        self.infra_processes = []
        if include_resident:
            self.resident_processes = []
        self._close_logs()

    def cleanup_all(self) -> None:
        self.shutdown_all()

    def _cleanup_processes(
        self,
        processes: list[ManagedRuntimeProcess],
        *,
        grace_period_sec: float,
    ) -> None:
        if self.supervisor_client is None:
            self.cleanup.cleanup_processes(
                [ManagedProcess(process.name, process.process) for process in processes],
                grace_period_sec=grace_period_sec,
            )
            return

        for managed_process in reversed(processes):
            process_id = getattr(managed_process.process, "process_id", None)
            if process_id is not None:
                self.supervisor_client.signal(process_id, signal.SIGINT)
        if grace_period_sec > 0:
            self.sleeper(grace_period_sec)
        for managed_process in reversed(processes):
            process_id = getattr(managed_process.process, "process_id", None)
            if process_id is not None:
                self.supervisor_client.signal(process_id, signal.SIGKILL)
                self.supervisor_client.release(process_id)

    def _force_cleanup_os(self) -> None:
        if self.supervisor_client is None:
            self.cleanup.force_cleanup_os()
            return

        env = os.environ.copy()
        cwd = Path.home()
        for sig in (15, 9):
            for pattern in DEFAULT_PROCESS_PATTERNS:
                command = (
                    f"pkill -{sig} -f {shlex.quote(pattern)} "
                    "> /dev/null 2>&1 || true"
                )
                self.supervisor_client.run(
                    [self.bash_executable, "-lc", command],
                    cwd=cwd,
                    env=env,
                )
            if sig == 15:
                self.sleeper(1.0)
        for command in DEFAULT_CLEANUP_COMMANDS:
            self.supervisor_client.run(
                [self.bash_executable, "-lc", f"{command} || true"],
                cwd=cwd,
                env=env,
            )

    def _resolve_output_target(
        self,
        task: InfraTask,
        *,
        output_dir: Path,
    ):
        if task.log_filename is None:
            return subprocess.DEVNULL
        return self._open_log(output_dir / task.log_filename)

    def _open_log(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "w", encoding="utf-8")
        self._log_handles.append(handle)
        return handle

    @staticmethod
    def _read_log_tail(path: Path | None, *, line_count: int = 100) -> str:
        if path is None or not path.exists():
            return ""
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-line_count:])

    def _close_logs(self) -> None:
        for handle in self._log_handles:
            handle.close()
        self._log_handles = []

    @staticmethod
    def _normalize_shell_command(command: str | list[str]) -> str:
        if isinstance(command, str):
            return command
        return " ".join(shlex.quote(part) for part in command)
