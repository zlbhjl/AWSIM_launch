import math

import pytest

from orchestration.importance.input_law import (
    InputVariables,
    draw_proposal,
    draw_target,
    parse_input_laws,
)

VARIABLES = InputVariables(names=("speed", "gap"), label="walk", ranges={"gap": (0.0, 10.0)})
TARGET = {"parameters": {
    "speed": {"distribution": "histogram", "bins": [{"min": 0.0, "max": 1.0, "weight": 0.5},
                                                    {"min": 1.0, "max": 3.0, "weight": 0.5}]},
    "gap": {"distribution": "uniform", "min": 0.0, "max": 10.0},
}}


def test_any_target_declares_its_variables_and_gets_p_and_q() -> None:
    laws = parse_input_laws(TARGET, {"parameters": {
        "speed": {"distribution": "mixture_target_focus", "base_weight": 0.25,
                  "focus": {"distribution": "histogram", "bins": [{"min": 1.0, "max": 3.0, "weight": 1.0}]}},
        "gap": {"distribution": "mixture_uniform_focus", "base_weight": 0.5, "focus": {"min": 0.0, "max": 1.0}},
    }}, VARIABLES)
    assert laws.weight_upper_bound == pytest.approx(4.0 * 2.0)
    draw = draw_proposal(laws, seed=3)
    assert list(draw.values) == ["speed", "gap"]
    assert draw.log_p - draw.log_q <= math.log(laws.weight_upper_bound) + 1e-12
    assert draw_proposal(laws, seed=3) == draw
    assert set(draw_target(laws, seed=3)) == {"speed", "gap"}


def test_a_proposal_without_input_laws_keeps_p() -> None:
    laws = parse_input_laws(TARGET, {}, VARIABLES)
    draw = draw_proposal(laws, seed=1)
    assert draw.log_p == draw.log_q and laws.weight_upper_bound == 1.0
    assert draw.values == draw_target(laws, seed=1)


def test_a_default_target_is_used_when_the_plan_gives_none() -> None:
    variables = InputVariables(names=("x",), label="t", default_target={"x": {"distribution": "uniform", "min": 0, "max": 2}})
    assert draw_proposal(parse_input_laws({}, {}, variables), seed=0).log_p == -math.log(2.0)
    with pytest.raises(ValueError, match="walk target_measure.definition.parameters"):
        parse_input_laws({}, {}, VARIABLES)


@pytest.mark.parametrize(("proposal", "message"), [
    ({"parameters": {"speed": {"distribution": "target"}}}, "must be exactly"),
    ({"parameters": {"speed": {"distribution": "target"},
                     "gap": {"distribution": "mixture_uniform_focus", "base_weight": 0.5,
                             "focus": {"min": 5.0, "max": 11.0}}}}, "outside scenario range"),
    ({"parameters": {"speed": {"distribution": "mixture_uniform_focus", "base_weight": 0.5,
                               "focus": {"min": 0.0, "max": 1.0}},
                     "gap": {"distribution": "target"}}}, "needs a uniform P"),
    ({"parameters": {"speed": {"distribution": "mixture_target_focus", "base_weight": 0.5,
                               "focus": {"distribution": "histogram", "bins": [{"min": 2.0, "max": 4.0, "weight": 1.0}]}},
                     "gap": {"distribution": "target"}}}, "inside P support"),
    ({"parameters": {"speed": {"distribution": "target"}, "gap": {"distribution": "uniform", "min": 0.0, "max": 5.0}}},
     "same support as P"),
])
def test_invalid_proposals_are_rejected(proposal, message) -> None:
    with pytest.raises(ValueError, match=message):
        parse_input_laws(TARGET, proposal, VARIABLES)
