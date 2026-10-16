from __future__ import annotations

import json
import os
import socket
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


PROCESS_SUPERVISOR_SOCKET_ENV = "AWSIM_PROCESS_SUPERVISOR_SOCKET"
_DEFAULT_SOCKET_TIMEOUT = object()


class SupervisorRequestError(RuntimeError):
    pass


@dataclass(frozen=True)
class SupervisorCommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class SupervisedProcess:
    client: "SupervisorClient"
    process_id: str
    pid: int
    returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        response = self.client.poll(self.process_id)
        returncode = response.get("returncode")
        if isinstance(returncode, int):
            self.returncode = returncode
        return self.returncode


class SupervisorClient:
    def __init__(self, socket_path: str | Path, *, timeout_sec: float = 120.0) -> None:
        self.socket_path = str(socket_path)
        self.timeout_sec = float(timeout_sec)

    def spawn(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path,
        env: Mapping[str, str],
        name: str,
        log_path: str | Path | None = None,
    ) -> SupervisedProcess:
        response = self._request(
            {
                "op": "spawn",
                "command": list(command),
                "cwd": str(cwd),
                "env": dict(env),
                "name": str(name),
                "log_path": str(log_path) if log_path is not None else None,
            }
        )
        return SupervisedProcess(
            client=self,
            process_id=str(response["process_id"]),
            pid=int(response["pid"]),
        )

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path,
        env: Mapping[str, str],
        timeout_sec: float | None = None,
    ) -> SupervisorCommandResult:
        response = self._request(
            {
                "op": "run",
                "command": list(command),
                "cwd": str(cwd),
                "env": dict(env),
                "timeout_sec": timeout_sec,
            },
            timeout_sec=(
                max(float(timeout_sec) + 30.0, self.timeout_sec)
                if timeout_sec is not None
                else None
            ),
        )
        return SupervisorCommandResult(
            returncode=int(response["returncode"]),
            stdout=str(response.get("stdout", "")),
            stderr=str(response.get("stderr", "")),
        )

    def poll(self, process_id: str) -> dict[str, object]:
        return self._request({"op": "poll", "process_id": process_id})

    def signal(self, process_id: str, signal_number: int) -> None:
        self._request(
            {
                "op": "signal",
                "process_id": process_id,
                "signal": int(signal_number),
            }
        )

    def release(self, process_id: str) -> None:
        self._request({"op": "release", "process_id": process_id})

    def shutdown(self) -> None:
        self._request({"op": "shutdown"})

    def _request(
        self,
        payload: Mapping[str, object],
        *,
        timeout_sec: float | None | object = _DEFAULT_SOCKET_TIMEOUT,
    ) -> dict[str, object]:
        request = dict(payload)
        request["request_id"] = uuid.uuid4().hex
        encoded = (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
        chunks: list[bytes] = []
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client_socket:
            resolved_timeout = (
                self.timeout_sec
                if timeout_sec is _DEFAULT_SOCKET_TIMEOUT
                else timeout_sec
            )
            client_socket.settimeout(resolved_timeout)
            client_socket.connect(self.socket_path)
            client_socket.sendall(encoded)
            while True:
                chunk = client_socket.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
        if not chunks:
            raise SupervisorRequestError("Process supervisor returned no response")
        response = json.loads(b"".join(chunks).split(b"\n", 1)[0].decode("utf-8"))
        if not isinstance(response, dict):
            raise SupervisorRequestError("Process supervisor returned an invalid response")
        if not response.get("ok", False):
            raise SupervisorRequestError(str(response.get("error", "process supervisor request failed")))
        return response


def supervisor_client_from_environment(
    env: Mapping[str, str] | None = None,
) -> SupervisorClient | None:
    source_env = os.environ if env is None else env
    socket_path = source_env.get(PROCESS_SUPERVISOR_SOCKET_ENV)
    if not socket_path:
        return None
    return SupervisorClient(socket_path)
