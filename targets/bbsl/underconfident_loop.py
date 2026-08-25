from __future__ import annotations

from collections.abc import Callable, Sequence

from targets.bbsl.ft4d_analysis import summarize_ft4d_completion
from targets.bbsl.batch_loop import (
    load_bbsl_resume_state,
    run_bbsl_noisy_batch_once,
)
from targets.bbsl.ft4d_bridge import evaluate_ft4d_from_bbsl_batch_outputs


NODE_TO_CONDITION = {
    "SALT_PEPPER": "salt_pepper",
    "OCCLUSION": "occlusion",
    "BLUR": "blur",
    "SP_OCC": "sp_occ",
    "SP_BLUR": "sp_blur",
    "OCC_BLUR": "occ_blur",
    "ALL_THREE": "all_three",
}


def default_bbsl_conditions(tree: str) -> list[str]:
    if tree == "basic":
        return ["salt_pepper", "occlusion", "blur"]
    if tree in {"combined", "all"}:
        return [
            "salt_pepper",
            "occlusion",
            "blur",
            "sp_occ",
            "sp_blur",
            "occ_blur",
            "all_three",
        ]
    raise ValueError(f"Unsupported BBSL tree mode: {tree!r}")


def select_conditions_from_confidence_summary(
    confidence_summary: dict[str, object] | None,
    *,
    tree: str = "basic",
) -> list[str]:
    defaults = default_bbsl_conditions(tree)
    if not confidence_summary:
        return defaults

    selected: list[str] = []
    for event in confidence_summary.get("underconfident_events", []):
        if event.get("type") != "basic":
            continue
        condition = NODE_TO_CONDITION.get(event.get("node_id"))
        if condition in defaults and condition not in selected:
            selected.append(condition)
    return selected or defaults


def run_bbsl_underconfident_loop(
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
    refresh_clean_baseline: bool = False,
    resume_batches: bool = True,
    max_batches: int | None = None,
    max_total_trials: int = 500000,
    no_progress_patience: int = 3,
    min_confidence: float = 0.95,
    sigma_pf_assumption_overrides: dict[str, float] | None = None,
    summarize_confidence_gaps_fn: Callable[[dict[str, object], float | None], dict[str, object]] | None = None,
    select_conditions_fn: Callable[[dict[str, object] | None, str], Sequence[str]] | None = None,
    load_resume_state_fn: Callable[..., dict[str, object]] | None = None,
    run_noisy_batch_once_fn: Callable[..., str] | None = None,
    evaluate_batch_outputs_fn: Callable[..., dict[str, object]] | None = None,
    summarize_completion_fn: Callable[..., dict[str, object]] | None = None,
) -> dict[str, object]:
    if summarize_confidence_gaps_fn is None:
        raise ValueError("summarize_confidence_gaps_fn is required")

    resolved_conditions = list(conditions or default_bbsl_conditions(tree))
    select_fn = select_conditions_fn or (
        lambda summary, resolved_tree: select_conditions_from_confidence_summary(
            summary,
            tree=resolved_tree,
        )
    )
    load_fn = load_resume_state_fn or load_bbsl_resume_state
    noisy_fn = run_noisy_batch_once_fn or run_bbsl_noisy_batch_once
    evaluate_fn = evaluate_batch_outputs_fn or evaluate_ft4d_from_bbsl_batch_outputs
    summarize_fn = summarize_completion_fn or summarize_ft4d_completion

    resume_state = load_fn(
        target_repo=target_repo,
        mini=mini,
        tree=tree,
        sigma_pf_source=sigma_pf_source,
        sigma_pb_mode=sigma_pb_mode,
        and_rule=and_rule,
        master_seed=master_seed,
        conditions=resolved_conditions,
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
            "reuse_clean_baseline": not refresh_clean_baseline,
        },
        sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
    )
    clean_output_json = resume_state["clean_output_json"]
    clean_success_path = resume_state["clean_success_path"]
    batch_output_paths = list(resume_state["batch_output_paths"])
    batch_history: list[dict[str, object]] = []
    previous_status = resume_state["status"]
    no_progress_streak = 0
    batch_id = resume_state["next_batch_id"]
    current_conditions = list(resolved_conditions)
    ft4d_result = resume_state["final_result"]
    confidence_summary = (
        summarize_confidence_gaps_fn(ft4d_result, min_confidence)
        if ft4d_result is not None
        else None
    )
    stop_reason = None

    if previous_status is not None:
        batch_history.append(
            {
                "batch_id": "resume",
                "conditions": list(current_conditions),
                "batch_output_jsons": list(batch_output_paths),
                "status": previous_status,
            }
        )
        current_conditions = list(select_fn(confidence_summary, tree))
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
            conditions=current_conditions,
            clean_success_path=clean_success_path,
            salt_pepper_density_range=salt_pepper_density_range,
            occlusion_severity_range=occlusion_severity_range,
            blur_kernel_range=blur_kernel_range,
            detect_timeout=detect_timeout,
        )
        batch_output_paths.append(batch_output_path)
        ft4d_result = evaluate_fn(
            clean_output_json,
            batch_output_paths,
            tree_mode=tree,
            sigma_pf_source=sigma_pf_source,
            sigma_pb_mode=sigma_pb_mode,
            and_rule=and_rule,
            sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
        )
        confidence_summary = summarize_confidence_gaps_fn(
            ft4d_result,
            min_confidence,
        )
        status = summarize_fn(
            ft4d_result,
            min_confidence=min_confidence,
        )
        batch_history.append(
            {
                "batch_id": batch_id,
                "conditions": list(current_conditions),
                "batch_output_json": batch_output_path,
                "status": status,
            }
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
        elif status["insufficient_basic_events"] < previous_status["insufficient_basic_events"]:
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
        current_conditions = list(select_fn(confidence_summary, tree))
        batch_id += 1

    if ft4d_result is None:
        raise RuntimeError("Adaptive batch-loop did not produce any FT4D result.")

    ft4d_result["batch_loop"] = {
        "clean_baseline_json": clean_output_json,
        "clean_success_path": clean_success_path,
        "source_batch_jsons": batch_output_paths,
        "master_seed": master_seed,
        "resume_batches": resume_batches,
        "max_batches": max_batches,
        "max_total_trials": max_total_trials,
        "no_progress_patience": no_progress_patience,
        "min_confidence_target": min_confidence,
        "stop_reason": stop_reason,
        "completed_batches": len(batch_output_paths),
        "condition_policy": "underconfident",
        "batch_history": batch_history,
        "last_status": summarize_fn(
            ft4d_result,
            min_confidence=min_confidence,
        ),
    }
    return {
        "ft4d_result": ft4d_result,
        "confidence_summary": confidence_summary,
    }
