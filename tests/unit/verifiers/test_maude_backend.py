from pathlib import Path

import pytest

from verifiers.maude.backend import (
    MaudeBackendConfig,
    build_command,
    resolve_python_executable,
    run_checker,
)


def test_build_command_includes_trace_and_formulas(tmp_path: Path) -> None:
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    trace_path = tmp_path / "trace.json"
    trace_path.write_text("{}", encoding="utf-8")

    command = build_command(
        trace_path,
        formulas=["F(a)", "G(b)"],
        config=MaudeBackendConfig(tool_dir=tool_dir),
    )

    assert command[0] == "python3"
    assert command[1] == "aw_checkerpy.py"
    assert command[2] == str(trace_path.resolve())
    assert command[3:] == ["F(a)", "G(b)"]


def test_build_command_uses_tool_venv_python_when_available(tmp_path: Path) -> None:
    tool_dir = tmp_path / "tool"
    venv_python = tool_dir / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n", encoding="utf-8")
    trace_path = tmp_path / "trace.json"
    trace_path.write_text("{}", encoding="utf-8")

    command = build_command(trace_path, config=MaudeBackendConfig(tool_dir=tool_dir))

    assert command[0] == str(venv_python)


def test_resolve_python_executable_keeps_explicit_python() -> None:
    config = MaudeBackendConfig(python_executable="/custom/python")

    assert resolve_python_executable(config) == "/custom/python"


def test_run_checker_executes_script_and_captures_output(tmp_path: Path) -> None:
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    script_path = tool_dir / "aw_checkerpy.py"
    script_path.write_text(
        "import sys\n"
        "print('ARGS=' + '|'.join(sys.argv[1:]))\n"
        "print('ERRLINE', file=sys.stderr)\n",
        encoding="utf-8",
    )
    trace_path = tmp_path / "trace.json"
    trace_path.write_text("{}", encoding="utf-8")

    result = run_checker(
        trace_path,
        formulas=["F(a)"],
        config=MaudeBackendConfig(tool_dir=tool_dir),
    )

    assert result.returncode == 0
    assert "ARGS=" in result.stdout
    assert "F(a)" in result.stdout
    assert "ERRLINE" in result.stderr


def test_run_checker_raises_when_script_is_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_checker(
            tmp_path / "trace.json",
            config=MaudeBackendConfig(tool_dir=tmp_path / "missing"),
        )
