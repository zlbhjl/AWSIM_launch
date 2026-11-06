"""The cluster executor gives the same results as a local run (docs/cluster_importance_sampling_plan.md §4).

No Ray: an in-process TaskQueue and fake workers that take several cases at a
time, finish them in a shuffled order, and can drop or repeat a case.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from orchestration.importance.cluster_executor import CaseLost, ClusterExecutor, JsonlRecordFeed
from orchestration.importance.runner import ImportanceRunConfig, ImportanceSamplingRunner
from orchestration.worker_loop import WorkerLoop
from runtime.cluster.ray_queue import TaskQueue
from runtime.cluster.result_sink import serialize_evaluation_record
from runtime.cluster.task_queue_gateway import TaskQueueGateway
from runtime.repository.importance_sampling_results import ImportanceSamplingResultsRepository
from targets.dynamics.backend import DynamicsBackend, DynamicsBackendConfig
from targets.dynamics.importance_sampling import DynamicsImportanceSampling
from targets.dynamics.result_interpreter import DynamicsResultInterpreter

PLAN = Path("configs/is_final/ou_a_3sd_two_sided_q1d.yaml")  # streams policy, defensive mixture


class FakeCluster:
    """Workers driven by the executor's sleep: each tick they take a few cases and finish them shuffled."""

    def __init__(self, tmp_path: Path, *, seed: int = 0, batch: int = 3, drop_first: set[str] = frozenset(),
                 repeat: set[str] = frozenset(), drop_always: set[str] = frozenset()):
        self.queue = TaskQueue()
        self.gateway = TaskQueueGateway(actor=self.queue)
        self.records_path = tmp_path / "cluster" / "records.jsonl"
        self.records_path.parent.mkdir(parents=True, exist_ok=True)
        self.rng = random.Random(seed)
        self.batch = batch
        self.drop_first, self.repeat, self.drop_always = set(drop_first), set(repeat), set(drop_always)
        self.now = 0.0
        self.loop = WorkerLoop(
            backend=DynamicsBackend(DynamicsBackendConfig(output_root=tmp_path / "artifacts", artifact_policy="summary")),
            result_interpreter=DynamicsResultInterpreter(),
            result_sink=self._write,
        )

    def _write(self, record) -> None:
        with self.records_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(serialize_evaluation_record(record)) + "\n")

    def tick(self, seconds: float) -> None:
        self.now += seconds
        taken = []
        for _ in range(self.batch):
            case = self.gateway.fetch_next()
            if case is None:
                break
            taken.append(case)
        self.rng.shuffle(taken)
        for case in taken:
            if case.case_id in self.drop_always or case.case_id in self.drop_first:
                self.drop_first.discard(case.case_id)
                continue  # the worker died: the case stays in flight, no record
            record = self.loop.run_once(case)
            if case.case_id in self.repeat:
                self._write(record)
            self.queue.report_completion(case.meta["global_loop_num"], record.status.value)

    def executor(self, tmp_path: Path, **options) -> ClusterExecutor:
        return ClusterExecutor(queue=self.gateway, feed=JsonlRecordFeed(self.records_path),
                               side_root=tmp_path / "side", max_in_flight=5, poll_interval_sec=1.0,
                               clock=lambda: self.now, sleep=self.tick, **options)


def _local(tmp_path: Path, **config) -> dict:
    runner = ImportanceSamplingRunner(
        backend=DynamicsBackend(DynamicsBackendConfig(output_root=tmp_path / "la", artifact_policy="summary")),
        result_interpreter=DynamicsResultInterpreter(), hooks=DynamicsImportanceSampling(),
    )
    return runner.run(ImportanceRunConfig(sampling_plan_path=PLAN, output_root=tmp_path / "local", **config))


def _cluster(tmp_path: Path, cluster: FakeCluster, **config) -> dict:
    runner = ImportanceSamplingRunner(hooks=DynamicsImportanceSampling(), executor=cluster.executor(tmp_path / "c"))
    return runner.run(ImportanceRunConfig(sampling_plan_path=PLAN, output_root=tmp_path / "cluster_run", **config))


def _values(root: Path) -> dict[str, list]:
    repository = ImportanceSamplingResultsRepository(root)
    return {kind: [(r.case_id, r.output, r.meta["sampling"]) for r in repository.load_records(kind)]
            for kind in ("ordinary", "importance")}


