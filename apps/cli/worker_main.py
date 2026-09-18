from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from contracts.execution import RunStatus, TestCase
from orchestration.worker_loop import (
    WorkerControlPlaneLost,
    WorkerInfrastructureUnavailable,
    WorkerLoop,
    WorkerLoopContext,
)
from runtime.cluster.ray_client import (
    RayActorLocator,
    RayConnectionConfig,
    is_ray_control_plane_error,
)
from runtime.cluster.task_queue_gateway import TaskQueueGateway, TaskQueueGatewayConfig
from runtime.cluster.result_sink import (
    CompositeResultSink,
    JsonlResultSink,
    OptionalResultSink,
    RaySharedStoreResultSink,
    SharedStoreResultSink,
)
from runtime.repository.local_history import LocalHistory
from runtime.container.profile import SUPPORTED_CONTAINER_PROFILES
from runtime.container.gpu_health import probe_nvidia_smi
from targets.registry import build_target_components
from targets.awsim.case_kinds import (
    SUPPORTED_SCENARIO_PROFILES,
    build_default_case_kind_module_name,
    load_case_definition,
    load_focus_points,
)


LEGACY_MODES = [
    "explore",
    "focus",
    "margin",
    "jama_edge",
    "ttc_edge",
    "worst_ttc",
    "dkw",
    "dkw_fixed",
    "verify_consistency",
    "binomial_ci",
    "boundary_gap",
    "replay",
    "sprt",
    "ebstop",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the v2 worker for a direct fixture or one queued target test case."
    )
    parser.add_argument(
        "--fixture",
        default=None,
        help="Path to a local target fixture.",
    )
    parser.add_argument(
        "--fixture-raw-run-status",
        choices=[status.value for status in RunStatus],
        default=None,
        help=(
            "Optional execution status to attach when importing an existing fixture. "
            "Use this when the saved trace came from a timed-out or failed run."
        ),
    )
    parser.add_argument(
        "--param",
        dest="params",
        action="append",
        default=[],
        help="Direct simulation parameter in key=value form. Repeat this option to pass multiple values.",
    )
    parser.add_argument(
        "--queue-actor-name",
        default=None,
        help="Ray actor name for queue mode. If set, the worker fetches one task from the queue.",
    )
    parser.add_argument(
        "--queue-namespace",
        default=None,
        help="Optional Ray namespace used when resolving the queue actor.",
    )
    parser.add_argument(
        "--queue-address",
        default=None,
        help="Optional Ray address used when connecting queue mode worker runs.",
    )
    parser.add_argument(
        "--queue-connect-timeout",
        type=float,
        default=30.0,
        help="Maximum seconds to wait for the queue actor to appear.",
    )
    parser.add_argument(
        "--queue-connect-poll-interval",
        type=float,
        default=5.0,
        help="Seconds between queue actor lookup retries.",
    )
    parser.add_argument(
        "--queue-connect-retries",
        type=int,
        default=6,
        help="Number of ray.init attempts before the queue worker exits.",
    )
    parser.add_argument(
        "--queue-connect-retry-interval",
        type=float,
        default=15.0,
        help="Seconds to wait between ray.init retry attempts.",
    )
    parser.add_argument(
        "--queue-empty-wait-timeout",
        type=float,
        default=0.0,
        help="Queue mode only. Seconds to keep polling when the queue is temporarily empty.",
    )
    parser.add_argument(
        "--queue-empty-wait-interval",
        type=float,
        default=5.0,
        help="Seconds between empty queue polling attempts.",
    )
    parser.add_argument(
        "--queue-heartbeat-interval",
        type=float,
        default=0.0,
        help="Queue mode only. Seconds between status heartbeat updates while a case is running.",
    )
    parser.add_argument(
        "--worker-gpu-health-check",
        action="store_true",
        help="Run nvidia-smi before taking each queued simulation case.",
    )
    parser.add_argument(
        "--worker-gpu-health-timeout-sec",
        type=float,
        default=5.0,
        help="Timeout for the worker-side nvidia-smi health check.",
    )
    parser.add_argument(
        "--refresh-interval",
        type=int,
        default=10,
        help="Optional queue-mode refresh policy. When set, the worker exits after this many processed cases.",
    )
    parser.add_argument(
        "--restart-on-refresh",
        action="store_true",
        help=(
            "Queue-mode CLI only. When refresh is requested, immediately start the next "
            "batch in the same worker process instead of exiting."
        ),
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to the JSONL file where EvaluationRecord rows are appended.",
    )
    parser.add_argument("--case-id", default=None, help="Override case id. Defaults to fixture stem.")
    parser.add_argument("--case-kind", default="uturn", help="Case kind label.")
    parser.add_argument(
        "--type",
        dest="legacy_type",
        default=None,
        help="Legacy scenario type alias. When --case-kind is not given, this becomes the AWSIM case kind.",
    )
    parser.add_argument(
        "--mode",
        choices=LEGACY_MODES,
        default="explore",
        help="Legacy worker mode label preserved as v2 execution metadata.",
    )
    parser.add_argument(
        "--target",
        default="awsim",
        choices=["awsim", "bbsl", "prism"],
        help="Target name for the worker.",
    )
    parser.add_argument("--worker-id", default="worker_v2_local", help="Worker identifier.")
    parser.add_argument("--reason", default="manual_local_run", help="Task reason label.")
    parser.add_argument(
        "--tag",
        dest="tags",
        action="append",
        default=[],
        help="Task tag. Repeat this option to add multiple tags.",
    )
    parser.add_argument(
        "--config-module",
        default=None,
        help="Rule spec module used by the AWSIM result interpreter. Defaults to targets.awsim.case_kinds.<case-kind>.",
    )
    parser.add_argument(
        "--focus_points",
        default=None,
        help="Legacy JSON string for focus mode points. When omitted in focus mode, FOCUS_POINTS is loaded from the case-kind module.",
    )
    parser.add_argument(
        "--ext_mode",
        default="cvm",
        help="Kinematics extractor mode forwarded to AWSIM infra tasks (for example: cvm, ctrv, maude).",
    )
    parser.add_argument(
        "--dkw-bounds",
        default=None,
        help="Legacy DKW bounds JSON accepted for CLI compatibility.",
    )
    parser.add_argument(
        "--dkw-region",
        default="custom",
        help="Legacy DKW region label accepted for CLI compatibility.",
    )
    parser.add_argument(
        "--dkw-pure-smc",
        action="store_true",
        help="Legacy DKW pure-SMC flag accepted for CLI compatibility.",
    )
    parser.add_argument(
        "--dkw-simultaneous",
        action="store_true",
        help="Legacy DKW simultaneous flag accepted for CLI compatibility.",
    )
    parser.add_argument(
        "--path-root",
        default=None,
        help="Optional root used when relativizing evidence paths on save.",
    )
    parser.add_argument(
        "--dataset-csv",
        default=None,
        help="Optional dataset CSV path for shared-store style merged output.",
    )
    parser.add_argument(
        "--shared-store-actor-name",
        default=None,
        help="Optional detached Ray actor name used for merged dataset persistence.",
    )
    parser.add_argument(
        "--shared-store-namespace",
        default=None,
        help="Optional Ray namespace used when resolving the shared store actor.",
    )
    parser.add_argument(
        "--shared-store-address",
        default=None,
        help="Optional Ray address used when connecting the shared store actor.",
    )
    parser.add_argument(
        "--history-path",
        default=None,
        help="Optional path for processed loop history. When set, duplicate loop numbers are skipped.",
    )
    parser.add_argument(
        "--history-loop-num",
        type=int,
        default=None,
        help="Optional loop number used for history tracking in direct fixture mode.",
    )
    parser.add_argument(
        "--scenario-type",
        default=None,
        help="Optional scenario type for direct simulation mode. Defaults to case-kind.",
    )
    parser.add_argument(
        "--simulation-output-dir",
        default=None,
        help="Optional output directory used by the AWSIM backend in direct simulation mode.",
    )
    parser.add_argument(
        "--expected-trace-path",
        default=None,
        help="Optional explicit trace JSON path expected from direct simulation mode.",
    )
    parser.add_argument(
        "--local-loop-num",
        type=int,
        default=None,
        help="Optional local loop number used when naming direct simulation outputs.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run AWSIM simulation mode with Xvfb-based headless display setup.",
    )
    parser.add_argument(
        "--container-profile",
        choices=SUPPORTED_CONTAINER_PROFILES,
        default=None,
        help="Optional named container runtime profile (for example: legacy, autoware171, autoware180).",
    )
    parser.add_argument(
        "--scenario-profile",
        choices=SUPPORTED_SCENARIO_PROFILES,
        default=None,
        help="Optional named scenario spec profile (for example: legacy, autoware171).",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    fixture_mode = bool(args.fixture)
    simulation_mode = bool(args.params)
    queue_mode = bool(args.queue_actor_name)
    mode_count = sum([fixture_mode, simulation_mode, queue_mode])
    if mode_count != 1:
        raise ValueError(
            "Specify exactly one input mode: --fixture, --param key=value, or --queue-actor-name"
        )
    if fixture_mode and simulation_mode:
        raise ValueError("Do not mix --fixture with --param key=value")
    if args.target != "awsim" and args.headless:
        raise ValueError("--headless is only supported for target=awsim")
    queue_connect_timeout = getattr(args, "queue_connect_timeout", 30.0)
    if queue_connect_timeout is not None and float(queue_connect_timeout) < 0.0:
        raise ValueError("--queue-connect-timeout must be non-negative")
    queue_connect_retries = getattr(args, "queue_connect_retries", 6)
    if queue_connect_retries is not None and int(queue_connect_retries) <= 0:
        raise ValueError("--queue-connect-retries must be a positive integer")
    queue_connect_retry_interval = getattr(args, "queue_connect_retry_interval", 15.0)
    if (
        queue_connect_retry_interval is not None
        and float(queue_connect_retry_interval) < 0.0
    ):
        raise ValueError("--queue-connect-retry-interval must be non-negative")
    queue_connect_poll_interval = getattr(args, "queue_connect_poll_interval", 5.0)
    if queue_connect_poll_interval is not None and float(queue_connect_poll_interval) < 0.0:
        raise ValueError("--queue-connect-poll-interval must be non-negative")
    queue_empty_wait_timeout = getattr(args, "queue_empty_wait_timeout", 0.0)
    if queue_empty_wait_timeout is not None and float(queue_empty_wait_timeout) < 0.0:
        raise ValueError("--queue-empty-wait-timeout must be non-negative")
    queue_empty_wait_interval = getattr(args, "queue_empty_wait_interval", 5.0)
    if queue_empty_wait_interval is not None and float(queue_empty_wait_interval) < 0.0:
        raise ValueError("--queue-empty-wait-interval must be non-negative")
    queue_heartbeat_interval = getattr(args, "queue_heartbeat_interval", 0.0)
    if queue_heartbeat_interval is not None and float(queue_heartbeat_interval) < 0.0:
        raise ValueError("--queue-heartbeat-interval must be non-negative")
    worker_gpu_health_timeout_sec = getattr(args, "worker_gpu_health_timeout_sec", 5.0)
    if worker_gpu_health_timeout_sec is not None and float(worker_gpu_health_timeout_sec) <= 0.0:
        raise ValueError("--worker-gpu-health-timeout-sec must be positive")
    if (
        getattr(args, "dataset_csv", None) is not None
        and getattr(args, "shared_store_actor_name", None) is not None
    ):
        raise ValueError("--dataset-csv and --shared-store-actor-name are mutually exclusive")
    refresh_interval = getattr(args, "refresh_interval", None)
    if refresh_interval is not None and refresh_interval <= 0:
        raise ValueError("--refresh-interval must be a positive integer")
    if args.target != "awsim" and any(
        [
            getattr(args, "scenario_type", None) is not None,
            getattr(args, "simulation_output_dir", None) is not None,
            getattr(args, "expected_trace_path", None) is not None,
            getattr(args, "local_loop_num", None) is not None,
            getattr(args, "legacy_type", None) is not None,
            getattr(args, "focus_points", None) is not None,
        ]
    ):
        raise ValueError("AWSIM-only direct execution options are not supported for non-awsim targets")


def normalize_args(
    args: argparse.Namespace,
    *,
    argv: Sequence[str] | None = None,
) -> argparse.Namespace:
    raw_argv = list(argv) if argv is not None else []
    explicit_case_kind = "--case-kind" in raw_argv
    explicit_config_module = "--config-module" in raw_argv
    explicit_scenario_type = "--scenario-type" in raw_argv
    explicit_focus_points = "--focus_points" in raw_argv

    if args.target != "awsim":
        if args.config_module is None:
            args.config_module = "targets.awsim.case_kinds.uturn"
        return args

    if (
        getattr(args, "scenario_profile", None) is None
        and getattr(args, "container_profile", None) in SUPPORTED_SCENARIO_PROFILES
    ):
        args.scenario_profile = args.container_profile

    if args.legacy_type and not explicit_case_kind:
        args.case_kind = args.legacy_type

    if args.config_module is None or not explicit_config_module:
        args.config_module = build_default_case_kind_module_name(
            case_kind=args.case_kind,
            scenario_profile=getattr(args, "scenario_profile", None),
        )

    case_definition = load_case_definition(
        case_kind=args.case_kind,
        module_name=args.config_module,
    )
    if not explicit_scenario_type:
        args.scenario_type = str(case_definition.get("scenario_type", args.case_kind))

    if args.mode == "focus":
        args.focus_points = _resolve_focus_points(
            case_kind=args.case_kind,
            config_module=args.config_module,
            raw_focus_points=args.focus_points if explicit_focus_points else None,
        )
    else:
        args.focus_points = _resolve_focus_points(
            case_kind=args.case_kind,
            config_module=args.config_module,
            raw_focus_points=args.focus_points if explicit_focus_points else None,
            allow_config_default=False,
        )
    return args


def _resolve_focus_points(
    *,
    case_kind: str,
    config_module: str,
    raw_focus_points: str | None,
    allow_config_default: bool = True,
) -> list[dict[str, object]] | None:
    if raw_focus_points:
        try:
            parsed = json.loads(raw_focus_points)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--focus_points must be valid JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise ValueError("--focus_points must decode to a list of objects")
        return [dict(point) for point in parsed]
    if not allow_config_default:
        return None
    return load_focus_points(case_kind=case_kind, module_name=config_module)


def build_test_case(args: argparse.Namespace) -> TestCase | None:
    if not args.fixture and not args.params:
        return None

    history_loop_num = getattr(args, "history_loop_num", None)
    if args.fixture:
        fixture_path = Path(args.fixture).expanduser().resolve()
        case_id = args.case_id or fixture_path.stem
        input_payload = {"fixture_path": str(fixture_path)}
        fixture_raw_run_status = getattr(args, "fixture_raw_run_status", None)
        if fixture_raw_run_status is not None:
            input_payload["fixture_raw_run_status"] = str(fixture_raw_run_status)
    else:
        input_payload = build_direct_input(args)
        case_id = args.case_id or f"{args.case_kind}_direct"

    return TestCase(
        case_id=case_id,
        target=args.target,
        case_kind=args.case_kind,
        input=input_payload,
        tags=list(args.tags),
        reason=args.reason,
        meta={
            "source_cli": "run_worker_v2.py",
            "config_module": getattr(
                args,
                "config_module",
                build_default_case_kind_module_name(
                    case_kind=args.case_kind,
                    scenario_profile=getattr(args, "scenario_profile", None),
                ),
            ),
            **(
                {"container_profile": str(args.container_profile)}
                if getattr(args, "container_profile", None)
                else {}
            ),
            **(
                {"scenario_profile": str(args.scenario_profile)}
                if getattr(args, "scenario_profile", None)
                else {}
            ),
            "run_mode": getattr(args, "mode", "explore"),
            "ext_mode": getattr(args, "ext_mode", "cvm"),
            "dkw_region": getattr(args, "dkw_region", "custom"),
            "dkw_pure_smc": bool(getattr(args, "dkw_pure_smc", False)),
            "dkw_simultaneous": bool(getattr(args, "dkw_simultaneous", False)),
            **(
                {"focus_points": [dict(point) for point in args.focus_points]}
                if getattr(args, "focus_points", None)
                else {}
            ),
            **(
                {"dkw_bounds": str(args.dkw_bounds)}
                if getattr(args, "dkw_bounds", None) is not None
                else {}
            ),
            **({"history_loop_num": history_loop_num} if history_loop_num is not None else {}),
        },
    )


def build_direct_input(args: argparse.Namespace) -> dict[str, object]:
    if args.target == "awsim":
        return build_simulation_input(args)
    return parse_param_assignments(args.params)


def build_simulation_input(args: argparse.Namespace) -> dict[str, object]:
    payload = parse_param_assignments(args.params)
    scenario_type = args.scenario_type or args.case_kind
    payload["scenario_type"] = scenario_type
    payload["ext_mode"] = getattr(args, "ext_mode", "cvm")
    if args.simulation_output_dir:
        payload["output_dir"] = str(Path(args.simulation_output_dir).expanduser().resolve())
    if args.expected_trace_path:
        payload["expected_trace_path"] = str(Path(args.expected_trace_path).expanduser().resolve())
    if args.local_loop_num is not None:
        payload["local_loop_num"] = args.local_loop_num
    return payload


def parse_param_assignments(values: Sequence[str]) -> dict[str, object]:
    params: dict[str, object] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"Parameter must be key=value: {item}")
        key, raw_value = item.split("=", 1)
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError(f"Parameter key must not be empty: {item}")
        params[normalized_key] = _parse_param_value(raw_value.strip())
    return params


