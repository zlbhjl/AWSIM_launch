from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Mapping, Sequence

from .client import PROCESS_SUPERVISOR_SOCKET_ENV


MAX_REQUEST_BYTES = 16 * 1024 * 1024


@dataclass
class ManagedChild:
    process_id: str
    name: str
    process: subprocess.Popen[str]
    log_handle: IO[str] | None = None


class ProcessRegistry:
    def __init__(self) -> None:
        self.children: dict[str, ManagedChild] = {}
        self._lock = threading.RLock()

    def spawn(
        self,
        command: Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str],
        name: str,
        log_path: str | None,
    ) -> ManagedChild:
        resolved_command = _validate_command(command)
        log_handle: IO[str] | None = None
        output_target: int | IO[str] = subprocess.DEVNULL
        if log_path:
            resolved_log_path = Path(log_path).expanduser().resolve()
            resolved_log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = resolved_log_path.open("w", encoding="utf-8")
            output_target = log_handle
        try:
            process = subprocess.Popen(
                resolved_command,
                cwd=_validate_cwd(cwd),
                env=_validate_env(env),
                start_new_session=True,
                stdout=output_target,
                stderr=output_target,
                text=True,
            )
        except Exception:
            if log_handle is not None:
                log_handle.close()
            raise
        process_id = uuid.uuid4().hex
        child = ManagedChild(
            process_id=process_id,
            name=name,
            process=process,
            log_handle=log_handle,
        )
        with self._lock:
            self.children[process_id] = child
        return child

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str],
        timeout_sec: float | None,
    ) -> subprocess.CompletedProcess[str]:
        resolved_command = _validate_command(command)
        process = subprocess.Popen(
            resolved_command,
            cwd=_validate_cwd(cwd),
            env=_validate_env(env),
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        process_id = uuid.uuid4().hex
        child = ManagedChild(
            process_id=process_id,
            name=f"command: {resolved_command[0]}",
            process=process,
        )
        with self._lock:
            self.children[process_id] = child
        try:
            stdout, stderr = process.communicate(timeout=timeout_sec)
            return subprocess.CompletedProcess(
                args=resolved_command,
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            final_stdout, final_stderr = process.communicate()
            return subprocess.CompletedProcess(
                args=resolved_command,
                returncode=124,
                stdout=_coerce_subprocess_output(exc.stdout) or final_stdout,
                stderr=(
                    _coerce_subprocess_output(exc.stderr)
                    or final_stderr
                    or "command timed out"
                ),
            )
        finally:
            with self._lock:
                self.children.pop(process_id, None)

    def poll(self, process_id: str) -> tuple[int, int | None]:
        child = self._get(process_id)
        returncode = child.process.poll()
        if returncode is not None:
            self._close_log(child)
        return child.process.pid, returncode

    def signal(self, process_id: str, signal_number: int) -> None:
        child = self._get(process_id)
        if child.process.poll() is not None:
            self._close_log(child)
            return
        try:
            os.killpg(child.process.pid, signal_number)
        except ProcessLookupError:
            pass

    def release(self, process_id: str) -> None:
        with self._lock:
            child = self.children.pop(process_id, None)
        if child is None:
            return
        if child.process.poll() is None:
            try:
                os.killpg(child.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                child.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
        self._close_log(child)

    def stop_all(self, *, grace_period_sec: float = 3.0) -> None:
        with self._lock:
            children = list(self.children.values())
        active = [child for child in children if child.process.poll() is None]
        for child in reversed(active):
            try:
                os.killpg(child.process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + max(float(grace_period_sec), 0.0)
        while active and time.monotonic() < deadline:
            active = [child for child in active if child.process.poll() is None]
            if active:
                time.sleep(0.05)
        for child in reversed(active):
            try:
                os.killpg(child.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for child in active:
            try:
                child.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
        for child in children:
            self._close_log(child)

    def _get(self, process_id: str) -> ManagedChild:
        with self._lock:
            try:
                return self.children[process_id]
            except KeyError as exc:
                raise ValueError(f"Unknown supervised process: {process_id}") from exc

    @staticmethod
    def _close_log(child: ManagedChild) -> None:
        if child.log_handle is not None and not child.log_handle.closed:
            child.log_handle.close()


class ProcessSupervisorServer:
    def __init__(
        self,
        socket_path: str | Path,
        *,
        registry: ProcessRegistry | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.registry = registry or ProcessRegistry()
        self.stop_requested = False
        self._connection_threads: list[threading.Thread] = []

    def serve(
        self,
        *,
        worker_command: str | None = None,
        startup_commands: Sequence[str] = (),
        startup_warmup_sec: float = 0.0,
    ) -> int:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.socket_path.unlink(missing_ok=True)
        worker: subprocess.Popen[str] | None = None
        server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server_socket.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            server_socket.listen(8)
            server_socket.settimeout(0.5)
            startup_children = [
                self.registry.spawn(
                    ["/bin/bash", "-lc", f"exec {command}"],
                    cwd=os.getcwd(),
                    env=os.environ.copy(),
                    name=f"startup process {index}",
                    log_path=None,
                )
                for index, command in enumerate(startup_commands, start=1)
            ]
            if startup_children and startup_warmup_sec > 0:
                time.sleep(startup_warmup_sec)
            for child in startup_children:
                returncode = child.process.poll()
                if returncode is not None:
                    raise RuntimeError(
                        f"{child.name} exited during startup with return code {returncode}"
                    )
            if worker_command is not None:
                worker_env = os.environ.copy()
                worker_env[PROCESS_SUPERVISOR_SOCKET_ENV] = str(self.socket_path)
                worker = subprocess.Popen(
                    ["/bin/bash", "-c", worker_command],
                    env=worker_env,
                    start_new_session=True,
                    text=True,
                )
            while not self.stop_requested:
                if worker is not None and worker.poll() is not None:
                    break
                try:
                    connection, _address = server_socket.accept()
                except socket.timeout:
                    continue
                thread = threading.Thread(
                    target=self._handle_and_close_connection,
                    args=(connection,),
                    daemon=True,
                )
                thread.start()
                self._connection_threads = [
                    active_thread
                    for active_thread in self._connection_threads
                    if active_thread.is_alive()
                ]
                self._connection_threads.append(thread)
        finally:
            self.registry.stop_all()
            for thread in self._connection_threads:
                thread.join(timeout=1.0)
            self.registry.stop_all(grace_period_sec=0.0)
            if worker is not None and worker.poll() is None:
                _stop_process_group(worker)
            server_socket.close()
            self.socket_path.unlink(missing_ok=True)
        if worker is None:
            return 0
        returncode = worker.poll()
        if returncode is None:
            return 1
        return returncode if returncode >= 0 else 128 + abs(returncode)

    def _handle_and_close_connection(self, connection: socket.socket) -> None:
        with connection:
            self._handle_connection(connection)

    def _handle_connection(self, connection: socket.socket) -> None:
        try:
            request = _receive_json_line(connection)
            response = self._dispatch(request)
            response.update({"ok": True, "request_id": request.get("request_id")})
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        try:
            connection.sendall(
                (json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8")
            )
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _dispatch(self, request: Mapping[str, object]) -> dict[str, object]:
        operation = request.get("op")
        if operation == "spawn":
            child = self.registry.spawn(
                _require_command(request),
                cwd=str(request.get("cwd", "")),
                env=_require_env(request),
                name=str(request.get("name", "process")),
                log_path=(
                    str(request["log_path"])
                    if request.get("log_path") is not None
                    else None
                ),
            )
            return {"process_id": child.process_id, "pid": child.process.pid}
        if operation == "run":
            completed = self.registry.run(
                _require_command(request),
                cwd=str(request.get("cwd", "")),
                env=_require_env(request),
                timeout_sec=(
                    float(request["timeout_sec"])
                    if request.get("timeout_sec") is not None
                    else None
                ),
            )
            return {
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        if operation == "poll":
            pid, returncode = self.registry.poll(str(request.get("process_id", "")))
            return {"pid": pid, "returncode": returncode}
        if operation == "signal":
            self.registry.signal(
                str(request.get("process_id", "")),
                int(request.get("signal", signal.SIGTERM)),
            )
            return {}
        if operation == "release":
            self.registry.release(str(request.get("process_id", "")))
            return {}
        if operation == "shutdown":
            self.stop_requested = True
            return {}
        raise ValueError(f"Unsupported supervisor operation: {operation}")


def _validate_command(command: Sequence[str]) -> list[str]:
    if isinstance(command, (str, bytes)) or not command:
        raise ValueError("command must be a non-empty argument list")
    return [str(part) for part in command]


def _validate_cwd(cwd: str) -> str:
    path = Path(cwd).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"working directory does not exist: {path}")
    return str(path)


def _validate_env(env: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(env, Mapping):
        raise ValueError("env must be an object")
    return {str(name): str(value) for name, value in env.items()}


def _require_command(request: Mapping[str, object]) -> list[str]:
    command = request.get("command")
    if not isinstance(command, list):
        raise ValueError("command must be a list")
    return _validate_command(command)


def _require_env(request: Mapping[str, object]) -> dict[str, str]:
    env = request.get("env")
    if not isinstance(env, dict):
        raise ValueError("env must be an object")
    return _validate_env(env)


def _receive_json_line(connection: socket.socket) -> dict[str, object]:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = connection.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_REQUEST_BYTES:
            raise ValueError("supervisor request is too large")
        if b"\n" in chunk:
            break
    payload = b"".join(chunks).split(b"\n", 1)[0]
    request = json.loads(payload.decode("utf-8"))
    if not isinstance(request, dict):
        raise ValueError("supervisor request must be an object")
    return request


def _stop_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _coerce_subprocess_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a local process supervisor for an AWSIM worker.")
    parser.add_argument("--socket", required=True, help="Unix socket used by the worker process.")
    parser.add_argument("--worker-command", required=True, help="Worker shell command to supervise.")
    parser.add_argument(
        "--startup-command",
        action="append",
        default=[],
        help="Shell command started and owned by the supervisor before the worker.",
    )
    parser.add_argument(
        "--startup-warmup-sec",
        type=float,
        default=0.0,
        help="Seconds to wait after starting startup commands.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    server = ProcessSupervisorServer(args.socket)

    def request_stop(_signal_number, _frame) -> None:
        server.stop_requested = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    return server.serve(
        worker_command=args.worker_command,
        startup_commands=args.startup_command,
        startup_warmup_sec=args.startup_warmup_sec,
    )


if __name__ == "__main__":
    sys.exit(main())
