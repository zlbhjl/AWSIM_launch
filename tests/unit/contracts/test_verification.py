from contracts.verification import FT4DResult, VerificationInput


def test_verification_input_defaults_are_empty_collections() -> None:
    verification_input = VerificationInput(
        tree_mode="basic",
        universal_dataset={"clean/0001.png#obj0"},
        events={"E1": {"sigma_pf": 0.1}},
    )

    assert verification_input.assumptions == {}
    assert verification_input.meta == {}


def test_verification_input_accepts_mixed_dataset_id_types() -> None:
    verification_input = VerificationInput(
        tree_mode="combined",
        universal_dataset={"clean/0001.png#obj0", 42},
        events={"E1": {"label": "collision"}},
        assumptions={"sigma_pf_source": "dataset"},
        meta={"source_module": "targets.bbsl.verification_input"},
    )

    assert "clean/0001.png#obj0" in verification_input.universal_dataset
    assert 42 in verification_input.universal_dataset
    assert verification_input.assumptions["sigma_pf_source"] == "dataset"
    assert verification_input.meta["source_module"] == "targets.bbsl.verification_input"


def test_ft4d_result_keeps_required_fields() -> None:
    result = FT4DResult(
        tree_mode="basic",
        top_sigma_pe=0.012,
        confidence=0.97,
        node_summaries=[{"node_id": "root", "confidence": 0.97}],
        raw_result={"top_event": "collision"},
    )

    assert result.tree_mode == "basic"
    assert result.top_sigma_pe == 0.012
    assert result.confidence == 0.97
    assert result.node_summaries == [{"node_id": "root", "confidence": 0.97}]
    assert result.raw_result == {"top_event": "collision"}
