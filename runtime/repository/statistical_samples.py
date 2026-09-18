from __future__ import annotations

from pathlib import Path

import pandas as pd


class StatisticalSamplesRepository:
    def __init__(
        self,
        *,
        scenario_name: str,
        traces_dir: str | Path = "~/simulation_traces",
        dkw_samples_csv: str | Path | None = None,
        binomial_ci_samples_csv: str | Path | None = None,
        sprt_samples_csv: str | Path | None = None,
        ebstop_samples_csv: str | Path | None = None,
    ) -> None:
        self.scenario_name = scenario_name
        self.traces_dir = Path(traces_dir).expanduser()
        self.dkw_samples_path = (
            Path(dkw_samples_csv).expanduser()
            if dkw_samples_csv is not None
            else self.traces_dir / f"{scenario_name}_dkw_samples.csv"
        )
        self.binomial_ci_samples_path = (
            Path(binomial_ci_samples_csv).expanduser()
            if binomial_ci_samples_csv is not None
            else self.traces_dir / f"{scenario_name}_binomial_ci_samples.csv"
        )
        self.sprt_samples_path = (
            Path(sprt_samples_csv).expanduser()
            if sprt_samples_csv is not None
            else self.traces_dir / f"{scenario_name}_sprt_samples.csv"
        )
        self.ebstop_samples_path = (
            Path(ebstop_samples_csv).expanduser()
            if ebstop_samples_csv is not None
            else self.traces_dir / f"{scenario_name}_ebstop_samples.csv"
        )

    def save_dkw_samples(self, df: pd.DataFrame | None) -> bool:
        return self._save_dataframe(df, self.dkw_samples_path)

    def save_binomial_ci_samples(self, df: pd.DataFrame | None) -> bool:
        return self._save_dataframe(df, self.binomial_ci_samples_path)

    def save_sprt_samples(self, df: pd.DataFrame | None) -> bool:
        return self._save_dataframe(df, self.sprt_samples_path)

    def save_ebstop_samples(self, df: pd.DataFrame | None) -> bool:
        return self._save_dataframe(df, self.ebstop_samples_path)

    @staticmethod
    def _save_dataframe(df: pd.DataFrame | None, path: Path) -> bool:
        if df is None:
            return False
        if not isinstance(df, pd.DataFrame) or df.empty:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
        return True


__all__ = [
    "StatisticalSamplesRepository",
]
