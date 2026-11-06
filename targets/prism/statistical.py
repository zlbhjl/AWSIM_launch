from __future__ import annotations

from contracts.statistical_region import PassthroughStatisticalRegionPolicy
from contracts.metric_spec import binary, bounded
from targets.statistical import StatisticalTargetProfile


STATISTICAL_TARGET_PROFILE = StatisticalTargetProfile(
    target="prism",
    binary_metric="c_failure",
    dkw_metric="steps_to_failure_capped",
    region="custom",
    region_policy_name="passthrough",
    minimum_dkw_value=0.0,
    metric_specs={
        "c_failure": binary(),
        # First failure step, or horizon + 1 when the path does not fail (trace_parser.py).
        "steps_to_failure_capped": lambda params: (
            bounded(0, int(params["steps"]) + 1) if params.get("steps") is not None else None
        ),
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
