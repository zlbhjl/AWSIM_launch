from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Sequence

from runtime.container.supervised_process import supervisor_client_from_environment
from targets.bbsl.dataset_adapter import BBSLExperimentAdapter


def _run_command(command: list[str], *, cwd: str):
    process_supervisor = supervisor_client_from_environment()
    if process_supervisor is None:
        return subprocess.run(command, cwd=cwd, check=False)
    completed = process_supervisor.run(command, cwd=cwd, env=os.environ.copy())
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return completed


def cleanup_bbsl_batch_state(target_repo: str) -> dict[str, object]:
    target_repo = os.path.abspath(target_repo)
    adapter = BBSLExperimentAdapter()
    batch_dir = adapter.batch_output_dir(target_repo)
    output_dir = os.path.join(target_repo, "output")
    generated_root = os.path.join(target_repo, "data", "kitti", "ft4d_batches")

    removed_json_files: list[str] = []
    removed_detect_pngs: list[str] = []
    removed_generated_dirs: list[str] = []

    if os.path.isdir(batch_dir):
        for name in sorted(os.listdir(batch_dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(batch_dir, name)
            if os.path.isfile(path):
                os.remove(path)
                removed_json_files.append(path)

    if os.path.isdir(output_dir):
        for name in sorted(os.listdir(output_dir)):
            if not name.endswith(".png"):
                continue
            path = os.path.join(output_dir, name)
            if os.path.isfile(path):
                os.remove(path)
                removed_detect_pngs.append(path)

    if os.path.isdir(generated_root):
        for name in sorted(os.listdir(generated_root)):
            path = os.path.join(generated_root, name)
            if os.path.isdir(path):
                shutil.rmtree(path)
                removed_generated_dirs.append(path)

    return {
        "batch_output_dir": batch_dir,
        "output_dir": output_dir,
        "generated_root": generated_root,
        "removed_json_files": removed_json_files,
        "removed_detect_pngs": removed_detect_pngs,
        "removed_generated_dirs": removed_generated_dirs,
    }


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

    completed = _run_command(command, cwd=target_repo)
    if completed.returncode != 0:
        raise RuntimeError(
            f"BBSL experiment failed with return code {completed.returncode}"
        )

    adapter = BBSLExperimentAdapter()
    return adapter.default_output_path(target_repo, mini, prefer_raw=True)


def run_bbsl_clean_baseline(
    target_repo: str,
    *,
    mini: bool,
    max_images: int | None,
    detect_timeout: int | None,
    output_json: str | None = None,
) -> str:
    target_repo = os.path.abspath(target_repo)
    script_path = os.path.join(target_repo, "examples", "run_ft4d_batch_experiment.py")
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"BBSL batch runner not found: {script_path}")

    command = [
        "python3",
        script_path,
        "--mode",
        "clean-baseline",
    ]
    if mini:
        command.append("--mini")
    if max_images is not None:
        command.extend(["--max-images", str(max_images)])
    if detect_timeout is not None:
        command.extend(["--detect-timeout", str(detect_timeout)])
    if output_json is not None:
        command.extend(["--output-json", output_json])

    completed = _run_command(command, cwd=target_repo)
    if completed.returncode != 0:
        raise RuntimeError(
            f"BBSL clean baseline failed with return code {completed.returncode}"
        )
    adapter = BBSLExperimentAdapter()
    return output_json or adapter.clean_baseline_output_path(target_repo, mini)


def run_bbsl_noisy_batch(
    target_repo: str,
    *,
    mini: bool,
    max_images: int | None,
    batch_id: int,
    master_seed: int,
    conditions: Sequence[str],
    clean_success_path: str,
    salt_pepper_density_range: tuple[float, float],
    occlusion_severity_range: tuple[float, float],
    blur_kernel_range: tuple[int, int],
    detect_timeout: int | None,
    output_json: str | None = None,
) -> str:
    target_repo = os.path.abspath(target_repo)
    script_path = os.path.join(target_repo, "examples", "run_ft4d_batch_experiment.py")
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"BBSL batch runner not found: {script_path}")

    command = [
        "python3",
        script_path,
        "--mode",
        "noisy-batch",
        "--batch-id",
        str(batch_id),
        "--master-seed",
        str(master_seed),
        "--clean-success-path",
        clean_success_path,
        "--salt-pepper-density-range",
        str(salt_pepper_density_range[0]),
        str(salt_pepper_density_range[1]),
        "--occlusion-severity-range",
        str(occlusion_severity_range[0]),
        str(occlusion_severity_range[1]),
        "--blur-kernel-range",
        str(blur_kernel_range[0]),
        str(blur_kernel_range[1]),
        "--conditions",
        *list(conditions),
    ]
    if mini:
        command.append("--mini")
    if max_images is not None:
        command.extend(["--max-images", str(max_images)])
    if detect_timeout is not None:
        command.extend(["--detect-timeout", str(detect_timeout)])
    if output_json is not None:
        command.extend(["--output-json", output_json])

    completed = _run_command(command, cwd=target_repo)
    if completed.returncode != 0:
        raise RuntimeError(
            f"BBSL noisy batch failed with return code {completed.returncode}"
        )
    adapter = BBSLExperimentAdapter()
    return output_json or adapter.noisy_batch_output_path(target_repo, batch_id, mini)
