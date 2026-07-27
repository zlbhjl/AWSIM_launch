from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


@dataclass
class AWSIMDatasetAdapter:
    sample_id_column: str = "sample_id"
    loop_num_column: str = "loop_num"

    def load_dataframe(self, csv_path: str) -> pd.DataFrame:
        df = pd.read_csv(csv_path, engine="python", on_bad_lines="skip")
        return self.normalize_dataframe(df)

    def normalize_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        normalized = df.copy()
        if self.sample_id_column not in normalized.columns:
            if self.loop_num_column in normalized.columns:
                normalized[self.sample_id_column] = (
                    normalized[self.loop_num_column].apply(lambda value: f"loop_{int(value)}")
                )
            else:
                normalized[self.sample_id_column] = [
                    f"sample_{index}" for index in range(len(normalized))
                ]
        normalized[self.sample_id_column] = normalized[self.sample_id_column].astype(str)
        return normalized

    def universal_dataset(self, df: pd.DataFrame) -> set[str]:
        normalized = self.normalize_dataframe(df)
        return set(normalized[self.sample_id_column].astype(str))

    def subset_ids(self, rows: pd.DataFrame) -> set[str]:
        return set(rows[self.sample_id_column].astype(str))

