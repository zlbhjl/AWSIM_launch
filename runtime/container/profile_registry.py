"""Discover container launch profiles in the target packages.

A target that runs in cluster containers keeps its profiles in
``targets/<target>/container_profiles/<name>.py``; each module exports
``CONTAINER_LAUNCH_PROFILE``. Profiles are found like target plugins
(``targets/registry.py``), so adding one needs no list in shared code.

Checks: the profile's ``name`` equals the module name, names are unique across
targets, and ``profile.target`` equals the target package that holds it.
"""

from __future__ import annotations

import importlib
from functools import lru_cache
from pathlib import Path

import targets

PROFILE_PACKAGE = "container_profiles"


@lru_cache(maxsize=1)
def _discover() -> dict[str, object]:
    from runtime.container.profile import ContainerLaunchProfile

    profiles: dict[str, object] = {}
    seen_roots: set[Path] = set()
    for root in targets.__path__:
        # The namespace path can list one directory twice (e.g. "" and its absolute path).
        resolved_root = Path(root).resolve()
        if resolved_root in seen_roots:
            continue
        seen_roots.add(resolved_root)
        for profile_file in sorted(resolved_root.glob(f"*/{PROFILE_PACKAGE}/*.py")):
            target = profile_file.parent.parent.name
            name = profile_file.stem
            if target.startswith(("_", ".")) or name.startswith(("_", ".")):
                continue
            if name in profiles and profiles[name].target == target:
                continue  # the same target earlier on the path wins, as for plugins
            module_name = f"{targets.__name__}.{target}.{PROFILE_PACKAGE}.{name}"
            profile = getattr(importlib.import_module(module_name), "CONTAINER_LAUNCH_PROFILE", None)
            if not isinstance(profile, ContainerLaunchProfile):
                raise TypeError(f"{module_name} must export CONTAINER_LAUNCH_PROFILE")
            if profile.name != name:
                raise ValueError(f"{module_name}: profile name {profile.name!r} must match the module name")
            if profile.target != target:
                raise ValueError(f"{module_name}: profile target {profile.target!r} must be {target!r}")
            if name in profiles:
                raise ValueError(f"container profile {name!r} is defined by more than one target")
            profiles[name] = profile
    return profiles


def reload_container_profiles() -> None:
    _discover.cache_clear()


def list_container_profiles(target: str | None = None) -> tuple[str, ...]:
    """Names of the discovered profiles, optionally only those of ``target``."""
    return tuple(
        sorted(name for name, profile in _discover().items() if target is None or profile.target == target)
    )


def get_container_profile(name: str):
    profiles = _discover()
    if name not in profiles:
        raise ValueError(f"Unsupported container profile: {name}")
    return profiles[name]


__all__ = ["get_container_profile", "list_container_profiles", "reload_container_profiles"]
