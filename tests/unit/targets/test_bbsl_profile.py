from pathlib import Path

import pytest

from targets.bbsl.profile import BBSLExecutionProfile, build_execution_profile


def test_build_execution_profile_normalizes_defaults(tmp_path: Path) -> None:
    target_repo = tmp_path / "bbsl_repo"

    profile = build_execution_profile(
        {"target_repo": str(target_repo)},
        default_target_repo="/tmp/unused",
    )

    assert profile == BBSLExecutionProfile(
        target_repo=str(target_repo.resolve()),
        mini=False,
        max_images=None,
        tree_mode="basic",
        sigma_pf_source="dataset",
        sigma_pb_mode="delta-clean",
        and_rule="min",
        detect_timeout=None,
        reuse_existing_output=False,
    )


def test_build_execution_profile_accepts_tree_alias_and_string_values(
    tmp_path: Path,
) -> None:
    target_repo = tmp_path / "bbsl_repo"

    profile = build_execution_profile(
        {
            "target_repo": str(target_repo),
            "mini": "true",
            "max_images": "8",
            "tree": "combined",
            "sigma_pf_source": "dataset",
            "sigma_pb_mode": "raw",
            "and_rule": "product",
            "detect_timeout": "15",
            "reuse_existing_output": "yes",
        },
        default_target_repo="/tmp/unused",
    )

    assert profile.mini is True
    assert profile.max_images == 8
    assert profile.tree_mode == "combined"
    assert profile.sigma_pb_mode == "raw"
    assert profile.and_rule == "product"
    assert profile.detect_timeout == 15
    assert profile.reuse_existing_output is True


def test_build_execution_profile_uses_default_target_repo_when_missing(
    tmp_path: Path,
) -> None:
    default_repo = tmp_path / "default_bbsl_repo"

    profile = build_execution_profile(
        {"mini": True},
        default_target_repo=str(default_repo),
    )

    assert profile.target_repo == str(default_repo.resolve())
    assert profile.mini is True


def test_build_execution_profile_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="explicit input parameters"):
        build_execution_profile({}, default_target_repo="/tmp/bbsl")
