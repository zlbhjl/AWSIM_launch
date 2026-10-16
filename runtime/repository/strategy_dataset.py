from __future__ import annotations

from pathlib import Path

import pandas as pd

from runtime.repository.dataset_restore import DatasetPaths


class StrategyDatasetRepository:
    def __init__(
        self,
        scenario_name: str,
        *,
        traces_dir: str | Path = "~/simulation_traces",
        dataset_csv: str | Path | None = None,
        base_csv: str | Path | None = None,
    ):
        self.paths = DatasetPaths(
            traces_dir=Path(traces_dir).expanduser(),
            scenario_name=scenario_name,
        )
        self.dataset_csv = (
            Path(dataset_csv).expanduser() if dataset_csv is not None else self.paths.dataset_csv
        )
        self.base_csv = Path(base_csv).expanduser() if base_csv is not None else self.paths.base_csv

    def load_dataset(self) -> pd.DataFrame | None:
        frames: list[pd.DataFrame] = []
        for csv_path in (self.base_csv, self.dataset_csv):
            if not csv_path.exists():
                continue
            try:
                frames.append(
                    pd.read_csv(
                        csv_path,
                        engine="python",
                        on_bad_lines="skip",
                    )
                )
            except Exception as exc:
                print(f"[StrategyDatasetRepository] failed to read {csv_path}: {exc}")

        if not frames:
            return None
        if len(frames) == 1:
            return frames[0]
        return pd.concat(frames, ignore_index=True)
