"""What AWSIM tells the shared GP exploration (orchestration/exploration/spec.py).

- Training rules: rows with -1 in a ``c_*`` / ``formula_*`` column are failed
  judgements and are dropped; a collision implies every ``c_ttc_*`` label; the
  ``c_ttc_*`` labels are monotone across thresholds; rows with a collision,
  ``min_ttc < 1.5`` or ``min_distance < 2.0`` are kept when thinning.
- Failed runs to recover: ``min_ttc == -1`` or ``c_collision == -1``.
- Point rules of the extraction modes: the AWSIM extractors (targets/awsim/point_rules.py:
  JAMA / TTC edges, worst TTC, near-miss consistency, boundary-gap cells).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from targets.awsim import point_rules
from evaluation.gp_boundary import TrainingRules
from orchestration.exploration.spec import ExplorationSpec
from targets.awsim.statistical import AWSIM_REGION_VOCABULARY


def _clean(working_df: pd.DataFrame) -> pd.DataFrame:
    for col in working_df.columns:
        if col.startswith("c_") or col.startswith("formula_"):
            working_df = working_df[~working_df[col].isin([-1, "-1", -1.0])]

    if "c_collision" in working_df.columns:
        collision_mask = working_df["c_collision"].isin([1, "1", 1.0])
        for col in working_df.columns:
            if col.startswith("c_ttc_"):
                working_df.loc[collision_mask, col] = 1

    ttc_cols = sorted(
        [c for c in working_df.columns if c.startswith("c_ttc_")],
        key=lambda x: float(x.split("_")[-1]),
    )
    for i in range(len(ttc_cols) - 1):
        working_df.loc[
            working_df[ttc_cols[i]].isin([1, "1", 1.0]),
            ttc_cols[i + 1],
        ] = 1
    return working_df


def _critical(valid_df: pd.DataFrame) -> pd.Series:
    return (
        (valid_df.get("c_collision", 0) == 1)
        | (valid_df.get("min_ttc", 999.0) < 1.5)
        | (valid_df.get("min_distance", 999.0) < 2.0)
    )


def _failed_rows(df_dataset: pd.DataFrame) -> pd.Series:
    min_ttc_numeric = pd.to_numeric(
        df_dataset.get("min_ttc", pd.Series(np.nan, index=df_dataset.index)), errors="coerce",
    )
    c_collision_numeric = pd.to_numeric(
        df_dataset.get("c_collision", pd.Series(np.nan, index=df_dataset.index)), errors="coerce",
    )
    return (min_ttc_numeric == -1) | (c_collision_numeric == -1)


AWSIM_TRAINING_RULES = TrainingRules(clean=_clean, critical=_critical)
AWSIM_EXPLORATION = ExplorationSpec(
    training_rules=AWSIM_TRAINING_RULES,
    failed_rows=_failed_rows,
    point_rules=point_rules,
    region_vocabulary=AWSIM_REGION_VOCABULARY,
)

__all__ = ["AWSIM_EXPLORATION", "AWSIM_TRAINING_RULES"]
