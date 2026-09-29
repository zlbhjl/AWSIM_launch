import itertools

import pytest

from contracts.evaluation import EvaluationRecord, ensure_evaluation_meta
from contracts.execution import RunStatus, TestCase
import orchestration.orchestrator as orchestrator_module
from orchestration.final_report import build_final_report
from orchestration.orchestrator import (
    Orchestrator,
    OrchestratorConfig,
    build_task_payload,
    build_worker_argv,
)
from runtime.cluster.ray_queue import TaskQueue
from runtime.cluster.result_sink import SharedStoreResultSink


def test_build_task_payload_uses_fixture_stem_by_default() -> None:
    test_case = TestCase(
        case_id="timeout_trace",
        target="awsim",
        case_kind="uturn",
        input={"fixture_path": "/tmp/timeout_trace.txt"},
        tags=["smoke"],
        reason="manual",
    )

    payload = build_task_payload(test_case)

    assert payload["case_id"] == "timeout_trace"
    assert str(payload["fixture_path"]).endswith("timeout_trace.txt")
    assert payload["tags"] == ["smoke"]


def test_build_task_payload_keeps_case_fields_from_test_case() -> None:
    test_case = TestCase(
        case_id="queue_case_7",
        target="awsim",
        case_kind="uturn",
        input={"fixture_path": "/tmp/trace.json", "dx0": 12.5},
        tags=["smoke"],
        reason="boundary_explore",
    )

    payload = build_task_payload(test_case)

    assert payload["case_id"] == "queue_case_7"
    assert payload["dx0"] == 12.5
    assert payload["reason"] == "boundary_explore"


def test_build_worker_argv_forwards_optional_paths() -> None:
    config = OrchestratorConfig(
        fixture="tests/fixtures/awsim/timeout_trace.txt",
        output="/tmp/records.jsonl",
        dataset_csv="/tmp/uturn_dataset.csv",
        history_path="/tmp/processed_loops_history.csv",
        refresh_interval=10,
        headless=True,
    )

    argv = build_worker_argv(config)

    assert "--dataset-csv" in argv
    assert "/tmp/uturn_dataset.csv" in argv
    assert "--history-path" in argv
    assert "/tmp/processed_loops_history.csv" in argv
    assert "--refresh-interval" in argv
    assert "10" in argv
    assert "--headless" in argv


def test_orchestrator_enqueues_one_task_and_returns_summary() -> None:
    queue = TaskQueue()
    captured: dict[str, object] = {}

    def fake_worker_runner(argv, *, task_source):
        captured["argv"] = list(argv)
        test_case = task_source.fetch_next()
        assert test_case is not None
        task_source.update_worker_status("worker-21", "success")
        task_source.report_completion(1, "success")
        return 0

    orchestrator = Orchestrator(queue=queue, worker_runner=fake_worker_runner)
    summary = orchestrator.run(
        OrchestratorConfig(
            fixture="tests/fixtures/awsim/timeout_trace.txt",
            output="/tmp/records.jsonl",
            worker_id="worker-21",
        )
    )

    assert summary["mode"] == "local_queue"
    assert summary["enqueued"] == 1
    assert summary["worker_exit_code"] == 0
    assert summary["queue_size"] == 0
    assert summary["completed_count"] == 1
    assert summary["worker_statuses"] == {"worker-21": "success"}
    assert "--queue-actor-name" in captured["argv"]


def test_orchestrator_includes_maintenance_events_in_summary() -> None:
    queue = TaskQueue()
    emitted = False

    def fake_worker_runner(_argv, *, task_source):
        test_case = task_source.fetch_next()
        assert test_case is not None
        task_source.update_worker_status("worker-21", "success")
        task_source.report_completion(1, "success")
        return 0

    def fake_maintenance(_snapshot, _config):
        nonlocal emitted
        if emitted:
            return []
        emitted = True
        return [{"worker_id": "worker_21", "action": "restart"}]

    orchestrator = Orchestrator(
        queue=queue,
        worker_runner=fake_worker_runner,
        maintenance_callback=fake_maintenance,
    )
    summary = orchestrator.run(
        OrchestratorConfig(
            fixture="tests/fixtures/awsim/timeout_trace.txt",
            output="/tmp/records.jsonl",
            worker_id="worker-21",
        )
    )

    assert summary["maintenance_events"] == [
        {"worker_id": "worker_21", "action": "restart"}
    ]


