#!/usr/bin/env python3
"""Report timeout rows whose matching worker still has a late AWSIM trace.

This tool is intentionally read-only. It never changes the dataset CSV, trace
files, Ray state, or containers.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from redis_cluster import cluster_config  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only reconciliation of timeout rows and late AWSIM traces.",
    )
    parser.add_argument("--dataset-csv", type=Path, required=True)
    parser.add_argument("--case-kind", default="uturn")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--connect-timeout", type=int, default=10)
    return parser.parse_args(argv)


def _worker_nodes() -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for info in cluster_config.CLUSTER_NODES.values():
        container = dict(info.get("container", {}))
        domain_id = container.get("ros_domain_id")
        if domain_id is None:
            continue
        result[f"worker_{domain_id}"] = dict(info)
    return result


def _host_trace_dir(info: dict[str, object]) -> str:
    container = dict(info["container"])
    host_user = str(info["user"])
    return f"/home/{host_user}/simulation_traces_sim_worker_{container['ros_domain_id']}"


def _scan_script() -> str:
    return """import json, sys
from pathlib import Path
root = Path(sys.argv[1])
case_kind = sys.argv[2]
loops = [int(item) for item in sys.argv[3].split(',') if item]
for loop in loops:
    expected = root / f'{case_kind}_eval_sim{loop}.json'
    local = root / f'{case_kind}_test_sim{loop}.json'
    def kind(path):
        if not path.is_file(): return 'missing'
        text = path.read_text(encoding='utf-8', errors='replace').lstrip()
        if text.strip() == 'TIMEOUT': return 'timeout_marker'
        return 'json' if text.startswith('{') else 'other'
    print(json.dumps({'loop_num': loop, 'expected_kind': kind(expected), 'local_kind': kind(local), 'expected_path': str(expected), 'local_path': str(local)}))
"""


def _run_scan(
    info: dict[str, object], *, case_kind: str, loops: list[int], connect_timeout: int
) -> list[dict[str, object]]:
    if not loops:
        return []
    directory = _host_trace_dir(info)
    remote = str(info["ip"]) != cluster_config.MASTER_IP
    command = ["python3", "-c", _scan_script(), directory, case_kind, ",".join(map(str, loops))]
    if remote:
        remote_command = " ".join(shlex.quote(part) for part in command)
        command = [
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", f"ConnectTimeout={connect_timeout}",
            f"{info['user']}@{info['ip']}", remote_command,
        ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"trace scan failed for {info['machine']}: {result.stderr.strip()}")
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset = pd.read_csv(args.dataset_csv)
    required = {"loop_num", "status", "worker_id"}
    missing = required - set(dataset.columns)
    if missing:
        raise ValueError(f"dataset CSV is missing columns: {', '.join(sorted(missing))}")
    timeouts = dataset[dataset["status"].eq("timeout")]
    nodes = _worker_nodes()
    findings: list[dict[str, object]] = []
    for worker_id, group in timeouts.groupby("worker_id"):
        info = nodes.get(str(worker_id))
        if info is None:
            continue
        loops = sorted(pd.to_numeric(group["loop_num"], errors="coerce").dropna().astype(int).unique())
        for item in _run_scan(info, case_kind=args.case_kind, loops=loops, connect_timeout=args.connect_timeout):
            if item["expected_kind"] == "timeout_marker" and item["local_kind"] == "json":
                item["worker_id"] = str(worker_id)
                item["machine"] = str(info["machine"])
                findings.append(item)
    report = {
        "dataset_csv": str(args.dataset_csv.resolve()),
        "case_kind": args.case_kind,
        "timeout_rows": int(len(timeouts)),
        "late_artifact_count": len(findings),
        "late_artifacts": findings,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
