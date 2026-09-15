import time

import pytest

from contracts.evaluation import EvaluationRecord
from contracts.execution import RawRunResult, RunStatus, TestCase
from orchestration.worker_loop import (
    WorkerControlPlaneLost,
    WorkerInfrastructureUnavailable,
    WorkerLoop,
    WorkerLoopContext,
)


class RecordingSink:
    def __init__(self) -> None:
        self.records: list[EvaluationRecord] = []

    def save(self, record: EvaluationRecord) -> None:
        self.records.append(record)


def test_worker_loop_runs_successful_case_and_saves_record() -> None:
    test_case = TestCase(
        case_id="loop_1",
        target="awsim",
        case_kind="uturn",
        input={"dx0": 15.0},
        tags=["smoke"],
        reason="boundary_explore",
        meta={"requested_by": "unit_test"},
    )
    sink = RecordingSink()

    def backend(case: TestCase) -> RawRunResult:
        assert case is test_case
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.SUCCESS,
            evidence={"trace_json": "/tmp/loop_1.json"},
        )

    def interpreter(raw: RawRunResult) -> EvaluationRecord:
        assert raw.case_id == "loop_1"
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
            output={"c_collision": 0},
            evidence=dict(raw.evidence),
            meta={"verifier_name": "maude"},
        )

    loop = WorkerLoop(
        backend=backend,
        result_interpreter=interpreter,
        result_sink=sink,
        context=WorkerLoopContext(worker_id="worker-21"),
    )

    record = loop.run_once(test_case)

    assert record is not None
    assert record.status == RunStatus.SUCCESS
    assert record.input == {"dx0": 15.0}
    assert record.output["c_collision"] == 0
    assert record.meta["schema_version"] == 1
    assert record.meta["source_module"] == "orchestration.worker_loop"
    assert record.meta["analysis_status"] == "success"
    assert record.meta["worker_id"] == "worker-21"
    assert record.meta["task_reason"] == "boundary_explore"
    assert record.meta["task_tags"] == ["smoke"]
    assert record.meta["requested_by"] == "unit_test"
    assert sink.records == [record]


def test_worker_loop_converts_backend_exception_to_execution_error() -> None:
    test_case = TestCase(case_id="loop_2", target="awsim", case_kind="uturn")
    sink = RecordingSink()

    def failing_backend(_: TestCase) -> RawRunResult:
        raise RuntimeError("awsim crashed")

    loop = WorkerLoop(
        backend=failing_backend,
        result_interpreter=lambda _: None,
        result_sink=sink,
    )

    record = loop.run_once(test_case)

    assert record is not None
    assert record.status == RunStatus.EXECUTION_ERROR
    assert record.meta["schema_version"] == 1
    assert record.meta["source_module"] == "orchestration.worker_loop"
    assert record.meta["execution_status"] == "execution_error"
    assert record.meta["analysis_status"] == "not_run"
    assert "created_at" in record.meta
    assert record.meta["error_message"] == "awsim crashed"
    assert sink.records == [record]


def test_worker_loop_converts_interpreter_exception_to_analysis_error() -> None:
    test_case = TestCase(case_id="loop_3", target="awsim", case_kind="uturn")
    sink = RecordingSink()

    def backend(case: TestCase) -> RawRunResult:
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.TIMEOUT,
            evidence={"trace_json": "/tmp/loop_3.json"},
            meta={"returncode": 0},
        )

    def failing_interpreter(_: RawRunResult) -> EvaluationRecord:
        raise ValueError("maude output missing headers")

    loop = WorkerLoop(
        backend=backend,
        result_interpreter=failing_interpreter,
        result_sink=sink,
    )

    record = loop.run_once(test_case)

    assert record is not None
    assert record.status == RunStatus.TIMEOUT
    assert record.evidence["trace_json"] == "/tmp/loop_3.json"
    assert record.meta["schema_version"] == 1
    assert record.meta["source_module"] == "orchestration.worker_loop"
    assert record.meta["raw_run_status"] == "timeout"
    assert record.meta["execution_status"] == "timeout"
    assert record.meta["raw_run_meta"] == {"returncode": 0}
    assert record.meta["analysis_status"] == "analysis_error"
    assert record.meta["error_message"] == "maude output missing headers"
    assert sink.records == [record]


