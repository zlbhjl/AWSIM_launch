from pathlib import Path

from runtime.container.launcher import ContainerLauncher, LaunchRequest
from runtime.container.profile import build_runtime_profile
from runtime.container.runner import CommandResult


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run_command(self, command, *, cwd, env, source_setup_script):
        self.calls.append(
            {
                "command": command,
                "cwd": cwd,
                "env": env,
                "source_setup_script": source_setup_script,
            }
        )
        return CommandResult(returncode=0, stdout="ok", stderr="")


def test_container_launcher_uses_runtime_profile_for_launch_context() -> None:
    runner = FakeRunner()
    launcher = ContainerLauncher(runner=runner)
    profile = build_runtime_profile(
        case_kind="uturn",
        launch_dir="/tmp/project",
        source_setup_script="/tmp/setup.bash",
    )

    result = launcher.launch(
        LaunchRequest(
            command=["python3", "run_scenario.py", "--type", "uturn"],
            env={"AW_OUTPUT_DIR": "/tmp/out"},
        ),
        runtime_profile=profile,
    )

    assert result.returncode == 0
    assert runner.calls == [
        {
            "command": ["python3", "run_scenario.py", "--type", "uturn"],
            "cwd": Path("/tmp/project").resolve(),
            "env": {"AW_OUTPUT_DIR": "/tmp/out"},
            "source_setup_script": Path("/tmp/setup.bash").resolve(),
        }
    ]


def test_container_launcher_prepare_launch_applies_headless_env_and_tracks_session() -> None:
    calls: list[tuple[str, object]] = []

    class FakeXvfbController:
        def start(self, config):
            calls.append(("start", config.enabled))
            return "session-1"

        def apply_environment(self, config, *, env):
            updated_env = dict(env)
            updated_env["DISPLAY"] = ":199"
            return updated_env

        def stop(self, session):
            calls.append(("stop", session))

    launcher = ContainerLauncher(
        runner=FakeRunner(),
        xvfb_controller=FakeXvfbController(),
    )
    profile = build_runtime_profile(
        case_kind="uturn",
        launch_dir="/tmp/project",
        source_setup_script="/tmp/setup.bash",
        headless=True,
    )

    prepared = launcher.prepare_launch(
        LaunchRequest(
            command=["python3", "run_scenario.py", "--type", "uturn"],
            env={"AW_OUTPUT_DIR": "/tmp/out"},
        ),
        runtime_profile=profile,
    )

    assert prepared.command == ["python3", "run_scenario.py", "--type", "uturn"]
    assert prepared.cwd == Path("/tmp/project").resolve()
    assert prepared.source_setup_script == Path("/tmp/setup.bash").resolve()
    assert prepared.env == {
        "AW_OUTPUT_DIR": "/tmp/out",
        "DISPLAY": ":199",
    }
    assert prepared.xvfb_session == "session-1"

    launcher.close_launch(prepared)

    assert calls == [("start", True), ("stop", "session-1")]
