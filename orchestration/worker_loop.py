from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from contracts.evaluation import (
    DEFAULT_EVALUATION_SCHEMA_VERSION,
    EvaluationRecord,
    ensure_evaluation_meta,
)
from contracts.execution import RawRunResult, RunStatus, TestCase


@dataclass(frozen=True)
class WorkerLoopContext:
    worker_id: str = "worker_v2"
    source_module: str = "orchestration.worker_loop"
    schema_version: int = DEFAULT_EVALUATION_SCHEMA_VERSION


@dataclass(frozen=True)
class WorkerPreparation:
    status: str
    test_case: TestCase | None = None
    reason: str = ""
    history_loop_num: int | None = None


@dataclass(frozen=True)
class WorkerRunOutcome:
    status: str
    test_case: TestCase | None = None
    record: EvaluationRecord | None = None
    reason: str = ""
    history_loop_num: int | None = None


@dataclass(frozen=True)
class WorkerBatchOutcome:
    terminal_status: str
    records: tuple[EvaluationRecord, ...] = ()
    processed_count: int = 0
    skipped_count: int = 0
    reason: str = ""
    last_outcome: WorkerRunOutcome | None = None


class WorkerLoop:
    def __init__(
        self,
        *,
        backend: Any,
        result_interpreter: Any,
        result_sink: Any | None = None,
        task_source: Any | None = None,
        context: WorkerLoopContext | None = None,
    ):
        self.backend = backend
        self.result_interpreter = result_interpreter
        self.result_sink = result_sink
        self.task_source = task_source
        self.context = context or WorkerLoopContext()
        self.last_stop_reason = ""

    def run_once(self, test_case: TestCase | None = None) -> EvaluationRecord | None:
        try:
            resolved_test_case = test_case or self.fetch_next_test_case()
        except StopIteration:
            return None
        if resolved_test_case is None:
            return None

        self._notify_worker_status("running")

        try:
            raw_run_result = self._run_backend(resolved_test_case)
        except Exception as exc:
            record = self._error_record(
                resolved_test_case,
                status=RunStatus.EXECUTION_ERROR,
                error_message=str(exc),
            )
            self._save_record(record)
            self._notify_after_completion(record)
            return record

        try:
            record = self._interpret(raw_run_result)
        except Exception as exc:
            record = self._error_record(
                resolved_test_case,
                status=RunStatus.ANALYSIS_ERROR,
                error_message=str(exc),
                evidence=dict(raw_run_result.evidence),
                meta={"raw_run_meta": dict(raw_run_result.meta)},
            )
            self._save_record(record)
            self._notify_after_completion(record)
            return record

        self._merge_test_case_context(record, resolved_test_case)
        self._save_record(record)
        self._notify_after_completion(record)
        return record

    def run_until_empty(self, *, max_cases: int | None = None) -> list[EvaluationRecord]:
        records: list[EvaluationRecord] = []
        while max_cases is None or len(records) < max_cases:
            record = self.run_once()
            if record is None:
                break
            records.append(record)
        return records

    def execute(
        self,
        *,
        direct_test_case: TestCase | None = None,
        history: Any | None = None,
    ) -> WorkerRunOutcome:
        preparation = self.prepare_test_case(
            direct_test_case=direct_test_case,
            history=history,
        )
        if preparation.status != "ready":
            return WorkerRunOutcome(
                status=preparation.status,
                test_case=preparation.test_case,
                reason=preparation.reason,
                history_loop_num=preparation.history_loop_num,
            )

        record = self.run_once(preparation.test_case)
        if record is None:
            return WorkerRunOutcome(
                status="no_record",
                test_case=preparation.test_case,
                history_loop_num=preparation.history_loop_num,
            )

        if history is not None and preparation.history_loop_num is not None:
            self._history_append(history, preparation.history_loop_num)

        return WorkerRunOutcome(
            status=record.status.value,
            test_case=preparation.test_case,
            record=record,
            history_loop_num=preparation.history_loop_num,
        )

    def execute_until_exit(
        self,
        *,
        direct_test_case: TestCase | None = None,
        history: Any | None = None,
        max_cases: int | None = None,
        refresh_interval: int | None = None,
    ) -> WorkerBatchOutcome:
        records: list[EvaluationRecord] = []
        processed_count = 0
        skipped_count = 0
        last_outcome: WorkerRunOutcome | None = None

        while True:
            outcome = self.execute(
                direct_test_case=direct_test_case,
                history=history,
            )
            last_outcome = outcome
            if outcome.record is not None:
                records.append(outcome.record)
                processed_count += 1
                if refresh_interval is not None and processed_count >= refresh_interval:
                    return WorkerBatchOutcome(
                        terminal_status="refresh_requested",
                        records=tuple(records),
                        processed_count=processed_count,
                        skipped_count=skipped_count,
                        reason="refresh_interval_reached",
                        last_outcome=outcome,
                    )
                if max_cases is not None and processed_count >= max_cases:
                    return WorkerBatchOutcome(
                        terminal_status="max_cases_reached",
                        records=tuple(records),
                        processed_count=processed_count,
                        skipped_count=skipped_count,
                        last_outcome=outcome,
                    )
                if direct_test_case is not None:
                    return WorkerBatchOutcome(
                        terminal_status=outcome.status,
                        records=tuple(records),
                        processed_count=processed_count,
                        skipped_count=skipped_count,
                        last_outcome=outcome,
                    )
                continue

            if outcome.status == "skipped_duplicate" and direct_test_case is None:
                skipped_count += 1
                continue

            return WorkerBatchOutcome(
                terminal_status=outcome.status,
                records=tuple(records),
                processed_count=processed_count,
                skipped_count=skipped_count,
                reason=outcome.reason,
                last_outcome=outcome,
            )

    def prepare_test_case(
        self,
        *,
        direct_test_case: TestCase | None = None,
        history: Any | None = None,
    ) -> WorkerPreparation:
        selected_test_case = direct_test_case
        if selected_test_case is None:
            try:
                selected_test_case = self.fetch_next_test_case()
            except StopIteration:
                return WorkerPreparation(
                    status="stopped",
                    reason=self.last_stop_reason,
                )
            if selected_test_case is None:
                return WorkerPreparation(status="no_task")

        history_loop_num = self._resolve_history_loop_num(selected_test_case)
        if history is not None and history_loop_num is not None and self._history_contains(
            history,
            history_loop_num,
        ):
            self._notify_worker_status("skipped_duplicate")
            self._report_completion(history_loop_num, "skipped_duplicate")
            return WorkerPreparation(
                status="skipped_duplicate",
                test_case=selected_test_case,
                history_loop_num=history_loop_num,
            )

        return WorkerPreparation(
            status="ready",
            test_case=selected_test_case,
            history_loop_num=history_loop_num,
        )

    def fetch_next_test_case(self) -> TestCase | None:
        if self.task_source is None:
            return None
        self._notify_worker_status("waiting")
        if hasattr(self.task_source, "get_next"):
            return self._capture_stop_iteration(self.task_source.get_next)
        if hasattr(self.task_source, "fetch_next"):
            return self._capture_stop_iteration(self.task_source.fetch_next)
        if callable(self.task_source):
            return self._capture_stop_iteration(self.task_source)
        raise TypeError("task_source must expose get_next(), fetch_next(), or be callable")

    def _capture_stop_iteration(self, fetcher: Callable[[], TestCase | None]) -> TestCase | None:
        try:
            self.last_stop_reason = ""
            return fetcher()
        except StopIteration as exc:
            self.last_stop_reason = str(exc)
            raise

    def _run_backend(self, test_case: TestCase) -> RawRunResult:
        if hasattr(self.backend, "run"):
            return self.backend.run(test_case)
        if callable(self.backend):
            return self.backend(test_case)
        raise TypeError("backend must expose run() or be callable")

    def _interpret(self, raw_run_result: RawRunResult) -> EvaluationRecord:
        if hasattr(self.result_interpreter, "interpret_raw_run_result"):
            return self.result_interpreter.interpret_raw_run_result(raw_run_result)
        if hasattr(self.result_interpreter, "interpret"):
            return self.result_interpreter.interpret(raw_run_result)
        if callable(self.result_interpreter):
            return self.result_interpreter(raw_run_result)
        raise TypeError(
            "result_interpreter must expose interpret_raw_run_result(), "
            "interpret(), or be callable"
        )

    def _save_record(self, record: EvaluationRecord) -> None:
        if self.result_sink is None:
            return
        if hasattr(self.result_sink, "save"):
            self.result_sink.save(record)
            return
        if hasattr(self.result_sink, "write"):
            self.result_sink.write(record)
            return
        if callable(self.result_sink):
            self.result_sink(record)
            return
        raise TypeError("result_sink must expose save(), write(), or be callable")

    def _notify_worker_status(self, status: str) -> None:
        if self.task_source is None or not hasattr(self.task_source, "update_worker_status"):
            return
        self.task_source.update_worker_status(self.context.worker_id, status)

    def notify_worker_status(self, status: str) -> None:
        self._notify_worker_status(status)

    def _notify_after_completion(self, record: EvaluationRecord) -> None:
        self._notify_worker_status(record.status.value)
        loop_num = record.meta.get("global_loop_num")
        if isinstance(loop_num, str) and loop_num.isdigit():
            loop_num = int(loop_num)
        if isinstance(loop_num, int):
            self._report_completion(loop_num, record.status.value)

    def _report_completion(self, loop_num: int, status: str) -> None:
        if self.task_source is None or not hasattr(self.task_source, "report_completion"):
            return
        self.task_source.report_completion(loop_num, status)

    def _history_contains(self, history: Any, loop_num: int) -> bool:
        if hasattr(history, "contains"):
            return bool(history.contains(loop_num))
        if hasattr(history, "load"):
            return loop_num in history.load()
        if callable(history):
            return bool(history(loop_num))
        raise TypeError("history must expose contains(), load(), or be callable")

    def _history_append(self, history: Any, loop_num: int) -> None:
        if hasattr(history, "append"):
            history.append(loop_num)
            return
        if callable(history):
            history(loop_num)
            return
        raise TypeError("history must expose append() or be callable")

    def _resolve_history_loop_num(self, test_case: TestCase | None) -> int | None:
        if test_case is None:
            return None
        for key in ("global_loop_num", "history_loop_num"):
            value = test_case.meta.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        return None

    def _merge_test_case_context(
        self,
        record: EvaluationRecord,
        test_case: TestCase,
    ) -> None:
        merged_input = dict(test_case.input)
        merged_input.update(record.input)
        record.input = merged_input

        merged_meta = ensure_evaluation_meta(
            record.meta,
            source_module=self.context.source_module,
            schema_version=self.context.schema_version,
        )
        merged_meta.setdefault("worker_id", self.context.worker_id)
        merged_meta.setdefault("task_reason", test_case.reason)
        merged_meta.setdefault("task_tags", list(test_case.tags))
        merged_meta.setdefault(
            "processed_at",
            datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        for key, value in test_case.meta.items():
            merged_meta.setdefault(key, value)
        record.meta = merged_meta

    def _error_record(
        self,
        test_case: TestCase,
        *,
        status: RunStatus,
        error_message: str,
        evidence: dict[str, str] | None = None,
        meta: dict[str, object] | None = None,
    ) -> EvaluationRecord:
        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        merged_meta = {
            "schema_version": self.context.schema_version,
            "worker_id": self.context.worker_id,
            "source_module": self.context.source_module,
            "created_at": created_at,
            "task_reason": test_case.reason,
            "task_tags": list(test_case.tags),
            "processed_at": created_at,
            "error_message": error_message,
        }
        for key, value in test_case.meta.items():
            merged_meta.setdefault(key, value)
        if meta:
            for key, value in meta.items():
                if key in {"schema_version", "source_module", "created_at"}:
                    continue
                merged_meta[key] = value
        return EvaluationRecord(
            case_id=test_case.case_id,
            target=test_case.target,
            case_kind=test_case.case_kind,
            status=status,
            input=dict(test_case.input),
            evidence=evidence or {},
            meta=merged_meta,
        )
