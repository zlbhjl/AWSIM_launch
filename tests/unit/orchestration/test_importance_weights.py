import math

import pytest

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from contracts.importance_sampling_plugin import InternalProposal
from orchestration.importance.weights import (
    choose_component,
    compose_internal_weight,
    mixture_log_q,
    mixture_weight_bound,
    needs_choice,
)


def _proposal(weights=(1.0,), beta=None, names=None):
    names = names or tuple(f"c{index}" for index in range(len(weights)))
    return InternalProposal(components=names, weights=tuple(weights), definition=None,
                            fingerprint_source={}, defensive_weight=beta)


def test_components_are_chosen_from_one_uniform_draw() -> None:
    assert choose_component((0.5, 0.5), None, 0.2) == 0
    assert choose_component((0.5, 0.5), None, 0.7) == 1
    assert choose_component((1.0,), 0.2, 0.1) == "target"
    assert choose_component((1.0,), 0.2, 0.5) == 0
    assert choose_component((1.0,), 1.0, 0.9) == "target"
    assert choose_component((1.0,), None, None) == 0
    assert not needs_choice((1.0,), None) and needs_choice((1.0,), 0.2) and needs_choice((0.5, 0.5), None)


def test_mixture_density_and_bound() -> None:
    log_p, log_r = -2.0, (-1.0, -3.0)
    expected = math.log(0.2 * math.exp(log_p) + 0.8 * (0.25 * math.exp(-1.0) + 0.75 * math.exp(-3.0)))
    assert mixture_log_q((0.25, 0.75), 0.2, log_p, log_r) == pytest.approx(expected)
    # A single direct component is its own density, exactly.
    assert mixture_log_q((1.0,), None, log_p, (-1.25,)) == -1.25
    assert mixture_weight_bound(("c0",), 0.2) == 5.0
    assert mixture_weight_bound(("target",), None) == 1.0
    assert mixture_weight_bound(("c0",), None) is None
    assert mixture_weight_bound(("c0",), None, component_bound=7.0) == 7.0


def _record(sampling):
    return EvaluationRecord("c", "t", "k", RunStatus.SUCCESS, output={"c_event": 1}, meta={"sampling": sampling})


def test_the_internal_ratio_is_added_to_the_input_ratio() -> None:
    sampling = {"importance_weight_status": "available", "log_p": -1.0, "log_q": -1.5,
                "log_importance_weight": 0.5,
                "internal": {"log_p": -4.0, "log_q": [-3.0], "sampled_component": 0}}
    composed = compose_internal_weight(_record(sampling), _proposal()).meta["sampling"]
    assert composed["log_p"] == -5.0 and composed["log_q"] == -4.5
    assert composed["log_importance_weight"] == -0.5
    assert composed["internal"]["support"] == "positive"


def test_records_without_a_report_or_weight_are_unchanged() -> None:
    plain = _record({"importance_weight_status": "not_applicable"})
    assert compose_internal_weight(plain, None) is plain
    with pytest.raises(ValueError, match="needs the internal proposal"):
        compose_internal_weight(_record({"importance_weight_status": "available", "log_p": 0.0, "log_q": 0.0,
                                         "log_importance_weight": 0.0,
                                         "internal": {"log_p": -1.0, "log_q": [-1.0], "sampled_component": 0}}),
                                None)
