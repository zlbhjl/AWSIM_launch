"""Put the rows of a statistical run in sample order before any rule looks at them.

Statistical rules assume they see samples in the order they were drawn. On a
cluster the rows arrive in the order workers finish, and runs that end early
(for example AWSIM runs that end in a collision) would arrive first and bias a
sequential decision. ``prepare_statistical_sample`` therefore

- orders rows by their sample number (``sample_index``),
- keeps one row per sample: its first successful attempt, or, if every allowed
  attempt failed, its last failed attempt (rules drop non-success rows as before),
- keeps only the contiguous prefix of resolved samples: a sample that has not
  come back yet, or is waiting for another attempt, cuts the prefix there,
- and summarises what it did (cuts, retries, duplicates, unresolved samples).

A sample is resolved when it has a success, when all ``max_attempts`` attempts
failed, or when the queue gave it up as lost (``abandoned``). Without a
``sample_index`` column the data is returned unchanged.

docs/statistical_modes_hardening_plan.md §3.1.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field

import pandas as pd

INDEX_COLUMN = "sample_index"
SUCCESS = "success"
_LISTED = 50


@dataclass
class PreparedSample:
    frame: pd.DataFrame | None
    summary: dict[str, object] = field(default_factory=dict)

    @property
    def degraded(self) -> bool:
        """Some samples in the prefix are unresolved failures or were lost."""
        return bool(self.summary.get("unresolved_samples") or self.summary.get("lost_samples"))


def prepare_statistical_sample(
    data: pd.DataFrame | None,
    *,
    max_attempts: int = 1,
    abandoned: Collection[int] = (),
    issued: Collection[int] | None = None,
) -> PreparedSample:
    """``issued``: the sample numbers handed out so far (rows already present
    count as issued too). Without it every number between the first and the
    last is expected, which suits dense numbering."""
    if data is None or data.empty or INDEX_COLUMN not in data.columns:
        return PreparedSample(data, {"ordered": False})
    indexes = pd.to_numeric(data[INDEX_COLUMN], errors="coerce")
    statuses = (
        data["status"].fillna(SUCCESS).astype(str).str.lower()
        if "status" in data.columns
        else pd.Series(SUCCESS, index=data.index)
    )
    rows: dict[int, list[int]] = {}
    unnumbered = 0
    for position, value in enumerate(indexes.to_numpy()):
        if pd.isna(value):
            unnumbered += 1
            continue
        rows.setdefault(int(value), []).append(position)
    abandoned_set = {int(index) for index in abandoned}
    known = set(rows) | abandoned_set | {int(index) for index in (issued or ())}
    if not known:
        return PreparedSample(data, {"ordered": False})

    status_list = statuses.to_list()
    chosen: list[int] = []
    duplicates = retried = unresolved = lost = 0
    attempts_to_success: dict[int, int] = {}
    unresolved_indexes: list[int] = []
    cut_at: int | None = None
    first = min(known)
    expected = sorted(known) if issued is not None else range(first, max(known) + 1)
    for index in expected:
        positions = rows.get(index, [])
        successes = [position for position in positions if status_list[position] == SUCCESS]
        if successes:
            chosen.append(successes[0])
            duplicates += len(successes) - 1
            failed_before = sum(1 for position in positions if position < successes[0])
            if failed_before:
                retried += 1
                attempts_to_success[failed_before + 1] = attempts_to_success.get(failed_before + 1, 0) + 1
            continue
        if positions and (len(positions) >= max_attempts or index in abandoned_set):
            chosen.append(positions[-1])
            unresolved += 1
            unresolved_indexes.append(index)
            continue
        if not positions and index in abandoned_set:
            lost += 1
            unresolved_indexes.append(index)
            continue
        cut_at = index
        break

    waiting = 0 if cut_at is None else sum(1 for index in rows if index > cut_at)
    summary: dict[str, object] = {
        "ordered": True,
        "used_samples": len(chosen) + lost,
        "first_sample_index": first,
        "cut_at_sample_index": cut_at,
        "waiting_samples": waiting,
        "duplicate_rows": duplicates,
        "retried_samples": retried,
        "attempts_to_success": {str(key): value for key, value in sorted(attempts_to_success.items())},
        "unresolved_samples": unresolved,
        "lost_samples": lost,
        "unresolved_sample_indexes": unresolved_indexes[:_LISTED],
        "rows_without_sample_index": unnumbered,
        "max_attempts": max_attempts,
        "max_sample_index": max(known),
    }
    if chosen == list(range(len(data))):
        return PreparedSample(data, summary)
    return PreparedSample(data.iloc[chosen], summary)


__all__ = ["INDEX_COLUMN", "PreparedSample", "prepare_statistical_sample"]