def test_worker_loop_can_pull_cases_from_task_source_until_empty() -> None:
    sink = RecordingSink()
    cases = [
        TestCase(case_id="loop_10", target="awsim", case_kind="uturn"),
        TestCase(case_id="loop_11", target="awsim", case_kind="uturn"),
    ]

    class TaskSource:
        def fetch_next(self) -> TestCase | None:
            if not cases:
                return None
            return cases.pop(0)

    def backend(case: TestCase) -> RawRunResult:
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.SUCCESS,
        )

    def interpreter(raw: RawRunResult) -> EvaluationRecord:
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
        )

    loop = WorkerLoop(
        backend=backend,
        result_interpreter=interpreter,
        result_sink=sink,
        task_source=TaskSource(),
    )

    records = loop.run_until_empty()

    assert [record.case_id for record in records] == ["loop_10", "loop_11"]
    assert [record.case_id for record in sink.records] == ["loop_10", "loop_11"]


def test_worker_loop_does_not_take_task_when_gpu_health_check_fails() -> None:
    class TaskSource:
        def __init__(self) -> None:
            self.fetch_count = 0
            self.statuses: list[tuple[str, str]] = []

        def fetch_next(self) -> TestCase | None:
            self.fetch_count += 1
            return TestCase(case_id="must_not_run", target="awsim", case_kind="uturn")

        def update_worker_status(self, worker_id: str, status: str) -> None:
            self.statuses.append((worker_id, status))

    class Unhealthy:
        healthy = False
        detail = "Failed to initialize NVML: Unknown Error"

    task_source = TaskSource()
    loop = WorkerLoop(
        backend=lambda _: None,
        result_interpreter=lambda _: None,
        task_source=task_source,
        context=WorkerLoopContext(worker_id="worker_21"),
        pre_fetch_health_check=lambda: Unhealthy(),
    )

    with pytest.raises(WorkerInfrastructureUnavailable, match="NVML"):
        loop.fetch_next_test_case()

    assert task_source.fetch_count == 0
    assert task_source.statuses == [("worker_21", "gpu_unavailable")]


def test_worker_loop_stops_cleanly_when_task_source_raises_stop_iteration() -> None:
    sink = RecordingSink()

    class TaskSource:
        def fetch_next(self) -> TestCase | None:
            raise StopIteration("master stopped")

    loop = WorkerLoop(
        backend=lambda _: None,
        result_interpreter=lambda _: None,
        result_sink=sink,
        task_source=TaskSource(),
    )

    assert loop.run_once() is None
    assert loop.last_stop_reason == "master stopped"
    assert sink.records == []


def test_worker_loop_prepare_test_case_returns_no_task_when_queue_is_empty() -> None:
    class TaskSource:
        def fetch_next(self) -> TestCase | None:
            return None

    loop = WorkerLoop(
        backend=lambda _: None,
        result_interpreter=lambda _: None,
        task_source=TaskSource(),
    )

    preparation = loop.prepare_test_case()

    assert preparation.status == "no_task"
    assert preparation.test_case is None


def test_worker_loop_prepare_test_case_marks_duplicate_history_case() -> None:
    class TaskSource:
        def __init__(self) -> None:
            self.status_updates: list[tuple[str, str]] = []
            self.completions: list[tuple[int, str]] = []

        def fetch_next(self) -> TestCase | None:
            return TestCase(
                case_id="loop_30",
                target="awsim",
                case_kind="uturn",
                meta={"global_loop_num": 30},
            )

        def update_worker_status(self, worker_id: str, status: str) -> None:
            self.status_updates.append((worker_id, status))

        def report_completion(self, loop_num: int, status: str) -> None:
            self.completions.append((loop_num, status))

    class History:
        def contains(self, loop_num: int) -> bool:
            return loop_num == 30

    task_source = TaskSource()
    loop = WorkerLoop(
        backend=lambda _: None,
        result_interpreter=lambda _: None,
        task_source=task_source,
        context=WorkerLoopContext(worker_id="worker-queue"),
    )

    preparation = loop.prepare_test_case(history=History())

    assert preparation.status == "skipped_duplicate"
    assert preparation.history_loop_num == 30
    assert task_source.status_updates == [
        ("worker-queue", "waiting"),
        ("worker-queue", "skipped_duplicate"),
    ]
    assert task_source.completions == [(30, "skipped_duplicate")]


