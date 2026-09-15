from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from runtime.container.supervised_process import supervisor_client_from_environment


DEFAULT_TOOL_DIR = Path("/home/passd/aw-cheaker/Maude-3.5.1/AW-CheckerPy")
DEFAULT_VENV_PYTHON = Path(".venv/bin/python")


@dataclass(frozen=True)
class MaudeBackendConfig:
    tool_dir: Path = DEFAULT_TOOL_DIR
    python_executable: str | None = None
    script_name: str = "aw_checkerpy.py"
    env_overrides: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MaudeRunResult:
    command: list[str]
    workdir: str
    returncode: int
    stdout: str
    stderr: str


def build_command(
    trace_json_path: str | Path,
    formulas: Sequence[str] | None = None,
    config: MaudeBackendConfig | None = None,
) -> list[str]:
    resolved_config = config or MaudeBackendConfig()
    python_executable = resolve_python_executable(resolved_config)
    command = [
        python_executable,
        resolved_config.script_name,
        str(Path(trace_json_path).expanduser().resolve()),
    ]
    if formulas:
        command.extend(formulas)
    return command


def run_checker(
    trace_json_path: str | Path,
    formulas: Sequence[str] | None = None,
    config: MaudeBackendConfig | None = None,
) -> MaudeRunResult:
    resolved_config = config or MaudeBackendConfig()
    script_path = resolved_config.tool_dir / resolved_config.script_name
    if not script_path.exists():
        raise FileNotFoundError(f"Maude checker script not found: {script_path}")

    command = build_command(trace_json_path, formulas=formulas, config=resolved_config)
    env = os.environ.copy()
    env["PWD"] = str(resolved_config.tool_dir)
    env.update(resolved_config.env_overrides)
    process_supervisor = supervisor_client_from_environment(env)
    if process_supervisor is not None:
        completed = process_supervisor.run(
            command,
            cwd=resolved_config.tool_dir,
            env=env,
        )
    else:
        completed = subprocess.run(
            command,
            cwd=resolved_config.tool_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    return MaudeRunResult(
        command=command,
        workdir=str(resolved_config.tool_dir),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def resolve_python_executable(config: MaudeBackendConfig | None = None) -> str:
    resolved_config = config or MaudeBackendConfig()
    if resolved_config.python_executable:
        return resolved_config.python_executable

    venv_python = resolved_config.tool_dir / DEFAULT_VENV_PYTHON
    if venv_python.exists():
        return str(venv_python)
    return "python3"
