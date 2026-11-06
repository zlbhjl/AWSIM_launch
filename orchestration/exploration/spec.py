"""What a target tells the shared GP exploration (docs/exploration_unification_plan.md §3.1)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from evaluation.gp_boundary import TrainingRules


@dataclass(frozen=True)
class ExplorationSpec:
    # How result rows become GP training data (evaluation/gp_boundary.py).
    training_rules: TrainingRules | None = None
    # Mask of result rows that are failed runs to try again near the same point
    # (None: no recovery).
    failed_rows: Callable[[pd.DataFrame], pd.Series] | None = None
    # Target-specific point rules for the extraction modes (jama_edge, ttc_edge,
    # worst_ttc, verify_consistency, boundary_gap): an object with EXTRACTORS,
    # summarize_boundary_gap_progress, classify_consistency, save_dataframe_to_csv.
    point_rules: object | None = None
    # Region names usable in --dkw-region when the region policy declares none
    # (evaluation/region_filter.py).
    region_vocabulary: object | None = None


__all__ = ["ExplorationSpec"]
