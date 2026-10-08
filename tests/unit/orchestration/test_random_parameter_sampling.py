from orchestration.random_parameter_sampling import (
    RandomParameterSamplingConfig,
    RandomParameterSamplingStrategy,
)


def _strategy(seed: int = 17) -> RandomParameterSamplingStrategy:
    return RandomParameterSamplingStrategy(
        RandomParameterSamplingConfig(
            target="dynamics",
            case_kind="uturn",
            params={"jama_profile": "ai_aeb"},
            max_samples=3,
            experiment_id="dynamics-iid",
            seed=seed,
            input_distribution={
                "dx0": {"distribution": "uniform", "min": 10, "max": 25},
                "ego_speed": {"distribution": "uniform", "min": 30, "max": 40},
                "npc_speed": {"distribution": "uniform", "min": 10, "max": 25},
            },
        )
    )


def test_random_sampling_is_reproducible_and_records_provenance() -> None:
    first = _strategy().next_test_case()
    second = _strategy().next_test_case()
    different_seed = _strategy(seed=18).next_test_case()

    assert first is not None and second is not None and different_seed is not None
    assert first.input["sampled_parameters"] == second.input["sampled_parameters"]
    assert first.input["sampled_parameters"] != different_seed.input["sampled_parameters"]
    assert first.input["sampling_seed"] == 17
    assert first.input["sampling_distribution"]["dx0"]["min"] == 10.0
    assert 10.0 <= first.input["dx0"] <= 25.0


def test_sde_sampling_derives_an_independent_path_seed_per_sample() -> None:
    strategy = RandomParameterSamplingStrategy(
        RandomParameterSamplingConfig(
            target="dynamics", case_kind="uturn", params={"solver_kind": "sde", "sde_seed": 100},
            max_samples=2, input_distribution={"dx0": {"min": 15, "max": 15},
            "ego_speed": {"min": 36, "max": 36}, "npc_speed": {"min": 18, "max": 18}},
        )
    )

    first = strategy.next_test_case()
    second = strategy.next_test_case()

    assert first is not None and second is not None
    assert first.input["sde_seed"] == 101
    assert second.input["sde_seed"] == 102
