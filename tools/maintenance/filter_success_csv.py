#!/usr/bin/env python3
"""Write only successful evaluation rows to a separate CSV file."""
from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class FilterSummary:
    input_csv: str
    output_csv: str
    total_rows: int
    success_rows: int
    excluded_rows: int


def filter_success_csv(
    input_path: Path,
    output_path: Path,
    *,
    overwrite: bool = False,
) -> FilterSummary:
    source = input_path.expanduser().resolve()
    destination = output_path.expanduser().resolve()
    if source == destination:
        raise ValueError("output CSV must be different from the input CSV")
    if not source.is_file():
        raise FileNotFoundError(f"input CSV does not exist: {source}")
    if destination.exists() and not overwrite:
        raise FileExistsError(
            f"output CSV already exists: {destination}; use --overwrite to replace it"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    total_rows = 0
    success_rows = 0
    temporary_path: Path | None = None
    try:
        with source.open("r", encoding="utf-8", newline="") as input_file:
            reader = csv.reader(input_file)
            try:
                header = next(reader)
            except StopIteration as exc:
                raise ValueError("input CSV is empty") from exc
            if "status" not in header:
                raise ValueError("input CSV is missing required column: status")
            status_index = header.index("status")

            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as output_file:
                temporary_path = Path(output_file.name)
                writer = csv.writer(output_file)
                writer.writerow(header)
                for row_number, row in enumerate(reader, start=2):
                    total_rows += 1
                    if len(row) <= status_index:
                        raise ValueError(
                            f"input CSV row {row_number} does not contain the status column"
                        )
                    if row[status_index].strip().lower() != "success":
                        continue
                    writer.writerow(row)
                    success_rows += 1
                output_file.flush()
                os.fsync(output_file.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return FilterSummary(
        input_csv=str(source),
        output_csv=str(destination),
        total_rows=total_rows,
        success_rows=success_rows,
        excluded_rows=total_rows - success_rows,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a CSV containing only rows whose status is success.",
    )
    parser.add_argument("--dataset-csv", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        help="Output path. Defaults to <input-stem>_success.csv beside the input.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing output CSV. The input CSV is never overwritten.",
    )
    return parser.parse_args(argv)


def _default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_success{input_path.suffix}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output_path = args.output or _default_output_path(args.dataset_csv)
    summary = filter_success_csv(
        args.dataset_csv,
        output_path,
        overwrite=bool(args.overwrite),
    )
    print(json.dumps(asdict(summary), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
