#!/usr/bin/env python3
"""Compatible entry for the legacy scripts (strategist.py, estimator.py, tools/analysis).

The point rules live in targets/awsim/point_rules.py and the region filter in
evaluation/region_filter.py (docs/point_extractors_split_plan.md). The new
framework does not import this module.
"""

from evaluation.region_filter import save_dataframe_to_csv  # noqa: F401
from targets.awsim.point_rules import *  # noqa: F401,F403
from targets.awsim.point_rules import (  # noqa: F401
    EXTRACTORS,
    REGION_MACROS,
    REGION_REQUIRED_COLUMNS,
    STRING_QUERY_COLUMNS,
    _build_boundary_gap_binning,
    _clean_dataframe,
    _empty_like,
    _extract_points,
    _point_to_boundary_gap_key,
    classify_consistency,
    collect_boundary_gap_cells,
    filter_by_region_and_bounds,
    summarize_boundary_gap_progress,
)
