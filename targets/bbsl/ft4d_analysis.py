from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def flatten_ft4d_tree_nodes(
    node: Mapping[str, Any],
    *,
    labels: Mapping[str, str] | None = None,
    tree_mode: str = "basic",
    path: list[str] | None = None,
) -> list[dict[str, object]]:
    resolved_labels = labels or {}
    current_path = list(path or [])
    label = str(resolved_labels.get(node["id"], node["id"]))
    current_path = current_path + [label]
    entry = {
        "tree_mode": tree_mode,
        "node_id": node["id"],
        "label": label,
        "path": " > ".join(current_path),
        "type": node.get("type", "gate"),
        "gate": node.get("gate"),
        "sigma_pf": node.get("sigma_pf", 0.0),
        "sigma_pe": node.get("sigma_pe", 0.0),
        "confidence": node.get("confidence", 1.0),
        "confidence_delta": node.get("confidence_delta", 0.0),
        "has_recognition_test": "recognition_test" in node,
        "recognition_test": node.get("recognition_test"),
    }
    flattened = [entry]
    for child in node.get("children", []):
        flattened.extend(
            flatten_ft4d_tree_nodes(
                child,
                labels=resolved_labels,
                tree_mode=tree_mode,
                path=current_path,
            )
        )
    return flattened


def classify_ft4d_gap_reason(
    event: Mapping[str, object],
    threshold: float,
) -> str:
    if event["type"] == "basic":
        if not event["has_any_recognition_test"]:
            return "missing-recognition-test"
        if int(event["insufficient_sample_occurrences"]) > 0:
            return "insufficient-samples"
        if int(event["failed_test_occurrences"]) > 0:
            return "failed-recognition-test"
        if float(event["min_confidence"]) < threshold:
            return "low-confidence"
        return "ok"

    if float(event["min_confidence"]) < threshold:
        return "child-confidence-propagation"
    return "ok"


def recommend_ft4d_action(gap_reason: str) -> str:
    actions = {
        "missing-recognition-test": "add_recognition_test",
        "insufficient-samples": "collect_more_samples",
        "failed-recognition-test": "inspect_basic_event",
        "child-confidence-propagation": "inspect_child_events",
        "low-confidence": "review_confidence_target",
        "ok": "no_action",
    }
    return actions.get(str(gap_reason), "review_event")


