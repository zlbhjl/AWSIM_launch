import math

import numpy as np
import pytest

from tools.analysis.calibrate_uturn_start_geometry import (
    GeometrySample,
    fit_linear_geometry,
    measure_npc_turn,
)


def test_fit_linear_geometry_recovers_linear_targets() -> None:
    samples = []
    for index in range(15):
        features = (1.0, float(index), float(index % 3), float(index % 5))
        targets = tuple(
            2.0 + feature_index * 0.5 + 3.0 * features[1]
            for feature_index in range(7)
        )
        samples.append(GeometrySample(str(index), features, targets))

    result = fit_linear_geometry(samples[:12], samples[12:])

    assert set(result["coefficients"]) == {
        "longitudinal_center_m",
        "lateral_center_m",
        "ego_speed_mps",
        "npc_speed_mps",
        "npc_heading_rad",
        "turn_radius_m",
        "turn_angle_rad",
    }
    assert max(result["validation_mae"].values()) == pytest.approx(0.0, abs=1e-10)


def _pose_rows(points: np.ndarray, headings: np.ndarray) -> list[dict[str, object]]:
    return [
        {
            "groundtruth_vehicles": [
                {
                    "pose": {
                        "position": {"x": float(x), "y": float(y)},
                        "rotation": {"z": math.degrees(float(heading))},
                    }
                }
            ]
        }
        for (x, y), heading in zip(points, headings, strict=True)
    ]


def test_measure_npc_turn_uses_settled_window_not_trace_end() -> None:
    radius = 3.4
    angle = math.radians(176.0)
    arc_step = 0.05
    turn_s = np.arange(0.0, radius * angle, arc_step)
    heading = math.pi - turn_s / radius
    # Clockwise circle starting at the origin with heading pi.
    turn_points = np.column_stack(
        (-radius * np.sin(turn_s / radius), radius - radius * np.cos(turn_s / radius))
    )
    exit_heading = math.pi - angle
    straight_s = np.arange(arc_step, 30.0, arc_step)
    straight_points = turn_points[-1] + np.column_stack(
        (straight_s * math.cos(exit_heading), straight_s * math.sin(exit_heading))
    )
    # A later road curve after the settled window must not change the angle.
    curve_s = np.arange(arc_step, 20.0, arc_step)
    curve_heading = exit_heading + curve_s / 10.0
    curve_points = straight_points[-1] + np.cumsum(
        arc_step * np.column_stack((np.cos(curve_heading), np.sin(curve_heading))),
        axis=0,
    )
    points = np.vstack((turn_points, straight_points, curve_points))
    headings = np.concatenate(
        (heading, np.full(len(straight_points), exit_heading), curve_heading)
    )

    measured = measure_npc_turn(_pose_rows(points, headings))

    assert measured is not None
    assert measured[0] == pytest.approx(radius, rel=0.02)
    assert measured[1] == pytest.approx(angle, abs=math.radians(0.5))
