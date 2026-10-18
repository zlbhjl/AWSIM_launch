from __future__ import annotations

from collections.abc import Mapping
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
    # Metric for --mode ebstop; defaults to dkw_metric.
    ebstop_metric: str | None = None
    # True when statistical modes draw independent inputs from a distribution
    # (orchestration.random_parameter_sampling) instead of the AWSIM strategist.
    iid_sampling: bool = False
    # Input distribution used when --dynamics-input-distribution is not given.
    default_input_distribution: Mapping[str, Mapping[str, object]] | None = None

    @property
    def resolved_ebstop_metric(self) -> str:
        return self.ebstop_metric or self.dkw_metric


def supports_iid_sampling(target: str) -> bool:
    """Return True when the target's statistical modes use IID input sampling."""
    try:
        return load_statistical_target_profile(target).iid_sampling
    except (ImportError, TypeError):
        return False


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
    "supports_iid_sampling",
]
