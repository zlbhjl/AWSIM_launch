from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from contracts.execution import TestCase


@dataclass(frozen=True)
class ReplayCsvStrategyConfig:
    source_csv: str | Path
    input_types: Mapping[str, object]
    case_kind: str = "uturn"
    scenario_type: str = "uturn"
    target: str = "awsim"
    reason_pattern: str = "BINOMIAL_CI:"
    expected_count: int | None = None
    completed_source_loop_nums: frozenset[int] = field(default_factory=frozenset)


class ReplayCsvStrategy:
    """Replay statistically eligible rows from an earlier dataset."""

    def __init__(self, config: ReplayCsvStrategyConfig):
        self.config = config
        self.source_csv = Path(config.source_csv).expanduser().resolve()
        self._cases = self._load_cases()
        self.eligible_count = len(self._cases)
        if config.expected_count is not None and self.eligible_count != config.expected_count:
            raise ValueError(
                "replay CSV eligible row count mismatch: "
                f"expected {config.expected_count}, found {self.eligible_count}"
            )
        self._pending_cases = [
            case
            for case in self._cases
            if int(case.meta["replay_source_loop_num"])
            not in config.completed_source_loop_nums
        ]
        self.skipped_completed_count = self.eligible_count - len(self._pending_cases)
        self._index = 0

    def next_test_case(self) -> TestCase | None:
        if self._index >= len(self._pending_cases):
            return None
        test_case = self._pending_cases[self._index]
        self._index += 1
        return test_case

    def _load_cases(self) -> list[TestCase]:
        if not self.source_csv.is_file():
            raise FileNotFoundError(f"replay CSV does not exist: {self.source_csv}")

        with self.source_csv.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            self._validate_header(reader.fieldnames)
            cases: list[TestCase] = []
            seen_loop_nums: set[int] = set()
            for row_number, row in enumerate(reader, start=2):
                if not self._is_eligible(row):
                    continue
                source_loop_num = self._parse_source_loop_num(row, row_number)
                if source_loop_num in seen_loop_nums:
                    raise ValueError(
                        f"replay CSV contains duplicate loop_num {source_loop_num}"
                    )
                seen_loop_nums.add(source_loop_num)
                cases.append(self._build_case(row, row_number, source_loop_num))
        return cases

    def _validate_header(self, fieldnames: list[str] | None) -> None:
        if fieldnames is None:
            raise ValueError("replay CSV is empty")
        required = {
            "loop_num",
            "status",
            "reason",
            "c_collision",
            *self.config.input_types.keys(),
        }
        missing = sorted(required.difference(fieldnames))
        if missing:
            raise ValueError(
                "replay CSV is missing required columns: " + ", ".join(missing)
            )

    def _is_eligible(self, row: Mapping[str, str]) -> bool:
        if str(row.get("status", "")).strip().lower() != "success":
            return False
        if self.config.reason_pattern not in str(row.get("reason", "")):
            return False
        try:
            return float(str(row.get("c_collision", "")).strip()) in {0.0, 1.0}
        except ValueError:
            return False

    @staticmethod
    def _parse_source_loop_num(row: Mapping[str, str], row_number: int) -> int:
        raw_value = str(row.get("loop_num", "")).strip()
        try:
            loop_num = int(raw_value)
        except ValueError as exc:
            raise ValueError(
                f"replay CSV row {row_number} has invalid loop_num: {raw_value!r}"
            ) from exc
        if loop_num <= 0:
            raise ValueError(
                f"replay CSV row {row_number} has non-positive loop_num: {loop_num}"
            )
        return loop_num

    def _build_case(
        self,
        row: Mapping[str, str],
        row_number: int,
        source_loop_num: int,
    ) -> TestCase:
        input_payload = {
            name: self._coerce_value(
                str(row.get(name, "")),
                reference_value,
                column=name,
                row_number=row_number,
            )
            for name, reference_value in self.config.input_types.items()
        }
        input_payload["scenario_type"] = self.config.scenario_type
        source_collision = int(float(str(row["c_collision"]).strip()))
        source_case_id = str(row.get("case_id", "")).strip() or None
        return TestCase(
            case_id=f"{self.config.case_kind}_replay_source_{source_loop_num}",
            target=self.config.target,
            case_kind=self.config.case_kind,
            input=input_payload,
            tags=["replay", "binomial_ci"],
            reason=f"REPLAY: BINOMIAL_CI source_loop={source_loop_num}",
            meta={
                "source_module": "orchestration.replay",
                "run_mode": "replay",
                "replay_source_loop_num": source_loop_num,
                "replay_source_case_id": source_case_id,
                "replay_source_collision": source_collision,
                "replay_source_reason": str(row.get("reason", "")),
                "replay_source_csv": str(self.source_csv),
            },
        )

    @staticmethod
    def _coerce_value(
        raw_value: str,
        reference_value: object,
        *,
        column: str,
        row_number: int,
    ) -> object:
        value = raw_value.strip()
        if not value:
            raise ValueError(
                f"replay CSV row {row_number} has an empty input value for {column}"
            )
        try:
            if isinstance(reference_value, bool):
                normalized = value.lower()
                if normalized not in {"true", "false", "1", "0"}:
                    raise ValueError
                return normalized in {"true", "1"}
            if isinstance(reference_value, int):
                numeric_value = float(value)
                if not numeric_value.is_integer():
                    raise ValueError
                return int(numeric_value)
            if isinstance(reference_value, float):
                return float(value)
            return value
        except ValueError as exc:
            raise ValueError(
                f"replay CSV row {row_number} has invalid {column}: {raw_value!r}"
            ) from exc


__all__ = ["ReplayCsvStrategy", "ReplayCsvStrategyConfig"]
