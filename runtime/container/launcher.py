from __future__ import annotations

from dataclasses import dataclass, field

from .profile import ContainerRuntimeProfile
from .runner import CommandResult, ContainerRunner
from .xvfb import XvfbConfig, XvfbController, XvfbSession


@dataclass(frozen=True)
class LaunchRequest:
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class PreparedLaunch:
    command: list[str]
    env: dict[str, str]
    cwd: object
    source_setup_script: object
    xvfb_session: XvfbSession | None = None


class ContainerLauncher:
    def __init__(
        self,
        runner: ContainerRunner | None = None,
        xvfb_controller: XvfbController | None = None,
    ):
        self.runner = runner or ContainerRunner()
        self.xvfb_controller = xvfb_controller or XvfbController()

    def prepare_launch(
        self,
        request: LaunchRequest,
        *,
        runtime_profile: ContainerRuntimeProfile,
        xvfb_config: XvfbConfig | None = None,
    ) -> PreparedLaunch:
        resolved_xvfb_config = xvfb_config or XvfbConfig(enabled=runtime_profile.headless)
        xvfb_session = self.xvfb_controller.start(resolved_xvfb_config)
        env = self.xvfb_controller.apply_environment(resolved_xvfb_config, env=request.env)
        return PreparedLaunch(
            command=list(request.command),
            env=env,
            cwd=runtime_profile.launch_dir,
            source_setup_script=runtime_profile.source_setup_script,
            xvfb_session=xvfb_session,
        )

    def launch_prepared(
        self,
        prepared_launch: PreparedLaunch,
    ) -> CommandResult:
        return self.runner.run_command(
            prepared_launch.command,
            cwd=prepared_launch.cwd,
            env=dict(prepared_launch.env),
            source_setup_script=prepared_launch.source_setup_script,
        )

    def close_launch(self, prepared_launch: PreparedLaunch) -> None:
        self.xvfb_controller.stop(prepared_launch.xvfb_session)

    def launch(
        self,
        request: LaunchRequest,
        *,
        runtime_profile: ContainerRuntimeProfile,
        xvfb_config: XvfbConfig | None = None,
    ) -> CommandResult:
        prepared_launch = self.prepare_launch(
            request,
            runtime_profile=runtime_profile,
            xvfb_config=xvfb_config,
        )
        try:
            return self.launch_prepared(prepared_launch)
        finally:
            self.close_launch(prepared_launch)
