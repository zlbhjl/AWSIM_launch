from pathlib import Path

from runtime.container.runner import CommandResult, ContainerRunner


class Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_container_runner_runs_command_without_setup_script() -> None:
    captured: dict[str, object] = {}

    def fake_subprocess_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Completed(returncode=0, stdout="ok", stderr="")

    runner = ContainerRunner(subprocess_runner=fake_subprocess_run)
    result = runner.run_command(
        ["python3", "run_scenario.py", "--type", "uturn"],
        cwd=Path("/tmp/project"),
        env={"AW_OUTPUT_DIR": "/tmp/out"},
        source_setup_script=None,
    )

    assert isinstance(result, CommandResult)
    assert result.returncode == 0
    assert captured["command"] == ["python3", "run_scenario.py", "--type", "uturn"]
    assert captured["kwargs"]["cwd"] == Path("/tmp/project")
    assert captured["kwargs"]["env"] == {"AW_OUTPUT_DIR": "/tmp/out"}


def test_container_runner_sources_setup_script_when_requested() -> None:
    captured: dict[str, object] = {}

    def fake_subprocess_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Completed(returncode=0, stdout="ok", stderr="")

    runner = ContainerRunner(subprocess_runner=fake_subprocess_run)
    result = runner.run_command(
        ["python3", "run_scenario.py", "--type", "uturn"],
        cwd=Path("/tmp/project"),
        env={"AW_OUTPUT_DIR": "/tmp/out"},
        source_setup_script=Path("/tmp/setup.bash"),
    )

    assert result.returncode == 0
    assert captured["command"][0:2] == ["/bin/bash", "-lc"]
    assert "source /tmp/setup.bash && python3 run_scenario.py --type uturn" in captured["command"][2]
