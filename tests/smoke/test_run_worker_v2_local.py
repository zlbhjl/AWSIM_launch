import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "awsim" / "normal_trace_maude.json"


def test_run_worker_v2_local_smoke(tmp_path: Path) -> None:
    output_path = tmp_path / "worker_records.jsonl"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_worker_v2.py"),
            "--fixture",
            str(FIXTURE),
            "--output",
            str(output_path),
            "--case-id",
            "worker_smoke_case",
            "--tag",
            "smoke",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert output_path.exists()

    stdout_payload = json.loads(completed.stdout.strip())
    assert stdout_payload["case_id"] == "worker_smoke_case"
    assert stdout_payload["status"] == "success"

    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["case_id"] == "worker_smoke_case"
    assert record["status"] == "success"
    assert record["meta"]["verifier_name"] == "maude"


def test_run_worker_v2_param_headless_subprocess_smoke(tmp_path: Path) -> None:
    output_path = tmp_path / "worker_param_records.jsonl"
    marker_path = tmp_path / "headless_marker.json"
    trace_path = tmp_path / "uturn_eval_sim1.json"
    sitecustomize_path = tmp_path / "sitecustomize.py"
    sitecustomize_path.write_text(
        f"""
import json
import os
from pathlib import Path

from contracts.evaluation import EvaluationRecord
from contracts.execution import RawRunResult, RunStatus
import apps.cli.worker_main as worker_main


def fake_build_target_components(args, *, backend=None, result_interpreter=None):
    marker_path = Path(os.environ["RUN_WORKER_HEADLESS_MARKER"])
    marker_path.write_text(
        json.dumps(
            {{
                "headless": args.headless,
                "case_kind": args.case_kind,
            }}
        ),
        encoding="utf-8",
    )

    class FakeBackend:
        def run(self, case):
            trace_path = Path(os.environ["RUN_WORKER_TRACE_PATH"])
            trace_path.write_text("{{}}", encoding="utf-8")
            return RawRunResult(
                case_id=case.case_id,
                target=case.target,
                case_kind=case.case_kind,
                status=RunStatus.SUCCESS,
                evidence={{"trace_json": str(trace_path)}},
            )

    class FakeInterpreter:
        def __call__(self, raw):
            return EvaluationRecord(
                case_id=raw.case_id,
                target=raw.target,
                case_kind=raw.case_kind,
                status=RunStatus.SUCCESS,
                input={{}},
                output={{"c_collision": 0}},
                evidence=dict(raw.evidence),
                meta={{"verifier_name": "fake"}},
            )

    class Components:
        backend = FakeBackend()
        result_interpreter = FakeInterpreter()

    return Components()


worker_main.build_target_components = fake_build_target_components
""".strip()
        + "\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{tmp_path}:{ROOT}"
        if not existing_pythonpath
        else f"{tmp_path}:{ROOT}:{existing_pythonpath}"
    )
    env["RUN_WORKER_HEADLESS_MARKER"] = str(marker_path)
    env["RUN_WORKER_TRACE_PATH"] = str(trace_path)

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_worker_v2.py"),
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
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert output_path.exists()
    assert marker_path.exists()

    marker_payload = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker_payload == {
        "headless": True,
        "case_kind": "uturn",
    }

    stdout_payload = json.loads(completed.stdout.strip())
    assert stdout_payload["case_id"] == "uturn_direct"
    assert stdout_payload["status"] == "success"

    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["case_id"] == "uturn_direct"
    assert record["status"] == "success"
    assert record["input"]["dx0"] == 15.0
    assert record["output"]["c_collision"] == 0
    assert record["meta"]["verifier_name"] == "fake"
    assert record["evidence"]["trace_json"] == str(trace_path)
