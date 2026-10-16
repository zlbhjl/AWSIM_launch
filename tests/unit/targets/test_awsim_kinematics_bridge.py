import sys
from pathlib import Path

import pytest

from targets.awsim.kinematics_bridge import (
    DEFAULT_EXTRACTOR_DIR,
    KinematicsBridge,
    KinematicsBridgeConfig,
    extract_kinematics_metrics,
)


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "awsim"


def _load_legacy_pipeline():
    root_dir = Path(__file__).resolve().parents[3]
    extractor_dir = root_dir / "AW_Kinematics_Extractor"
    if str(extractor_dir) not in sys.path:
        sys.path.insert(0, str(extractor_dir))
    from AW_Kinematics_Extractor.main import AWKinematicsPipeline

    return AWKinematicsPipeline


def test_extract_kinematics_metrics_matches_legacy_pipeline_on_fixture() -> None:
    fixture_path = FIXTURES / "normal_trace_kinematics.json"
    expected = _load_legacy_pipeline()(mode="cvm", target_npcs=["npc1"]).get_metrics(str(fixture_path))

    actual = extract_kinematics_metrics(fixture_path)

    assert actual == expected


def test_kinematics_bridge_normalizes_supported_metrics_only() -> None:
    class StubPipeline:
        def get_metrics(self, filepath: str) -> dict[str, object]:
            assert filepath.endswith("normal_trace_kinematics.json")
            return {
                "min_ttc": 1.2,
                "min_distance": 3.4,
                "min_ttb": 0.7,
                "z_margin": 0.5,
                "c_collision": True,
                "ignored": "value",
            }

    bridge = KinematicsBridge(
        config=KinematicsBridgeConfig(extractor_dir=DEFAULT_EXTRACTOR_DIR),
        pipeline_factory=lambda mode, target_npcs: StubPipeline(),
    )

    metrics = bridge.extract(FIXTURES / "normal_trace_kinematics.json")

    assert metrics == {
        "min_ttc": 1.2,
        "min_distance": 3.4,
        "min_ttb": 0.7,
        "z_margin": 0.5,
        "c_collision": 1,
    }


def test_kinematics_bridge_raises_for_missing_trace() -> None:
    with pytest.raises(FileNotFoundError):
        extract_kinematics_metrics(FIXTURES / "missing_trace.json")
