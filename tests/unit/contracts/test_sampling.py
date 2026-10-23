from __future__ import annotations

import pytest

from contracts.sampling import (
    ProposalCandidate,
    SamplingMetadataValidationError,
    copy_sampling_metadata,
    normalize_sampling_metadata,
)


def test_proposal_candidate_keeps_target_definition_opaque():
    definition = {"target_specific": {"transition": [0.01, 0.02]}}

    candidate = ProposalCandidate(
        plan_id="prism_is_v1",
        proposal_id="candidate_001",
        proposal_version=1,
        definition=definition,
        reason="human-configured search",
        diagnostics={"effective_sample_size": 50.0},
    )

    assert candidate.definition is definition


def _sampling() -> dict[str, object]:
    return {
        "plan_id": "awsim_uturn_is_v1",
        "target_measure_id": "normal_input_v1",
        "proposal_id": "near_collision_v1",
        "proposal_version": 2,
        "importance_weight_status": "available",
        "log_p": -4.25,
        "log_q": -2.5,
        "log_importance_weight": -1.75,
        "sampling_mode": "manual_proposal",
    }


def test_sampling_metadata_is_copied_as_common_provenance():
    sampling = _sampling()

    copied = copy_sampling_metadata({"sampling": sampling}, {"source_module": "backend"})

    assert copied["source_module"] == "backend"
    assert copied["sampling"] == sampling
    assert copied["sampling"] is not sampling


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.pop("proposal_id"), "proposal_id must be a non-empty string"),
        (lambda value: value.__setitem__("proposal_version", 0), "positive integer"),
        (lambda value: value.__setitem__("log_importance_weight", 0.0), "log_p - log_q"),
    ],
)
def test_sampling_metadata_rejects_invalid_common_fields(mutate, message):
    value = _sampling()
    mutate(value)

    with pytest.raises(SamplingMetadataValidationError, match=message):
        normalize_sampling_metadata(value)


def test_uniform_sampling_does_not_require_a_proposal_or_log_weights():
    value = {
        "plan_id": "prism_baseline_v1",
        "target_measure_id": "original_model_v1",
        "sampling_mode": "uniform",
        "importance_weight_status": "not_applicable",
    }

    assert normalize_sampling_metadata(value) == value


def test_manual_proposal_can_be_recorded_before_log_weights_are_available():
    value = {
        "plan_id": "prism_failure_v1",
        "target_measure_id": "original_model_v1",
        "sampling_mode": "manual_proposal",
        "proposal_id": "failure_biased_v1",
        "importance_weight_status": "unavailable",
        "update_reason": "initial PRISM integration",
    }

    assert normalize_sampling_metadata(value) == value


def test_adaptive_sampling_requires_proposal_version():
    value = _sampling()
    value["sampling_mode"] = "adaptive"
    value.pop("proposal_version")

    with pytest.raises(SamplingMetadataValidationError, match="required for adaptive"):
        normalize_sampling_metadata(value)


def _zero_weight() -> dict[str, object]:
    value = _sampling()
    value.update({"weight_kind": "zero", "log_p": None, "log_importance_weight": None})
    return value


def test_zero_weight_keeps_a_finite_log_q():
    normalized = normalize_sampling_metadata(_zero_weight())

    assert normalized["weight_kind"] == "zero"
    assert normalized["log_p"] is None
    assert normalized["log_q"] == -2.5


def test_zero_weight_rejects_a_numeric_log_p():
    value = _zero_weight()
    value["log_p"] = -4.25

    with pytest.raises(SamplingMetadataValidationError, match="weight_kind='zero'"):
        normalize_sampling_metadata(value)


def test_zero_weight_rejects_a_non_finite_log_q():
    value = _zero_weight()
    value["log_q"] = float("-inf")

    with pytest.raises(SamplingMetadataValidationError, match="log_q must be a finite number"):
        normalize_sampling_metadata(value)


def test_zero_weight_requires_an_available_weight():
    value = _zero_weight()
    value["importance_weight_status"] = "unavailable"

    with pytest.raises(SamplingMetadataValidationError, match="requires importance_weight_status"):
        normalize_sampling_metadata(value)


def test_unknown_weight_kind_is_rejected():
    value = _sampling()
    value["weight_kind"] = "negative"

    with pytest.raises(SamplingMetadataValidationError, match="weight_kind must be one of"):
        normalize_sampling_metadata(value)


def test_positive_weight_kind_is_the_legacy_default():
    value = _sampling()
    value["weight_kind"] = "positive"

    assert normalize_sampling_metadata(value)["log_importance_weight"] == -1.75
