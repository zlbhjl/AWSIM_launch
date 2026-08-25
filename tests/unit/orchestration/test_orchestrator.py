from contracts.execution import TestCase
import orchestration.orchestrator as orchestrator_module
from orchestration.final_report import build_final_report
from orchestration.orchestrator import (
    Orchestrator,
    OrchestratorConfig,
    build_task_payload,
    build_worker_argv,
)
from runtime.cluster.ray_queue import TaskQueue


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
