from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from typing import Any, Callable

from targets.bbsl.ft4d_analysis import summarize_ft4d_completion
from targets.bbsl.dataset_adapter import BBSLExperimentAdapter
from targets.bbsl.ft4d_bridge import evaluate_ft4d_from_bbsl_batch_outputs
from targets.bbsl.runner import run_bbsl_clean_baseline, run_bbsl_noisy_batch


def ensure_bbsl_clean_baseline(
    *,
    target_repo: str,
    mini: bool = False,
    max_images: int | None = None,
    detect_timeout: int | None = None,
    reuse_clean_baseline: bool = True,
    adapter: BBSLExperimentAdapter | None = None,
    clean_baseline_runner: Callable[..., str] | None = None,
) -> dict[str, str]:
    bridge_adapter = adapter or BBSLExperimentAdapter()
    runner = clean_baseline_runner or run_bbsl_clean_baseline
    clean_output_json = bridge_adapter.clean_baseline_output_path(target_repo, mini)
    clean_success_path = bridge_adapter.clean_success_path(target_repo, mini)
    should_regenerate = (
        not reuse_clean_baseline
        or not os.path.exists(clean_output_json)
        or not os.path.exists(clean_success_path)
    )

    if not should_regenerate:
        payload = bridge_adapter.load_output(clean_output_json)
        if bool(payload.get("mini_mode", False)) != bool(mini):
            should_regenerate = True
        elif payload.get("max_images") != max_images:
            should_regenerate = True

    if should_regenerate:
        clean_output_json = runner(
            target_repo,
            mini=mini,
            max_images=max_images,
            detect_timeout=detect_timeout,
            output_json=clean_output_json,
        )
    return {
        "clean_output_json": clean_output_json,
        "clean_success_path": clean_success_path,
    }


def run_bbsl_noisy_batch_once(
    *,
    target_repo: str,
    mini: bool = False,
    max_images: int | None = None,
    batch_id: int,
    master_seed: int,
    conditions: Sequence[str],
    clean_success_path: str,
    salt_pepper_density_range: tuple[float, float] = (0.01, 0.08),
    occlusion_severity_range: tuple[float, float] = (0.2, 0.5),
    blur_kernel_range: tuple[int, int] = (5, 11),
    detect_timeout: int | None = None,
    adapter: BBSLExperimentAdapter | None = None,
    noisy_batch_runner: Callable[..., str] | None = None,
) -> str:
    bridge_adapter = adapter or BBSLExperimentAdapter()
    runner = noisy_batch_runner or run_bbsl_noisy_batch
    return runner(
        target_repo,
        mini=mini,
        max_images=max_images,
        batch_id=batch_id,
        master_seed=master_seed,
        conditions=conditions,
        clean_success_path=clean_success_path,
        salt_pepper_density_range=salt_pepper_density_range,
        occlusion_severity_range=occlusion_severity_range,
        blur_kernel_range=blur_kernel_range,
        detect_timeout=detect_timeout,
        output_json=bridge_adapter.noisy_batch_output_path(target_repo, batch_id, mini),
    )


def load_bbsl_resume_state(
    *,
    target_repo: str,
    mini: bool,
    tree: str,
    sigma_pf_source: str,
    sigma_pb_mode: str,
    and_rule: str,
    master_seed: int,
    conditions: Sequence[str],
    salt_pepper_density_range: tuple[float, float],
    occlusion_severity_range: tuple[float, float],
    blur_kernel_range: tuple[int, int],
    min_confidence: float,
    resume_batches: bool,
    ensure_clean_kwargs: dict[str, object],
    sigma_pf_assumption_overrides: dict[str, float] | None = None,
    adapter: BBSLExperimentAdapter | None = None,
    ensure_clean_baseline_fn: Callable[..., dict[str, str]] | None = None,
    evaluate_batch_outputs_fn: Callable[..., dict[str, object]] | None = None,
) -> dict[str, object]:
    bridge_adapter = adapter or BBSLExperimentAdapter()
    ensure_fn = ensure_clean_baseline_fn or ensure_bbsl_clean_baseline
    evaluate_fn = evaluate_batch_outputs_fn or evaluate_ft4d_from_bbsl_batch_outputs

    baseline = ensure_fn(**ensure_clean_kwargs)
    clean_output_json = baseline["clean_output_json"]
    clean_success_path = baseline["clean_success_path"]

    batch_output_paths: list[str] = []
    final_result = None
    status = None
    next_batch_id = 1

    if resume_batches:
        candidate_paths = bridge_adapter.list_noisy_batch_output_paths(target_repo, mini)
        allowed_conditions = tuple(conditions)
        for path in candidate_paths:
            payload = bridge_adapter.load_output(path)
            if not bbsl_batch_matches_config(
                payload,
                tree=tree,
                master_seed=master_seed,
                max_images=ensure_clean_kwargs.get("max_images"),
                salt_pepper_density_range=salt_pepper_density_range,
                occlusion_severity_range=occlusion_severity_range,
                blur_kernel_range=blur_kernel_range,
                allowed_conditions=allowed_conditions,
            ):
                continue
            batch_output_paths.append(path)

        if batch_output_paths:
            final_result = evaluate_fn(
                clean_output_json,
                batch_output_paths,
                tree_mode=tree,
                sigma_pf_source=sigma_pf_source,
                sigma_pb_mode=sigma_pb_mode,
                and_rule=and_rule,
                sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
            )
            status = summarize_ft4d_completion(
                final_result,
                min_confidence=min_confidence,
            )
            next_batch_id = max(
                bridge_adapter.noisy_batch_id_from_path(path) or 0
                for path in batch_output_paths
            ) + 1

    return {
        "clean_output_json": clean_output_json,
        "clean_success_path": clean_success_path,
        "batch_output_paths": batch_output_paths,
        "final_result": final_result,
        "status": status,
        "next_batch_id": next_batch_id,
    }


