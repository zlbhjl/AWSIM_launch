from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from runtime.repository.dataset_csv import DatasetCsvRepository


BOUNDARY_GAP_PROGRESS_FIELDS = (
    "cycle",
    "snapshot_kind",
    "point_index",
    "point_count",
    "status",
    "sample_count",
    "collision_ratio",
    "near_ratio",
    "is_candidate",
    "total_cells",
    "candidate_cells",
    "needs_more_data_cells",
    "densified_cells",
    "clarified_cells",
)


class BoundaryGapProgressRepository:
    def __init__(
        self,
        *,
        scenario_name: str,
        traces_dir: str | Path = "~/simulation_traces",
        progress_csv: str | Path | None = None,
    ):
        self.scenario_name = scenario_name
        self.traces_dir = Path(traces_dir).expanduser()
        self.progress_path = (
            Path(progress_csv).expanduser()
            if progress_csv is not None
            else self.traces_dir / f"{scenario_name}_boundary_gap_progress.csv"
        )
        self._repository = DatasetCsvRepository(self.progress_path)

    def append_snapshot(
        self,
        *,
        cycle: int,
        summary: Mapping[str, object],
        snapshot_kind: str = "cycle",
    ) -> None:
        target_rows = summary.get("target_cells")
        if isinstance(target_rows, Sequence) and not isinstance(target_rows, (str, bytes)):
            normalized_rows = [row for row in target_rows if isinstance(row, Mapping)]
        else:
            normalized_rows = []

        point_count = len(normalized_rows)
        base_row = {
            "cycle": int(cycle),
            "snapshot_kind": str(snapshot_kind),
            "point_count": point_count,
            "total_cells": summary.get("total_cells"),
            "candidate_cells": summary.get("candidate_cells"),
            "needs_more_data_cells": summary.get("needs_more_data_cells"),
            "densified_cells": summary.get("densified_cells"),
            "clarified_cells": summary.get("clarified_cells"),
        }

        if not normalized_rows:
            self._repository.append_row(self._ordered_row(base_row, BOUNDARY_GAP_PROGRESS_FIELDS))
            return

        for point_index, target_row in enumerate(normalized_rows, start=1):
            row = dict(base_row)
            row["point_index"] = point_index
            row.update(dict(target_row))
            self._repository.append_row(self._ordered_row(row, BOUNDARY_GAP_PROGRESS_FIELDS))

    def read_rows(self) -> list[dict[str, str]]:
        return self._repository.read_rows()

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
    "BOUNDARY_GAP_PROGRESS_FIELDS",
    "BoundaryGapProgressRepository",
]