def test_fixed_n_on_the_cluster_equals_a_local_run(tmp_path: Path) -> None:
    local = _local(tmp_path, max_samples=12, root_entropy=5)["comparison"]
    clustered = _cluster(tmp_path, FakeCluster(tmp_path, seed=3), max_samples=12, root_entropy=5)["comparison"]

    assert clustered["final_status"] == "complete"
    for key in ("diagnostic", "guarantee", "audit"):
        assert clustered[key] == local[key]
    assert _values(tmp_path / "cluster_run") == _values(tmp_path / "local")


def test_a_lost_case_is_submitted_again_and_extra_records_are_kept_aside(tmp_path: Path) -> None:
    lost = "ou_a_3sd_two_sided__q1d_b0.2__final-importance-000002"
    repeated = "ou_a_3sd_two_sided__q1d_b0.2__final-ordinary-000003"
    cluster = FakeCluster(tmp_path, seed=1, drop_first={lost}, repeat={repeated})
    local = _local(tmp_path, max_samples=6, root_entropy=8)["comparison"]

    runner = ImportanceSamplingRunner(hooks=DynamicsImportanceSampling(),
                                      executor=cluster.executor(tmp_path / "c", case_timeout_sec=5.0))
    clustered = runner.run(ImportanceRunConfig(sampling_plan_path=PLAN, output_root=tmp_path / "cluster_run",
                                               max_samples=6, root_entropy=8))["comparison"]

    assert clustered["diagnostic"] == local["diagnostic"]
    importance = ImportanceSamplingResultsRepository(tmp_path / "cluster_run").load_records("importance")
    assert importance[1].case_id == f"{lost}-retry1" and importance[1].meta["attempt_id"] == 1
    duplicates = (tmp_path / "c" / "side" / "duplicates.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["case_id"] for line in duplicates] == [repeated]


def test_a_case_lost_on_every_attempt_leaves_the_run_incomplete(tmp_path: Path) -> None:
    lost = "ou_a_3sd_two_sided__q1d_b0.2__final-ordinary-000002"
    cluster = FakeCluster(tmp_path, drop_always={lost, f"{lost}-retry1"})
    runner = ImportanceSamplingRunner(hooks=DynamicsImportanceSampling(),
                                      executor=cluster.executor(tmp_path / "c", case_timeout_sec=5.0, max_attempts=2))

    comparison = runner.run(ImportanceRunConfig(sampling_plan_path=PLAN, output_root=tmp_path / "cluster_run",
                                                max_samples=4, root_entropy=2))["comparison"]

    assert comparison["final_status"] == "incomplete"
    assert comparison["audit"]["ordinary"]["unresolved_sample_ids"] == ["ordinary-000002"]


def test_a_sequential_stop_decides_on_the_ordered_prefix_like_a_local_run(tmp_path: Path) -> None:
    config = dict(max_samples=200, root_entropy=4, stop_mode="importance_width", target_width=0.004)
    local = _local(tmp_path, **config)
    clustered = _cluster(tmp_path, FakeCluster(tmp_path, seed=7, batch=4), **config)

    assert clustered["stopped_by_rule"] is True
    assert clustered["sample_count"] == local["sample_count"] < 200
    assert clustered["statistical_report"] == local["statistical_report"]
    assert _values(tmp_path / "cluster_run")["importance"] == _values(tmp_path / "local")["importance"]
    # Cases the workers had already taken when the rule stopped are kept out of the results.
    late = (tmp_path / "c" / "side" / "late.jsonl").read_text(encoding="utf-8").splitlines()
    assert late and all(json.loads(line)["input"]["sample_index"] > local["sample_count"] for line in late)


def test_the_cluster_needs_the_streams_seed_policy(tmp_path: Path) -> None:
    cluster = FakeCluster(tmp_path)
    runner = ImportanceSamplingRunner(hooks=DynamicsImportanceSampling(), executor=cluster.executor(tmp_path))
    with pytest.raises(ValueError, match="'streams' seed policy"):
        runner.run(ImportanceRunConfig(sampling_plan_path="configs/dynamics_importance_baseline.yaml",
                                       output_root=tmp_path / "run", max_samples=2, seed=1))


def test_a_sequential_run_fails_when_a_case_never_comes_back(tmp_path: Path) -> None:
    lost = "ou_a_3sd_two_sided__q1d_b0.2__final-importance-000001"
    cluster = FakeCluster(tmp_path, drop_always={lost})
    runner = ImportanceSamplingRunner(hooks=DynamicsImportanceSampling(),
                                      executor=cluster.executor(tmp_path / "c", case_timeout_sec=3.0, max_attempts=1))
    with pytest.raises(CaseLost):
        runner.run(ImportanceRunConfig(sampling_plan_path=PLAN, output_root=tmp_path / "run", max_samples=20,
                                       root_entropy=4, stop_mode="importance_width", target_width=0.004))