def run_bbsl_until_ft4d_confident(
    *,
    target_repo: str,
    mini: bool = False,
    max_images: int | None = None,
    tree: str = "basic",
    sigma_pf_source: str = "dataset",
    sigma_pb_mode: str = "delta-clean",
    and_rule: str = "min",
    detect_timeout: int | None = None,
    master_seed: int = 1000,
    conditions: Sequence[str] | None = None,
    salt_pepper_density_range: tuple[float, float] = (0.01, 0.08),
    occlusion_severity_range: tuple[float, float] = (0.2, 0.5),
    blur_kernel_range: tuple[int, int] = (5, 11),
    reuse_clean_baseline: bool = True,
    resume_batches: bool = True,
    max_batches: int | None = None,
    max_total_trials: int = 500000,
    no_progress_patience: int = 3,
    min_confidence: float = 0.95,
    sigma_pf_assumption_overrides: dict[str, float] | None = None,
    load_resume_state_fn: Callable[..., dict[str, object]] | None = None,
    run_noisy_batch_once_fn: Callable[..., str] | None = None,
    evaluate_batch_outputs_fn: Callable[..., dict[str, object]] | None = None,
) -> dict[str, object]:
    if conditions is None:
        if tree == "basic":
            conditions = ("salt_pepper", "occlusion", "blur")
        elif tree in {"combined", "all"}:
            conditions = (
                "salt_pepper",
                "occlusion",
                "blur",
                "sp_occ",
                "sp_blur",
                "occ_blur",
                "all_three",
            )
        else:
            raise ValueError(f"Unsupported tree mode: {tree!r}")

    load_fn = load_resume_state_fn or load_bbsl_resume_state
    noisy_fn = run_noisy_batch_once_fn or run_bbsl_noisy_batch_once
    evaluate_fn = evaluate_batch_outputs_fn or evaluate_ft4d_from_bbsl_batch_outputs

    resume_state = load_fn(
        target_repo=target_repo,
        mini=mini,
        tree=tree,
        sigma_pf_source=sigma_pf_source,
        sigma_pb_mode=sigma_pb_mode,
        and_rule=and_rule,
        master_seed=master_seed,
        conditions=conditions,
        salt_pepper_density_range=salt_pepper_density_range,
        occlusion_severity_range=occlusion_severity_range,
        blur_kernel_range=blur_kernel_range,
        min_confidence=min_confidence,
        resume_batches=resume_batches,
        ensure_clean_kwargs={
            "target_repo": target_repo,
            "mini": mini,
            "max_images": max_images,
            "detect_timeout": detect_timeout,
            "reuse_clean_baseline": reuse_clean_baseline,
        },
        sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
    )
    clean_output_json = resume_state["clean_output_json"]
    clean_success_path = resume_state["clean_success_path"]

    batch_output_paths = list(resume_state["batch_output_paths"])
    batch_id = resume_state["next_batch_id"]
    previous_status = resume_state["status"]
    no_progress_streak = 0
    final_result = resume_state["final_result"]
    stop_reason = None

    if previous_status is not None:
        if previous_status["complete"]:
            stop_reason = "already_complete_resume"
        elif previous_status["total_trials"] >= max_total_trials:
            stop_reason = "max_total_trials_resume"

    while stop_reason is None:
        if max_batches is not None and batch_id > max_batches:
            stop_reason = "max_batches"
            break

        batch_output_path = noisy_fn(
            target_repo=target_repo,
            mini=mini,
            max_images=max_images,
            batch_id=batch_id,
            master_seed=master_seed,
            conditions=conditions,
            clean_success_path=clean_success_path,
            salt_pepper_density_range=salt_pepper_density_range,
            occlusion_severity_range=occlusion_severity_range,
            blur_kernel_range=blur_kernel_range,
            detect_timeout=detect_timeout,
        )
        batch_output_paths.append(batch_output_path)

        final_result = evaluate_fn(
            clean_output_json,
            batch_output_paths,
            tree_mode=tree,
            sigma_pf_source=sigma_pf_source,
            sigma_pb_mode=sigma_pb_mode,
            and_rule=and_rule,
            sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
        )
        status = summarize_ft4d_completion(
            final_result,
            min_confidence=min_confidence,
        )

        if status["complete"]:
            stop_reason = "confidence-satisfied"
            break
        if status["total_trials"] >= max_total_trials:
            stop_reason = "max_total_trials"
            break

        improved = False
        if previous_status is None:
            improved = True
        else:
            if (
                status["insufficient_basic_events"]
                < previous_status["insufficient_basic_events"]
            ):
                improved = True
            elif status["min_confidence"] > previous_status["min_confidence"]:
                improved = True

        if improved:
            no_progress_streak = 0
        else:
            no_progress_streak += 1
            if no_progress_streak >= no_progress_patience:
                stop_reason = "no_progress"
                break

        previous_status = status
        batch_id += 1

    if final_result is None:
        raise RuntimeError("Batch-loop execution did not produce any FT4D result.")

    final_result["batch_loop"] = {
        "clean_baseline_json": clean_output_json,
        "clean_success_path": clean_success_path,
        "source_batch_jsons": batch_output_paths,
        "master_seed": master_seed,
        "conditions": list(conditions),
        "salt_pepper_density_range": list(salt_pepper_density_range),
        "occlusion_severity_range": list(occlusion_severity_range),
        "blur_kernel_range": list(blur_kernel_range),
        "max_batches": max_batches,
        "max_total_trials": max_total_trials,
        "no_progress_patience": no_progress_patience,
        "min_confidence_target": min_confidence,
        "stop_reason": stop_reason,
        "completed_batches": len(batch_output_paths),
        "last_status": summarize_ft4d_completion(
            final_result,
            min_confidence=min_confidence,
        ),
    }
    return final_result


