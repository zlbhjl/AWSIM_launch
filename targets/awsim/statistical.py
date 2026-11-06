from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from targets.awsim.theory import build_theory_metrics, default_theory_columns
from contracts.metric_spec import binary, real
from evaluation.region_filter import RegionVocabulary

# Named regions of AWSIM results (moved from point_extractors.py).
AWSIM_REGION_VOCABULARY = RegionVocabulary(
    macros={
        "emp_safe": "(c_collision == 0)",
        "jama_safe": "(theory_margin_a_human >= 0.0)",
        "intersect_safe": "((c_collision == 0) and (theory_margin_a_human >= 0.0))",
        "union_safe": "((c_collision == 0) or (theory_margin_a_human >= 0.0))",
    },
    required_columns={
        "c_collision": ("c_collision",),
        "theory_margin_a_human": ("theory_margin_a_human",),
    },
    string_columns=frozenset({"theory_zone_a", "theory_zone_b", "npc_id"}),
)
from targets.statistical import StatisticalTargetProfile


# min_ttc has no upper bound (infinite when a collision is never in prospect,
# well over 10 s in some approaches), so it must not be used with EBStop, whose
# guarantee needs every value in a fixed range: an EBStop interval on it carries
# no guarantee. Use DKW for its distribution / quantiles, or a 0/1 metric with a
# binomial interval. See docs/statistical_method_selection.md (EBStop).
# min_distance and min_ttb have no fixed upper bound either.
STATISTICAL_TARGET_PROFILE = StatisticalTargetProfile(
    target="awsim",
    binary_metric="c_collision",
    dkw_metric="min_ttc",
    region="custom",
    region_policy_name="awsim_theory",
    region_vocabulary=AWSIM_REGION_VOCABULARY,
    minimum_dkw_value=0.0,
    metric_specs={
        "c_collision": binary(),
        "min_ttc": real(),
        "min_distance": real(),
        "min_ttb": real(),
    },
)


class AWSIMStatisticalRegionPolicy:
    # Region names of AWSIM results, used by the statistical modes.
    vocabulary = AWSIM_REGION_VOCABULARY

    def __init__(
        self,
        *,
        case_kind: str,
        config_module_name: str | None = None,
    ) -> None:
        self.case_kind = case_kind
        self.config_module_name = config_module_name

    def build_filter_frame(self, point: Mapping[str, object]) -> pd.DataFrame:
        row: dict[str, object] = dict(point)
        row.update(default_theory_columns())
        row.update(
            build_theory_metrics(
                case_kind=self.case_kind,
                values=row,
                config_module_name=self.config_module_name,
            )
        )
        row.setdefault("c_collision", 0)
        row.setdefault("min_ttc", 99.9)
        row.setdefault("min_distance", 99.9)
        row.setdefault("min_ttb", 99.9)
        return pd.DataFrame([row])


def build_statistical_region_policy(
    *,
    case_kind: str,
    config_module_name: str | None = None,
) -> AWSIMStatisticalRegionPolicy:
    return AWSIMStatisticalRegionPolicy(
        case_kind=case_kind,
        config_module_name=config_module_name,
    )


__all__ = [
    "AWSIMStatisticalRegionPolicy",
    "AWSIM_REGION_VOCABULARY",
    "STATISTICAL_TARGET_PROFILE",
    "build_statistical_region_policy",
]
