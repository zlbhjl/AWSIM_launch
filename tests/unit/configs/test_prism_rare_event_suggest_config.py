from __future__ import annotations

from pathlib import Path

from orchestration.sampling_plan import load_sampling_plan
from targets.prism.model_catalog import get_model_definition
from targets.prism.proposal import build_proposal_candidates


def test_human_configured_suggest_plan_constructs_nine_candidates() -> None:
    project_root = Path(__file__).resolve().parents[3]
    plan = load_sampling_plan(project_root / "configs" / "prism_rare_event_suggest.yaml")
    constants = plan.target_measure_definition["constants"]

    candidates = build_proposal_candidates(
        plan,
        get_model_definition("simple_reliability_dtmc"),
        target_constants=constants,
    )

    assert len(candidates) == 9
    assert all(candidate.diagnostics["search_policy"] == "suggest" for candidate in candidates)
