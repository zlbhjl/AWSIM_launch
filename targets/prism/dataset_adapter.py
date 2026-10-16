from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from contracts.evaluation import EvaluationRecord
from evaluation.dkw import records_to_data_frame


class PrismDatasetAdapter:
    """Normalize PRISM execution records without mixing exact and sample rows."""

    def load_dataframe(self, csv_path: str | Path) -> pd.DataFrame:
        path = Path(csv_path).expanduser().resolve()
        return self.normalize_dataframe(
            pd.read_csv(path, engine="python", on_bad_lines="skip")
        )

    def records_to_dataframe(
        self,
        records: Sequence[EvaluationRecord],
    ) -> pd.DataFrame:
        dataframe = records_to_data_frame(records)
        if dataframe is None:
            return pd.DataFrame()
        return self.normalize_dataframe(dataframe)

    def normalize_dataframe(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        normalized = dataframe.copy()
        if "record_kind" not in normalized.columns:
            normalized["record_kind"] = "sample"
        normalized["record_kind"] = (
            normalized["record_kind"].fillna("sample").astype(str)
        )
        return normalized

    def sample_rows(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        normalized = self.normalize_dataframe(dataframe)
        return normalized[normalized["record_kind"].eq("sample")].copy()

    def exact_model_check_rows(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        normalized = self.normalize_dataframe(dataframe)
        return normalized[
            normalized["record_kind"].eq("exact_model_check")
        ].copy()

    @staticmethod
    def sample_records(
        records: Sequence[EvaluationRecord],
    ) -> list[EvaluationRecord]:
        return [
            record
            for record in records
            if str(record.input.get("record_kind", "sample")) == "sample"
        ]


__all__ = ["PrismDatasetAdapter"]
