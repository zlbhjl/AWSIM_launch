import json
import math
from pathlib import Path

import pytest

from tools.analysis.validate_dynamics_against_awsim import (
    awsim_world_frame_min_ttc,
    confusion_summary,
    continuous_ttc_summary,
)


def test_confusion_summary_reports_error_directions() -> None:
    rows = [
        {"expected": 1, "predicted": 1},
        {"expected": 0, "predicted": 0},
        {"expected": 0, "predicted": 1},
        {"expected": 1, "predicted": 0},
    ]

    result = confusion_summary(rows, expected_key="expected", predicted_key="predicted")

    assert result == {
        "sample_count": 4,
        "true_positive": 1,
        "true_negative": 1,
        "false_positive": 1,
        "false_negative": 1,
        "accuracy": 0.5,
        "sensitivity": 0.5,
        "specificity": 0.5,
    }


def test_continuous_ttc_summary_ignores_non_finite_pairs() -> None:
    result = continuous_ttc_summary(
        [
            {"awsim_min_ttc": 1.0, "dynamics_min_ttc": 1.25},
            {"awsim_min_ttc": 2.0, "dynamics_min_ttc": 1.5},
            {"awsim_min_ttc": math.inf, "dynamics_min_ttc": 3.0},
        ]
    )

    assert result["finite_pair_count"] == 2
    assert result["mae_sec"] == pytest.approx(0.375)
    assert result["median_absolute_error_sec"] == pytest.approx(0.375)
    assert result["bias_sec"] == pytest.approx(-0.125)


def _actor(x: float, y: float, yaw_deg: float, speed: float) -> dict[str, object]:
    yaw = math.radians(yaw_deg)
    return {
        "pose": {"position": {"x": x, "y": y}, "rotation": {"z": yaw_deg}},
        # AWSIM stores twist.linear in the world frame.
        "twist": {"linear": {"x": speed * math.cos(yaw), "y": speed * math.sin(yaw)}},
    }


def test_awsim_world_frame_ttc_does_not_rotate_twist_twice(tmp_path: Path) -> None:
    rows = []
    for index in range(120):
        time_sec = index * 0.05
        npc_yaw = 270.0 if index < 100 else 269.0
        rows.append(
            {
                "timestamp": time_sec,
                # Ego drives north (yaw 90 deg) toward an NPC 40 m up the road.
                "groundtruth_ego": _actor(0.0, 5.0 * time_sec, 90.0, 5.0),
                "groundtruth_vehicles": [_actor(0.0, 40.0, npc_yaw, 1.0 if index >= 100 else 0.0)],
            }
        )
    payload = {
        "groundtruth_kinematic": rows,
        "groundtruth_size": {
            "vehicle_sizes": [
                {"name": "ego", "center": {"x": 0.0}, "size": {"x": 4.0, "y": 2.0}},
                {"name": "npc1", "center": {"x": 0.0}, "size": {"x": 4.0, "y": 2.0}},
            ]
        },
    }
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    ttc = awsim_world_frame_min_ttc(path)

    # The NPC trigger is at t=5.0 s.  The last row (ego y=29.75) has a 6.25 m
    # body gap closing at 6 m/s, so the 0.1 s CVM projection overlaps at 1.1 s.
    # Rotating the world-frame twist by yaw again would send ego west: inf.
    assert ttc == pytest.approx(1.1)
