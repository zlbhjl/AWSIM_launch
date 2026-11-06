from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from contracts.metric_spec import MetricSpec, check_method_fits
from evaluation.region_filter import GENERIC_VOCABULARY, RegionVocabulary

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
    # What values each metric takes (contracts/metric_spec.py): a spec, or a
    # function of the run's parameters (for example PRISM's horizon).
    metric_specs: Mapping[str, MetricSpec | Callable[[Mapping[str, object]], MetricSpec | None]] = field(
        default_factory=dict
    )
    # Further metrics without a fixed range (refused by EBStop); the 'real'
    # metrics of metric_specs count as well.
    unbounded_metrics: frozenset[str] = frozenset()
    # Region names usable in --dkw-region (evaluation/region_filter.py).
    region_vocabulary: RegionVocabulary = GENERIC_VOCABULARY

    @property
    def resolved_ebstop_metric(self) -> str:
        return self.ebstop_metric or self.dkw_metric

    def metric_spec(self, metric: str, params: Mapping[str, object] | None = None) -> MetricSpec | None:
        spec = self.metric_specs.get(metric)
        if callable(spec) and not isinstance(spec, MetricSpec):
            spec = spec(dict(params or {}))
        if spec is None and metric in self.unbounded_metrics:
            return MetricSpec("real")
        return spec


def target_metric_spec(target: str, metric: str, params: Mapping[str, object] | None = None) -> MetricSpec | None:
    try:
        profile = load_statistical_target_profile(target)
    except (ImportError, TypeError, ValueError):
        return None
    return profile.metric_spec(metric, params)


def check_metric_method(target: str, metric: str, method: str, params: Mapping[str, object] | None = None) -> None:
    """Refuse a statistical method that does not fit the target's declared metric (before anything runs)."""
    check_method_fits(target, metric, method, target_metric_spec(target, metric, params))


def check_ebstop_metric(target: str, metric: str) -> None:
    check_metric_method(target, metric, "ebstop")


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
