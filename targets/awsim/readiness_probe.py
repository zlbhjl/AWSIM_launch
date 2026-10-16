from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from runtime.container.runner import CommandResult, ContainerRunner


DEFAULT_REQUIRED_SERVICES = (
    "/dynamic_control/script/awsim_script_srv",
    "/simulation/gt_srv/execution_state",
    "/api/localization/initialize",
    "/dynamic_control/map/network",
)


@dataclass(frozen=True)
class AWSIMReadinessConfig:
    required_services: tuple[str, ...] = DEFAULT_REQUIRED_SERVICES
    probe_timeout_sec: float = 30.0
    per_service_timeout_sec: float = 2.0
    stability_checks: int = 2
    stability_interval_sec: float = 1.0


@dataclass(frozen=True)
class AWSIMReadinessResult:
    ready: bool
    attempts: int
    checked_services: tuple[str, ...]
    message: str = ""


class AWSIMReadinessProbe:
    def __init__(
        self,
        *,
        runner: ContainerRunner | None = None,
        python_executable: str = "python3",
        module_name: str = "targets.awsim.readiness_probe",
    ) -> None:
        self.runner = runner or ContainerRunner()
        self.python_executable = python_executable
        self.module_name = module_name

    def probe(
        self,
        *,
        cwd: Path,
        env: Mapping[str, str],
        source_setup_script: Path | None,
        config: AWSIMReadinessConfig,
    ) -> AWSIMReadinessResult:
        command = [
            self.python_executable,
            "-m",
            self.module_name,
            "--probe-timeout-sec",
            str(float(config.probe_timeout_sec)),
            "--per-service-timeout-sec",
            str(float(config.per_service_timeout_sec)),
            "--stability-checks",
            str(int(config.stability_checks)),
            "--stability-interval-sec",
            str(float(config.stability_interval_sec)),
        ]
        for service_name in config.required_services:
            command.extend(["--required-service", service_name])

        command_result = self.runner.run_command(
            command,
            cwd=cwd,
            env=dict(env),
            source_setup_script=source_setup_script,
        )
        return _parse_probe_command_result(
            command_result,
            checked_services=config.required_services,
        )


