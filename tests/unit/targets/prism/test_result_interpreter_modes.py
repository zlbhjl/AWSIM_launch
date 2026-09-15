from targets.prism.result_interpreter import PrismResultInterpreter


class RejectingChecker:
    def evaluate(self, _trace_summary):
        raise AssertionError("Maude must not run for an exact model-check record")


def test_exact_model_check_record_is_not_treated_as_a_sample() -> None:
    interpreter = PrismResultInterpreter(checker=RejectingChecker())

    record = interpreter.interpret_payload(
        {
            "record_kind": "exact_model_check",
            "model": "simple_reliability_dtmc",
            "constants": {"P_NORMAL_FAILURE": 0.1},
            "horizon": 20,
            "property_results": {
                "eventual_failure": 1.0,
                "bounded_failure": 0.25,
            },
        },
        case_id="exact",
    )

    assert record.meta["record_kind"] == "exact_model_check"
    assert record.output == {
        "prism_eventual_failure_probability": 1.0,
        "prism_bounded_failure_probability": 0.25,
    }
    assert "c_failure" not in record.output