def test_orchestrator_enqueues_parameter_case_and_preserves_inputs() -> None:
    queue = TaskQueue()
    captured: dict[str, object] = {}

    def fake_worker_runner(argv, *, task_source):
        test_case = task_source.fetch_next()
        assert test_case is not None
        captured["test_case"] = test_case
        task_source.update_worker_status("worker-21", "success")
        task_source.report_completion(1, "success")
        return 0

    orchestrator = Orchestrator(queue=queue, worker_runner=fake_worker_runner)
    summary = orchestrator.run(
        OrchestratorConfig(
            output="/tmp/records.jsonl",
            params={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
            worker_id="worker-21",
            simulation_output_dir="/tmp/awsim-output",
            local_loop_num=3,
        )
    )

    queued_test_case = captured["test_case"]
    assert queued_test_case.input["dx0"] == 15.0
    assert queued_test_case.input["scenario_type"] == "uturn"
    assert queued_test_case.input["output_dir"] == "/tmp/awsim-output"
    assert queued_test_case.input["local_loop_num"] == 3
    assert summary["worker_exit_code"] == 0


def test_orchestrator_applies_resume_state_to_queue_start_counts(tmp_path) -> None:
    queue = TaskQueue()
    resume_from = tmp_path / "resume"
    output_dir = tmp_path / "out"
    resume_from.mkdir()
    output_dir.mkdir()
    (resume_from / "uturn_dataset.csv").write_text(
        "loop_num,status\n4,success\n7,timeout\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_worker_runner(argv, *, task_source):
        test_case = task_source.fetch_next()
        assert test_case is not None
        captured["global_loop_num"] = test_case.meta["global_loop_num"]
        task_source.update_worker_status("worker-21", "success")
        task_source.report_completion(8, "success")
        return 0

    orchestrator = Orchestrator(queue=queue, worker_runner=fake_worker_runner)
    summary = orchestrator.run(
        OrchestratorConfig(
            fixture="tests/fixtures/awsim/timeout_trace.txt",
            output=str(output_dir / "records.jsonl"),
            worker_id="worker-21",
            resume_from=str(resume_from),
        )
    )

    assert captured["global_loop_num"] == 8
    assert summary["last_loop_num"] == 7
    assert summary["next_loop_num"] == 8
    assert str(summary["restored_base_csv"]).endswith("uturn_dataset_base.csv")


def test_orchestrator_restarts_worker_when_refresh_is_requested() -> None:
    queue = TaskQueue()
    captured: dict[str, object] = {"calls": 0}

    def fake_worker_runner(argv, *, task_source):
        captured["calls"] = int(captured["calls"]) + 1
        test_case = task_source.fetch_next()
        assert test_case is not None
        call_num = int(captured["calls"])
        task_source.update_worker_status("worker-21", "success")
        task_source.report_completion(call_num, "success")
        if call_num == 1:
            task_source.actor.add_task(
                {
                    "case_id": "queue_case_refresh_2",
                    "target": "awsim",
                    "case_kind": "uturn",
                    "fixture_path": "/tmp/trace_2.json",
                    "reason": "followup",
                }
            )
            return {
                "exit_code": 0,
                "terminal_status": "refresh_requested",
                "status": "success",
            }
        return {
            "exit_code": 0,
            "terminal_status": "no_task",
            "status": "success",
        }

    orchestrator = Orchestrator(queue=queue, worker_runner=fake_worker_runner)
    summary = orchestrator.run(
        OrchestratorConfig(
            fixture="tests/fixtures/awsim/timeout_trace.txt",
            output="/tmp/records.jsonl",
            worker_id="worker-21",
            refresh_interval=1,
        )
    )

    assert captured["calls"] == 2
    assert summary["worker_exit_code"] == 0
    assert summary["completed_count"] == 2
    assert len(summary["worker_summaries"]) == 2
    assert summary["worker_summaries"][0]["terminal_status"] == "refresh_requested"


def test_orchestrator_runs_multiple_strategy_cases_until_strategy_stops() -> None:
    queue = TaskQueue()
    captured_case_ids: list[str] = []

    class FakeStrategy:
        def __init__(self) -> None:
            self.index = 0

        def next_test_case(self):
            self.index += 1
            if self.index > 2:
                return None
            return TestCase(
                case_id=f"strategy_case_{self.index}",
                target="awsim",
                case_kind="uturn",
                input={"dx0": float(self.index)},
                reason="strategy",
            )

    class StrategyOrchestrator(Orchestrator):
        def _build_strategy(self, config):
            return FakeStrategy()

    def fake_worker_runner(argv, *, task_source):
        test_case = task_source.fetch_next()
        assert test_case is not None
        captured_case_ids.append(test_case.case_id)
        task_source.update_worker_status("worker-21", "success")
        task_source.report_completion(len(captured_case_ids), "success")
        return {
            "exit_code": 0,
            "terminal_status": "no_task",
            "status": "success",
        }

    orchestrator = StrategyOrchestrator(queue=queue, worker_runner=fake_worker_runner)
    summary = orchestrator.run(
        OrchestratorConfig(
            output="/tmp/records.jsonl",
            worker_id="worker-21",
            dataset_csv="/tmp/uturn_dataset.csv",
        )
    )

    assert captured_case_ids == ["strategy_case_1", "strategy_case_2"]
    assert summary["enqueued"] == 2
    assert summary["completed_count"] == 2
    assert len(summary["worker_summaries"]) == 2


def test_orchestrator_does_not_recheck_strategy_after_initial_stop() -> None:
    queue = TaskQueue()

    class FakeStrategy:
        def __init__(self) -> None:
            self.calls = 0

        def next_test_case(self):
            self.calls += 1
            return None

    fake_strategy = FakeStrategy()

    class StrategyOrchestrator(Orchestrator):
        def _build_strategy(self, config):
            return fake_strategy

    orchestrator = StrategyOrchestrator(
        queue=queue,
        worker_runner=lambda *args, **kwargs: 0,
    )
    summary = orchestrator.run(
        OrchestratorConfig(
            output="/tmp/records.jsonl",
            worker_id="worker-21",
            dataset_csv="/tmp/uturn_dataset.csv",
            run_mode="dkw",
        )
    )

    assert fake_strategy.calls == 1
    assert summary["enqueued"] == 0
    assert summary["completed_count"] == 0
    assert summary["stop_reason"] == "Strategist Stop"


def test_orchestrator_limits_strategy_cases_when_max_strategy_cases_is_set() -> None:
    queue = TaskQueue()
    captured_case_ids: list[str] = []

    class FakeStrategy:
        def __init__(self) -> None:
            self.index = 0

        def next_test_case(self):
            self.index += 1
            if self.index > 3:
                return None
            return TestCase(
                case_id=f"strategy_case_{self.index}",
                target="awsim",
                case_kind="uturn",
                input={"dx0": float(self.index)},
                reason="strategy",
            )

    class StrategyOrchestrator(Orchestrator):
        def _build_strategy(self, config):
            return FakeStrategy()

    def fake_worker_runner(argv, *, task_source):
        test_case = task_source.fetch_next()
        assert test_case is not None
        captured_case_ids.append(test_case.case_id)
        task_source.update_worker_status("worker-21", "success")
        task_source.report_completion(len(captured_case_ids), "success")
        return {
            "exit_code": 0,
            "terminal_status": "no_task",
            "status": "success",
        }

    orchestrator = StrategyOrchestrator(queue=queue, worker_runner=fake_worker_runner)
    summary = orchestrator.run(
        OrchestratorConfig(
            output="/tmp/records.jsonl",
            worker_id="worker-21",
            dataset_csv="/tmp/uturn_dataset.csv",
            max_strategy_cases=1,
        )
    )

    assert captured_case_ids == ["strategy_case_1"]
    assert summary["enqueued"] == 1
    assert summary["completed_count"] == 1


def test_orchestrator_builds_active_strategy_with_runtime_dataset_paths(
    monkeypatch,
    tmp_path,
) -> None:
    captured: dict[str, object] = {}

    class FakeStrategist:
        def __init__(self, scenario_name, config, **kwargs):
            captured["scenario_name"] = scenario_name
            captured["config"] = config
            captured["kwargs"] = kwargs

    monkeypatch.setattr(orchestrator_module, "ActiveLearningStrategist", FakeStrategist)
    monkeypatch.setattr(orchestrator_module.importlib, "import_module", lambda name: object())

    dataset_csv = tmp_path / "custom_dataset.csv"
    orchestrator = Orchestrator()
    strategy = orchestrator._build_strategy(
        OrchestratorConfig(
            output=str(tmp_path / "records.jsonl"),
            dataset_csv=str(dataset_csv),
            run_mode="dkw_fixed",
            focus_points=[{"dx0": 15.0}],
            config_module="targets.awsim.case_kinds.uturn",
            dkw_bounds={"dx0": [15.0, 20.0]},
            dkw_region="intersect_safe",
            dkw_pure_smc=True,
            dkw_simultaneous=True,
            max_samples=12,
            binomial_target="c_ttc_1.1",
            binomial_method="clopper-pearson",
            binomial_confidence=0.9,
            binomial_target_width=0.05,
            binomial_min_samples=40,
        )
    )

    assert isinstance(strategy, FakeStrategist)
    assert captured["scenario_name"] == "uturn"
    kwargs = captured["kwargs"]
    repository = kwargs["dataset_repository"]
    history_repository = kwargs["statistical_history_repository"]
    assert kwargs["run_mode"] == "dkw_fixed"
    assert kwargs["focus_points"] == [{"dx0": 15.0}]
    assert kwargs["dkw_bounds"] == {"dx0": [15.0, 20.0]}
    assert kwargs["dkw_region"] == "intersect_safe"
    assert kwargs["dkw_pure_smc"] is True
    assert kwargs["dkw_simultaneous"] is True
    assert kwargs["max_samples"] == 12
    assert kwargs["binomial_target"] == "c_ttc_1.1"
    assert kwargs["binomial_method"] == "clopper-pearson"
    assert kwargs["binomial_confidence"] == 0.9
    assert kwargs["binomial_target_width"] == 0.05
    assert kwargs["binomial_min_samples"] == 40
    assert str(repository.dataset_csv) == str(dataset_csv.resolve())
    assert str(repository.base_csv).endswith("custom_dataset_base.csv")
    assert str(history_repository.traces_dir) == str(dataset_csv.resolve().parent)


def test_orchestrator_manages_external_workers_with_queue_watermarks() -> None:
    queue = TaskQueue()
    captured_case_ids: list[str] = []

    class FakeStrategy:
        def __init__(self) -> None:
            self.index = 0

        def next_test_case(self):
            self.index += 1
            if self.index > 3:
                return None
            return TestCase(
                case_id=f"strategy_case_{self.index}",
                target="awsim",
                case_kind="uturn",
                input={"dx0": float(self.index)},
                reason="strategy",
            )

    class StrategyOrchestrator(Orchestrator):
        def _build_strategy(self, config):
            return FakeStrategy()

    def fake_sleeper(_seconds: float) -> None:
        payload = queue.get_next_task()
        if payload is None or payload.get("system_command") == "stop":
            return
        captured_case_ids.append(str(payload["case_id"]))
        queue.update_worker_status("worker-remote", "success")
        queue.report_completion(int(payload["global_loop_num"]), "success")

    orchestrator = StrategyOrchestrator(
        queue=queue,
        worker_runner=lambda *args, **kwargs: 0,
        sleeper=fake_sleeper,
    )
    summary = orchestrator.run(
        OrchestratorConfig(
            output="/tmp/records.jsonl",
            worker_id="worker-remote",
            dataset_csv="/tmp/uturn_dataset.csv",
            run_inline_worker=False,
            worker_count=1,
            queue_high_water=2,
            queue_low_water=1,
            poll_interval_sec=0.0,
        )
    )

    assert captured_case_ids == [
        "strategy_case_1",
        "strategy_case_2",
        "strategy_case_3",
    ]
    assert summary["mode"] == "cluster_queue"
    assert summary["completed_count"] == 3
    assert summary["queue_size"] == 0
    assert summary["stop_reason"] == "Strategist Stop"


def test_orchestrator_finishes_when_stop_decided_but_all_workers_have_died() -> None:
    """Regression test: if every worker exits before draining the pending
    queue, nothing else will ever pop the leftover tasks. The orchestrator
    must eventually cancel them and finish once the strategist has decided
    no more samples are needed, instead of polling forever."""
    queue = TaskQueue()

    class FakeStrategy:
        def __init__(self) -> None:
            self.index = 0

        def next_test_case(self):
            self.index += 1
            if self.index > 1:
                return None
            return TestCase(
                case_id="strategy_case_1",
                target="awsim",
                case_kind="uturn",
                input={"dx0": 1.0},
                reason="strategy",
            )

    class StrategyOrchestrator(Orchestrator):
        def _build_strategy(self, config):
            return FakeStrategy()

    sleeper_calls = 0
    fake_now = 0.0

    def dead_worker_sleeper(_seconds: float) -> None:
        # Simulates every worker having already exited: nobody ever polls
        # get_next_task(), so the queue is never drained by anyone. Advance
        # the fake clock well past a single poll interval each call so the
        # test doesn't need to wait out the real _STALLED_DRAIN_TIMEOUT_SEC.
        nonlocal sleeper_calls, fake_now
        sleeper_calls += 1
        fake_now += 120.0
        assert sleeper_calls < 50, "orchestrator did not finish; queue never drained"

    orchestrator = StrategyOrchestrator(
        queue=queue,
        worker_runner=lambda *args, **kwargs: 0,
        sleeper=dead_worker_sleeper,
        time_func=lambda: fake_now,
    )
    summary = orchestrator.run(
        OrchestratorConfig(
            output="/tmp/records.jsonl",
            worker_id="worker-remote",
            dataset_csv="/tmp/uturn_dataset.csv",
            run_inline_worker=False,
            worker_count=1,
            queue_high_water=2,
            queue_low_water=1,
            poll_interval_sec=0.0,
        )
    )

    assert summary["queue_size"] == 0
    assert summary["stop_reason"] == "Strategist Stop"
    # The orphaned task was cancelled, not silently counted as completed.
    assert summary["completed_count"] == 0


def test_orchestrator_does_not_cancel_task_a_busy_worker_still_picks_up() -> None:
    """A worker can be alive but simply mid-simulation for a while, not
    polling for new work. As long as it comes back and drains the queue
    before _STALLED_DRAIN_TIMEOUT_SEC has elapsed, its pending task must not
    be cancelled out from under it."""
    queue = TaskQueue()

    class FakeStrategy:
        def __init__(self) -> None:
            self.index = 0

        def next_test_case(self):
            self.index += 1
            if self.index > 1:
                return None
            return TestCase(
                case_id="strategy_case_1",
                target="awsim",
                case_kind="uturn",
                input={"dx0": 1.0},
                reason="strategy",
            )

    class StrategyOrchestrator(Orchestrator):
        def _build_strategy(self, config):
            return FakeStrategy()

    sleeper_calls = 0
    fake_now = 0.0

    def busy_then_returning_worker_sleeper(_seconds: float) -> None:
        # The worker is alive but busy (e.g. running a long simulation) for
        # several polls, well under the abandonment timeout, before it comes
        # back and claims the still-pending task itself.
        nonlocal sleeper_calls, fake_now
        sleeper_calls += 1
        fake_now += 5.0
        if sleeper_calls >= 10:
            payload = queue.get_next_task()
            if payload and payload.get("system_command") != "stop":
                queue.report_completion(int(payload["global_loop_num"]), "success")

    orchestrator = StrategyOrchestrator(
        queue=queue,
        worker_runner=lambda *args, **kwargs: 0,
        sleeper=busy_then_returning_worker_sleeper,
        time_func=lambda: fake_now,
    )
    summary = orchestrator.run(
        OrchestratorConfig(
            output="/tmp/records.jsonl",
            worker_id="worker-remote",
            dataset_csv="/tmp/uturn_dataset.csv",
            run_inline_worker=False,
            worker_count=1,
            queue_high_water=2,
            queue_low_water=1,
            poll_interval_sec=0.0,
        )
    )

    assert summary["queue_size"] == 0
    assert summary["completed_count"] == 1


def test_orchestrator_builds_active_strategy_with_legacy_cache_policy(
    monkeypatch,
    tmp_path,
) -> None:
    captured: dict[str, object] = {}

    class FakeStrategist:
        def __init__(self, scenario_name, config, **kwargs):
            captured["kwargs"] = kwargs

    monkeypatch.setattr(orchestrator_module, "ActiveLearningStrategist", FakeStrategist)
    monkeypatch.setattr(orchestrator_module.importlib, "import_module", lambda name: object())

    orchestrator = Orchestrator()
    strategy = orchestrator._build_strategy(
        OrchestratorConfig(
            output=str(tmp_path / "records.jsonl"),
            dataset_csv=str(tmp_path / "uturn_dataset.csv"),
            worker_count=3,
        )
    )

    assert isinstance(strategy, FakeStrategist)
    assert captured["kwargs"]["cache_size"] == 6


def test_orchestrator_dkw_and_verify_consistency_do_not_use_repeat_count_target_total(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        orchestrator_module.importlib,
        "import_module",
        lambda _name: type("Cfg", (), {"REPEAT_COUNT": 1000})(),
    )

    orchestrator = Orchestrator()

    assert (
        orchestrator._resolve_target_total(
            OrchestratorConfig(
                output="/tmp/records.jsonl",
                dataset_csv="/tmp/uturn_dataset.csv",
                run_mode="dkw",
            ),
            last_loop_num=7,
        )
        is None
    )
    assert (
        orchestrator._resolve_target_total(
            OrchestratorConfig(
                output="/tmp/records.jsonl",
                dataset_csv="/tmp/uturn_dataset.csv",
                run_mode="verify_consistency",
                max_samples=50,
            ),
            last_loop_num=7,
        )
        is None
    )


def test_orchestrator_dkw_fixed_still_uses_sampling_cap_as_target_total() -> None:
    orchestrator = Orchestrator()

    assert orchestrator._resolve_target_total(
        OrchestratorConfig(
            output="/tmp/records.jsonl",
            dataset_csv="/tmp/uturn_dataset.csv",
            run_mode="dkw_fixed",
            max_samples=12,
        ),
        last_loop_num=4,
    ) == 16


def test_orchestrator_prism_sprt_and_ebstop_do_not_stop_after_one_task() -> None:
    # Found by running the real PRISM/Maude binaries end to end: without this,
    # target=prism with --param falls through to "last_loop_num + 1", so the
    # run stops as soon as the single exact_model_check task completes and
    # never issues any sample-path TestCases at all.
    orchestrator = Orchestrator()

    for run_mode in ("binomial_ci", "sprt", "ebstop", "dkw", "dkw_fixed"):
        assert (
            orchestrator._resolve_target_total(
                OrchestratorConfig(
                    output="/tmp/records.jsonl",
                    dataset_csv="/tmp/prism.csv",
                    target="prism",
                    run_mode=run_mode,
                    params={"model": "simple_reliability_dtmc", "steps": 20},
                    max_samples=8,
                ),
                last_loop_num=0,
            )
            is None
        )


def test_orchestrator_summary_includes_strategy_final_report() -> None:
    queue = TaskQueue()

    class FakeStrategy:
        def __init__(self) -> None:
            self.index = 0
            self.latest_final_report = None

        def next_test_case(self):
            self.index += 1
            if self.index > 1:
                self.latest_final_report = build_final_report(
                    num_samples=1,
                    target="Binomial CI",
                    reason="Binomial CI Complete: c_collision CI width 0.01000",
                )
                return None
            return TestCase(
                case_id="strategy_case_1",
                target="awsim",
                case_kind="uturn",
                input={"dx0": 1.0},
                reason="strategy",
            )

    class StrategyOrchestrator(Orchestrator):
        def _build_strategy(self, config):
            return FakeStrategy()

    def fake_worker_runner(argv, *, task_source):
        test_case = task_source.fetch_next()
        assert test_case is not None
        task_source.update_worker_status("worker-21", "success")
        task_source.report_completion(1, "success")
        return {
            "exit_code": 0,
            "terminal_status": "no_task",
            "status": "success",
        }

    orchestrator = StrategyOrchestrator(queue=queue, worker_runner=fake_worker_runner)
    summary = orchestrator.run(
        OrchestratorConfig(
            output="/tmp/records.jsonl",
            worker_id="worker-21",
            dataset_csv="/tmp/uturn_dataset.csv",
        )
    )

    assert summary["final_report"]["target"] == "Binomial CI"
    assert "Binomial CI Complete" in summary["final_report"]["reason"]


def test_build_strategy_threads_binomial_anytime_valid_into_prism_statistical_request() -> None:
    config = OrchestratorConfig(
        output="/tmp/prism.jsonl",
        dataset_csv="/tmp/prism.csv",
        target="prism",
        case_kind="simple_reliability_dtmc",
        run_mode="binomial_ci",
        params={"model": "simple_reliability_dtmc", "steps": 20},
        max_samples=20,
        binomial_anytime_valid=True,
    )

    strategy = Orchestrator()._build_strategy(config)

    assert strategy.statistical_request.options["anytime_valid"] is True


def test_build_strategy_binomial_anytime_valid_defaults_to_false_for_prism() -> None:
    config = OrchestratorConfig(
        output="/tmp/prism.jsonl",
        dataset_csv="/tmp/prism.csv",
        target="prism",
        case_kind="simple_reliability_dtmc",
        run_mode="binomial_ci",
        params={"model": "simple_reliability_dtmc", "steps": 20},
        max_samples=20,
    )

    strategy = Orchestrator()._build_strategy(config)

    assert strategy.statistical_request.options["anytime_valid"] is False


def test_build_strategy_rejects_prism_boundary_gap_instead_of_silent_fallthrough() -> None:
    # boundary_gap has no live branch in _build_strategy for target=prism; without
    # this guard it would silently fall through to a single one-off
    # ParameterCaseStrategy with no statistical evaluation at all instead of
    # failing loudly.
    config = OrchestratorConfig(
        output="/tmp/prism.jsonl",
        dataset_csv="/tmp/prism.csv",
        target="prism",
        case_kind="simple_reliability_dtmc",
        run_mode="boundary_gap",
        params={"model": "simple_reliability_dtmc", "steps": 20},
        max_samples=20,
    )

    with pytest.raises(ValueError, match="no live execution path"):
        Orchestrator()._build_strategy(config)


def test_orchestrator_runs_prism_fixed_sampling_until_ci_is_sufficient(
    tmp_path,
) -> None:
    queue = TaskQueue()
    dataset_csv = tmp_path / "prism_samples.csv"
    result_sink = SharedStoreResultSink.from_dataset_csv(dataset_csv)

    def fake_worker_runner(_argv, *, task_source):
        test_case = task_source.fetch_next()
        assert test_case is not None
        loop_num = int(test_case.meta["global_loop_num"])
        is_model_check = test_case.input.get("record_kind") == "exact_model_check"
        result_sink.save(
            EvaluationRecord(
                case_id=test_case.case_id,
                target="prism",
                case_kind=test_case.case_kind,
                status=RunStatus.SUCCESS,
                input=dict(test_case.input),
                output=(
                    {
                        "prism_eventual_failure_probability": 1.0,
                        "prism_bounded_failure_probability": 0.1,
                    }
                    if is_model_check
                    else {"c_failure": 0}
                ),
                meta=ensure_evaluation_meta(
                    {
                        "global_loop_num": loop_num,
                        "task_reason": test_case.reason,
                    },
                    source_module="tests.fake_prism_worker",
                ),
            )
        )
        task_source.report_completion(loop_num, "success")
        return {
            "exit_code": 0,
            "terminal_status": "no_task",
            "status": "success",
        }

    summary = Orchestrator(
        queue=queue,
        worker_runner=fake_worker_runner,
    ).run(
        OrchestratorConfig(
            output=str(tmp_path / "records.jsonl"),
            dataset_csv=str(dataset_csv),
            target="prism",
            case_kind="simple_reliability_dtmc",
            run_mode="binomial_ci",
            params={"model": "simple_reliability_dtmc", "steps": 20},
            experiment_id="phase4-test",
            max_samples=20,
            binomial_target="c_failure",
            binomial_min_samples=10,
            binomial_target_width=0.3,
            queue_high_water=1,
            queue_low_water=0,
        )
    )

    assert summary["enqueued"] == 11
    assert summary["completed_count"] == 11
    assert "interval sufficient" in summary["stop_reason"]
    assert summary["statistical_report"]["sample_count"] == 10
    assert summary["statistical_report"]["sufficient"] is True
    assert summary["statistical_report"]["exact_model_check"] == {
        "eventual_failure_probability": 1.0,
        "bounded_failure_probability": 0.1,
        "comparison_metric": "c_failure",
        "ci_contains_bounded_probability": True,
        "absolute_estimation_error": 0.1,
    }


def test_orchestrator_runs_prism_dkw_sequential_from_a_cold_start(tmp_path) -> None:
    # DKWModeRunner.handle_sequential evaluates *before* dispatching a new
    # sample task. With zero prior samples that evaluation fails outright and
    # handle_sequential treats it as a terminal stop, so a genuinely fresh
    # PRISM run (no pre-existing dataset, unlike AWSIM's usual dkw usage)
    # would stop right after the exact_model_check without ever issuing a
    # single sample-path task. This regression test pins the fix: at least
    # one real sample-path task must be dispatched and evaluated.
    queue = TaskQueue()
    dataset_csv = tmp_path / "prism_dkw_samples.csv"
    result_sink = SharedStoreResultSink.from_dataset_csv(dataset_csv)

    def fake_worker_runner(_argv, *, task_source):
        test_case = task_source.fetch_next()
        assert test_case is not None
        loop_num = int(test_case.meta["global_loop_num"])
        is_model_check = test_case.input.get("record_kind") == "exact_model_check"
        result_sink.save(
            EvaluationRecord(
                case_id=test_case.case_id,
                target="prism",
                case_kind=test_case.case_kind,
                status=RunStatus.SUCCESS,
                input=dict(test_case.input),
                output=(
                    {
                        "prism_eventual_failure_probability": 1.0,
                        "prism_bounded_failure_probability": 0.1,
                    }
                    if is_model_check
                    else {"steps_to_failure_capped": 12.0}
                ),
                meta=ensure_evaluation_meta(
                    {
                        "global_loop_num": loop_num,
                        "task_reason": test_case.reason,
                    },
                    source_module="tests.fake_prism_worker",
                ),
            )
        )
        task_source.report_completion(loop_num, "success")
        return {
            "exit_code": 0,
            "terminal_status": "no_task",
            "status": "success",
        }

    summary = Orchestrator(
        queue=queue,
        worker_runner=fake_worker_runner,
    ).run(
        OrchestratorConfig(
            output=str(tmp_path / "records.jsonl"),
            dataset_csv=str(dataset_csv),
            target="prism",
            case_kind="simple_reliability_dtmc",
            run_mode="dkw",
            params={"model": "simple_reliability_dtmc", "steps": 20},
            experiment_id="dkw-cold-start-test",
            max_samples=200,
            prism_dkw_target_metric="steps_to_failure_capped",
            prism_dkw_target_epsilon=0.3,
            queue_high_water=1,
            queue_low_water=0,
        )
    )

    assert summary["enqueued"] >= 2  # model check + at least one sample-path task
    assert summary["completed_count"] == summary["enqueued"]
    assert "SMC Verification Complete" in summary["stop_reason"]
    # n=1 is a degenerate case for the DKW quantile search (evaluation/dkw.py
    # rejects it as "not enough data yet"), so at least 2 real samples must
    # have been evaluated before a "sufficient" report is possible.
    assert summary["statistical_report"]["sample_count"] >= 2
    assert summary["statistical_report"]["metric"] == "steps_to_failure_capped"
    assert summary["statistical_report"]["exact_model_check"]["comparison_skipped_reason"]


def test_orchestrator_runs_prism_dkw_sequential_keeps_collecting_with_real_variance(
    tmp_path,
) -> None:
    # With genuinely varying values (unlike the constant-value cold-start test
    # above, which trivially converges at n=2 because the data has zero
    # variance), sequential dkw should keep dispatching sample-path tasks
    # past the first couple of samples instead of stopping prematurely.
    queue = TaskQueue()
    dataset_csv = tmp_path / "prism_dkw_variance_samples.csv"
    result_sink = SharedStoreResultSink.from_dataset_csv(dataset_csv)
    values = itertools.cycle([1.0, 20.0, 3.0, 18.0, 5.0, 15.0])

    def fake_worker_runner(_argv, *, task_source):
        test_case = task_source.fetch_next()
        assert test_case is not None
        loop_num = int(test_case.meta["global_loop_num"])
        is_model_check = test_case.input.get("record_kind") == "exact_model_check"
        result_sink.save(
            EvaluationRecord(
                case_id=test_case.case_id,
                target="prism",
                case_kind=test_case.case_kind,
                status=RunStatus.SUCCESS,
                input=dict(test_case.input),
                output=(
                    {
                        "prism_eventual_failure_probability": 1.0,
                        "prism_bounded_failure_probability": 0.1,
                    }
                    if is_model_check
                    else {"steps_to_failure_capped": next(values)}
                ),
                meta=ensure_evaluation_meta(
                    {
                        "global_loop_num": loop_num,
                        "task_reason": test_case.reason,
                    },
                    source_module="tests.fake_prism_worker",
                ),
            )
        )
        task_source.report_completion(loop_num, "success")
        return {
            "exit_code": 0,
            "terminal_status": "no_task",
            "status": "success",
        }

    summary = Orchestrator(
        queue=queue,
        worker_runner=fake_worker_runner,
    ).run(
        OrchestratorConfig(
            output=str(tmp_path / "records.jsonl"),
            dataset_csv=str(dataset_csv),
            target="prism",
            case_kind="simple_reliability_dtmc",
            run_mode="dkw",
            params={"model": "simple_reliability_dtmc", "steps": 20},
            experiment_id="dkw-variance-test",
            max_samples=200,
            prism_dkw_target_metric="steps_to_failure_capped",
            prism_dkw_target_epsilon=10.0,
            queue_high_water=1,
            queue_low_water=0,
        )
    )

    assert summary["statistical_report"]["sample_count"] > 2
    assert summary["statistical_report"]["sufficient"] is True
    assert "SMC Verification Complete" in summary["stop_reason"]


def test_orchestrator_runs_prism_dkw_sequential_stops_at_max_samples_without_converging(
    tmp_path,
) -> None:
    # DKWModeRunner.handle_sequential previously never checked max_samples at
    # all, so an unreachable target_epsilon would make it dispatch real
    # sample-path tasks forever. This pins the fix: it must stop cleanly once
    # dispatched_task_count reaches max_samples, without ever converging.
    queue = TaskQueue()
    dataset_csv = tmp_path / "prism_dkw_unreachable_samples.csv"
    result_sink = SharedStoreResultSink.from_dataset_csv(dataset_csv)
    values = itertools.cycle([1.0, 20.0, 3.0, 18.0, 5.0, 15.0])

    def fake_worker_runner(_argv, *, task_source):
        test_case = task_source.fetch_next()
        assert test_case is not None
        loop_num = int(test_case.meta["global_loop_num"])
        is_model_check = test_case.input.get("record_kind") == "exact_model_check"
        result_sink.save(
            EvaluationRecord(
                case_id=test_case.case_id,
                target="prism",
                case_kind=test_case.case_kind,
                status=RunStatus.SUCCESS,
                input=dict(test_case.input),
                output=(
                    {
                        "prism_eventual_failure_probability": 1.0,
                        "prism_bounded_failure_probability": 0.1,
                    }
                    if is_model_check
                    else {"steps_to_failure_capped": next(values)}
                ),
                meta=ensure_evaluation_meta(
                    {
                        "global_loop_num": loop_num,
                        "task_reason": test_case.reason,
                    },
                    source_module="tests.fake_prism_worker",
                ),
            )
        )
        task_source.report_completion(loop_num, "success")
        return {
            "exit_code": 0,
            "terminal_status": "no_task",
            "status": "success",
        }

    summary = Orchestrator(
        queue=queue,
        worker_runner=fake_worker_runner,
    ).run(
        OrchestratorConfig(
            output=str(tmp_path / "records.jsonl"),
            dataset_csv=str(dataset_csv),
            target="prism",
            case_kind="simple_reliability_dtmc",
            run_mode="dkw",
            params={"model": "simple_reliability_dtmc", "steps": 20},
            experiment_id="dkw-unreachable-test",
            max_samples=15,
            prism_dkw_target_metric="steps_to_failure_capped",
            prism_dkw_target_epsilon=1e-9,
            queue_high_water=1,
            queue_low_water=0,
        )
    )

    assert "max_samples=15" in summary["stop_reason"]
    assert "without converging" in summary["stop_reason"]
    assert summary["statistical_report"]["sufficient"] is False
    assert summary["statistical_report"]["sample_count"] <= 15


def test_orchestrator_runs_prism_dkw_fixed_dispatches_all_samples_then_evaluates_once(
    tmp_path,
) -> None:
    queue = TaskQueue()
    dataset_csv = tmp_path / "prism_dkw_fixed_samples.csv"
    result_sink = SharedStoreResultSink.from_dataset_csv(dataset_csv)
    values = iter([5.0, 10.0, 15.0, 20.0, 25.0])

    def fake_worker_runner(_argv, *, task_source):
        test_case = task_source.fetch_next()
        assert test_case is not None
        loop_num = int(test_case.meta["global_loop_num"])
        is_model_check = test_case.input.get("record_kind") == "exact_model_check"
        result_sink.save(
            EvaluationRecord(
                case_id=test_case.case_id,
                target="prism",
                case_kind=test_case.case_kind,
                status=RunStatus.SUCCESS,
                input=dict(test_case.input),
                output=(
                    {
                        "prism_eventual_failure_probability": 1.0,
                        "prism_bounded_failure_probability": 0.1,
                    }
                    if is_model_check
                    else {"steps_to_failure_capped": next(values)}
                ),
                meta=ensure_evaluation_meta(
                    {
                        "global_loop_num": loop_num,
                        "task_reason": test_case.reason,
                    },
                    source_module="tests.fake_prism_worker",
                ),
            )
        )
        task_source.report_completion(loop_num, "success")
        return {
            "exit_code": 0,
            "terminal_status": "no_task",
            "status": "success",
        }

    summary = Orchestrator(
        queue=queue,
        worker_runner=fake_worker_runner,
    ).run(
        OrchestratorConfig(
            output=str(tmp_path / "records.jsonl"),
            dataset_csv=str(dataset_csv),
            target="prism",
            case_kind="simple_reliability_dtmc",
            run_mode="dkw_fixed",
            params={"model": "simple_reliability_dtmc", "steps": 20},
            experiment_id="dkw-fixed-test",
            max_samples=5,
            prism_dkw_target_metric="steps_to_failure_capped",
            prism_dkw_target_epsilon=100.0,
            queue_high_water=1,
            queue_low_water=0,
        )
    )

    assert summary["enqueued"] == 6  # model check + exactly max_samples sample-path tasks
    assert summary["completed_count"] == 6
    assert "Fixed Sampling + DKW Complete" in summary["stop_reason"]
    assert summary["statistical_report"]["sample_count"] == 5