def wait_until_ready(
    config: AWSIMReadinessConfig,
    *,
    service_checker: Callable[[str, float], bool],
    execution_state_checker: Callable[[float], bool],
    monotonic: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> AWSIMReadinessResult:
    resolved_monotonic = monotonic or time.monotonic
    resolved_sleeper = sleeper or time.sleep
    required_services = tuple(config.required_services)
    required_successes = max(int(config.stability_checks), 1)
    deadline = resolved_monotonic() + max(float(config.probe_timeout_sec), 0.0)
    consecutive_successes = 0
    attempts = 0
    last_message = "probe_timeout"

    while True:
        attempts += 1
        round_ok, round_message = _run_single_round(
            config,
            service_checker=service_checker,
            execution_state_checker=execution_state_checker,
        )
        if round_ok:
            consecutive_successes += 1
            if consecutive_successes >= required_successes:
                return AWSIMReadinessResult(
                    ready=True,
                    attempts=attempts,
                    checked_services=required_services,
                    message="ready",
                )
            last_message = (
                f"stability_check_pending:{consecutive_successes}/{required_successes}"
            )
        else:
            consecutive_successes = 0
            last_message = round_message

        now = resolved_monotonic()
        if now >= deadline:
            break
        if config.stability_interval_sec > 0:
            resolved_sleeper(min(float(config.stability_interval_sec), max(deadline - now, 0.0)))

    return AWSIMReadinessResult(
        ready=False,
        attempts=attempts,
        checked_services=required_services,
        message=last_message,
    )


def _run_single_round(
    config: AWSIMReadinessConfig,
    *,
    service_checker: Callable[[str, float], bool],
    execution_state_checker: Callable[[float], bool],
) -> tuple[bool, str]:
    timeout_sec = max(float(config.per_service_timeout_sec), 0.0)
    for service_name in config.required_services:
        try:
            if not service_checker(service_name, timeout_sec):
                return False, f"service_unavailable:{service_name}"
        except Exception as exc:  # pragma: no cover - exercised via callers
            return False, f"service_exception:{service_name}:{exc}"

    try:
        if not execution_state_checker(timeout_sec):
            return False, "execution_state_unavailable"
    except Exception as exc:  # pragma: no cover - exercised via callers
        return False, f"execution_state_exception:{exc}"

    return True, "ready"


def _parse_probe_command_result(
    command_result: CommandResult,
    *,
    checked_services: Sequence[str],
) -> AWSIMReadinessResult:
    checked_services_tuple = tuple(checked_services)
    payload = _parse_json_payload(command_result.stdout)
    if isinstance(payload, dict):
        return AWSIMReadinessResult(
            ready=bool(payload.get("ready", False)),
            attempts=int(payload.get("attempts", 0)),
            checked_services=tuple(payload.get("checked_services", checked_services_tuple)),
            message=str(payload.get("message", "")),
        )

    message = command_result.stderr.strip() or command_result.stdout.strip()
    if not message:
        message = (
            "readiness probe failed"
            if command_result.returncode != 0
            else "readiness probe returned invalid output"
        )
    return AWSIMReadinessResult(
        ready=bool(command_result.returncode == 0),
        attempts=0,
        checked_services=checked_services_tuple,
        message=message,
    )


def _parse_json_payload(stdout: str) -> dict[str, object] | None:
    if not stdout.strip():
        return None
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded readiness probe for AWSIM/Autoware.")
    parser.add_argument(
        "--required-service",
        dest="required_services",
        action="append",
        default=[],
        help="Service name that must be available before a scenario starts.",
    )
    parser.add_argument("--probe-timeout-sec", type=float, default=30.0)
    parser.add_argument("--per-service-timeout-sec", type=float, default=2.0)
    parser.add_argument("--stability-checks", type=int, default=2)
    parser.add_argument("--stability-interval-sec", type=float, default=1.0)
    return parser


def _run_ros_probe(config: AWSIMReadinessConfig) -> AWSIMReadinessResult:
    import rclpy
    from rclpy.node import Node

    from autoware_adapi_v1_msgs.srv import InitializeLocalization
    from aw_monitor.srv import DynamicControl, ExecutionState

    service_types = {
        "/dynamic_control/script/awsim_script_srv": DynamicControl,
        "/simulation/gt_srv/execution_state": ExecutionState,
        "/api/localization/initialize": InitializeLocalization,
        "/dynamic_control/map/network": DynamicControl,
    }

    class ProbeNode(Node):
        def __init__(self) -> None:
            super().__init__("awsim_readiness_probe")

    rclpy.init(args=None)
    node = ProbeNode()
    clients = {
        name: node.create_client(service_types[name], name)
        for name in config.required_services
        if name in service_types
    }
    if "/simulation/gt_srv/execution_state" not in clients:
        clients["/simulation/gt_srv/execution_state"] = node.create_client(
            ExecutionState,
            "/simulation/gt_srv/execution_state",
        )

    def service_checker(service_name: str, timeout_sec: float) -> bool:
        client = clients.get(service_name)
        if client is None:
            raise KeyError(f"Unsupported service: {service_name}")
        return bool(client.wait_for_service(timeout_sec=float(timeout_sec)))

    def call_empty(service_name: str, request_factory: Callable[[], object], timeout_sec: float) -> bool:
        client = clients[service_name]
        future = client.call_async(request_factory())
        rclpy.spin_until_future_complete(node, future, timeout_sec=max(float(timeout_sec), 0.0))
        if not future.done():
            return False
        return future.result() is not None

    try:
        return wait_until_ready(
            config,
            service_checker=service_checker,
            execution_state_checker=lambda timeout_sec: call_empty(
                "/simulation/gt_srv/execution_state",
                ExecutionState.Request,
                timeout_sec,
            ),
        )
    finally:
        node.destroy_node()
        rclpy.shutdown()


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    config = AWSIMReadinessConfig(
        required_services=tuple(args.required_services) or DEFAULT_REQUIRED_SERVICES,
        probe_timeout_sec=float(args.probe_timeout_sec),
        per_service_timeout_sec=float(args.per_service_timeout_sec),
        stability_checks=int(args.stability_checks),
        stability_interval_sec=float(args.stability_interval_sec),
    )
    result = _run_ros_probe(config)
    print(
        json.dumps(
            {
                "ready": result.ready,
                "attempts": result.attempts,
                "checked_services": list(result.checked_services),
                "message": result.message,
            },
            ensure_ascii=False,
        )
    )
    return 0 if result.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
