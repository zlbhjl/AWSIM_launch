"""The outputs of the point rules stay identical while they move (docs/point_extractors_split_plan.md P0).

Regenerate only on purpose: UPDATE_POINT_RULES_GOLDEN=1 python3 -m pytest tests/unit/test_point_extractors_golden.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import point_extractors
from targets.awsim.case_kinds import uturn as uturn_config

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "point_extractors" / "golden.json"
PARAMS = ["dx0", "ego_speed", "npc_speed"]


def _rows() -> pd.DataFrame:
    rng = np.random.default_rng(20261102)
    rows = []
    for index in range(1, 401):
        dx0, ego, npc = rng.uniform(10, 17.5), rng.uniform(30, 35), rng.uniform(10, 17.5)
        margin = dx0 - 0.35 * ego + 0.2 * npc
        collision = int(margin + rng.uniform(-1.5, 1.5) < 3.0)
        rows.append({
            "loop_num": index, "dx0": round(dx0, 6), "ego_speed": round(ego, 6), "npc_speed": round(npc, 6),
            "c_collision": -1 if index % 97 == 0 else collision,
            "min_ttc": round(max(0.0, 0.3 * margin + rng.normal(0, 0.1)), 6),
            "min_distance": round(max(0.0, margin), 6),
            "z_margin": round(margin / 5.0, 6),
            "theory_margin_a_human": round(margin - 1.0, 6),
            "theory_zone_a": "zone_a" if margin > 2 else "zone_b",
            "reason": "EXPLORATION",
        })
    # Repeated points for the consistency classification.
    for repeat in range(3):
        for point in range(5):
            base = rows[point]
            rows.append({**base, "loop_num": 500 + 10 * point + repeat,
                         "min_ttc": round(base["min_ttc"] + (0.05 if point % 2 else 0.6) * repeat, 6),
                         "reason": "[CONSISTENCY] Exact Point"})
    return pd.DataFrame(rows)


def _json(value):
    if isinstance(value, pd.DataFrame):
        return {"columns": list(value.columns),
                "rows": json.loads(value.reset_index(drop=True).to_json(orient="records", double_precision=12))}
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if np.isnan(value) else round(float(value), 9)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def outputs() -> dict[str, object]:
    df = _rows()
    out: dict[str, object] = {}
    with contextlib.redirect_stdout(io.StringIO()):
        for name, extractor in point_extractors.EXTRACTORS.items():
            out[f"extract:{name}"] = extractor(df, PARAMS, uturn_config)
        for region in ("custom", "emp_safe", "jama_safe", "intersect_safe", "union_safe",
                       "(min_ttc < 1.0) and (theory_zone_a == 'zone_a')"):
            filtered = point_extractors.filter_by_region_and_bounds(df, region=region)
            out[f"region:{region}"] = filtered["loop_num"].tolist()
        out["bounds"] = point_extractors.filter_by_region_and_bounds(
            df, region="emp_safe", bounds={"dx0": (11.0, 13.0), "ego_speed": (31.0, 34.0)})["loop_num"].tolist()
        out["region:missing_column"] = len(point_extractors.filter_by_region_and_bounds(
            df.drop(columns="theory_margin_a_human"), region="jama_safe"))
        cells = point_extractors.collect_boundary_gap_cells(df, PARAMS, uturn_config)
        out["boundary_gap_cells"] = cells
        summary = point_extractors.summarize_boundary_gap_progress(
            df, PARAMS, uturn_config, target_points=out["extract:boundary_gap"][:3] + [{"dx0": 24.0, "ego_speed": 39.0, "npc_speed": 24.0}])
        out["boundary_gap_summary"] = summary
        consistent, stochastic = point_extractors.classify_consistency(
            df[df["reason"].str.startswith("[CONSISTENCY]")], PARAMS, target_metric="min_ttc", threshold=0.2)
        out["consistency"] = {"consistent": consistent["loop_num"].tolist(), "stochastic": stochastic["loop_num"].tolist()}
    return _json(out)


def test_point_rules_reproduce_their_recorded_outputs() -> None:
    actual = outputs()
    if os.environ.get("UPDATE_POINT_RULES_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(actual) == set(expected)
    for key in expected:
        assert actual[key] == expected[key], key
