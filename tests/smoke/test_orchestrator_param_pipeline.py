import json
from pathlib import Path

import apps.cli.worker_main as worker_main_module
from apps.cli.orchestrator_main import run_orchestrator
from apps.cli.worker_main import run_worker
from contracts.evaluation import EvaluationRecord
from contracts.execution import RawRunResult, RunStatus
from orchestration.orchestrator import Orchestrator, OrchestratorConfig
from runtime.cluster.ray_queue import TaskQueue


def test_orchestrator_param_pipeline_smoke_runs_through_worker(tmp_path: Path) -> None:
    output_path = tmp_path / "param_records.jsonl"
    trace_path = tmp_path / "uturn_eval_sim1.json"
    queue = TaskQueue()

    def backend(case):
        assert case.case_id == "uturn_direct"
        assert case.input["dx0"] == 15.0
        assert case.input["ego_speed"] == 35.0
        assert case.input["npc_speed"] == 14.0
        assert case.input["scenario_type"] == "uturn"
        assert case.input["output_dir"] == str(tmp_path)
        trace_path.write_text("{}", encoding="utf-8")
        return RawRunResult(
            case_id=case.case_id,
            target=case.target,
            case_kind=case.case_kind,
            status=RunStatus.SUCCESS,
            evidence={"trace_json": str(trace_path)},
        )

    def interpreter(raw):
        return EvaluationRecord(
            case_id=raw.case_id,
            target=raw.target,
            case_kind=raw.case_kind,
            status=RunStatus.SUCCESS,
            input={},
            output={"c_collision": 0},
            evidence=dict(raw.evidence),
            meta={"verifier_name": "fake"},
        )

    def worker_runner(argv, *, task_source):
        return run_worker(
            list(argv),
            task_source=task_source,
            backend=backend,
            result_interpreter=interpreter,
        )

    orchestrator = Orchestrator(queue=queue, worker_runner=worker_runner)
    summary = orchestrator.run(
        OrchestratorConfig(
            output=str(output_path),
            params={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
            worker_id="worker-param-smoke",
            simulation_output_dir=str(tmp_path),
            local_loop_num=1,
        )
    )

    assert summary["worker_exit_code"] == 0
    assert summary["completed_count"] == 1
    assert summary["worker_statuses"] == {"worker-param-smoke": "waiting"}

    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["case_id"] == "uturn_direct"
    assert payload["status"] == "success"
    assert payload["input"]["dx0"] == 15.0
    assert payload["output"]["c_collision"] == 0
    assert payload["evidence"]["trace_json"] == str(trace_path)


def test_run_orchestrator_v2_param_headless_smoke_forwards_to_worker_backend(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    output_path = tmp_path / "headless_records.jsonl"
    trace_path = tmp_path / "uturn_eval_sim1.json"
    queue = TaskQueue()
    captured: dict[str, object] = {}

    class FakeBackend:
        def __init__(self, config):
            captured["headless"] = config.runtime_profile.headless
            captured["case_kind"] = config.runtime_profile.case_kind

        def run(self, case):
            assert case.case_id == "uturn_direct"
            assert case.input["dx0"] == 15.0
            assert case.input["ego_speed"] == 35.0
            assert case.input["npc_speed"] == 14.0
            assert case.input["scenario_type"] == "uturn"
            assert case.input["output_dir"] == str(tmp_path)
            trace_path.write_text("{}", encoding="utf-8")
            return RawRunResult(
                case_id=case.case_id,
                target=case.target,
                case_kind=case.case_kind,
                status=RunStatus.SUCCESS,
                evidence={"trace_json": str(trace_path)},
            )

    class FakeInterpreter:
        def __init__(self, context):
            captured["interpreter_target"] = context.target
            captured["interpreter_case_kind"] = context.case_kind

        def __call__(self, raw):
            return EvaluationRecord(
                case_id=raw.case_id,
                target=raw.target,
                case_kind=raw.case_kind,
                status=RunStatus.SUCCESS,
                input={},
                output={"c_collision": 0},
                evidence=dict(raw.evidence),
                meta={"verifier_name": "fake"},
            )

    def fake_build_target_components(args, *, backend=None, result_interpreter=None):
        class FakeComponents:
            backend = FakeBackend(
                type(
                    "Config",
                    (),
                    {
                        "runtime_profile": type(
                            "Profile",
                            (),
                            {
                                "headless": args.headless,
                                "case_kind": args.case_kind,
                            },
                        )()
                    },
                )()
            )
            result_interpreter = FakeInterpreter(
                type(
                    "Context",
                    (),
                    {
                        "target": args.target,
                        "case_kind": args.case_kind,
                    },
                )()
            )

        return FakeComponents()

    monkeypatch.setattr(worker_main_module, "build_target_components", fake_build_target_components)

    exit_code = run_orchestrator(
        [
            "--param",
            "dx0=15.0",
            "--param",
            "ego_speed=35.0",
            "--param",
            "npc_speed=14.0",
            "--output",
            str(output_path),
            "--simulation-output-dir",
            str(tmp_path),
            "--local-loop-num",
            "1",
            "--headless",
        ],
        queue=queue,
    )

    assert exit_code == 0
    assert captured["headless"] is True
    assert captured["case_kind"] == "uturn"
    assert captured["interpreter_target"] == "awsim"
    assert captured["interpreter_case_kind"] == "uturn"

    stdout_lines = [line for line in capsys.readouterr().out.strip().splitlines() if line.strip()]
    assert len(stdout_lines) == 2
    worker_payload = json.loads(stdout_lines[0])
    orchestrator_payload = json.loads(stdout_lines[1])
    assert worker_payload["case_id"] == "uturn_direct"
    assert worker_payload["status"] == "success"
    assert worker_payload["mode"] == "queue"
    assert orchestrator_payload["worker_exit_code"] == 0

    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["case_id"] == "uturn_direct"
    assert payload["status"] == "success"
    assert payload["input"]["dx0"] == 15.0
    assert payload["output"]["c_collision"] == 0
    assert payload["evidence"]["trace_json"] == str(trace_path)
