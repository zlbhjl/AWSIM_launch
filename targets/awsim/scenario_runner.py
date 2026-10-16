from __future__ import annotations

import argparse
import importlib
import sys
import time
from types import MethodType
from pathlib import Path
from typing import Callable, Mapping, Sequence

from targets.awsim.case_kinds import (
    SUPPORTED_SCENARIO_PROFILES,
    build_default_case_kind_module_name,
    load_case_definition,
)


HOME_DIR = Path("/home/passd")
LIB_DIR = HOME_DIR / "AWSIMScriptPy"
LAUNCH_DIR = Path(__file__).resolve().parents[2]


def ensure_runtime_paths() -> None:
    for path in (LIB_DIR, LAUNCH_DIR):
        resolved = str(path)
        if resolved not in sys.path:
            sys.path.append(resolved)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--type",
        type=str,
        required=True,
        help="Scenario type (e.g., uturn, cutin, cutout, swerve, deceleration)",
    )
    parser.add_argument(
        "--config-module",
        default=None,
        help="Optional case kind module override. Defaults to targets.awsim.case_kinds.<type>.",
    )
    parser.add_argument(
        "--scenario-profile",
        choices=SUPPORTED_SCENARIO_PROFILES,
        default=None,
        help="Optional named scenario spec profile (for example: legacy, autoware171).",
    )
    return parser


def parse_dynamic_params(unknown: Sequence[str]) -> dict[str, float]:
    if len(unknown) % 2 != 0:
        raise ValueError("Dynamic params must be given as --key value pairs")

    dynamic_params: dict[str, float] = {}
    for index in range(0, len(unknown), 2):
        raw_key = unknown[index]
        if not raw_key.startswith("--"):
            raise ValueError(f"Dynamic param key must start with '--': {raw_key}")
        key = raw_key.lstrip("-")
        raw_value = unknown[index + 1]
        dynamic_params[key] = float(raw_value)
    return dynamic_params


def build_scenario(
    scenario_type: str,
    dynamic_params: Mapping[str, float],
    *,
    case_kind_module_name: str | None = None,
    scenario_profile: str | None = None,
    scenario_manager: object | None = None,
    lane_offset_factory: Callable[[str, float], object] | None = None,
    scenario_builders: Mapping[str, Callable[..., object]] | None = None,
) -> tuple[object, object | None]:
    resolved_case_kind_module_name = case_kind_module_name or build_default_case_kind_module_name(
        case_kind=scenario_type,
        scenario_profile=scenario_profile,
    )
    case_definition = load_case_definition(
        case_kind=scenario_type,
        module_name=resolved_case_kind_module_name,
    )
    fixed_params = dict(case_definition.get("fixed_params", {}))

    resolved_manager = scenario_manager or _default_scenario_manager_factory()
    resolved_lane_offset_factory = lane_offset_factory or _default_lane_offset_factory
    resolved_scenario_builders = dict(scenario_builders or {})
    internal_builder = _load_internal_builder(
        scenario_type,
        scenario_profile=scenario_profile,
    )
    scenario_builder = resolved_scenario_builders.get(scenario_type) or _load_external_builder(
        scenario_type,
        scenario_profile=scenario_profile,
    )
    scenario = internal_builder(
        network=resolved_manager.network,
        dynamic_params=dynamic_params,
        fixed_params=fixed_params,
        scenario_profiles=case_definition.get("scenario_profiles"),
        lane_offset_factory=resolved_lane_offset_factory,
        scenario_builder=scenario_builder,
    )
    return resolved_manager, scenario


def run_scenario_case(
    scenario_type: str,
    dynamic_params: Mapping[str, float],
    *,
    case_kind_module_name: str | None = None,
    scenario_profile: str | None = None,
    scenario_manager: object | None = None,
    lane_offset_factory: Callable[[str, float], object] | None = None,
    scenario_builders: Mapping[str, Callable[..., object]] | None = None,
    printer: Callable[[str], None] = print,
    timeout_sec: float | None = None,
    timeout_monotonic: Callable[[], float] | None = None,
    timeout_spin_once: Callable[..., None] | None = None,
    timeout_goal_arrived_value: int | None = None,
) -> int:
    try:
        manager, scenario = build_scenario(
            scenario_type,
            dynamic_params,
            case_kind_module_name=case_kind_module_name,
            scenario_profile=scenario_profile,
            scenario_manager=scenario_manager,
            lane_offset_factory=lane_offset_factory,
            scenario_builders=scenario_builders,
        )
    except ModuleNotFoundError:
        printer(f"[Error] Config for '{scenario_type}' not found in case_kinds/ directory.")
        return 1
    except (KeyError, TypeError, ValueError) as exc:
        printer(f"[Error] {exc}")
        return 1

    if scenario is None:
        printer(f"[Error] Scenario object could not be created for type: {scenario_type}")
        return 1

    printer(f">>> [Runner] Starting '{scenario_type}' simulation...")
    manager.run([scenario])
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ensure_runtime_paths()
    args, unknown = build_parser().parse_known_args(list(argv) if argv is not None else None)
    try:
        dynamic_params = parse_dynamic_params(unknown)
    except ValueError as exc:
        print(f"[Error] {exc}")
        return 1

    return run_scenario_case(
        args.type,
        dynamic_params,
        case_kind_module_name=args.config_module,
        scenario_profile=args.scenario_profile,
    )
