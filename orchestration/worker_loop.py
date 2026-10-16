from __future__ import annotations

import time
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from contracts.evaluation import (
    DEFAULT_EVALUATION_SCHEMA_VERSION,
    EvaluationRecord,
    ensure_evaluation_meta,
)
from contracts.execution import RawRunResult, RunStatus, TestCase
from runtime.cluster.ray_client import is_ray_control_plane_error


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


class WorkerControlPlaneLost(RuntimeError):
    pass


class WorkerInfrastructureUnavailable(RuntimeError):
    pass


class WorkerLoop:
    def __init__(
        self,
        *,
        backend: Any,
        result_interpreter: Any,
        result_sink: Any | None = None,
        task_source: Any | None = None,
        context: WorkerLoopContext | None = None,
        sleeper: Callable[[float], None] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
        heartbeat_interval_sec: float = 0.0,
        pre_fetch_health_check: Callable[[], object] | None = None,
    ):
        self.backend = backend
        self.result_interpreter = result_interpreter
        self.result_sink = result_sink
        self.task_source = task_source
        self.context = context or WorkerLoopContext()
        self.last_stop_reason = ""
        self.sleeper = sleeper or time.sleep
        self.monotonic = monotonic_fn or time.monotonic
        self.heartbeat_interval_sec = float(heartbeat_interval_sec)
        self.pre_fetch_health_check = pre_fetch_health_check
        self._heartbeat_error: BaseException | None = None

    def run_once(self, test_case: TestCase | None = None) -> EvaluationRecord | None:
        try:
            resolved_test_case = test_case or self.fetch_next_test_case()
        except StopIteration:
            return None
        if resolved_test_case is None:
            return None

        self._notify_worker_status("running")
        heartbeat_stop = self._start_heartbeat("running")

        try:
            raw_run_result = self._run_backend(resolved_test_case)
            self._raise_if_heartbeat_failed()
        except Exception as exc:
            self._stop_heartbeat(heartbeat_stop)
            if isinstance(exc, WorkerControlPlaneLost) or is_ray_control_plane_error(exc):
                raise WorkerControlPlaneLost(str(exc)) from exc
            record = self._error_record(
                resolved_test_case,
                status=RunStatus.EXECUTION_ERROR,
                error_message=str(exc),
            )
            self._save_record(record)
            self._notify_after_completion(record)
            return record
        finally:
            self._stop_heartbeat(heartbeat_stop)

        try:
            record = self._interpret(raw_run_result)
        except Exception as exc:
            fallback_status = (
                raw_run_result.status
                if raw_run_result.status is not RunStatus.SUCCESS
                else RunStatus.ANALYSIS_ERROR
            )
            record = self._error_record(
                resolved_test_case,
                status=fallback_status,
                error_message=str(exc),
                evidence=dict(raw_run_result.evidence),
                meta={
                    "raw_run_meta": dict(raw_run_result.meta),
                    "raw_run_status": raw_run_result.status.value,
                    "execution_status": raw_run_result.status.value,
                    "analysis_status": RunStatus.ANALYSIS_ERROR.value,
                },
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
        empty_wait_timeout_sec: float = 0.0,
        empty_wait_interval_sec: float = 5.0,
    ) -> WorkerBatchOutcome:
        records: list[EvaluationRecord] = []
        processed_count = 0
        skipped_count = 0
        last_outcome: WorkerRunOutcome | None = None
        empty_wait_deadline = self.monotonic() + max(float(empty_wait_timeout_sec), 0.0)

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

            if (
                outcome.status == "no_task"
                and direct_test_case is None
                and self.task_source is not None
                and self.monotonic() < empty_wait_deadline
            ):
                self.sleeper(max(float(empty_wait_interval_sec), 0.0))
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
        self._ensure_pre_fetch_health()
        self._notify_worker_status("waiting")
        if hasattr(self.task_source, "get_next"):
            return self._capture_stop_iteration(self.task_source.get_next)
        if hasattr(self.task_source, "fetch_next"):
            return self._capture_stop_iteration(self.task_source.fetch_next)
        if callable(self.task_source):
            return self._capture_stop_iteration(self.task_source)
        raise TypeError("task_source must expose get_next(), fetch_next(), or be callable")

    def _ensure_pre_fetch_health(self) -> None:
        if self.pre_fetch_health_check is None:
            return
        result = self.pre_fetch_health_check()
        healthy = bool(getattr(result, "healthy", result))
        if healthy:
            return
        detail = str(getattr(result, "detail", "") or "worker health check failed")
        self._notify_worker_status("gpu_unavailable")
        raise WorkerInfrastructureUnavailable(detail)

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
        try:
            self.task_source.update_worker_status(self.context.worker_id, status)
        except Exception as exc:
            if is_ray_control_plane_error(exc):
                raise WorkerControlPlaneLost(
                    f"Ray control plane lost while updating worker status: {exc}"
                ) from exc
            raise

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
        try:
            self.task_source.report_completion(loop_num, status)
        except Exception as exc:
            if is_ray_control_plane_error(exc):
                raise WorkerControlPlaneLost(
                    f"Ray control plane lost while reporting completion: {exc}"
                ) from exc
            raise

    def _start_heartbeat(self, status: str) -> threading.Event | None:
        if (
            self.task_source is None
            or not hasattr(self.task_source, "update_worker_status")
            or self.heartbeat_interval_sec <= 0.0
        ):
            return None
        self._heartbeat_error = None
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(stop_event, status),
            daemon=True,
        )
        thread.start()
        setattr(stop_event, "_worker_loop_thread", thread)
        return stop_event

    def _stop_heartbeat(self, stop_event: threading.Event | None) -> None:
        if stop_event is None:
            return
        stop_event.set()
        thread = getattr(stop_event, "_worker_loop_thread", None)
        if isinstance(thread, threading.Thread):
            thread.join(timeout=1.0)

    def _heartbeat_loop(self, stop_event: threading.Event, status: str) -> None:
        while not stop_event.wait(self.heartbeat_interval_sec):
            try:
                self._notify_worker_status(status)
            except WorkerControlPlaneLost as exc:
                self._heartbeat_error = exc
                stop_event.set()
                return
            except Exception as exc:
                self._heartbeat_error = exc
                stop_event.set()
                return

    def _raise_if_heartbeat_failed(self) -> None:
        if self._heartbeat_error is not None:
            raise WorkerControlPlaneLost(str(self._heartbeat_error)) from self._heartbeat_error

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
        merged_meta.setdefault("analysis_status", record.status.value)
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
        if status is RunStatus.EXECUTION_ERROR:
            merged_meta["execution_status"] = status.value
            merged_meta["analysis_status"] = "not_run"
        else:
            merged_meta["analysis_status"] = status.value
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
