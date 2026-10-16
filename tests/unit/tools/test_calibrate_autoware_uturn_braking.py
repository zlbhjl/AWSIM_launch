import numpy as np
import pytest

from tools.analysis.calibrate_autoware_uturn_braking import (
    BrakingSample,
    braking_speed_curve,
    calibrate,
    extract_braking_sample,
    fit_braking_curve,
)


def _synthetic_trace() -> dict[str, object]:
    times = np.arange(0.0, 6.0, 0.025)
    acceleration = np.zeros_like(times)
    ramp = (times >= 2.6) & (times < 2.8)
    acceleration[ramp] = -4.0 * (times[ramp] - 2.6) / 0.2
    acceleration[times >= 2.8] = -4.0
    ego_speed = np.empty_like(times)
    ego_speed[0] = 10.0
    for index in range(1, len(times)):
        ego_speed[index] = max(
            0.0,
            ego_speed[index - 1] + acceleration[index - 1] * (times[index] - times[index - 1]),
        )
    rows = []
    for time_sec, speed in zip(times, ego_speed, strict=True):
        npc_heading = 100.0 if time_sec < 2.0 else 97.0 - 20.0 * (time_sec - 2.0)
        rows.append(
            {
                "timestamp": float(time_sec),
                "groundtruth_ego": {
                    "twist": {"linear": {"x": float(speed), "y": 0.0}}
                },
                "groundtruth_vehicles": [
                    {
                        "twist": {"linear": {"x": 5.0, "y": 0.0}},
                        "pose": {"rotation": {"z": float(npc_heading)}},
                    }
                ],
            }
        )
    return {"groundtruth_kinematic": rows}


def test_extract_braking_sample_recovers_synthetic_response() -> None:
    sample = extract_braking_sample(_synthetic_trace(), source="synthetic.json")

    assert sample is not None
    assert sample.response_delay_sec == pytest.approx(0.6, abs=0.15)
    assert sample.jerk_ramp_sec == pytest.approx(0.2, abs=0.15)
    assert sample.max_deceleration_mps2 == pytest.approx(4.0, abs=0.4)


def test_calibrate_requires_ten_samples_and_uses_medians() -> None:
    samples = [
        BrakingSample(str(index), 0.5 + index * 0.01, 0.2, 3.8, 10.0, 5.0)
        for index in range(10)
    ]

    result = calibrate(samples)

    assert result["profile"]["t_delay"] == pytest.approx(0.545)
    assert result["profile"]["t_jerk"] == pytest.approx(0.2)
    assert result["profile"]["a_max"] == pytest.approx(3.8)
    with pytest.raises(ValueError, match="at least 10"):
        calibrate(samples[:9])


def test_fit_braking_curve_recovers_exact_profile_from_speed_trace() -> None:
    elapsed = np.arange(0.0, 3.0, 0.02)
    speeds = braking_speed_curve(
        elapsed,
        initial_speed_mps=10.0,
        delay_sec=0.6,
        ramp_sec=0.1,
        deceleration_mps2=3.0,
    )

    fitted = fit_braking_curve(elapsed, speeds, initial=(0.4, 0.3, 4.0))

    assert fitted == pytest.approx((0.6, 0.1, 3.0), abs=1e-3)


def test_braking_speed_curve_never_reverses() -> None:
    speeds = braking_speed_curve(
        np.array([0.0, 1.0, 10.0]),
        initial_speed_mps=2.0,
        delay_sec=0.0,
        ramp_sec=0.1,
        deceleration_mps2=3.0,
    )

    assert speeds[0] == pytest.approx(2.0)
    assert speeds[-1] == 0.0
