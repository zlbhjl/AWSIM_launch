"""Target-specific CLI behaviour, dispatched to the target plugins.

The shared CLIs define every option; the plugins decide what their target
needs (optional attributes, read with ``getattr``):

- ``exclusive_options``: groups ``(option destinations, message)`` that only this
  target accepts; the CLIs reject them for every other target.
- ``normalize_cli_args(args, raw_argv, role)``: defaults ("orchestrator" / "worker").
- ``validate_orchestrator_args(args)``: checks of an orchestrator run.
- ``config_module_for(case_kind, scenario_profile)``: the config module default.
- ``scenario_profiles``: values accepted by ``--scenario-profile``.
- ``statistical_summary_extras(args)``: entries added to the summary of a statistical run.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from targets.registry import default_config_module, get_target_plugin, list_target_names


def _plugin(target: str):
    try:
        return get_target_plugin(target)
    except ValueError:
        return None


def _is_set(value: object) -> bool:
    return value is not None and value is not False


def reject_foreign_options(args: argparse.Namespace) -> None:
    """Reject options that belong to another target (plugin ``exclusive_options``)."""
    target = getattr(args, "target", None)
    for name in list_target_names():
        if name == target:
            continue
        for destinations, message in getattr(_plugin(name), "exclusive_options", ()):
            if any(_is_set(getattr(args, destination, None)) for destination in destinations):
                raise ValueError(message)


def normalize_for_target(args: argparse.Namespace, raw_argv: Sequence[str], *, role: str) -> None:
    hook = getattr(_plugin(args.target), "normalize_cli_args", None)
    if callable(hook):
        hook(args, raw_argv, role=role)


def validate_orchestrator_for_target(args: argparse.Namespace) -> None:
    hook = getattr(_plugin(args.target), "validate_orchestrator_args", None)
    if callable(hook):
        hook(args)


def config_module_for(target: str, case_kind: str, scenario_profile: str | None = None) -> str:
    hook = getattr(_plugin(target), "config_module_for", None)
    if callable(hook):
        return hook(case_kind, scenario_profile)
    return default_config_module(target, case_kind)


def statistical_summary_extras(args: argparse.Namespace) -> dict[str, object]:
    hook = getattr(_plugin(args.target), "statistical_summary_extras", None)
    return dict(hook(args)) if callable(hook) else {}


def scenario_profile_choices() -> tuple[str, ...]:
    names: set[str] = set()
    for target in list_target_names():
        names.update(getattr(_plugin(target), "scenario_profiles", ()))
    return tuple(sorted(names))


__all__ = [
    "config_module_for",
    "normalize_for_target",
    "reject_foreign_options",
    "scenario_profile_choices",
    "statistical_summary_extras",
    "validate_orchestrator_for_target",
]
