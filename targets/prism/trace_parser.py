from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable


STATE_LABELS = {0: "normal", 1: "degraded", 2: "failure"}


def parse_simpath_csv(path: str | Path) -> list[dict[str, object]]:
    """Parse PRISM ``-simpath ... sep=comma`` output into a stable trace schema."""

    resolved = Path(path).expanduser().resolve()
    with resolved.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(_data_rows(handle)))

    if not rows:
        raise ValueError("PRISM simulator produced an empty trace")

    trace: list[dict[str, object]] = []
    previous_step = -1
    for row in rows:
        if "step" not in row or "state" not in row:
            raise ValueError("PRISM trace must contain step and state columns")
        try:
            step = int(str(row["step"]).strip())
            state = int(str(row["state"]).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid PRISM trace row: {row}") from exc
        if step <= previous_step:
            raise ValueError("PRISM trace steps must be strictly increasing")
        if state not in STATE_LABELS:
            raise ValueError(f"Unknown PRISM reliability state: {state}")
        previous_step = step
        event: dict[str, object] = {
            "step": step,
            "state": state,
            "state_label": STATE_LABELS[state],
        }
        action = row.get("action")
        if action and action != "-":
            event["action"] = action
        trace.append(event)
    return trace


def summarize_trace(trace: Iterable[dict[str, object]], *, horizon: int) -> dict[str, object]:
    events = list(trace)
    if not events:
        raise ValueError("Cannot summarize an empty trace")
    states = [int(event["state"]) for event in events]
    failure_indices = [index for index, state in enumerate(states) if state == 2]
    first_failure_step = (
        int(events[failure_indices[0]]["step"]) if failure_indices else None
    )
    invalid_absorption = any(
        state != 2 for state in states[failure_indices[0] + 1 :]
    ) if failure_indices else False
    return {
        "trace_length": len(events),
        "failure_reached": int(bool(failure_indices)),
        "first_failure_step": first_failure_step,
        "steps_to_failure_capped": first_failure_step if first_failure_step is not None else horizon + 1,
        "degraded_visit_count": sum(state == 1 for state in states),
        "absorbing_state_valid": int(not invalid_absorption),
    }


def _data_rows(handle):
    """Drop PRISM informational lines preceding the CSV header."""

    started = False
    for raw_line in handle:
        line = raw_line.strip()
        if not line:
            continue
        if not started:
            columns = {part.strip() for part in line.split(",")}
            if {"step", "state"}.issubset(columns):
                started = True
            else:
                continue
        yield raw_line
