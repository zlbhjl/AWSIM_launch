from verifiers.maude.evaluator import FormulaSpec, evaluate_formula_results


def test_evaluator_parses_true_and_false_results() -> None:
    stdout = (
        "F(collision)\nModel checking result: False\n"
        "F(ttc)\nModel checking result: True\n"
    )
    summary = evaluate_formula_results(
        stdout,
        [
            FormulaSpec(formula="F(collision)", header="c_collision"),
            FormulaSpec(formula="F(ttc)", header="c_ttc_1.5"),
        ],
        base_output={"min_distance": 3.2},
    )

    assert summary.metrics == {"c_collision": 1, "c_ttc_1.5": 0}
    assert summary.output["c_collision"] == 1
    assert summary.output["c_ttc_1.5"] == 1
    assert summary.output["min_distance"] == 0.0
    assert summary.has_error is False


def test_evaluator_marks_missing_formula_as_error() -> None:
    stdout = "F(collision)\nModel checking result: True\n"
    summary = evaluate_formula_results(
        stdout,
        [
            FormulaSpec(formula="F(collision)", header="c_collision"),
            FormulaSpec(formula="F(ttc)", header="c_ttc_1.5"),
        ],
    )

    assert summary.metrics["c_collision"] == 0
    assert summary.metrics["c_ttc_1.5"] == -1
    assert summary.missing_headers == ["c_ttc_1.5"]
    assert summary.has_error is True


def test_evaluator_applies_invalid_conditions() -> None:
    stdout = "F(collision)\nModel checking result: True\n"
    summary = evaluate_formula_results(
        stdout,
        [FormulaSpec(formula="F(collision)", header="c_collision")],
        invalid_conditions={"c_collision": 0},
    )

    assert summary.output["c_collision"] == 0
    assert summary.has_error is True
