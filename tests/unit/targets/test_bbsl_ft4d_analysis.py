from targets.bbsl.ft4d_analysis import (
    aggregate_ft4d_nodes,
    flatten_ft4d_tree_nodes,
    summarize_ft4d_completion,
    summarize_ft4d_confidence_gaps,
)


def test_flatten_ft4d_tree_nodes_preserves_path_and_tree_mode() -> None:
    nodes = flatten_ft4d_tree_nodes(
        {
            "id": "TOP",
            "type": "gate",
            "confidence": 0.97,
            "children": [
                {
                    "id": "SALT_PEPPER",
                    "type": "basic",
                    "confidence": 0.91,
                    "recognition_test": {"has_required_sample_size": True},
                    "children": [],
                }
            ],
        },
        labels={"TOP": "Top Event", "SALT_PEPPER": "Salt Pepper"},
        tree_mode="basic",
    )

    assert [node["path"] for node in nodes] == [
        "Top Event",
        "Top Event > Salt Pepper",
    ]
    assert all(node["tree_mode"] == "basic" for node in nodes)


def test_aggregate_ft4d_nodes_classifies_basic_event_gap_reason() -> None:
    aggregated, underconfident = aggregate_ft4d_nodes(
        [
            {
                "tree_mode": "basic",
                "node_id": "SALT_PEPPER",
                "label": "Salt Pepper",
                "path": "Top > Salt Pepper",
                "type": "basic",
                "gate": None,
                "sigma_pf": 0.1,
                "sigma_pe": 0.2,
                "confidence": 0.8,
                "confidence_delta": 0.15,
                "has_recognition_test": True,
                "recognition_test": {
                    "has_required_sample_size": False,
                    "passed": False,
                },
            }
        ],
        threshold=0.95,
    )

    assert len(aggregated) == 1
    assert aggregated[0]["gap_reason"] == "insufficient-samples"
    assert aggregated[0]["recommended_action"] == "collect_more_samples"
    assert underconfident[0]["node_id"] == "SALT_PEPPER"


def test_summarize_ft4d_confidence_gaps_collects_underconfident_events() -> None:
    summary = summarize_ft4d_confidence_gaps(
        {
            "tree_mode": "basic",
            "local_ft4d_report": {
                "labels": {"TOP": "Top", "BLUR": "Blur"},
                "tree": {
                    "id": "TOP",
                    "type": "gate",
                    "confidence": 0.93,
                    "confidence_delta": 0.02,
                    "children": [
                        {
                            "id": "BLUR",
                            "type": "basic",
                            "confidence": 0.93,
                            "sigma_pe": 0.3,
                            "recognition_test": {
                                "has_required_sample_size": True,
                                "passed": True,
                            },
                            "children": [],
                        }
                    ],
                },
            },
            "top_sigma_pe": 0.3,
        },
        min_confidence=0.95,
    )

    assert summary["underconfident_count"] == 2
    assert summary["underconfident_unique_count"] == 2
    assert summary["underconfident_events"][0]["node_id"] == "BLUR"


def test_summarize_ft4d_completion_reports_total_trials_and_completion() -> None:
    result = summarize_ft4d_completion(
        {
            "tree_mode": "basic",
            "event_inputs": {
                "SALT_PEPPER": {"total_count": 5},
            },
            "local_ft4d_report": {
                "tree": {
                    "id": "TOP",
                    "type": "gate",
                    "confidence": 0.98,
                    "children": [
                        {
                            "id": "SALT_PEPPER",
                            "type": "basic",
                            "confidence": 0.98,
                            "recognition_test": {
                                "has_required_sample_size": True,
                            },
                            "children": [],
                        }
                    ],
                }
            },
        },
        min_confidence=0.95,
    )

    assert result["complete"] is True
    assert result["min_confidence"] == 0.98
    assert result["insufficient_basic_events"] == 0
    assert result["total_trials"] == 5