def test_worker_loop_updates_statuses_and_reports_completion_for_queue_case() -> None:
    sink = RecordingSink()
    cases = [
        TestCase(
            case_id="loop_20",
            target="awsim",
            case_kind="uturn",
            meta={"global_loop_num": 20},
        )
    ]

    class TaskSource:
        def __init__(self) -> None:
            self.status_updates: list[tuple[str, str]] = []
            self.completions: list[tuple[int, str]] = []

        def fetch_next(self) -> TestCase | None:
            if not cases:
                return None
            return cases.pop(0)

        def update_worker_status(self, worker_id: str, status: str) -> None:
            self.status_updates.append((worker_id, status))

        def report_completion(self, loop_num: int, status: str) -> None:
            self.completions.append((loop_num, status))

    task_source = TaskSource()

    def backend(case: TestCase) -> RawRunResult:
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.TIMEOUT,
            evidence={"trace_json": "/tmp/loop_20.json"},
        )

    def interpreter(raw: RawRunResult) -> EvaluationRecord:
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.TIMEOUT,
            evidence=dict(raw.evidence),
        )

    loop = WorkerLoop(
        backend=backend,
        result_interpreter=interpreter,
        result_sink=sink,
        task_source=task_source,
        context=WorkerLoopContext(worker_id="worker-queue"),
    )

    record = loop.run_once()

    assert record is not None
    assert record.status is RunStatus.TIMEOUT
    assert task_source.status_updates == [
        ("worker-queue", "waiting"),
        ("worker-queue", "running"),
        ("worker-queue", "timeout"),
    ]
    assert task_source.completions == [(20, "timeout")]


def test_worker_loop_heartbeats_while_backend_is_running() -> None:
    sink = RecordingSink()
    case = TestCase(
        case_id="loop_21",
        target="awsim",
        case_kind="uturn",
        meta={"global_loop_num": 21},
    )

    class TaskSource:
        def __init__(self) -> None:
            self.status_updates: list[tuple[str, str]] = []
            self.completions: list[tuple[int, str]] = []

        def fetch_next(self) -> TestCase | None:
            return case

        def update_worker_status(self, worker_id: str, status: str) -> None:
            self.status_updates.append((worker_id, status))

        def report_completion(self, loop_num: int, status: str) -> None:
            self.completions.append((loop_num, status))

    task_source = TaskSource()

    def backend(test_case: TestCase) -> RawRunResult:
        time.sleep(0.05)
        return RawRunResult(
            case_id=test_case.case_id,
            target=test_case.target,
            case_kind=test_case.case_kind,
            status=RunStatus.SUCCESS,
        )

    def interpreter(raw: RawRunResult) -> EvaluationRecord:
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
        )

    loop = WorkerLoop(
        backend=backend,
        result_interpreter=interpreter,
        result_sink=sink,
        task_source=task_source,
        context=WorkerLoopContext(worker_id="worker-queue"),
        heartbeat_interval_sec=0.01,
    )

    record = loop.run_once()

    assert record is not None
    assert task_source.status_updates.count(("worker-queue", "running")) >= 2
    assert task_source.status_updates[-1] == ("worker-queue", "success")
    assert task_source.completions == [(21, "success")]


def test_worker_loop_stops_without_record_when_heartbeat_loses_ray() -> None:
    sink = RecordingSink()
    case = TestCase(
        case_id="loop_22",
        target="awsim",
        case_kind="uturn",
        meta={"global_loop_num": 22},
    )

    class TaskSource:
        def __init__(self) -> None:
            self.status_updates = 0

        def fetch_next(self) -> TestCase | None:
            return case

        def update_worker_status(self, _worker_id: str, _status: str) -> None:
            self.status_updates += 1
            if self.status_updates >= 3:
                raise RuntimeError("Ray Client is not connected")

    def backend(test_case: TestCase) -> RawRunResult:
        time.sleep(0.04)
        return RawRunResult(
            case_id=test_case.case_id,
            target=test_case.target,
            case_kind=test_case.case_kind,
            status=RunStatus.SUCCESS,
        )

    loop = WorkerLoop(
        backend=backend,
        result_interpreter=lambda raw: EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
        ),
        result_sink=sink,
        task_source=TaskSource(),
        context=WorkerLoopContext(worker_id="worker-queue"),
        heartbeat_interval_sec=0.01,
    )

    try:
        loop.run_once()
    except WorkerControlPlaneLost as exc:
        assert "Ray Client is not connected" in str(exc)
    else:
        raise AssertionError("expected heartbeat Ray loss to stop worker")

    assert sink.records == []


def test_worker_loop_execute_appends_history_after_success() -> None:
    class History:
        def __init__(self) -> None:
            self.values: list[int] = []

        def contains(self, loop_num: int) -> bool:
            return False

        def append(self, loop_num: int) -> None:
            self.values.append(loop_num)

    test_case = TestCase(
        case_id="loop_40",
        target="awsim",
        case_kind="uturn",
        meta={"history_loop_num": 40},
    )

    def backend(case: TestCase) -> RawRunResult:
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.SUCCESS,
        )

    def interpreter(raw: RawRunResult) -> EvaluationRecord:
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
        )

    history = History()
    loop = WorkerLoop(
        backend=backend,
        result_interpreter=interpreter,
    )

    outcome = loop.execute(
        direct_test_case=test_case,
        history=history,
    )

    assert outcome.record is not None
    assert outcome.status == "success"
    assert history.values == [40]


