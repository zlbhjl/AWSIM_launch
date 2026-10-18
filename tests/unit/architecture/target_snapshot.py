"""Record what the shared code builds for each target and mode.

The snapshot pins the behavior before the target plugin refactor: component
classes and their settings, CLI choices and normalized arguments, and the
strategy, statistical request, and sampling settings for each target and mode.
Regenerate with ``UPDATE_TARGET_SNAPSHOT=1 python3 -m pytest tests/unit/architecture``
only when a behavior change is intended.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from types import SimpleNamespace

TARGETS = ("awsim", "bbsl", "bernoulli", "dynamics", "prism")
STATISTICAL_MODES = ("binomial_ci", "sprt", "dkw", "dkw_fixed", "ebstop")
AWSIM_MODES = (
    "explore", "margin", "focus", "boundary_gap", "jama_edge", "ttc_edge", "worst_ttc", "verify_consistency",
    *STATISTICAL_MODES,
)
STRATEGIST_ATTRIBUTES = ("run_mode", "scenario_name", "param_names", "random_seed", "MAX_SAMPLES", "strategy_settings")
MODE_RUNNERS = ("binomial_mode", "sprt_mode", "ebstop_mode", "dkw_mode")
SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "architecture" / "target_snapshot.json"


def _plain(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_plain(item) for item in value]
        return sorted(items, key=json.dumps) if isinstance(value, (set, frozenset)) else items
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return f"<{type(value).__module__}.{type(value).__qualname__}>"


def _components(target: str) -> dict[str, object]:
    from targets.registry import build_target_components

    args = SimpleNamespace(
        target=target,
        case_kind="uturn",
        config_module="targets.awsim.case_kinds.uturn",
        headless=True,
        container_profile="autoware171",
        scenario_profile="autoware171",
        ext_mode="cvm",
    )
    components = build_target_components(args)
    return {
        "backend": f"{type(components.backend).__module__}.{type(components.backend).__qualname__}",
        "backend_config": _plain(getattr(components.backend, "config", None)),
        "result_interpreter": (
            f"{type(components.result_interpreter).__module__}.{type(components.result_interpreter).__qualname__}"
        ),
        "interpreter_context": _plain(getattr(components.result_interpreter, "context", None)),
    }


def _cli_choices() -> dict[str, object]:
    from apps.cli import orchestrator_main, worker_main

    result = {}
    for name, module in (("orchestrator_main", orchestrator_main), ("worker_main", worker_main)):
        parser = module.build_parser()
        action = next(action for action in parser._actions if "--target" in action.option_strings)
        result[name] = sorted(action.choices)
    return result


def _normalized_cli(target: str, mode: str, tmp: Path) -> dict[str, object]:
    from apps.cli import orchestrator_main

    argv = [
        "--target", target, "--case-kind", "uturn", "--mode", mode,
        "--output", str(tmp / "records.jsonl"), "--dataset-csv", str(tmp / "dataset.csv"),
        "--max-samples", "10",
    ]
    if target == "prism":
        argv += ["--param", "steps=20"]
    parser = orchestrator_main.build_parser()
    args = orchestrator_main.normalize_args(parser.parse_args(argv), argv=argv)
    config = orchestrator_main.build_orchestrator_config(args)
    keys = (
        "target", "run_mode", "case_kind", "config_module", "binomial_target", "sprt_target", "ebstop_target",
        "dynamics_input_distribution", "params", "max_samples", "scenario_profile", "container_profile",
    )
    return {key: _plain(getattr(config, key, None)) for key in keys}


def _replay_csv(tmp: Path) -> Path:
    """A small replay source with every input column of the AWSIM U-turn case."""
    import csv

    from targets.awsim.case_kinds import uturn

    rows = [
        (1, "success", "BINOMIAL_CI: Sampling (1)", 0, {"dx0": 12.0, "ego_speed": 35.0, "npc_speed": 15.0}),
        (2, "success", "BINOMIAL_CI: Sampling (2)", 1, {"dx0": 11.0, "ego_speed": 38.0, "npc_speed": 20.0}),
        (3, "timeout", "BINOMIAL_CI: Sampling (3)", -1, {"dx0": 14.0, "ego_speed": 31.0, "npc_speed": 12.0}),
    ]
    path = tmp / "replay_source.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["loop_num", "status", "reason", "c_collision", *uturn.FIXED_PARAMS, *uturn.PARAM_RANGES]
        )
        writer.writeheader()
        for loop_num, status, reason, collision, varied in rows:
            writer.writerow(
                {"loop_num": loop_num, "status": status, "reason": reason, "c_collision": collision,
                 **uturn.FIXED_PARAMS, **varied}
            )
    return path


def _strategy(target: str, mode: str, tmp: Path) -> dict[str, object]:
    from apps.cli import orchestrator_main
    from orchestration.orchestrator import Orchestrator

    replay = mode == "replay"
    argv = [
        "--target", target, "--case-kind", "uturn", "--mode", mode,
        "--output", str(tmp / "records.jsonl"), "--dataset-csv", str(tmp / "dataset.csv"),
        "--max-samples", "10",
    ]
    if target == "prism":
        argv += ["--param", "steps=20"]
    if replay:
        argv += ["--replay-csv", str(_replay_csv(tmp))]
    parser = orchestrator_main.build_parser()
    args = orchestrator_main.normalize_args(parser.parse_args(argv), argv=argv)
    config = orchestrator_main.build_orchestrator_config(args)
    strategy = Orchestrator()._build_strategy(config)
    request = getattr(strategy, "statistical_request", None)
    strategy_config = getattr(strategy, "config", None)
    summary: dict[str, object] = {
        "strategy": f"{type(strategy).__module__}.{type(strategy).__qualname__}",
        "request": _plain(request),
        "target_total": Orchestrator()._resolve_target_total(config, 0),
    }
    if strategy_config is not None and dataclasses.is_dataclass(strategy_config):
        summary["strategy_config"] = {
            key: value
            for key, value in _plain(strategy_config).items()
            if key not in {"experiment_id"}
        }
    if type(strategy).__name__ == "ActiveLearningStrategist":
        summary["strategist"] = {name: _plain(getattr(strategy, name, None)) for name in STRATEGIST_ATTRIBUTES}
        summary["mode_runners"] = {
            name: {
                "class": type(getattr(strategy, name)).__name__,
                "config": _plain(getattr(getattr(strategy, name), "config", None)),
            }
            for name in MODE_RUNNERS
            if getattr(strategy, name, None) is not None
        }
    if replay:
        summary["replay"] = {
            "eligible_count": getattr(strategy, "eligible_count", None),
            "cases": [
                {"input": _plain(case.input), "reason": case.reason}
                for case in getattr(strategy, "_pending_cases", [])
            ],
        }
    return summary


def build_snapshot(tmp: Path) -> dict[str, object]:
    """Build the snapshot with machine-dependent defaults pinned to placeholders."""
    import runtime.container.profile as profile

    pinned = {
        "DEFAULT_LAUNCH_DIR": Path("/<repo>"),
        "DEFAULT_CONTAINER_OUTPUT_DIR": Path("/<home>/simulation_traces"),
        "DEFAULT_HOST_OUTPUT_DIR": Path("/<home>/simulation_traces_host"),
    }
    original = {name: getattr(profile, name) for name in pinned}
    previous_domain = os.environ.get("ROS_DOMAIN_ID")
    os.environ["ROS_DOMAIN_ID"] = "0"
    try:
        for name, value in pinned.items():
            setattr(profile, name, value)
        return _build_snapshot(tmp)
    finally:
        for name, value in original.items():
            setattr(profile, name, value)
        if previous_domain is None:
            os.environ.pop("ROS_DOMAIN_ID", None)
        else:
            os.environ["ROS_DOMAIN_ID"] = previous_domain


def _build_snapshot(tmp: Path) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "components": {target: _components(target) for target in TARGETS},
        "cli_choices": _cli_choices(),
        "normalized_cli": {},
        "strategies": {},
    }
    combinations = [("awsim", mode) for mode in AWSIM_MODES] + [("awsim", "replay")] + [
        (target, mode) for target in ("dynamics", "bernoulli", "prism") for mode in STATISTICAL_MODES
    ] + [("prism", "explore")]
    for target, mode in combinations:
        key = f"{target}:{mode}"
        if mode != "replay":
            snapshot["normalized_cli"][key] = _normalized_cli(target, mode, tmp)
        snapshot["strategies"][key] = _strategy(target, mode, tmp)
    # Temporary paths differ between runs; keep the snapshot independent of them.
    return json.loads(json.dumps(snapshot).replace(str(tmp), "<tmp>"))


def load_snapshot() -> dict[str, object]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def write_snapshot(snapshot: dict[str, object]) -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
