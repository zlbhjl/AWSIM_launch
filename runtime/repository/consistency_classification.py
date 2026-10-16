from __future__ import annotations

from pathlib import Path

import pandas as pd

import point_extractors


class ConsistencyClassificationRepository:
    def __init__(
        self,
        *,
        scenario_name: str,
        traces_dir: str | Path = "~/simulation_traces",
        consistent_csv: str | Path | None = None,
        stochastic_csv: str | Path | None = None,
    ):
        self.scenario_name = scenario_name
        self.traces_dir = Path(traces_dir).expanduser()
        self.consistent_path = (
            Path(consistent_csv).expanduser()
            if consistent_csv is not None
            else self.traces_dir / f"{scenario_name}_consistent_risk.csv"
        )
        self.stochastic_path = (
            Path(stochastic_csv).expanduser()
            if stochastic_csv is not None
            else self.traces_dir / f"{scenario_name}_stochastic_risk.csv"
        )

    def save_consistent(self, df: pd.DataFrame | None) -> bool:
        return point_extractors.save_dataframe_to_csv(df, str(self.consistent_path))

    def save_stochastic(self, df: pd.DataFrame | None) -> bool:
        return point_extractors.save_dataframe_to_csv(df, str(self.stochastic_path))


__all__ = ["ConsistencyClassificationRepository"]
