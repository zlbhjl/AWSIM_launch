from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class AWSIMDatasetAdapter:
    sample_id_column: str = "sample_id"
    loop_num_column: str = "loop_num"

    def load_dataframe(self, csv_path: str | Path) -> pd.DataFrame:
        path = Path(csv_path).expanduser().resolve()
        dataframe = pd.read_csv(path, engine="python", on_bad_lines="skip")
        return self.normalize_dataframe(dataframe)

    def normalize_dataframe(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        normalized = dataframe.copy()
        if self.sample_id_column not in normalized.columns:
            normalized[self.sample_id_column] = self._build_sample_ids(normalized)
        normalized[self.sample_id_column] = normalized[self.sample_id_column].astype(str)
        return normalized

    def universal_dataset(self, source: pd.DataFrame | str | Path) -> set[str]:
        normalized = self._normalize_source(source)
        return set(normalized[self.sample_id_column].astype(str))

    def subset_ids(self, rows: pd.DataFrame) -> set[str]:
        normalized = self.normalize_dataframe(rows)
        return set(normalized[self.sample_id_column].astype(str))

    def _normalize_source(self, source: pd.DataFrame | str | Path) -> pd.DataFrame:
        if isinstance(source, pd.DataFrame):
            return self.normalize_dataframe(source)
        return self.load_dataframe(source)

    def _build_sample_ids(self, dataframe: pd.DataFrame) -> list[str]:
        if self.loop_num_column in dataframe.columns:
            sample_ids: list[str] = []
            for value in dataframe[self.loop_num_column]:
                try:
                    sample_ids.append(f"loop_{int(value)}")
                except (TypeError, ValueError):
                    sample_ids.append(f"loop_{value}")
            return sample_ids

        return [f"sample_{index}" for index in range(len(dataframe))]
