"""AWSIM checks and defaults for the shared orchestrator and worker CLIs.

The options themselves are defined by the shared CLIs; this module holds what
only AWSIM needs (scenario profile, case-kind module, focus points, DKW bounds,
replay rules, strategy modes). Called through the awsim plugin hooks
``normalize_cli_args`` and ``validate_orchestrator_args``.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from targets.awsim.case_kinds import (
    SUPPORTED_SCENARIO_PROFILES,
    build_default_case_kind_module_name,
    load_case_definition,
    load_focus_points,
)

STRATEGY_MODES = (
    "explore", "focus", "margin", "jama_edge", "ttc_edge", "worst_ttc", "dkw", "dkw_fixed",
    "verify_consistency", "binomial_ci", "boundary_gap", "replay", "sprt", "ebstop",
)


def config_module_for(case_kind: str, scenario_profile: str | None) -> str:
    return build_default_case_kind_module_name(case_kind=case_kind, scenario_profile=scenario_profile)


def resolve_focus_points(
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


def normalize_cli_args(args: argparse.Namespace, raw_argv: Sequence[str], *, role: str) -> None:
    explicit_case_kind = "--case-kind" in raw_argv
    explicit_config_module = "--config-module" in raw_argv
    explicit_scenario_type = "--scenario-type" in raw_argv
    explicit_focus_points = "--focus_points" in raw_argv
    explicit_dkw_bounds = any(flag in raw_argv for flag in ("--dkw-bounds", "--dkw_bounds"))

    if (
        getattr(args, "scenario_profile", None) is None
        and getattr(args, "container_profile", None) in SUPPORTED_SCENARIO_PROFILES
    ):
        args.scenario_profile = args.container_profile
    if role == "worker" and args.legacy_type and not explicit_case_kind:
        args.case_kind = args.legacy_type
    if args.config_module is None or not explicit_config_module:
        args.config_module = config_module_for(args.case_kind, getattr(args, "scenario_profile", None))
    if role == "worker":
        case_definition = load_case_definition(case_kind=args.case_kind, module_name=args.config_module)
        if not explicit_scenario_type:
            args.scenario_type = str(case_definition.get("scenario_type", args.case_kind))
    args.focus_points = resolve_focus_points(
        case_kind=args.case_kind,
        config_module=args.config_module,
        raw_focus_points=args.focus_points if explicit_focus_points else None,
        allow_config_default=args.mode == "focus",
    )
    if role == "orchestrator" and explicit_dkw_bounds and args.dkw_bounds:
        try:
            parsed_bounds = json.loads(args.dkw_bounds)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--dkw-bounds must be valid JSON: {exc}") from exc
        if not isinstance(parsed_bounds, dict):
            raise ValueError("--dkw-bounds must decode to an object")
        args.dkw_bounds = parsed_bounds


def validate_orchestrator_args(args: argparse.Namespace) -> None:
    fixture_mode = bool(getattr(args, "fixture", None))
    simulation_mode = bool(getattr(args, "params", []))
    replay_mode = bool(getattr(args, "replay_csv", None))
    strategy_mode = not fixture_mode and not simulation_mode and not replay_mode
    if replay_mode and getattr(args, "mode", None) != "replay":
        raise ValueError("--replay-csv requires --mode replay")
    if getattr(args, "mode", None) == "replay" and not replay_mode:
        raise ValueError("--mode replay requires --replay-csv")
    if replay_mode and not getattr(args, "run_id", None):
        raise ValueError("--run-id is required in replay mode")
    if replay_mode and not str(getattr(args, "replay_reason_pattern", "")).strip():
        raise ValueError("--replay-reason-pattern must not be empty")
    if replay_mode and getattr(args, "dataset_csv", None):
        source = Path(str(args.replay_csv)).expanduser().resolve()
        destination = Path(str(args.dataset_csv)).expanduser().resolve()
        if source == destination:
            raise ValueError("--replay-csv and --dataset-csv must be different files")
    if (strategy_mode or replay_mode) and getattr(args, "dataset_csv", None) is None:
        raise ValueError("--dataset-csv is required for AWSIM strategy mode")
    if strategy_mode and getattr(args, "mode", "explore") not in STRATEGY_MODES:
        raise ValueError("AWSIM strategy mode currently supports --mode " + "/".join(STRATEGY_MODES) + " only")


__all__ = [
    "STRATEGY_MODES",
    "config_module_for",
    "normalize_cli_args",
    "resolve_focus_points",
    "validate_orchestrator_args",
]
