from __future__ import annotations

from contracts.statistical_region import PassthroughStatisticalRegionPolicy
from contracts.metric_spec import binary, real
from targets.statistical import StatisticalTargetProfile


STATISTICAL_TARGET_PROFILE = StatisticalTargetProfile(
    target="dynamics",
    binary_metric="c_collision",
    dkw_metric="min_ttc",
    region="custom",
    region_policy_name="passthrough",
    minimum_dkw_value=0.0,
    iid_sampling=True,
    # The U-turn's TTC has no fixed range (see targets/awsim/statistical.py).
    metric_specs={"c_collision": binary(), "c_event": binary(), "min_ttc": real()},
)


def build_statistical_region_policy(
    *,
    case_kind: str,
    config_module_name: str | None = None,
) -> PassthroughStatisticalRegionPolicy:
    del case_kind, config_module_name
    return PassthroughStatisticalRegionPolicy()


__all__ = ["STATISTICAL_TARGET_PROFILE", "build_statistical_region_policy"]
