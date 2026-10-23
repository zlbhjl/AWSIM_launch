from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from apps.cli import dynamics_importance_main, importance_main

PLAN = Path("configs/dynamics_importance_ou_gaussian_q1.yaml")


def _common(tmp_path: Path, *extra: str) -> list[str]:
    return ["--sampling-plan", str(PLAN), "--output-root", str(tmp_path), "--max-samples", "5",
            "--root-entropy", "11", "--artifacts", "summary", *extra]


def test_the_plan_selects_the_target_and_its_options(tmp_path: Path, capsys) -> None:
    assert importance_main.main(_common(tmp_path / "generic")) == 0
    generic = json.loads(capsys.readouterr().out)
    assert dynamics_importance_main.main(_common(tmp_path / "legacy")) == 0
    legacy = json.loads(capsys.readouterr().out)

    assert generic["comparison"]["final_status"] == "complete"
    assert generic["comparison"]["diagnostic"]["importance"]["estimate"] == \
        legacy["comparison"]["diagnostic"]["importance"]["estimate"]


def test_help_with_a_plan_lists_the_target_options(capsys) -> None:
    with pytest.raises(SystemExit):
        importance_main.main(["--sampling-plan", str(PLAN), "--help"])
    assert "--root-entropy" in capsys.readouterr().out


def test_missing_plan_and_unsupported_target_are_reported(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit):
        importance_main.main(["--output-root", str(tmp_path)])
    plan = tmp_path / "plan.yaml"
    plan.write_text(yaml.safe_dump({
        "schema_version": 1, "experiment_id": "b", "target": "bernoulli",
        "target_measure": {"id": "p", "definition": {}},
        "proposal": {"id": "q", "mode": "manual_proposal", "definition": {}},
    }), encoding="utf-8")
    with pytest.raises(SystemExit):
        importance_main.main(["--sampling-plan", str(plan), "--output-root", str(tmp_path)])
    assert "does not support importance sampling" in capsys.readouterr().err


def test_target_checks_become_cli_errors(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit):
        importance_main.run_for_target("prism", [
            "--sampling-plan", "configs/prism_rare_event_q1.yaml", "--output-root", str(tmp_path),
            "--max-samples", "5", "--stop-mode", "importance_width",
        ])
    assert "--target-width is required" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        importance_main.run_for_target("dynamics", ["--sampling-plan", str(PLAN), "--output-root", str(tmp_path)])
    assert "--max-samples is required" in capsys.readouterr().err
