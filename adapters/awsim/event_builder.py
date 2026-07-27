from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from .dataset_adapter import AWSIMDatasetAdapter


Condition = str | Callable[[pd.DataFrame], pd.Series] | None


def _resolve_condition(df: pd.DataFrame, condition: Condition) -> pd.Series:
    if condition is None:
        return pd.Series(True, index=df.index, dtype=bool)

    if callable(condition):
        result = condition(df)
    elif isinstance(condition, str):
        result = df.eval(condition)
    else:
        raise TypeError(f"Unsupported condition type: {type(condition)!r}")

    if not isinstance(result, pd.Series):
        result = pd.Series(result, index=df.index)
    return result.fillna(False).astype(bool)


class AWSIMEventSetBuilder:
    def __init__(self, adapter: AWSIMDatasetAdapter | None = None):
        self.adapter = adapter or AWSIMDatasetAdapter()

    def build_event_inputs(
        self,
        df: pd.DataFrame,
        event_definitions: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        normalized = self.adapter.normalize_dataframe(df)
        universal_dataset = self.adapter.universal_dataset(normalized)
        event_inputs: dict[str, dict[str, Any]] = {}

        for event_id, event_def in event_definitions.items():
            dataset_mask = _resolve_condition(
                normalized,
                event_def.get("dataset_filter"),
            )
            error_mask = _resolve_condition(
                normalized,
                event_def.get("error_filter"),
            )
            dataset_rows = normalized[dataset_mask]
            error_rows = normalized[dataset_mask & error_mask]

            dataset_d = self.adapter.subset_ids(dataset_rows)
            dataset_e = self.adapter.subset_ids(error_rows)
            event_inputs[event_id] = {
                "dataset_d": dataset_d,
                "dataset_e": dataset_e,
                "total_count": len(dataset_d),
                "error_count": len(dataset_e),
                "correct_count": len(dataset_d) - len(dataset_e),
                "target_column": event_def.get("target_column"),
            }

        return {
            "dataframe": normalized,
            "universal_dataset": universal_dataset,
            "events": event_inputs,
        }

