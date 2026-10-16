from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Tuple

import matplotlib

matplotlib.use("Agg")
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))


def resolve_dataset_path(target_path: str, scenario_type: str) -> Tuple[str, str]:
    target_path = os.path.expanduser(target_path)
    if target_path.endswith(".csv"):
        if not os.path.exists(target_path):
            raise FileNotFoundError(f"CSV not found: {target_path}")
        return target_path, os.path.dirname(target_path)

    csv_file_fixed = os.path.join(target_path, f"{scenario_type}_dataset_fixed.csv")
    csv_file_normal = os.path.join(target_path, f"{scenario_type}_dataset.csv")

    if os.path.exists(csv_file_fixed):
        return csv_file_fixed, target_path
    if os.path.exists(csv_file_normal):
        return csv_file_normal, target_path
    raise FileNotFoundError(
        f"Dataset not found: {csv_file_normal} (or _fixed.csv)"
    )


def load_dataset(csv_file: str) -> pd.DataFrame:
    return pd.read_csv(csv_file, engine="python", on_bad_lines="skip")


def resolve_output_path(target_dir: str, output: str) -> str:
    output = os.path.expanduser(output)
    if os.path.isabs(output):
        return output
    return os.path.join(target_dir, output)


def parse_slice_filters(slice_values: list[str] | None) -> dict[str, float]:
    filters: dict[str, float] = {}
    for raw in slice_values or []:
        if "=" not in raw:
            raise ValueError(f"Slice must be given as key=value: {raw}")
        key, raw_value = raw.split("=", 1)
        filters[key.strip()] = float(raw_value)
    return filters


def apply_slice_filters(
    df: pd.DataFrame,
    slice_filters: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, dict[str, float]]:
    if df is None or df.empty or not slice_filters:
        return df, {}

    filtered_df = df.copy()
    applied_filters: dict[str, float] = {}
    for column, target in slice_filters.items():
        if column not in filtered_df.columns:
            raise ValueError(f"Slice column not found in dataset: {column}")

        numeric_values = pd.to_numeric(filtered_df[column], errors="coerce")
        valid_values = numeric_values.dropna()
        if valid_values.empty:
            raise ValueError(f"Slice column has no numeric values: {column}")

        nearest_index = (valid_values - target).abs().argmin()
        nearest = float(valid_values.loc[nearest_index])
        mask = np.isclose(numeric_values, nearest, atol=1e-6, rtol=0.0)
        filtered_df = filtered_df.loc[mask].copy()
        applied_filters[column] = nearest
        if filtered_df.empty:
            break

    return filtered_df, applied_filters


def format_slice_suffix(applied_filters: dict[str, float]) -> str:
    if not applied_filters:
        return ""
    formatted = ", ".join(f"{key}={value:g}" for key, value in applied_filters.items())
    return f" | slice: {formatted}"
