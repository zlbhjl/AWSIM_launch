from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class ContainerRunner:
    def __init__(
        self,
        subprocess_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ):
        self.subprocess_runner = subprocess_runner or subprocess.run

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
            completed = self.subprocess_runner(
                ["/bin/bash", "-lc", shell_command],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            completed = self.subprocess_runner(
                command,
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
