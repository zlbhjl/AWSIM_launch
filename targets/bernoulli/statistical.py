from __future__ import annotations

from contracts.statistical_region import PassthroughStatisticalRegionPolicy
from contracts.metric_spec import binary, bounded, real
from targets.statistical import StatisticalTargetProfile

from .model import INPUT_NAMES


STATISTICAL_TARGET_PROFILE = StatisticalTargetProfile(
    target="bernoulli",
    binary_metric="c_failure",
    dkw_metric="time_to_failure",
    region="custom",
    region_policy_name="passthrough",
    minimum_dkw_value=0.0,
    ebstop_metric="score",
    metric_specs={"c_failure": binary(), "time_to_failure": real(), "score": bounded(0.0, 1.0)},
    iid_sampling=True,
    default_input_distribution={
        name: {"distribution": "uniform", "min": 0.0, "max": 1.0} for name in INPUT_NAMES
    },
)


def build_statistical_region_policy(
    *,
    case_kind: str,
    config_module_name: str | None = None,
) -> PassthroughStatisticalRegionPolicy:
    del case_kind, config_module_name
    return PassthroughStatisticalRegionPolicy()


__all__ = ["STATISTICAL_TARGET_PROFILE", "build_statistical_region_policy"]
