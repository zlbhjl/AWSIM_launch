"""Compatibility names; profiles now live in targets/<target>/container_profiles/."""

from runtime.container.profile import resolve_container_launch_profile
from runtime.container.profile_registry import list_container_profiles


def __getattr__(name: str):
    if name == "SUPPORTED_CONTAINER_PROFILES":
        return list_container_profiles()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "SUPPORTED_CONTAINER_PROFILES",
    "list_container_profiles",
    "resolve_container_launch_profile",
]