def _parse_param_value(raw_value: str) -> object:
    if raw_value == "":
        return ""
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        pass

    lowered = raw_value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"

    try:
        if "." in raw_value or "e" in lowered:
            return float(raw_value)
        return int(raw_value)
    except ValueError:
        return raw_value


def merge_legacy_param_flags(
    args: argparse.Namespace,
    unknown_argv: Sequence[str],
) -> argparse.Namespace:
    if not unknown_argv:
        return args
    if args.fixture or args.queue_actor_name:
        raise ValueError("Unknown arguments are only supported for direct simulation mode")

    merged_params = list(getattr(args, "params", []))
    index = 0
    while index < len(unknown_argv):
        token = unknown_argv[index]
        if not token.startswith("--") or token == "--":
            raise ValueError(f"Unsupported argument syntax: {token}")
        key = token[2:].strip()
        if not key:
            raise ValueError(f"Parameter key must not be empty: {token}")

        next_index = index + 1
        if next_index < len(unknown_argv) and not unknown_argv[next_index].startswith("--"):
            merged_params.append(f"{key}={unknown_argv[next_index]}")
            index += 2
            continue

        merged_params.append(f"{key}=true")
        index += 1

    args.params = merged_params
    return args


def build_task_source(
    args: argparse.Namespace,
    *,
    task_source: TaskQueueGateway | None = None,
    actor_locator: RayActorLocator | None = None,
) -> TaskQueueGateway | None:
    if task_source is not None:
        return task_source
    if not args.queue_actor_name:
        return None
    locator = actor_locator or RayActorLocator()
    queue_address = getattr(args, "queue_address", None) or os.environ.get("RAY_ADDRESS")
    ray_module, actor = locator.connect_and_get_actor(
        args.queue_actor_name,
        config=RayConnectionConfig(
            address=queue_address,
            namespace=args.queue_namespace,
            actor_lookup_timeout_sec=float(getattr(args, "queue_connect_timeout", 30.0)),
            actor_lookup_poll_interval_sec=float(
                getattr(args, "queue_connect_poll_interval", 5.0)
            ),
            connect_retries=int(getattr(args, "queue_connect_retries", 6)),
            connect_retry_interval_sec=float(
                getattr(args, "queue_connect_retry_interval", 15.0)
            ),
        ),
    )
    return TaskQueueGateway(
        actor=actor,
        config=TaskQueueGatewayConfig(
            target=args.target,
            case_kind=args.case_kind,
        ),
        ray_get=ray_module.get,
    )