def expected_bbsl_batch_tree_mode(tree: str) -> str:
    if tree == "basic":
        return "basic"
    if tree in {"combined", "all"}:
        return "combined"
    raise ValueError(f"Unsupported tree mode: {tree!r}")


def infer_bbsl_batch_tree_mode(payload: Mapping[str, Any]) -> str:
    tree_mode = payload.get("tree_mode")
    if tree_mode:
        return str(tree_mode)
    active_conditions = set(payload.get("active_conditions", []))
    composite = {"sp_occ", "sp_blur", "occ_blur", "all_three"}
    return "combined" if active_conditions & composite else "basic"


def bbsl_batch_matches_config(
    payload: Mapping[str, Any],
    *,
    tree: str,
    master_seed: int,
    max_images: int | None,
    salt_pepper_density_range: tuple[float, float],
    occlusion_severity_range: tuple[float, float],
    blur_kernel_range: tuple[int, int],
    allowed_conditions: Sequence[str],
) -> bool:
    if payload.get("mode") != "noisy_batch":
        return False
    if payload.get("master_seed") != master_seed:
        return False
    if payload.get("max_images") != max_images:
        return False
    if infer_bbsl_batch_tree_mode(payload) != expected_bbsl_batch_tree_mode(tree):
        return False
    noise_ranges = payload.get("noise_ranges", {})
    if noise_ranges.get("salt_pepper_density_range") != list(salt_pepper_density_range):
        return False
    if noise_ranges.get("occlusion_severity_range") != list(occlusion_severity_range):
        return False
    if noise_ranges.get("blur_kernel_range") != list(blur_kernel_range):
        return False
    active_conditions = set(payload.get("active_conditions", []))
    if not active_conditions:
        return False
    if not active_conditions.issubset(set(allowed_conditions)):
        return False
    return True