def _default_scenario_manager_factory() -> object:
    ensure_runtime_paths()
    from core.scenario_manager import ScenarioManager

    return ScenarioManager()


def _default_lane_offset_factory(lane_id: str, offset: float) -> object:
    ensure_runtime_paths()
    from core.scenario_manager import LaneOffset

    return LaneOffset(lane_id, offset)


def _load_uturn_builder() -> Callable[..., object]:
    ensure_runtime_paths()
    from scenarios.uturn.base import make_uturn_scenario

    return make_uturn_scenario


def _load_cutin_builder() -> Callable[..., object]:
    ensure_runtime_paths()
    from scenarios.cutin.base import make_cutin_scenario

    return make_cutin_scenario


def _load_cutout_builder() -> Callable[..., object]:
    ensure_runtime_paths()
    from scenarios.cutout.dynamic_spawn import make_cutout_scenario

    return make_cutout_scenario


def _load_deceleration_builder() -> Callable[..., object]:
    ensure_runtime_paths()
    from scenarios.deceleration.dynamic_spawn import make_deceleration_scenario

    return make_deceleration_scenario


def _load_swerve_builder() -> Callable[..., object]:
    ensure_runtime_paths()
    from scenarios.swerve.base import make_swerve_scenario

    return make_swerve_scenario


def _load_internal_builder(
    scenario_type: str,
    *,
    scenario_profile: str | None = None,
) -> Callable[..., object]:
    base_package = "targets.awsim.scenario_builders"
    module_name = (
        f"{base_package}.{scenario_profile}.{scenario_type}_builder"
        if scenario_profile
        else f"{base_package}.{scenario_type}_builder"
    )
    module = importlib.import_module(module_name)
    builder = getattr(module, f"build_{scenario_type}_scenario", None)
    if builder is None or not callable(builder):
        raise ValueError(f"Unsupported scenario type: {scenario_type}")
    return builder


def _load_external_builder(
    scenario_type: str,
    *,
    scenario_profile: str | None = None,
) -> Callable[..., object]:
    loader_map = {
        "uturn": _load_uturn_builder,
        "cutin": _load_cutin_builder,
        "cutout": _load_cutout_builder,
        "deceleration": _load_deceleration_builder,
        "swerve": _load_swerve_builder,
    }
    loader = loader_map.get(scenario_type)
    if loader is None:
        raise ValueError(f"Unsupported scenario type: {scenario_type}")
    return loader()


def _resolve_scenario_timeout_sec(
    scenario_type: str,
    *,
    case_kind_module_name: str | None,
    scenario_profile: str | None,
    timeout_sec: float | None,
) -> float | None:
    if timeout_sec is not None:
        return float(timeout_sec)

    case_definition = load_case_definition(
        case_kind=scenario_type,
        module_name=case_kind_module_name,
        scenario_profile=scenario_profile,
    )
    loaded_timeout = case_definition.get("timeout_sec")
    if loaded_timeout is None:
        return None
    return float(loaded_timeout)


def _install_scenario_goal_timeout(
    scenario: object,
    *,
    timeout_sec: float | None,
    printer: Callable[[str], None] = print,
    monotonic: Callable[[], float] | None = None,
    spin_once: Callable[..., None] | None = None,
    goal_arrived_value: int | None = None,
) -> None:
    if timeout_sec is None or timeout_sec <= 0:
        return
    if not hasattr(scenario, "my_spin") or not callable(getattr(scenario, "my_spin")):
        return
    if not hasattr(scenario, "global_state"):
        return

    import rclpy
    from rclpy.executors import ExternalShutdownException

    resolved_monotonic = monotonic or time.monotonic
    resolved_spin_once = spin_once or rclpy.spin_once
    resolved_goal_arrived_value = goal_arrived_value
    if resolved_goal_arrived_value is None:
        ensure_runtime_paths()
        from core.client_ros_node import AdsInternalStatus

        resolved_goal_arrived_value = AdsInternalStatus.GOAL_ARRIVED.value

    def timed_my_spin(self) -> None:
        deadline = resolved_monotonic() + float(timeout_sec)
        while self.global_state.get("ads_internal_status", 0) < resolved_goal_arrived_value:
            remaining = deadline - resolved_monotonic()
            if remaining <= 0:
                self.global_state["ads_internal_status"] = resolved_goal_arrived_value
                setattr(self, "_scenario_timeout_reached", True)
                _emit_scenario_log(
                    self,
                    "warning",
                    f"Scenario timed out after {float(timeout_sec):.1f}s before goal arrival.",
                    printer=printer,
                )
                return
            try:
                resolved_spin_once(self.client_node, timeout_sec=min(remaining, 1.0))
            except ExternalShutdownException:
                setattr(self, "_scenario_external_shutdown", True)
                _emit_scenario_log(
                    self,
                    "warning",
                    "Scenario spin interrupted by external shutdown.",
                    printer=printer,
                )
                return

        _emit_scenario_log(self, "info", "Scenario terminated", printer=printer)

    scenario.my_spin = MethodType(timed_my_spin, scenario)


def _emit_scenario_log(
    scenario: object,
    level: str,
    message: str,
    *,
    printer: Callable[[str], None],
) -> None:
    logger = getattr(scenario, "logger", None)
    log_method = getattr(logger, level, None) if logger is not None else None
    if callable(log_method):
        log_method(message)
        return
    printer(message)
