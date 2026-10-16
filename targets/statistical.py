from __future__ import annotations

from dataclasses import dataclass

from contracts.statistical_region import (
    PassthroughStatisticalRegionPolicy,
    StatisticalRegionPolicy,
)


@dataclass(frozen=True)
class StatisticalTargetProfile:
    target: str
    binary_metric: str
    dkw_metric: str
    region: str = "custom"
    region_policy_name: str = "passthrough"
    minimum_dkw_value: float | None = None


def load_statistical_target_profile(target: str) -> StatisticalTargetProfile:
    module_name = f"targets.{target}.statistical"
    module = __import__(module_name, fromlist=["STATISTICAL_TARGET_PROFILE"])
    profile = getattr(module, "STATISTICAL_TARGET_PROFILE", None)
    if not isinstance(profile, StatisticalTargetProfile):
        raise TypeError(
            f"targets.{target}.statistical must define STATISTICAL_TARGET_PROFILE"
        )
    return profile


def build_statistical_region_policy(
    target: str,
    *,
    case_kind: str,
    config_module_name: str | None = None,
) -> StatisticalRegionPolicy:
    module_name = f"targets.{target}.statistical"
    module = __import__(module_name, fromlist=["build_statistical_region_policy"])
    builder = getattr(module, "build_statistical_region_policy", None)
    if not callable(builder):
        return PassthroughStatisticalRegionPolicy()
    return builder(
        case_kind=case_kind,
        config_module_name=config_module_name,
    )


__all__ = [
    "StatisticalTargetProfile",
    "build_statistical_region_policy",
    "load_statistical_target_profile",
]
