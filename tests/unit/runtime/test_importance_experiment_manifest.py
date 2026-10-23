from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.repository.importance_experiment_manifest import (
    ExperimentManifest,
    ManifestMismatchError,
    fingerprint,
    load_manifest,
    save_or_verify_manifest,
)


def _manifest(**changes: object) -> ExperimentManifest:
    values: dict[str, object] = {
        "experiment_id": "ou_a",
        "target_definition": {"model_id": "ou_scalar", "theta": 1.0},
        "proposal_definition": {"noise_path_q": {"distribution": "gaussian_drift"}},
        "numerics": {"dt_sec": 0.01, "horizon_sec": 1.0},
        "rng": {"policy_version": 2, "root_entropy": "123", "phase": "final", "pairing": "independent"},
        "planned_samples": 10,
        "metric": "c_event",
    }
    values.update(changes)
    return ExperimentManifest(**values)


def test_fingerprint_ignores_key_order() -> None:
    assert fingerprint({"a": 1, "b": [1, 2]}) == fingerprint({"b": [1, 2], "a": 1})
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})


def test_target_fingerprint_includes_the_numerical_map() -> None:
    assert _manifest().target_fingerprint != _manifest(numerics={"dt_sec": 0.02, "horizon_sec": 1.0}).target_fingerprint


def test_new_manifest_is_written_with_environment_and_session(tmp_path: Path) -> None:
    stored = save_or_verify_manifest(tmp_path, _manifest())

    data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert data["target_fingerprint"] == stored.target_fingerprint
    assert set(data["environment"]) == {"git_revision", "python", "numpy", "scipy"}
    assert len(data["sessions"]) == 1


def test_resume_with_the_same_settings_appends_a_session(tmp_path: Path) -> None:
    save_or_verify_manifest(tmp_path, _manifest())
    save_or_verify_manifest(tmp_path, _manifest())

    assert len(load_manifest(tmp_path).sessions) == 2


@pytest.mark.parametrize(
    ("change", "field_name"),
    [
        ({"planned_samples": 20}, "planned_samples"),
        ({"numerics": {"dt_sec": 0.02, "horizon_sec": 1.0}}, "target_fingerprint"),
        ({"proposal_definition": {"noise_path_q": {"distribution": "defensive_gaussian_drift"}}}, "proposal_fingerprint"),
        ({"rng": {"policy_version": 2, "root_entropy": "124", "phase": "final", "pairing": "independent"}}, "rng"),
    ],
)
def test_resume_with_different_settings_is_rejected(tmp_path: Path, change: dict, field_name: str) -> None:
    save_or_verify_manifest(tmp_path, _manifest())

    with pytest.raises(ManifestMismatchError, match=field_name):
        save_or_verify_manifest(tmp_path, _manifest(**change))


def test_planned_samples_must_be_positive() -> None:
    with pytest.raises(ValueError, match="planned_samples"):
        _manifest(planned_samples=0)
