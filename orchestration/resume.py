from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from runtime.repository.dataset_csv import DatasetCsvRepository
from runtime.repository.dataset_restore import DatasetPaths


@dataclass(frozen=True)
class ResumeConfig:
    scenario_name: str
    traces_dir: str
    resume_from: str | None = None
    count_current_only: bool = False
    current_dataset_csv: str | None = None
    base_dataset_csv: str | None = None
    run_mode: str = "explore"


@dataclass(frozen=True)
class ResumeState:
    paths: DatasetPaths
    restored_base_csv: Path | None
    current_dataset_csv: Path
    base_dataset_csv: Path
    last_loop_num: int
    next_loop_num: int


class ResumeService:
    def prepare(self, config: ResumeConfig) -> ResumeState:
        paths = DatasetPaths(
            traces_dir=Path(config.traces_dir).expanduser(),
            scenario_name=config.scenario_name,
        )
        current_dataset_csv = (
            Path(config.current_dataset_csv).expanduser()
            if config.current_dataset_csv
            else paths.dataset_csv
        )
        base_dataset_csv = (
            Path(config.base_dataset_csv).expanduser()
            if config.base_dataset_csv
            else paths.base_csv
        )
        restored_base_csv = self._restore_base_dataset(
            config.resume_from,
            config.scenario_name,
            base_dataset_csv,
        )
        last_loop_num = self._resolve_last_loop_num(
            current_dataset_csv,
            base_dataset_csv,
            count_current_only=(
                config.count_current_only or config.run_mode == "dkw_fixed"
            ),
        )
        return ResumeState(
            paths=paths,
            restored_base_csv=restored_base_csv,
            current_dataset_csv=current_dataset_csv,
            base_dataset_csv=base_dataset_csv,
            last_loop_num=last_loop_num,
            next_loop_num=last_loop_num + 1,
        )

    def _restore_base_dataset(
        self,
        resume_from: str | None,
        scenario_name: str,
        destination_base_csv: Path,
    ) -> Path | None:
        if not resume_from:
            return None

        source_dir = Path(resume_from).expanduser()
        source_csv = source_dir / f"{scenario_name}_dataset.csv"
        if not source_csv.exists():
            raise FileNotFoundError(f"Dataset not found: {source_csv}")

        destination_base_csv.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_csv, destination_base_csv)
        return destination_base_csv

    def _resolve_last_loop_num(
        self,
        current_dataset_csv: Path,
        base_dataset_csv: Path,
        *,
        count_current_only: bool,
    ) -> int:
        dataset_repository = DatasetCsvRepository(current_dataset_csv)
        current_last_loop = dataset_repository.max_loop_num()
        if count_current_only:
            return current_last_loop

        base_repository = DatasetCsvRepository(base_dataset_csv)
        base_last_loop = base_repository.max_loop_num()
        return max(base_last_loop, current_last_loop)
