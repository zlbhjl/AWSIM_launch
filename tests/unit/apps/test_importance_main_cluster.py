"""`importance_main --cluster` with a fake cluster: an in-process queue and a worker thread (no Ray, no AWSIM)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from apps.cli import importance_main
from contracts.evaluation import EvaluationRecord
from contracts.execution import RawRunResult, RunStatus
from orchestration.worker_loop import WorkerLoop
from runtime.cluster.ray_queue import TaskQueue
from runtime.cluster.result_sink import serialize_evaluation_record
from runtime.cluster.task_queue_gateway import TaskQueueGateway

PLAN = "tests/fixtures/importance_cluster/awsim_streams_plan.yaml"


class EchoBackend:
    def run(self, case):
        return RawRunResult(case.case_id, case.target, case.case_kind, RunStatus.SUCCESS,
                            meta={"input_echo": dict(case.input)})


class Interpreter:
    def interpret_raw_run_result(self, raw):
        inputs = raw.meta["input_echo"]
        collision = int(float(inputs["ego_speed"]) - float(inputs["npc_speed"]) > 22.0)
        return EvaluationRecord(raw.case_id, raw.target, raw.case_kind, raw.status,
                                output={"c_collision": collision}, meta=dict(raw.meta))


def test_the_cli_runs_an_awsim_plan_on_cluster_workers(tmp_path: Path, monkeypatch) -> None:
    import apps.cli.orchestrator_cluster_main as cluster_main

    queue = TaskQueue()
    started: dict[str, object] = {}

    def fake_start_cluster(manager, launch_config, *, records_jsonl_path, **_kwargs):
        started["config"] = launch_config
        records = Path(records_jsonl_path)

        def write(record):
            with records.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(serialize_evaluation_record(record)) + "\n")

        def worker():
            gateway = TaskQueueGateway(actor=queue)
            loop = WorkerLoop(backend=EchoBackend(), result_interpreter=Interpreter(), result_sink=write)
            while True:
                try:
                    case = gateway.fetch_next()
                except StopIteration:
                    return
                if case is None:
                    threading.Event().wait(0.01)
                    continue
                started.setdefault("payload_plan_path", case.input.get("sampling_plan_path"))
                record = loop.run_once(case)
                queue.report_completion(case.meta["global_loop_num"], record.status.value)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        started["thread"] = thread
        return SimpleNamespace(head_address="local", ray_module=None, queue_actor=queue,
                               cluster_summary=SimpleNamespace(launched_workers=["worker_1", "worker_2"],
                                                               skipped_workers=[]))

    monkeypatch.setattr(cluster_main, "start_cluster", fake_start_cluster)

    exit_code = importance_main.main([
        "--sampling-plan", PLAN, "--output-root", str(tmp_path / "run"), "--max-samples", "4",
        "--root-entropy", "6", "--cluster", "--cluster-poll-interval", "0.01",
    ])

    comparison = json.loads((tmp_path / "run" / "comparison.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert comparison["final_status"] == "complete"
    assert comparison["diagnostic"]["importance"]["sample_size"] == 4
    config = started["config"]
    assert config.target == "awsim" and config.case_kind == "uturn" and config.fresh_infra_per_sample is True
    assert started["payload_plan_path"] == PLAN  # relative: the workers share the repository
    started["thread"].join(timeout=5)
    assert not started["thread"].is_alive()  # the stop signal ends the workers
