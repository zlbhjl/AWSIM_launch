import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "bbsl" / "experiment_all_raw_result_mini.json"


def _write_fake_bbsl_repo(root: Path) -> Path:
    repo = root / "fake_bbsl_repo"
    script_path = repo / "examples" / "run_full_experiment_all.py"
    output_dir = repo / "output"
    output_dir.mkdir(parents=True)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "active_conditions": ["clean", "salt_pepper", "occlusion", "blur"],
        "bbsl_results": {
            "clean": {
                "dataset_d": [],
                "dataset_e": ["image_0001"],
                "dataset_size": 1,
                "dataset_size_effective": 1,
                "sigma_pb": 1.0,
                "sigma_pb_mode": "delta-clean",
                "recognition_test": "pass",
            },
            "salt_pepper": {
                "dataset_d": [],
                "dataset_e": ["image_0001"],
                "dataset_size": 1,
                "dataset_size_effective": 1,
                "sigma_pb": 1.0,
                "sigma_pb_mode": "delta-clean",
                "recognition_test": "pass",
            },
            "occlusion": {
                "dataset_d": [],
                "dataset_e": ["image_0001"],
                "dataset_size": 1,
                "dataset_size_effective": 1,
                "sigma_pb": 1.0,
                "sigma_pb_mode": "delta-clean",
                "recognition_test": "pass",
            },
            "blur": {
                "dataset_d": [],
                "dataset_e": ["image_0001"],
                "dataset_size": 1,
                "dataset_size_effective": 1,
                "sigma_pb": 1.0,
                "sigma_pb_mode": "delta-clean",
                "recognition_test": "pass",
            },
        },
        "tree_mode": "basic",
        "statistical_test_config": {"method": "ft4d"},
        "universal_dataset": ["image_0001"],
        "universal_dataset_size": 1,
        "sigma_pf_source": "dataset",
        "sigma_pb_mode": "delta-clean",
        "and_rule": "min",
    }
    script_path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "import argparse",
                "import json",
                "from pathlib import Path",
                "",
                f"PAYLOAD = {json.dumps(payload, ensure_ascii=False)}",
                "",
                "parser = argparse.ArgumentParser()",
                "parser.add_argument('--tree', default='basic')",
                "parser.add_argument('--sigma-pf-source', default='dataset')",
                "parser.add_argument('--sigma-pb-mode', default='delta-clean')",
                "parser.add_argument('--and-rule', default='min')",
                "parser.add_argument('--ft4d-backend', default='none')",
                "parser.add_argument('--max-images', type=int, default=None)",
                "parser.add_argument('--detect-timeout', type=int, default=None)",
                "parser.add_argument('--mini', action='store_true')",
                "args = parser.parse_args()",
                "output_dir = Path('output')",
                "output_dir.mkdir(parents=True, exist_ok=True)",
                "filename = 'experiment_all_raw_result_mini.json' if args.mini else 'experiment_all_raw_result.json'",
                "payload = dict(PAYLOAD)",
                "payload['tree_mode'] = args.tree",
                "payload['sigma_pf_source'] = args.sigma_pf_source",
                "payload['sigma_pb_mode'] = args.sigma_pb_mode",
                "payload['and_rule'] = args.and_rule",
                "payload['detect_timeout'] = args.detect_timeout",
                "payload['max_images'] = args.max_images",
                "payload['mini_mode'] = args.mini",
                "(output_dir / filename).write_text(json.dumps(payload), encoding='utf-8')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return repo


def test_run_orchestrator_v2_bbsl_local_smoke(tmp_path: Path) -> None:
    output_path = tmp_path / "orchestrator_bbsl_records.jsonl"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_orchestrator_v2.py"),
            "--fixture",
            str(FIXTURE),
            "--output",
            str(output_path),
            "--target",
            "bbsl",
            "--case-kind",
            "full_all",
            "--case-id",
            "bbsl_orchestrator_smoke_case",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert output_path.exists()

    stdout_lines = [line for line in completed.stdout.strip().splitlines() if line.strip()]
    assert len(stdout_lines) == 2

    worker_payload = json.loads(stdout_lines[0])
    orchestrator_payload = json.loads(stdout_lines[1])
    assert worker_payload["case_id"] == "bbsl_orchestrator_smoke_case"
    assert worker_payload["target"] == "bbsl"
    assert worker_payload["case_kind"] == "full_all"
    assert worker_payload["status"] == "success"
    assert orchestrator_payload["worker_exit_code"] == 0

    record = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert record["case_id"] == "bbsl_orchestrator_smoke_case"
    assert record["target"] == "bbsl"
    assert record["case_kind"] == "full_all"
    assert record["status"] == "success"


def test_run_orchestrator_v2_bbsl_param_smoke(tmp_path: Path) -> None:
    output_path = tmp_path / "orchestrator_bbsl_param_records.jsonl"
    fake_repo = _write_fake_bbsl_repo(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_orchestrator_v2.py"),
            "--param",
            f"target_repo={fake_repo}",
            "--param",
            "mini=true",
            "--param",
            "tree=basic",
            "--param",
            "max_images=4",
            "--output",
            str(output_path),
            "--target",
            "bbsl",
            "--case-kind",
            "full_all",
            "--case-id",
            "bbsl_orchestrator_param_case",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert output_path.exists()

    stdout_lines = [line for line in completed.stdout.strip().splitlines() if line.strip()]
    assert len(stdout_lines) == 2

    worker_payload = json.loads(stdout_lines[0])
    orchestrator_payload = json.loads(stdout_lines[1])
    assert worker_payload["case_id"] == "bbsl_orchestrator_param_case"
    assert worker_payload["target"] == "bbsl"
    assert worker_payload["case_kind"] == "full_all"
    assert worker_payload["status"] == "success"
    assert orchestrator_payload["worker_exit_code"] == 0

    record = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert record["case_id"] == "bbsl_orchestrator_param_case"
    assert record["target"] == "bbsl"
    assert record["case_kind"] == "full_all"
    assert record["status"] == "success"
    assert record["input"]["tree_mode"] == "basic"
    assert record["input"]["mini_mode"] is True