def build_result_sink(
    args: argparse.Namespace,
    *,
    result_sink: object | None = None,
) -> object:
    if result_sink is not None:
        return result_sink

    jsonl_sink = JsonlResultSink(
        args.output,
        path_root=args.path_root,
    )
    if getattr(args, "shared_store_actor_name", None):
        try:
            shared_store_sink = RaySharedStoreResultSink.from_actor_name(
                args.shared_store_actor_name,
                address=getattr(args, "shared_store_address", None) or os.environ.get("RAY_ADDRESS"),
                namespace=getattr(args, "shared_store_namespace", None),
            )
        except Exception as exc:  # pragma: no cover - defensive branch
            print(
                f"[Worker] Shared-store actor setup failed; continuing with JSONL only: {exc}",
                file=sys.stderr,
            )
            return jsonl_sink
        return CompositeResultSink(
            [
                jsonl_sink,
                OptionalResultSink(shared_store_sink, label="shared_store_actor"),
            ]
        )
    if not args.dataset_csv:
        return jsonl_sink

    try:
        shared_store_sink = SharedStoreResultSink.from_dataset_csv(args.dataset_csv)
    except Exception as exc:  # pragma: no cover - defensive branch
        print(
            f"[Worker] Shared-store sink setup failed; continuing with JSONL only: {exc}",
            file=sys.stderr,
        )
        return jsonl_sink
    return CompositeResultSink(
        [
            jsonl_sink,
            OptionalResultSink(shared_store_sink, label="shared_store"),
        ]
    )


