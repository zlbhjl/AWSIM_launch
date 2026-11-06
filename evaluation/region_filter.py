"""Filter result rows by a region condition and rectangular bounds (docs/point_extractors_split_plan.md §3.1).

A region is either ``"custom"`` (no condition) or a pandas query expression,
which may use names a target declares (its :class:`RegionVocabulary`, for
example AWSIM's ``emp_safe`` = ``(c_collision == 0)``). The shared code knows
no target's names: a target's statistical profile gives its vocabulary.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

import pandas as pd

# Columns compared as strings in conditions, for every target.
COMMON_STRING_COLUMNS = frozenset({"reason", "worker_id", "filename"})


@dataclass(frozen=True)
class RegionVocabulary:
    """The region names a target declares.

    ``macros``: name -> condition. ``required_columns``: a token of the
    condition -> the columns it needs; rows lacking them give an empty result.
    ``string_columns``: further columns compared as strings (others are made numeric).
    """

    macros: Mapping[str, str] = field(default_factory=dict)
    required_columns: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    string_columns: frozenset[str] = frozenset()

    def expand(self, region: str) -> str:
        query = region
        for name, condition in self.macros.items():
            query = re.sub(rf"\b{name}\b", condition, query)
        return query

    def required(self, expression: str) -> list[str]:
        needed: set[str] = set()
        for token, columns in self.required_columns.items():
            if token in expression:
                needed.update(columns)
        return sorted(needed)

    def all_string_columns(self) -> frozenset[str]:
        return COMMON_STRING_COLUMNS | frozenset(self.string_columns)


GENERIC_VOCABULARY = RegionVocabulary()
_NAME = re.compile(r"[A-Za-z_]\w*")


def check_region_name(region: str | None, vocabulary: RegionVocabulary | None) -> None:
    """Refuse a bare region name the target does not declare (before anything runs)."""
    if not region or region == "custom" or not _NAME.fullmatch(region):
        return
    known = (vocabulary or GENERIC_VOCABULARY).macros
    if region not in known:
        names = ", ".join(sorted(known)) or "none"
        raise ValueError(f"unknown region {region!r}; the target declares: {names} (or write a condition)")


def filter_rows(
    df: pd.DataFrame | None,
    region: str | None = "custom",
    bounds: Mapping[str, tuple[float, float]] | None = None,
    *,
    vocabulary: RegionVocabulary | None = None,
) -> pd.DataFrame | None:
    """Rows satisfying the region condition and within the bounds."""
    if df is None or df.empty:
        return df
    vocabulary = vocabulary or GENERIC_VOCABULARY
    filtered_df = df.copy()

    if region and region != "custom":
        query_str = vocabulary.expand(region)
        missing_cols = [col for col in vocabulary.required(query_str) if col not in filtered_df.columns]
        if missing_cols:
            return filtered_df.iloc[0:0].copy()
        string_columns = vocabulary.all_string_columns()
        try:
            # Metric and parameter columns are made numeric (invalid values become NaN);
            # string columns are kept.
            for col in filtered_df.columns:
                if col not in string_columns:
                    filtered_df.loc[:, col] = pd.to_numeric(filtered_df[col], errors="coerce")
            filtered_df = filtered_df.query(query_str).copy()
        except Exception as e:
            raise ValueError(f"条件式の評価に失敗しました: {e}")

    if bounds:
        for col, (min_val, max_val) in bounds.items():
            if col in filtered_df.columns:
                filtered_df.loc[:, col] = pd.to_numeric(filtered_df[col], errors="coerce")
                filtered_df = filtered_df[(filtered_df[col] >= min_val) & (filtered_df[col] <= max_val)]

    return filtered_df


def save_dataframe_to_csv(df: pd.DataFrame | None, output_path: str, success_msg: str | None = None) -> bool:
    """Write a non-empty frame to CSV, creating its directory."""
    if df is not None and not df.empty:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        df.to_csv(output_path, index=False)
        if success_msg:
            print(success_msg)
        return True
    return False


__all__ = [
    "COMMON_STRING_COLUMNS",
    "GENERIC_VOCABULARY",
    "RegionVocabulary",
    "check_region_name",
    "filter_rows",
    "save_dataframe_to_csv",
]
