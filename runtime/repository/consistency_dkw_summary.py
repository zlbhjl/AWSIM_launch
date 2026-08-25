from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from contracts.statistics import StatisticalReport
from runtime.repository.dataset_csv import DatasetCsvRepository


CONSISTENCY_DKW_SUMMARY_FIELDS = (
    "classification",
    "metric",
    "method",
    "sample_count",
    "confidence_level",
    "estimate",
    "lower_bound",
    "upper_bound",
    "interval_width",
    "target_epsilon",
    "sufficient",
    "next_action",
    "status",
    "message",
)


class ConsistencyDkwSummaryRepository:
    def __init__(
        self,
        *,
        scenario_name: str,
        traces_dir: str | Path = "~/simulation_traces",
        summary_csv: str | Path | None = None,
    ):
        self.scenario_name = scenario_name
        self.traces_dir = Path(traces_dir).expanduser()
        self.summary_path = (
            Path(summary_csv).expanduser()
            if summary_csv is not None
            else self.traces_dir / f"{scenario_name}_consistency_dkw_summary.csv"
        )
        self._repository = DatasetCsvRepository(self.summary_path)

    def append_report(
        self,
        *,
        classification: str,
        report: StatisticalReport,
        confidence_level: float,
        target_epsilon: float | None,
    ) -> None:
        interval = report.interval
        diagnostics = dict(report.diagnostics)
        record = {
            "classification": str(classification),
            "metric": report.metric,
            "method": report.method,
            "sample_count": report.sample_count,
            "confidence_level": confidence_level,
            "estimate": report.estimate,
            "lower_bound": None if interval is None else interval[0],
            "upper_bound": None if interval is None else interval[1],
            "interval_width": report.interval_width,
            "target_epsilon": target_epsilon,
            "sufficient": report.sufficient,
            "next_action": report.next_action,
            "status": diagnostics.get("status"),
            "message": diagnostics.get("message"),
        }
        self._repository.append_row(self._ordered_row(record, CONSISTENCY_DKW_SUMMARY_FIELDS))

    def read_rows(self) -> list[dict[str, str]]:
        return self._repository.read_rows()

    def read_latest_rows_by_classification(self) -> dict[str, dict[str, str]]:
        latest: dict[str, dict[str, str]] = {}
        for row in self.read_rows():
            classification = row.get("classification")
            if not classification:
                continue
            latest[str(classification)] = dict(row)
        return latest

    @staticmethod
    def _ordered_row(
        row: dict[str, object],
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
    "CONSISTENCY_DKW_SUMMARY_FIELDS",
    "ConsistencyDkwSummaryRepository",
]
