from pathlib import Path

import pytest

from contracts.execution import RunStatus, TestCase
from targets.bbsl.backend import BBSLBackend


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "bbsl"


def test_bbsl_backend_wraps_fixture_as_raw_run_result() -> None:
    result = BBSLBackend().run(
        TestCase(
            case_id="bbsl_backend_1",
            target="bbsl",
            case_kind="full_all",
            input={"fixture_path": str(FIXTURES / "experiment_all_raw_result_mini.json")},
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert result.evidence["raw_result_json"].endswith("experiment_all_raw_result_mini.json")
    assert result.meta["backend_mode"] == "fixture"


def test_bbsl_backend_rejects_missing_fixture_path() -> None:
    with pytest.raises(ValueError, match="fixture_path"):
        BBSLBackend().run(
            TestCase(
                case_id="bbsl_backend_2",
                target="bbsl",
                case_kind="full_all",
                input={},
            )
        )


def test_bbsl_backend_runs_experiment_and_wraps_output(tmp_path: Path) -> None:
    target_repo = tmp_path / "bbsl_repo"
    output_path = target_repo / "output" / "experiment_all_raw_result_mini.json"
    output_path.parent.mkdir(parents=True)
    output_path.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_runner(
        repo,
        *,
        mini,
        max_images,
        tree,
        sigma_pf_source,
        sigma_pb_mode,
        and_rule,
        detect_timeout,
    ):
        captured["repo"] = repo
        captured["mini"] = mini
        captured["max_images"] = max_images
        captured["tree"] = tree
        captured["sigma_pf_source"] = sigma_pf_source
        captured["sigma_pb_mode"] = sigma_pb_mode
        captured["and_rule"] = and_rule
        captured["detect_timeout"] = detect_timeout
        return str(output_path)

    backend = BBSLBackend(experiment_runner=fake_runner)
    result = backend.run(
        TestCase(
            case_id="bbsl_backend_3",
            target="bbsl",
            case_kind="full_all",
            input={
                "target_repo": str(target_repo),
                "mini": True,
                "max_images": 8,
                "tree": "combined",
                "sigma_pf_source": "dataset",
                "sigma_pb_mode": "delta-clean",
                "and_rule": "min",
                "detect_timeout": 15,
            },
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert Path(result.evidence["raw_result_json"]) == output_path.resolve()
    assert result.meta["backend_mode"] == "execution"
    assert result.meta["tree_mode"] == "combined"
    assert result.meta["mini"] is True
    assert captured == {
        "repo": str(target_repo),
        "mini": True,
        "max_images": 8,
        "tree": "combined",
        "sigma_pf_source": "dataset",
        "sigma_pb_mode": "delta-clean",
        "and_rule": "min",
        "detect_timeout": 15,
    }


def test_bbsl_backend_can_reuse_existing_output_without_runner(tmp_path: Path) -> None:
    target_repo = tmp_path / "bbsl_repo"
    output_path = target_repo / "output" / "experiment_all_raw_result.json"
    output_path.parent.mkdir(parents=True)
    output_path.write_text("{}", encoding="utf-8")

    backend = BBSLBackend(
        experiment_runner=lambda *args, **kwargs: pytest.fail("runner should not be called")
    )
    result = backend.run(
        TestCase(
            case_id="bbsl_backend_4",
            target="bbsl",
            case_kind="full_all",
            input={
                "target_repo": str(target_repo),
                "reuse_existing_output": True,
            },
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert Path(result.evidence["raw_result_json"]) == output_path.resolve()
    assert result.meta["reuse_existing_output"] is True