def build_local_history(args: argparse.Namespace) -> LocalHistory | None:
    if not args.history_path:
        return None
    return LocalHistory(args.history_path)


def resolve_history_loop_num(test_case: TestCase | None) -> int | None:
    if test_case is None:
        return None

    for key in ("global_loop_num", "history_loop_num"):
        value = test_case.meta.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def run_worker(
    argv: Sequence[str] | None = None,
    *,
    backend: object | None = None,
    result_interpreter: object | None = None,
    result_sink: object | None = None,
    task_source: TaskQueueGateway | None = None,
) -> int:
    summary = run_worker_with_summary(
        argv,
        backend=backend,
        result_interpreter=result_interpreter,
        result_sink=result_sink,
        task_source=task_source,
        emit_summary=True,
    )
    return int(summary["exit_code"])


def run_worker_cli(
    argv: Sequence[str] | None = None,
    *,
    backend: object | None = None,
    result_interpreter: object | None = None,
    result_sink: object | None = None,
    task_source: TaskQueueGateway | None = None,
    emit_summary: bool = False,
) -> int:
    raw_argv = list(argv) if argv is not None else list(sys.argv[1:])
    parsed_args, _unknown_argv = build_parser().parse_known_args(raw_argv)
    restart_on_refresh = bool(getattr(parsed_args, "restart_on_refresh", False))

    while True:
        summary = run_worker_with_summary(
            raw_argv,
            backend=backend,
            result_interpreter=result_interpreter,
            result_sink=result_sink,
            task_source=task_source,
            emit_summary=emit_summary,
        )
        if (
            not restart_on_refresh
            or int(summary["exit_code"]) != 0
            or summary.get("terminal_status") != "refresh_requested"
        ):
            return int(summary["exit_code"])