def test_worker_loop_execute_until_exit_drains_queue_until_empty() -> None:
    sink = RecordingSink()
    cases = [
        TestCase(case_id="loop_50", target="awsim", case_kind="uturn"),
        TestCase(case_id="loop_51", target="awsim", case_kind="uturn"),
    ]

    class TaskSource:
        def fetch_next(self) -> TestCase | None:
            if not cases:
                return None
            return cases.pop(0)

    def backend(case: TestCase) -> RawRunResult:
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.SUCCESS,
        )

    def interpreter(raw: RawRunResult) -> EvaluationRecord:
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
        )

    loop = WorkerLoop(
        backend=backend,
        result_interpreter=interpreter,
        result_sink=sink,
        task_source=TaskSource(),
    )

    outcome = loop.execute_until_exit()

    assert outcome.terminal_status == "no_task"
    assert outcome.processed_count == 2
    assert [record.case_id for record in outcome.records] == ["loop_50", "loop_51"]


def test_worker_loop_waits_for_temporarily_empty_queue() -> None:
    sink = RecordingSink()
    calls = 0
    now = 100.0
    sleeps: list[float] = []

    class TaskSource:
        def fetch_next(self) -> TestCase | None:
            nonlocal calls
            calls += 1
            if calls < 3:
                return None
            return TestCase(case_id="loop_waited", target="awsim", case_kind="uturn")

    def sleeper(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    def backend(case: TestCase) -> RawRunResult:
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.SUCCESS,
        )

    def interpreter(raw: RawRunResult) -> EvaluationRecord:
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
        )

    loop = WorkerLoop(
        backend=backend,
        result_interpreter=interpreter,
        result_sink=sink,
        task_source=TaskSource(),
        sleeper=sleeper,
        monotonic_fn=lambda: now,
    )

    outcome = loop.execute_until_exit(
        max_cases=1,
        empty_wait_timeout_sec=15.0,
        empty_wait_interval_sec=5.0,
    )

    assert sleeps == [5.0, 5.0]
    assert outcome.terminal_status == "max_cases_reached"
    assert outcome.processed_count == 1
    assert [record.case_id for record in outcome.records] == ["loop_waited"]


def test_worker_loop_execute_until_exit_continues_after_duplicate_skip() -> None:
    sink = RecordingSink()
    cases = [
        TestCase(
            case_id="loop_60",
            target="awsim",
            case_kind="uturn",
            meta={"global_loop_num": 60},
        ),
        TestCase(
            case_id="loop_61",
            target="awsim",
            case_kind="uturn",
            meta={"global_loop_num": 61},
        ),
    ]

    class TaskSource:
        def fetch_next(self) -> TestCase | None:
            if not cases:
                return None
            return cases.pop(0)

    class History:
        def contains(self, loop_num: int) -> bool:
            return loop_num == 60

        def append(self, loop_num: int) -> None:
            return None

    def backend(case: TestCase) -> RawRunResult:
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.SUCCESS,
        )

    def interpreter(raw: RawRunResult) -> EvaluationRecord:
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
        )

    loop = WorkerLoop(
        backend=backend,
        result_interpreter=interpreter,
        result_sink=sink,
        task_source=TaskSource(),
    )

    outcome = loop.execute_until_exit(history=History())

    assert outcome.terminal_status == "no_task"
    assert outcome.skipped_count == 1
    assert outcome.processed_count == 1
    assert [record.case_id for record in outcome.records] == ["loop_61"]


def test_worker_loop_execute_until_exit_requests_refresh_after_interval() -> None:
    sink = RecordingSink()
    cases = [
        TestCase(case_id="loop_70", target="awsim", case_kind="uturn"),
        TestCase(case_id="loop_71", target="awsim", case_kind="uturn"),
    ]

    class TaskSource:
        def fetch_next(self) -> TestCase | None:
            if not cases:
                return None
            return cases.pop(0)

    def backend(case: TestCase) -> RawRunResult:
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.SUCCESS,
        )

    def interpreter(raw: RawRunResult) -> EvaluationRecord:
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
        )

    loop = WorkerLoop(
        backend=backend,
        result_interpreter=interpreter,
        result_sink=sink,
        task_source=TaskSource(),
    )

    outcome = loop.execute_until_exit(refresh_interval=1)

    assert outcome.terminal_status == "refresh_requested"
    assert outcome.reason == "refresh_interval_reached"
    assert outcome.processed_count == 1
    assert [record.case_id for record in outcome.records] == ["loop_70"]
