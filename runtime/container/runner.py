from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .supervised_process import SupervisorClient, supervisor_client_from_environment


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""


class ContainerRunner:
    def __init__(
        self,
        subprocess_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        supervisor_client: SupervisorClient | None = None,
    ):
        self.subprocess_runner = subprocess_runner or subprocess.run
        self.supervisor_client = (
            supervisor_client
            if supervisor_client is not None
            else supervisor_client_from_environment()
        )

    def run_command(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        source_setup_script: Path | None,
    ) -> CommandResult:
        if source_setup_script is not None:
            shell_command = (
                f"source {shlex.quote(str(source_setup_script))} && "
                + " ".join(shlex.quote(part) for part in command)
            )
            resolved_command = ["/bin/bash", "-lc", shell_command]
        else:
            resolved_command = command

        if self.supervisor_client is not None:
            completed = self.supervisor_client.run(
                resolved_command,
                cwd=cwd,
                env=env,
            )
        else:
            completed = self.subprocess_runner(
                resolved_command,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
