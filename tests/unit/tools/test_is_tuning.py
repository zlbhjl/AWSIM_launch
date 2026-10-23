from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from orchestration.sampling_plan import load_sampling_plan
from tools.validation.is_tuning import fix_trial, load_candidates, run_trials
from tools.validation.ou_proposal_designs import candidates, studies, write

BASE = Path("configs/dynamics_importance_ou_baseline.yaml")


def _candidates(tmp_path: Path, trials: list[dict], budget: int = 10) -> Path:
    path = tmp_path / "candidates.yaml"
    path.write_text(yaml.safe_dump({"budget": budget, "trials": trials}), encoding="utf-8")
    return path


def test_trials_are_recorded_ranked_and_fixed(tmp_path: Path) -> None:
    trials = [
        {"name": "weak", "noise": {"kind": "gaussian", "mean_rule": {"kind": "constant", "values": {"w": 0.1}}}},
        {"name": "strong", "noise_path_q": {"distribution": "gaussian_drift", "channel_drifts": {"w": 3.0}}},
    ]
    record = run_trials(base_plan=BASE, candidates=_candidates(tmp_path, trials), output_dir=tmp_path / "out",
                        samples=30, root_entropy=4)

    stored = json.loads((tmp_path / "out" / "tuning_record.json").read_text(encoding="utf-8"))
    assert stored["phase"] == "pilot" and [entry["name"] for entry in stored["trials"]] == ["weak", "strong"]
    assert stored["trials"][1]["final_status"] == "complete"
    assert (tmp_path / "out" / "tuning_record.png").exists()
    assert set(record["ranking"]) <= {"weak", "strong"}

    plan_out = tmp_path / "final.yaml"
    fix_trial(output_dir=tmp_path / "out", trial="strong", plan_out=plan_out)
    final = yaml.safe_load(plan_out.read_text(encoding="utf-8"))
    assert final["tuning_provenance"]["trial"] == "strong"
    assert final["proposal"]["definition"]["noise_path_q"]["channel_drifts"] == {"w": 3.0}
    assert load_sampling_plan(plan_out).proposal_id == "strong"
    with pytest.raises(ValueError, match="not in the record"):
        fix_trial(output_dir=tmp_path / "out", trial="missing", plan_out=plan_out)


def test_budget_and_names_are_enforced(tmp_path: Path) -> None:
    trial = {"name": "a", "noise": {"kind": "gaussian"}}
    with pytest.raises(ValueError, match="budget"):
        load_candidates(_candidates(tmp_path, [trial, {**trial, "name": "b"}], budget=1))
    with pytest.raises(ValueError, match="unique name"):
        load_candidates(_candidates(tmp_path, [trial, trial]))
    with pytest.raises(ValueError, match="not both"):
        load_candidates(_candidates(tmp_path, [{**trial, "noise_path_q": {"distribution": "gaussian_drift"}}]))


def test_generated_designs_parse_for_every_study(tmp_path: Path) -> None:
    from targets.dynamics.sampling import DynamicsModelSampler
    from tools.validation.is_tuning import trial_plan
    from orchestration.sampling_plan import sampling_plan_from_mapping

    write(tmp_path)
    for name, study in studies().items():
        base = yaml.safe_load((tmp_path / name / "base.yaml").read_text(encoding="utf-8"))
        for family, trials in candidates(study).items():
            assert 1 <= len(trials) <= 10, (name, family)
            for trial in trials:
                DynamicsModelSampler(sampling_plan_from_mapping(trial_plan(base, trial)))
