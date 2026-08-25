from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from runtime.repository.dataset_csv import DatasetCsvRepository


DKW_HISTORY_FIELDS = (
    "stage",
    "task_count",
    "metric",
    "ess",
    "estimate",
    "lower_bound",
    "upper_bound",
    "interval_width",
    "target_epsilon",
)

BINOMIAL_CI_HISTORY_FIELDS = (
    "task_count",
    "metric",
    "method",
    "confidence_level",
    "sample_size",
    "success_count",
    "estimate",
    "lower_bound",
    "upper_bound",
    "interval_width",
    "target_width",
)


class StatisticalHistoryRepository:
    def __init__(
        self,
        *,
        scenario_name: str,
        traces_dir: str | Path = "~/simulation_traces",
        dkw_history_csv: str | Path | None = None,
        binomial_ci_history_csv: str | Path | None = None,
    ):
        self.scenario_name = scenario_name
        self.traces_dir = Path(traces_dir).expanduser()
        self.dkw_history_path = (
            Path(dkw_history_csv).expanduser()
            if dkw_history_csv is not None
            else self.traces_dir / f"{scenario_name}_dkw_history.csv"
        )
        self.binomial_ci_history_path = (
            Path(binomial_ci_history_csv).expanduser()
            if binomial_ci_history_csv is not None
            else self.traces_dir / f"{scenario_name}_binomial_ci_history.csv"
        )
        self._dkw_repository = DatasetCsvRepository(self.dkw_history_path)
        self._binomial_repository = DatasetCsvRepository(self.binomial_ci_history_path)

    def append_dkw_record(self, record: Mapping[str, object]) -> None:
        self._dkw_repository.append_row(self._ordered_row(record, DKW_HISTORY_FIELDS))

    def append_dkw_records(self, records: Sequence[Mapping[str, object]]) -> None:
        for record in records:
            self.append_dkw_record(record)

    def append_binomial_ci_record(self, record: Mapping[str, object]) -> None:
        self._binomial_repository.append_row(
            self._ordered_row(record, BINOMIAL_CI_HISTORY_FIELDS)
        )

    def read_dkw_rows(self) -> list[dict[str, str]]:
        return self._dkw_repository.read_rows()

    def read_binomial_ci_rows(self) -> list[dict[str, str]]:
        return self._binomial_repository.read_rows()

    @staticmethod
    def _ordered_row(
        row: Mapping[str, object],
        ordered_fields: Sequence[str],
    ) -> dict[str, object]:
        normalized = dict(row)
        ordered: dict[str, object] = {}
        for field in ordered_fields:
            if field in normalized:
                ordered[field] = normalized.pop(field)
        for field in sorted(normalized):
            ordered[field] = normalized[field]
        return ordered


__all__ = [
    "BINOMIAL_CI_HISTORY_FIELDS",
    "DKW_HISTORY_FIELDS",
    "StatisticalHistoryRepository",
]