def run_worker_with_summary(
    argv: Sequence[str] | None = None,
    *,
    backend: object | None = None,
    result_interpreter: object | None = None,
    result_sink: object | None = None,
    task_source: TaskQueueGateway | None = None,
    emit_summary: bool = False,
) -> dict[str, object]:
    raw_argv = list(argv) if argv is not None else list(sys.argv[1:])
    args, unknown_argv = build_parser().parse_known_args(raw_argv)
    args = merge_legacy_param_flags(args, unknown_argv)
    args = normalize_args(args, argv=raw_argv)
    validate_args(args)
    direct_test_case = build_test_case(args)
    try:
        task_source_impl = build_task_source(args, task_source=task_source)
    except Exception as exc:
        if not is_ray_control_plane_error(exc):
            raise
        summary = {
            "case_id": direct_test_case.case_id if direct_test_case is not None else None,
            "target": args.target,
            "case_kind": args.case_kind,
            "status": "ray_control_plane_lost",
            "mode": "queue",
            "reason": str(exc),
            "processed_count": 0,
            "skipped_count": 0,
            "terminal_status": "ray_control_plane_lost",
            "output_path": str(Path(args.output).expanduser().resolve()),
            "exit_code": 1,
        }
        if emit_summary:
            print(json.dumps(summary, ensure_ascii=False))
        return summary
    history = build_local_history(args)

    target_components = build_target_components(
        args,
        backend=backend,
        result_interpreter=result_interpreter,
    )
    backend_impl = target_components.backend
    interpreter_impl = target_components.result_interpreter
    sink_impl = build_result_sink(args, result_sink=result_sink)
    loop = WorkerLoop(
        backend=backend_impl,
        result_interpreter=interpreter_impl,
        result_sink=sink_impl,
        task_source=task_source_impl,
        context=WorkerLoopContext(worker_id=args.worker_id),
        heartbeat_interval_sec=(
            float(getattr(args, "queue_heartbeat_interval", 0.0))
            if task_source_impl is not None
            else 0.0
        ),
        pre_fetch_health_check=(
            (
                lambda: probe_nvidia_smi(
                    timeout_sec=float(
                        getattr(args, "worker_gpu_health_timeout_sec", 5.0)
                    )
                )
            )
            if task_source_impl is not None
            and args.target == "awsim"
            and bool(getattr(args, "worker_gpu_health_check", False))
            else None
        ),
    )
    try:
        batch = loop.execute_until_exit(
            direct_test_case=direct_test_case,
            history=history,
            refresh_interval=args.refresh_interval if task_source_impl is not None else None,
            empty_wait_timeout_sec=(
                float(getattr(args, "queue_empty_wait_timeout", 0.0))
                if task_source_impl is not None
                else 0.0
            ),
            empty_wait_interval_sec=float(
                getattr(args, "queue_empty_wait_interval", 5.0)
            ),
        )
    except WorkerInfrastructureUnavailable as exc:
        summary = {
            "case_id": direct_test_case.case_id if direct_test_case is not None else None,
            "target": args.target,
            "case_kind": args.case_kind,
            "status": "gpu_unavailable",
            "mode": "queue" if task_source_impl is not None else "direct",
            "reason": str(exc),
            "processed_count": 0,
            "skipped_count": 0,
            "terminal_status": "gpu_unavailable",
            "output_path": str(Path(args.output).expanduser().resolve()),
            "exit_code": 75,
        }
        if emit_summary:
            print(json.dumps(summary, ensure_ascii=False))
        return summary
    except WorkerControlPlaneLost as exc:
        summary = {
            "case_id": direct_test_case.case_id if direct_test_case is not None else None,
            "target": args.target,
            "case_kind": args.case_kind,
            "status": "ray_control_plane_lost",
            "mode": "queue" if task_source_impl is not None else "direct",
            "reason": str(exc),
            "processed_count": 0,
            "skipped_count": 0,
            "terminal_status": "ray_control_plane_lost",
            "output_path": str(Path(args.output).expanduser().resolve()),
            "exit_code": 1,
        }
        if emit_summary:
            print(json.dumps(summary, ensure_ascii=False))
        return summary
    except Exception as exc:
        summary = {
            "case_id": direct_test_case.case_id if direct_test_case is not None else None,
            "target": args.target,
            "case_kind": args.case_kind,
            "status": "worker_error",
            "mode": "queue" if task_source_impl is not None else "direct",
            "reason": str(exc),
            "processed_count": 0,
            "skipped_count": 0,
            "terminal_status": "worker_error",
            "output_path": str(Path(args.output).expanduser().resolve()),
            "exit_code": 1,
        }
        if emit_summary:
            print(json.dumps(summary, ensure_ascii=False))
        return summary
    finally:
        close_backend = getattr(backend_impl, "close", None)
        if callable(close_backend):
            close_backend()
    last_outcome = batch.last_outcome
    summary: dict[str, object]
    if not batch.records:
        summary = {
            "case_id": last_outcome.test_case.case_id if last_outcome and last_outcome.test_case else None,
            "target": (
                last_outcome.test_case.target
                if last_outcome and last_outcome.test_case
                else args.target
            ),
            "case_kind": (
                last_outcome.test_case.case_kind
                if last_outcome and last_outcome.test_case
                else args.case_kind
            ),
            "status": batch.terminal_status,
            "mode": "queue" if task_source_impl is not None else "direct",
            "reason": batch.reason,
            "history_loop_num": (
                last_outcome.history_loop_num if last_outcome is not None else None
            ),
            "processed_count": batch.processed_count,
            "skipped_count": batch.skipped_count,
            "terminal_status": batch.terminal_status,
            "sink_warnings": list(getattr(sink_impl, "warnings", [])),
            "output_path": str(Path(args.output).expanduser().resolve()),
            "exit_code": 0,
        }
        if emit_summary:
            print(json.dumps(summary, ensure_ascii=False))
        return summary

    record = batch.records[-1]

    summary = {
        "case_id": record.case_id,
        "target": record.target,
        "case_kind": record.case_kind,
        "status": record.status.value,
        "mode": "queue" if task_source_impl is not None else "direct",
        "processed_count": batch.processed_count,
        "skipped_count": batch.skipped_count,
        "terminal_status": batch.terminal_status,
        "sink_warnings": list(getattr(sink_impl, "warnings", [])),
        "output_path": str(Path(args.output).expanduser().resolve()),
        "exit_code": 0,
    }
    if emit_summary:
        print(json.dumps(summary, ensure_ascii=False))
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    return run_worker_cli(argv, emit_summary=True)
