#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import shlex
import subprocess
from typing import Any

from redis_cluster import cluster_config


REMOTE_PROBE = r"""
hostname
echo 'OS='$(lsb_release -ds 2>/dev/null || . /etc/os-release && echo "${PRETTY_NAME:-unknown}")
echo 'PYTHON='$(python3 --version 2>/dev/null | awk '{print $2}')
echo 'RAY='$(ray --version 2>/dev/null | awk '{print $3}' | tr -d ',' || echo not_found)
echo 'DOCKER='$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',' || echo not_found)
echo 'NVIDIA='$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n 1 || echo unavailable)
docker image inspect autoware_internal:2026 --format 'IMAGE_ID={{.Id}}' 2>/dev/null || echo 'IMAGE_ID=missing'
docker image inspect autoware_internal:2026 --format 'IMAGE_DIGESTS={{json .RepoDigests}}' 2>/dev/null || echo 'IMAGE_DIGESTS=[]'
docker image inspect autoware_internal:2026 --format 'IMAGE_CREATED={{.Created}}' 2>/dev/null || echo 'IMAGE_CREATED=missing'
"""


def run_probe(hostname: str | None, user: str | None) -> dict[str, Any]:
    if hostname is None:
        cmd = ["/bin/bash", "-lc", REMOTE_PROBE]
    else:
        remote = f"{user}@{hostname}"
        cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8",
            remote,
            REMOTE_PROBE,
        ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    parsed = parse_probe(result.stdout)
    parsed["reachable"] = (result.returncode == 0)
    if result.returncode != 0:
        parsed["error"] = result.stderr.strip() or f"probe failed with code {result.returncode}"
    return parsed


def parse_probe(stdout: str) -> dict[str, Any]:
    data: dict[str, Any] = {"raw": stdout.strip()}
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return data

    data["hostname"] = lines[0]
    for line in lines[1:]:
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "IMAGE_DIGESTS":
            try:
                data[key.lower()] = json.loads(value)
            except json.JSONDecodeError:
                data[key.lower()] = value
        else:
            data[key.lower()] = value
    return data


def main() -> None:
    report: dict[str, Any] = {"nodes": {}}

    report["nodes"]["local"] = run_probe(None, None)

    for node_id, info in cluster_config.CLUSTER_NODES.items():
        if info["ip"] == cluster_config.MASTER_IP:
            continue
        report["nodes"][node_id] = run_probe(info["ip"], info["user"])

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
