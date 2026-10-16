from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from targets.awsim.theory import build_theory_metrics, default_theory_columns
from targets.statistical import StatisticalTargetProfile


STATISTICAL_TARGET_PROFILE = StatisticalTargetProfile(
    target="awsim",
    binary_metric="c_collision",
    dkw_metric="min_ttc",
    region="custom",
    region_policy_name="awsim_theory",
    minimum_dkw_value=0.0,
)


class AWSIMStatisticalRegionPolicy:
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
    "STATISTICAL_TARGET_PROFILE",
    "build_statistical_region_policy",
]
