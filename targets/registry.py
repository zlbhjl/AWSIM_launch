"""Find the target plugins and build their components.

A target is any package ``targets/<name>/`` whose ``plugin.py`` defines
``PLUGIN`` (see ``contracts.target_plugin``). Adding such a package makes the
target available to ``--target`` without editing this module.
"""

from __future__ import annotations

import argparse
import importlib
from functools import lru_cache
from pathlib import Path

import targets
from contracts.target_plugin import TargetComponents, TargetPlugin

PLUGIN_MODULE = "plugin"


@lru_cache(maxsize=1)
def _discover() -> dict[str, TargetPlugin]:
    plugins: dict[str, TargetPlugin] = {}
    # Target packages may be regular or namespace packages, so look for
    # <name>/plugin.py directly instead of relying on __init__.py.
    for root in targets.__path__:
        for plugin_file in sorted(Path(root).glob(f"*/{PLUGIN_MODULE}.py")):
            name = plugin_file.parent.name
            if name.startswith(("_", ".")) or name in plugins:
                continue
            module_name = f"{targets.__name__}.{name}.{PLUGIN_MODULE}"
            plugin = getattr(importlib.import_module(module_name), "PLUGIN", None)
            if not isinstance(plugin, TargetPlugin):
                raise TypeError(f"{module_name} must define PLUGIN implementing TargetPlugin")
            if plugin.name != name:
                raise ValueError(f"{module_name}: PLUGIN.name {plugin.name!r} must match the package name")
            plugins[name] = plugin
    return plugins


def reload_target_plugins() -> None:
    """Forget discovered plugins (for tests that add a target package)."""
    _discover.cache_clear()


def list_target_names() -> list[str]:
    return sorted(_discover())


def get_target_plugin(name: str) -> TargetPlugin:
    try:
        return _discover()[name]
    except KeyError:
        raise ValueError(f"Unsupported target: {name}") from None


def default_case_kind(target: str) -> str:
    """Case kind used when --case-kind is omitted (the plugin's, or the target name)."""
    return str(getattr(get_target_plugin(target), "default_case_kind", target))


def default_config_module(target: str, case_kind: str) -> str:
    """Config module used when --config-module is omitted for a non-AWSIM target.

    The module is only imported by the AWSIM strategist; other targets record it as
    a label, so a target without its own default uses its importable plugin module.
    """
    plugin = get_target_plugin(target)
    resolver = getattr(plugin, "default_config_module", None)
    if callable(resolver):
        return str(resolver(case_kind))
    return f"{targets.__name__}.{target}.{PLUGIN_MODULE}"


def apply_target_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """Fill --case-kind from the target when it was omitted. Safe to call repeatedly."""
    if getattr(args, "case_kind", None) is None:
        args.case_kind = default_case_kind(args.target)
    return args


def build_target_components(
    args: argparse.Namespace,
    *,
    backend: object | None = None,
    result_interpreter: object | None = None,
) -> TargetComponents:
    if backend is not None and result_interpreter is not None:
        return TargetComponents(backend=backend, result_interpreter=result_interpreter)
    components = get_target_plugin(args.target).build_components(args)
    return TargetComponents(
        backend=backend or components.backend,
        result_interpreter=result_interpreter or components.result_interpreter,
    )


__all__ = [
    "TargetComponents",
    "apply_target_defaults",
    "build_target_components",
    "default_case_kind",
    "default_config_module",
    "get_target_plugin",
    "list_target_names",
    "reload_target_plugins",
]
