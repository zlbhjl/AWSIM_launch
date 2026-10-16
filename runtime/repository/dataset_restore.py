from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DatasetPaths:
    traces_dir: Path
    scenario_name: str

    @property
    def base_csv(self) -> Path:
        return self.traces_dir / f"{self.scenario_name}_dataset_base.csv"

    @property
    def dataset_csv(self) -> Path:
        return self.traces_dir / f"{self.scenario_name}_dataset.csv"


def restore_base_dataset(
    resume_from: str | Path | None,
    scenario_name: str,
    traces_dir: str | Path,
) -> Path | None:
    if not resume_from:
        return None

    traces_path = Path(traces_dir).expanduser()
    source_dir = Path(resume_from).expanduser()
    source_csv = source_dir / f"{scenario_name}_dataset.csv"
    if not source_csv.exists():
        raise FileNotFoundError(f"Dataset not found: {source_csv}")

    paths = DatasetPaths(traces_dir=traces_path, scenario_name=scenario_name)
    paths.base_csv.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_csv, paths.base_csv)
    return paths.base_csv
