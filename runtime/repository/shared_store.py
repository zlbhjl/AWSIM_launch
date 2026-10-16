from __future__ import annotations

from datetime import datetime
from numbers import Real
from typing import Mapping

from runtime.repository.dataset_csv import DatasetCsvRepository
from runtime.repository.parameter_buffer import ParameterBuffer


class SharedStore:
    def __init__(
        self,
        dataset_repository: DatasetCsvRepository,
        parameter_buffer: ParameterBuffer | None = None,
    ):
        self.dataset_repository = dataset_repository
        self.parameter_buffer = parameter_buffer or ParameterBuffer()

    def buffer_parameters(
        self,
        loop_num: int,
        input_row: dict[str, object],
        reason: str = "",
        timestamp: datetime | None = None,
    ) -> None:
        self.parameter_buffer.cleanup_stale(now=timestamp)
        self.parameter_buffer.put(loop_num, input_row, reason=reason, timestamp=timestamp)

    def merge_result(self, result_row: Mapping[str, object]) -> dict[str, object] | None:
        loop_num = result_row.get("loop_num")
        if loop_num is None:
            raise ValueError("result_row must contain loop_num")

        entry = self.parameter_buffer.pop(int(loop_num))
        if entry is None:
            return None

        merged = dict(entry.params)
        merged.update(dict(result_row))
        merged["reason"] = entry.reason
        self.dataset_repository.append_row(self._normalize_row_for_dataset(merged))
        return merged

    def flush_timeout(
        self,
        loop_num: int,
        timeout_row: Mapping[str, object],
        input_row: Mapping[str, object] | None = None,
        reason: str = "",
        timestamp: datetime | None = None,
    ) -> dict[str, object]:
        entry = self.parameter_buffer.pop(loop_num)
        if entry is None:
            params = dict(input_row or {})
            merged_reason = reason
        else:
            params = dict(entry.params)
            merged_reason = entry.reason

        if input_row:
            params.update(dict(input_row))

        merged = dict(params)
        merged.update(dict(timeout_row))
        merged["loop_num"] = loop_num
        merged["reason"] = merged_reason
        self.dataset_repository.append_row(self._normalize_row_for_dataset(merged))
        return merged

    def _normalize_row_for_dataset(
        self,
        row: Mapping[str, object],
    ) -> dict[str, object]:
        normalized: dict[str, object] = {}
        for key, value in row.items():
            normalized[key] = self._normalize_value(value)
        return normalized

    def _normalize_value(self, value: object) -> object:
        if isinstance(value, bool):
            return value
        if isinstance(value, Real) and not isinstance(value, int):
            return f"{float(value):.4f}"
        return value