def aggregate_ft4d_nodes(
    all_nodes: list[Mapping[str, object]],
    *,
    threshold: float,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    grouped: dict[str, dict[str, object]] = {}
    for node in all_nodes:
        event = grouped.setdefault(
            str(node["node_id"]),
            {
                "node_id": node["node_id"],
                "label": node["label"],
                "type": node["type"],
                "gate": node["gate"],
                "tree_modes": set(),
                "occurrence_count": 0,
                "paths": [],
                "min_confidence": 1.0,
                "max_confidence_delta": 0.0,
                "max_sigma_pe": 0.0,
                "max_sigma_pf": 0.0,
                "has_any_recognition_test": False,
                "failed_test_occurrences": 0,
                "insufficient_sample_occurrences": 0,
                "recognition_test_samples": [],
            },
        )

        event["tree_modes"].add(node["tree_mode"])
        event["occurrence_count"] += 1
        event["paths"].append(node["path"])
        event["min_confidence"] = min(float(event["min_confidence"]), float(node["confidence"]))
        event["max_confidence_delta"] = max(
            float(event["max_confidence_delta"]),
            float(node["confidence_delta"]),
        )
        event["max_sigma_pe"] = max(float(event["max_sigma_pe"]), float(node["sigma_pe"]))
        event["max_sigma_pf"] = max(float(event["max_sigma_pf"]), float(node["sigma_pf"]))
        event["has_any_recognition_test"] = bool(event["has_any_recognition_test"]) or bool(
            node["has_recognition_test"]
        )

        recognition_test = node.get("recognition_test")
        if recognition_test is not None:
            event["recognition_test_samples"].append(recognition_test)
            if not recognition_test.get("has_required_sample_size", True):
                event["insufficient_sample_occurrences"] += 1
            if not recognition_test.get("passed", True):
                event["failed_test_occurrences"] += 1

    reason_priority = {
        "insufficient-samples": 0,
        "failed-recognition-test": 1,
        "child-confidence-propagation": 2,
        "missing-recognition-test": 3,
        "low-confidence": 4,
        "ok": 5,
    }
    aggregated_events: list[dict[str, object]] = []
    for event in grouped.values():
        event["tree_modes"] = sorted(event["tree_modes"])
        event["paths"] = sorted(set(event["paths"]))
        event["gap_reason"] = classify_ft4d_gap_reason(event, threshold)
        event["recommended_action"] = recommend_ft4d_action(str(event["gap_reason"]))
        event["is_underconfident"] = float(event["min_confidence"]) < threshold
        event["priority_score"] = (
            0 if event["type"] == "basic" else 1,
            reason_priority.get(str(event["gap_reason"]), 99),
            float(event["min_confidence"]),
            -float(event["max_sigma_pe"]),
            str(event["node_id"]),
        )
        aggregated_events.append(event)

    aggregated_events.sort(key=lambda event: event["priority_score"])
    underconfident_events = [
        event for event in aggregated_events if bool(event["is_underconfident"])
    ]
    return aggregated_events, underconfident_events


def summarize_ft4d_confidence_gaps(
    ft4d_result: Mapping[str, object],
    min_confidence: float | None = None,
) -> dict[str, object]:
    threshold = 0.95 if min_confidence is None else float(min_confidence)
    local_runs: dict[str, Mapping[str, object]]
    if ft4d_result.get("tree_mode") == "all":
        local_runs = dict(ft4d_result.get("local_runs", {}))
    else:
        tree_mode = str(ft4d_result["tree_mode"])
        local_runs = {
            tree_mode: {
                "local_ft4d_report": ft4d_result["local_ft4d_report"],
                "top_sigma_pe": ft4d_result.get("top_sigma_pe"),
            }
        }

    all_nodes: list[dict[str, object]] = []
    for tree_mode, run in local_runs.items():
        report = run["local_ft4d_report"]
        labels = report.get("labels", {})
        all_nodes.extend(
            flatten_ft4d_tree_nodes(
                report["tree"],
                labels=labels,
                tree_mode=str(tree_mode),
            )
        )

    underconfident_nodes = [
        node for node in all_nodes if float(node["confidence"]) < threshold
    ]
    underconfident_nodes.sort(
        key=lambda node: (
            float(node["confidence"]),
            -float(node["sigma_pe"]),
            str(node["path"]),
        )
    )
    nodes_with_tests = [node for node in all_nodes if bool(node["has_recognition_test"])]
    aggregated_events, underconfident_events = aggregate_ft4d_nodes(
        all_nodes,
        threshold=threshold,
    )

    return {
        "threshold": threshold,
        "total_nodes": len(all_nodes),
        "total_unique_events": len(aggregated_events),
        "nodes_with_recognition_test": len(nodes_with_tests),
        "has_any_recognition_test": bool(nodes_with_tests),
        "underconfident_count": len(underconfident_nodes),
        "underconfident_nodes": underconfident_nodes,
        "underconfident_unique_count": len(underconfident_events),
        "aggregated_events": aggregated_events,
        "underconfident_events": underconfident_events,
        "all_nodes": all_nodes,
    }


def summarize_ft4d_completion(
    ft4d_result: Mapping[str, object],
    *,
    min_confidence: float,
) -> dict[str, object]:
    confidence_summary = summarize_ft4d_confidence_gaps(
        ft4d_result,
        min_confidence=min_confidence,
    )
    aggregated_events = confidence_summary["aggregated_events"]
    insufficient_basic_events = sum(
        1
        for event in aggregated_events
        if event["type"] == "basic" and int(event["insufficient_sample_occurrences"]) > 0
    )

    if ft4d_result.get("tree_mode") == "all":
        run_payloads = list(ft4d_result.get("local_runs", {}).values())
    else:
        run_payloads = [ft4d_result]

    total_trials = sum(
        int(payload["total_count"])
        for payload in ft4d_result.get("event_inputs", {}).values()
    )
    if total_trials == 0 and run_payloads:
        total_trials = sum(
            int(payload["total_count"])
            for payload in run_payloads[0].get("event_inputs", {}).values()
        )

    return {
        "complete": (
            insufficient_basic_events == 0
            and float(confidence_summary["threshold"])
            <= min(
                (float(event["min_confidence"]) for event in aggregated_events),
                default=1.0,
            )
        ),
        "min_confidence": min(
            (float(node.get("confidence", 1.0)) for node in confidence_summary["all_nodes"]),
            default=1.0,
        ),
        "insufficient_basic_events": insufficient_basic_events,
        "basic_recognition_tests": [
            node.get("recognition_test")
            for node in confidence_summary["all_nodes"]
            if node.get("type") == "basic" and node.get("recognition_test") is not None
        ],
        "total_trials": total_trials,
    }


__all__ = [
    "aggregate_ft4d_nodes",
    "classify_ft4d_gap_reason",
    "flatten_ft4d_tree_nodes",
    "recommend_ft4d_action",
    "summarize_ft4d_completion",
    "summarize_ft4d_confidence_gaps",
]
