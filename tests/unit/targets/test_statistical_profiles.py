from targets.statistical import (
    build_statistical_region_policy,
    load_statistical_target_profile,
)


def test_awsim_and_prism_statistical_profiles_define_target_metrics() -> None:
    awsim = load_statistical_target_profile("awsim")
    prism = load_statistical_target_profile("prism")

    assert (awsim.binary_metric, awsim.dkw_metric) == ("c_collision", "min_ttc")
    assert awsim.region_policy_name == "awsim_theory"
    assert (prism.binary_metric, prism.dkw_metric, prism.region) == (
        "c_failure",
        "steps_to_failure_capped",
        "custom",
    )


def test_prism_region_policy_does_not_add_awsim_theory_columns() -> None:
    policy = build_statistical_region_policy(
        "prism",
        case_kind="simple_reliability_dtmc",
    )

    frame = policy.build_filter_frame({"steps": 20})

    assert frame.to_dict(orient="records") == [{"steps": 20}]
