from __future__ import annotations

import csv
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence


class DatasetCsvRepository:
    def __init__(self, csv_path: str | Path):
        self.path = Path(csv_path)

    def read_headers(self) -> list[str]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return []
        with self.path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            return next(reader, [])

    def read_rows(self) -> list[dict[str, str]]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return []
        with self.path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def append_row(self, row: Mapping[str, object]) -> None:
        serialized_row = {key: self._serialize_value(value) for key, value in row.items()}
        existing_headers = self.read_headers()
        existing_rows = self.read_rows() if existing_headers else []

        headers = self._merge_headers(existing_headers, list(serialized_row.keys()))
        self.path.parent.mkdir(parents=True, exist_ok=True)

        if not existing_headers:
            self._write_all_rows(headers, [serialized_row])
            return

        if headers != existing_headers:
            existing_rows.append(serialized_row)
            self._write_all_rows(headers, existing_rows)
            return

        with self.path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore", restval="")
            writer.writerow(serialized_row)

    def max_loop_num(self, loop_field: str = "loop_num") -> int:
        last_loop = 0
        for row in self.read_rows():
            try:
                loop_num = int(row.get(loop_field, 0))
            except (TypeError, ValueError):
                continue
            if loop_num > last_loop:
                last_loop = loop_num
        return last_loop

    def _write_all_rows(self, headers: Sequence[str], rows: Sequence[Mapping[str, object]]) -> None:
        with self.path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(headers), extrasaction="ignore", restval="")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    @staticmethod
    def _merge_headers(existing: Sequence[str], new_keys: Sequence[str]) -> list[str]:
        headers = list(existing)
        for key in new_keys:
            if key not in headers:
                headers.append(key)
        return headers

    @staticmethod
    def _serialize_value(value: object) -> str | object:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, Path):
            return str(value)
        return value
