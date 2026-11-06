"""How the importance-sampling runner executes its cases (docs/cluster_importance_sampling_plan.md §3.1).

The runner decides which cases to run and where each record goes; an executor
only runs them. ``LocalExecutor`` runs one case after the other in this
process. ``ClusterExecutor`` (orchestration/importance/cluster_executor.py)
hands them to the cluster's workers and stores their records in the same
order, so both give identical results.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from contracts.evaluation import EvaluationRecord
from contracts.execution import TestCase
from orchestration.worker_loop import WorkerLoop

RecordSink = Callable[[EvaluationRecord], object]


@dataclass(frozen=True)
class CaseJob:
    """One case and where its record goes. Jobs of one ``stream`` are stored in their given order."""

    case: TestCase
    sink: RecordSink
    stream: str


class CaseExecutor(Protocol):
    def run(self, jobs: Sequence[CaseJob]) -> None:
        """Run every job; each record goes to its job's sink, in job order within a stream."""

    def run_sequential(
        self,
        *,
        build_case: Callable[[int], TestCase],
        sink: RecordSink,
        first_index: int,
        max_index: int,
        should_stop: Callable[[], bool],
    ) -> None:
        """Run cases first_index, first_index + 1, ... until ``should_stop()`` (checked after each stored record) or max_index."""


class LocalExecutor:
    """Run the cases one after the other in this process."""

    def __init__(self, *, backend: object, result_interpreter: object):
        self.backend = backend
        self.result_interpreter = result_interpreter

    def _loop(self, sink: RecordSink) -> WorkerLoop:
        return WorkerLoop(backend=self.backend, result_interpreter=self.result_interpreter, result_sink=sink)

    def run(self, jobs: Sequence[CaseJob]) -> None:
        loops: dict[int, WorkerLoop] = {}
        for job in jobs:
            loop = loops.setdefault(id(job.sink), self._loop(job.sink))
            loop.run_once(job.case)

    def run_sequential(
        self,
        *,
        build_case: Callable[[int], TestCase],
        sink: RecordSink,
        first_index: int,
        max_index: int,
        should_stop: Callable[[], bool],
    ) -> None:
        loop = self._loop(sink)
        for index in range(first_index, max_index + 1):
            loop.run_once(build_case(index))
            if should_stop():
                return


__all__ = ["CaseExecutor", "CaseJob", "LocalExecutor", "RecordSink"]
