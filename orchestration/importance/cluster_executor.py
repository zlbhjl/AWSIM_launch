"""Run importance-sampling cases on the cluster's workers (docs/cluster_importance_sampling_plan.md §3).

Cases go to the task queue with their whole meta (``case_payload``); the
workers run them and the shared-store actor appends every full record to a
JSONL file, which this executor follows. The executor

- stores records in job order within each stream (run kind), as soon as the
  jobs before them are stored, so the stored records and every aggregate are
  identical to a local run, whatever order the workers finish in;
- re-issues a case whose record has not arrived ``case_timeout_sec`` after a
  worker took it (same inputs and seeds, ``attempt_id`` + 1), up to
  ``max_attempts`` submissions; a case that never arrives is reported lost;
- keeps every extra record (a second attempt that also finished, records after
  a sequential stop) out of the results, in ``duplicates.jsonl`` / ``late.jsonl``.

In a sequential run the stop rule sees only the contiguous prefix of samples,
so cases that finish early (for example AWSIM runs that end in a collision)
cannot bias the decision.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus, TestCase
from orchestration.importance.executors import CaseJob, RecordSink
from runtime.cluster.task_queue_gateway import case_payload


class RecordFeed(Protocol):
    def poll(self) -> list[EvaluationRecord]:
        """The records that arrived since the last poll."""


class JsonlRecordFeed:
    """Follow a JSONL file of serialized records (the shared-store actor's ``records_jsonl_path``)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._offset = 0

    def poll(self) -> list[EvaluationRecord]:
        if not self.path.exists():
            return []
        with self.path.open("rb") as handle:
            handle.seek(self._offset)
            data = handle.read()
        complete = data[: data.rfind(b"\n") + 1]
        self._offset += len(complete)
        return [record_from_payload(json.loads(line)) for line in complete.decode("utf-8").splitlines() if line.strip()]


def record_from_payload(row: dict[str, Any]) -> EvaluationRecord:
    return EvaluationRecord(
        case_id=str(row["case_id"]), target=str(row["target"]), case_kind=str(row["case_kind"]),
        status=RunStatus(str(row["status"])), input=dict(row.get("input", {})), output=dict(row.get("output", {})),
        evidence={str(key): str(value) for key, value in dict(row.get("evidence", {})).items()},
        meta=dict(row.get("meta", {})),
    )


class CaseLost(RuntimeError):
    """A case of a sequential run never came back after every allowed submission."""


@dataclass
class _Job:
    job: CaseJob
    position: int                      # order within its stream
    submissions: list[str] = field(default_factory=list)  # case ids, latest last
    taken_at: float | None = None      # when a worker took the latest submission
    record: EvaluationRecord | None = None
    done: bool = False
    lost: bool = False
    stored: bool = False


class ClusterExecutor:
    # Retries and reordering rely on sample ids and per-sample streams.
    requires_stream_seeds = True

    def __init__(
        self,
        *,
        queue: object,
        feed: RecordFeed,
        side_root: str | Path,
        max_in_flight: int = 8,
        case_timeout_sec: float | None = None,
        max_attempts: int = 3,
        poll_interval_sec: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = lambda message: None,
        maintenance: Callable[[dict[str, Any]], object] | None = None,
        path_root: str | Path | None = None,
    ):
        if max_in_flight < 1:
            raise ValueError("max_in_flight must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.queue = queue
        self.feed = feed
        self.side_root = Path(side_root)
        self.max_in_flight = max_in_flight
        self.case_timeout_sec = case_timeout_sec
        self.max_attempts = max_attempts
        self.poll_interval_sec = poll_interval_sec
        self.clock = clock
        self.sleep = sleep
        self.log = log
        # Called each tick with the queue snapshot (the worker watchdog of the cluster).
        self.maintenance = maintenance
        # Workers see the repository at the same relative paths, not the head's absolute ones.
        self.path_root = None if path_root is None else Path(path_root).resolve()
        self._by_case_id: dict[str, _Job] = {}

    # --- fixed N --------------------------------------------------------------

    def run(self, jobs: Sequence[CaseJob]) -> None:
        streams: dict[str, list[_Job]] = {}
        for job in jobs:
            members = streams.setdefault(job.stream, [])
            members.append(_Job(job, len(members)))
        # Interleave the streams, so ordinary and importance samples progress together.
        order = [members[index] for index in range(max((len(m) for m in streams.values()), default=0))
                 for members in streams.values() if index < len(members)]
        pending = list(order)
        while True:
            self._submit_from(pending)
            self._collect()
            for members in streams.values():
                self._store_prefix(members)
            self._check_timeouts(order)
            if all(entry.stored for entry in order):
                return
            self._tick()

    # --- sequential -----------------------------------------------------------

    def run_sequential(
        self,
        *,
        build_case: Callable[[int], TestCase],
        sink: RecordSink,
        first_index: int,
        max_index: int,
        should_stop: Callable[[], bool],
    ) -> None:
        members: list[_Job] = []
        next_index = first_index
        stopped = False
        while True:
            while not stopped and next_index <= max_index and self._in_flight(members) < self.max_in_flight:
                entry = _Job(CaseJob(build_case(next_index), sink, "importance"), len(members))
                members.append(entry)
                self._submit(entry)
                next_index += 1
            self._collect()
            # One record at a time, as in a local run: the rule is checked after each.
            while not stopped and self._store_next(members, stop_on_lost=True) is not None:
                if should_stop():
                    stopped = True
                    self._cancel_untaken(members)
                    self.log("sequential stop: waiting for the cases workers already took")
            if stopped:
                # Everything after the stop is kept out of the results.
                for entry in members:
                    if entry.done and not entry.stored:
                        if entry.record is not None:
                            self._side("late", entry.record)
                        entry.stored = True
                if all(entry.stored for entry in members):
                    return
            elif next_index > max_index and all(entry.stored for entry in members):
                return
            self._check_timeouts(members, reissue=not stopped)
            self._tick()

    def _cancel_untaken(self, members: Sequence[_Job]) -> None:
        """Drop the queued cases no worker took yet; they will never come back."""
        cancel = getattr(self.queue, "cancel_pending_tasks", None)
        if callable(cancel):
            cancel()
        taken = {str(task.get("case_id")) for task in self._in_flight_tasks()}
        self._collect()
        for entry in members:
            if not entry.done and entry.submissions and entry.submissions[-1] not in taken:
                entry.done = True
                entry.stored = True

    # --- shared steps ---------------------------------------------------------

    @staticmethod
    def _in_flight(members: Sequence[_Job]) -> int:
        return sum(1 for entry in members if entry.submissions and not entry.done)

    def _submit_from(self, pending: list[_Job]) -> None:
        in_flight = len({id(entry) for entry in self._by_case_id.values() if not entry.done})
        while pending and in_flight < self.max_in_flight:
            self._submit(pending.pop(0))
            in_flight += 1

    def _submit(self, entry: _Job) -> None:
        case = entry.job.case
        attempt = len(entry.submissions)
        if attempt:
            base_attempt = int(case.meta.get("attempt_id", 0))
            case = replace(
                case,
                case_id=f"{case.case_id}-retry{attempt}",
                meta={**case.meta, "attempt_id": base_attempt + attempt},
            )
        entry.submissions.append(case.case_id)
        entry.taken_at = None
        self._by_case_id[case.case_id] = entry
        self.queue.add_task(self._portable(case_payload(case)))

    def _portable(self, payload: dict[str, Any]) -> dict[str, Any]:
        plan_path = payload.get("sampling_plan_path")
        if self.path_root is None or not plan_path:
            return payload
        resolved = Path(str(plan_path)).resolve()
        try:
            relative = resolved.relative_to(self.path_root)
        except ValueError:
            raise ValueError(
                f"the sampling plan {resolved} must be inside {self.path_root}, which the workers share"
            ) from None
        return {**payload, "sampling_plan_path": str(relative)}

    def _tick(self) -> None:
        if self.maintenance is not None:
            getter = getattr(self.queue, "get_snapshot", None)
            if callable(getter):
                for event in self.maintenance(getter()) or ():
                    self.log(f"worker maintenance: {event}")
        self.sleep(self.poll_interval_sec)

    def _collect(self) -> None:
        for record in self.feed.poll():
            entry = self._by_case_id.get(record.case_id)
            if entry is None:
                self._side("unknown", record)
            elif entry.done:
                self._side("duplicates", record)
            else:
                entry.record = record
                entry.done = True

    def _store_next(self, members: Sequence[_Job], *, stop_on_lost: bool = False) -> _Job | None:
        """Store the record of the first job not yet stored, if it has finished; return that job."""
        for entry in members:
            if entry.stored:
                continue
            if not entry.done:
                return None
            if entry.lost:
                if stop_on_lost:
                    raise CaseLost(f"case {entry.job.case.case_id} did not come back after "
                                   f"{len(entry.submissions)} submission(s)")
            else:
                entry.job.sink(entry.record)
            entry.stored = True
            return entry
        return None

    def _store_prefix(self, members: Sequence[_Job]) -> None:
        """Store the records of the leading finished jobs, in order."""
        while self._store_next(members) is not None:
            pass

    def _check_timeouts(self, members: Sequence[_Job], *, reissue: bool = True) -> None:
        if self.case_timeout_sec is None:
            return
        now = self.clock()
        taken = {str(task.get("case_id")) for task in self._in_flight_tasks()}
        for entry in members:
            if entry.done or not entry.submissions:
                continue
            if entry.taken_at is None:
                if entry.submissions[-1] in taken:
                    entry.taken_at = now
                continue
            if now - entry.taken_at <= self.case_timeout_sec:
                continue
            if reissue and len(entry.submissions) < self.max_attempts:
                self.log(f"case {entry.submissions[-1]} timed out; submitting it again")
                self._submit(entry)
            else:
                self.log(f"case {entry.submissions[-1]} is lost after {len(entry.submissions)} submission(s)")
                entry.done = True
                entry.lost = True

    def _in_flight_tasks(self) -> list[dict[str, Any]]:
        getter = getattr(self.queue, "get_in_flight_tasks", None)
        return list(getter()) if callable(getter) else []

    def _side(self, name: str, record: EvaluationRecord) -> None:
        from runtime.cluster.result_sink import serialize_evaluation_record

        self.side_root.mkdir(parents=True, exist_ok=True)
        with (self.side_root / f"{name}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(serialize_evaluation_record(record), ensure_ascii=False) + "\n")


__all__ = ["CaseLost", "ClusterExecutor", "JsonlRecordFeed", "RecordFeed", "record_from_payload"]
