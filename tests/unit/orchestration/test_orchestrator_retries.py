"""The orchestrator configures queue retries and tells sampling strategies about lost samples."""

from __future__ import annotations

from types import SimpleNamespace

from orchestration.orchestrator import Orchestrator, OrchestratorConfig
from runtime.cluster.ray_queue import TaskQueue
from runtime.cluster.task_queue_gateway import TaskQueueGateway


def _orchestrator(queue: TaskQueue) -> Orchestrator:
    return Orchestrator(queue_gateway=TaskQueueGateway(actor=queue))


def test_external_awsim_runs_use_the_target_default_case_timeout() -> None:
    queue = TaskQueue()
    _orchestrator(queue)._configure_retries(
        OrchestratorConfig(output="o.jsonl", target="awsim", case_kind="uturn", run_inline_worker=False))
    assert queue.max_attempts == 3 and queue.case_timeout_sec == 410.0


def test_inline_runs_have_no_case_timeout_and_fixtures_are_not_retried() -> None:
    queue = TaskQueue()
    _orchestrator(queue)._configure_retries(OrchestratorConfig(output="o.jsonl", target="bernoulli"))
    assert queue.max_attempts == 3 and queue.case_timeout_sec is None
    _orchestrator(queue)._configure_retries(OrchestratorConfig(output="o.jsonl", fixture="trace.json"))
    assert queue.max_attempts == 1


def test_the_strategy_learns_its_attempts_and_lost_samples() -> None:
    strategy = SimpleNamespace(abandoned_sample_indexes=set(), max_attempts=1, experiment_id="e1")
    snapshot = {"abandoned_tasks": [{"experiment_id": "e1", "sample_index": 4},
                                    {"experiment_id": "other", "sample_index": 5}]}
    Orchestrator._sync_strategy_retries(strategy, OrchestratorConfig(output="o.jsonl", max_attempts=3), snapshot)
    assert strategy.max_attempts == 3 and strategy.abandoned_sample_indexes == {4}
