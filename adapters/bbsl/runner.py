from __future__ import annotations

import os
import subprocess
from typing import Any

from .dataset_adapter import BBSLExperimentAdapter


def run_bbsl_experiment(
    target_repo: str,
    *,
    mini: bool,
    max_images: int | None,
    tree: str,
    sigma_pf_source: str,
    sigma_pb_mode: str,
    and_rule: str,
    detect_timeout: int | None,
) -> str:
    target_repo = os.path.abspath(target_repo)
    script_path = os.path.join(target_repo, "examples", "run_full_experiment_all.py")
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"BBSL runner not found: {script_path}")

    command = [
        "python3",
        script_path,
        "--tree",
        tree,
        "--sigma-pf-source",
        sigma_pf_source,
        "--sigma-pb-mode",
        sigma_pb_mode,
        "--and-rule",
        and_rule,
        "--ft4d-backend",
        "none",
    ]
    if mini:
        command.append("--mini")
    if max_images is not None:
        command.extend(["--max-images", str(max_images)])
    if detect_timeout is not None:
        command.extend(["--detect-timeout", str(detect_timeout)])

    completed = subprocess.run(command, cwd=target_repo, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"BBSL experiment failed with return code {completed.returncode}"
        )

    adapter = BBSLExperimentAdapter()
    return adapter.default_output_path(target_repo, mini, prefer_raw=True)
