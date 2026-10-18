from contracts.experiment import Guarantee, SampleKind, Strategy, StoppingRuleSpec, combined_guarantee
from orchestration.fixed_parameter_sampling import FixedParameterSamplingStrategy
from orchestration.random_parameter_sampling import RandomParameterSamplingStrategy
from orchestration.replay import ReplayCsvStrategy
from orchestration.strategy import ActiveLearningStrategist, FixedCaseStrategy, ParameterCaseStrategy


def test_existing_strategies_follow_the_strategy_protocol() -> None:
    for strategy_class in (
        ActiveLearningStrategist, FixedCaseStrategy, ParameterCaseStrategy, ReplayCsvStrategy,
        FixedParameterSamplingStrategy, RandomParameterSamplingStrategy,
    ):
        assert callable(getattr(strategy_class, "next_test_case", None)), strategy_class
    assert Strategy is not None


def test_a_rule_keeps_its_guarantee_only_for_the_samples_it_needs() -> None:
    rule = StoppingRuleSpec(name="dkw_fixed_strict", method="dkw", guarantee=Guarantee.FIXED_SAMPLE)

    assert combined_guarantee(rule, SampleKind.IID_UNIFORM) is Guarantee.FIXED_SAMPLE
    assert combined_guarantee(rule, SampleKind.ADAPTIVE) is Guarantee.NONE
