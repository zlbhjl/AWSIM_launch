"""Compare FT4D AND-rule (min/product) predictions against a direct measurement.

This validates whether composing two marginally-measured hazard conditions
(HIGH_SPEED, SHORT_GAP) through the shared FT4D AND-gate machinery
(`verification_core/ft4d`) is an accurate/efficient substitute for directly
measuring the intersection region with `--mode binomial_ci --dkw_bounds`.

`sigma_pf` for each marginal condition is a modeling assumption (the fraction
of the declared PARAM_RANGES the condition covers, e.g. 0.5 when the
threshold splits the range in half). Because AWSIM_launch's random sampler
draws each scenario parameter independently and uniformly, the intersection
region's true size is exactly `sigma_pf_a * sigma_pf_b` -- not an additional
assumption. `sigma_pb` (the collision rate within a region) is the quantity
under test: it is measured, not assumed, for all three regions.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from runtime.repository.statistical_history import StatisticalHistoryRepository

DEFAULT_TREE_CONFIG = (
    Path(__file__).resolve().parents[2]
    / "verification_core"
    / "ft4d"
    / "config"
    / "awsim_and_rule_validation_tree.json"
)

HIGH_SPEED_EVENT_ID = "HIGH_SPEED"
SHORT_GAP_EVENT_ID = "SHORT_GAP"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare an FT4D AND-rule (min/product) prediction of the "
            "HIGH_SPEED x SHORT_GAP intersection collision rate against a "
            "direct binomial_ci measurement of that intersection region."
        )
    )
    parser.add_argument(
        "--marginal-a-history",
        required=True,
        help=(
            "Path to the uturn_binomial_ci_history.csv from the HIGH_SPEED "
            "marginal run (--dkw_bounds restricted to ego_speed only)."
        ),
    )
    parser.add_argument(
        "--marginal-b-history",
        required=True,
        help=(
            "Path to the uturn_binomial_ci_history.csv from the SHORT_GAP "
            "marginal run (--dkw_bounds restricted to dx0 only)."
        ),
    )
    parser.add_argument(
        "--intersection-history",
        required=True,
        help=(
            "Path to the uturn_binomial_ci_history.csv from the direct "
            "intersection run (--dkw_bounds restricted to both ego_speed "
            "and dx0)."
        ),
    )
    parser.add_argument(
        "--sigma-pf-a",
        type=float,
        default=0.5,
        help="Assumed sigma_pf for HIGH_SPEED (fraction of PARAM_RANGES it covers).",
    )
    parser.add_argument(
        "--sigma-pf-b",
        type=float,
        default=0.5,
        help="Assumed sigma_pf for SHORT_GAP (fraction of PARAM_RANGES it covers).",
    )
    parser.add_argument(
        "--tree-config",
        default=str(DEFAULT_TREE_CONFIG),
        help="Path to the FT4D tree config used for AND-rule composition.",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Optional path to also write the comparison as a one-row CSV.",
    )
    return parser


@dataclass(frozen=True, slots=True)
class MeasuredBinomialResult:
    estimate: float
    sample_size: int
    lower_bound: float
    upper_bound: float


def read_measured_binomial_result(history_csv: str | Path) -> MeasuredBinomialResult:
    """Return the last row of a binomial_ci history CSV as a MeasuredBinomialResult.

    Reads `lower_bound`/`upper_bound` in addition to the point estimate: a
    valid accuracy check against the FT4D AND-rule prediction must compare
    against the measured *interval*, not the point estimate, since both are
    noisy statistics.
    """
    path = Path(history_csv).expanduser()
    repository = StatisticalHistoryRepository(
        scenario_name="_validate_ft4d_and_rule",
        traces_dir=path.parent,
        binomial_ci_history_csv=path,
    )
    rows = repository.read_binomial_ci_rows()
    if not rows:
        raise ValueError(f"No binomial_ci history rows found in {path}")
    last_row = rows[-1]
    try:
        return MeasuredBinomialResult(
            estimate=float(last_row["estimate"]),
            sample_size=int(float(last_row["sample_size"])),
            lower_bound=float(last_row["lower_bound"]),
            upper_bound=float(last_row["upper_bound"]),
        )
    except (KeyError, ValueError) as exc:
        raise ValueError(
            "binomial_ci history row in "
            f"{path} is missing or has an invalid 'estimate'/'sample_size'/"
            f"'lower_bound'/'upper_bound': {last_row}"
        ) from exc


def predict_and_rule_sigma_pe(
    *,
    sigma_pf_a: float,
    sigma_pb_a: float,
    sigma_pf_b: float,
    sigma_pb_b: float,
    tree_config_path: str | Path,
    and_rule: str,
) -> float:
    """Feed the two marginal measurements into the shared FT4D AND-gate logic."""
    from verification_core.ft4d.calculator import FT4DCalculator
    from verification_core.ft4d.tree import FaultTree

    tree = FaultTree.from_json(str(tree_config_path))
    calculator = FT4DCalculator(tree, sigma_pf_source="assumption", and_rule=and_rule)
    calculator.set_basic_event(HIGH_SPEED_EVENT_ID, sigma_pf=sigma_pf_a, sigma_pb=sigma_pb_a)
    calculator.set_basic_event(SHORT_GAP_EVENT_ID, sigma_pf=sigma_pf_b, sigma_pb=sigma_pb_b)
    report = calculator.calculate()
    return report["tree"]["sigma_pe"]


def predict_and_rule_sigma_pe_interval(
    *,
    sigma_pf_a: float,
    sigma_pb_a_bounds: tuple[float, float],
    sigma_pf_b: float,
    sigma_pb_b_bounds: tuple[float, float],
    tree_config_path: str | Path,
    and_rule: str,
) -> tuple[float, float]:
    """Propagate the marginals' confidence intervals through the AND-rule.

    `sigma_pe = sigma_pf * sigma_pb` is monotonically non-decreasing in
    `sigma_pb` (sigma_pf is held fixed), and both the `min` and `product`
    AND-rules (`_combine_and_rates` in verification_core/ft4d/calculator.py)
    are monotonically non-decreasing in each non-negative child value. So
    plugging in both marginals' lower bounds yields the exact lower bound of
    the composed prediction, and both upper bounds yield the exact upper
    bound -- this is interval arithmetic, not an approximation.
    """
    lower = predict_and_rule_sigma_pe(
        sigma_pf_a=sigma_pf_a,
        sigma_pb_a=sigma_pb_a_bounds[0],
        sigma_pf_b=sigma_pf_b,
        sigma_pb_b=sigma_pb_b_bounds[0],
        tree_config_path=tree_config_path,
        and_rule=and_rule,
    )
    upper = predict_and_rule_sigma_pe(
        sigma_pf_a=sigma_pf_a,
        sigma_pb_a=sigma_pb_a_bounds[1],
        sigma_pf_b=sigma_pf_b,
        sigma_pb_b=sigma_pb_b_bounds[1],
        tree_config_path=tree_config_path,
        and_rule=and_rule,
    )
    return lower, upper


def classify_bound_verdict(
    predicted_bounds: tuple[float, float],
    measured_bounds: tuple[float, float],
) -> str:
    """Compare two confidence intervals, not point estimates.

    A point-estimate comparison (predicted >= measured) cannot distinguish a
    genuine AND-rule violation from ordinary sampling noise. This only
    declares "holds"/"violated" when the two intervals do not overlap.
    """
    predicted_lower, predicted_upper = predicted_bounds
    measured_lower, measured_upper = measured_bounds
    if predicted_lower >= measured_upper:
        return "holds (predicted interval entirely above measured interval)"
    if predicted_upper <= measured_lower:
        return "violated (predicted interval entirely below measured interval)"
    return "inconclusive (intervals overlap; more samples needed to distinguish)"


def render_and_rule_comparison(
    *,
    marginal_a: MeasuredBinomialResult,
    marginal_b: MeasuredBinomialResult,
    intersection: MeasuredBinomialResult,
    sigma_pf_a: float,
    sigma_pf_b: float,
    predicted_sigma_pe_min: tuple[float, float],
    predicted_sigma_pe_product: tuple[float, float],
) -> str:
    sigma_pf_intersection = sigma_pf_a * sigma_pf_b
    measured_sigma_pe_point = sigma_pf_intersection * intersection.estimate
    measured_sigma_pe_bounds = (
        sigma_pf_intersection * intersection.lower_bound,
        sigma_pf_intersection * intersection.upper_bound,
    )

    lines = ["=" * 70]
    lines.append("FT4D AND-rule validation: HIGH_SPEED x SHORT_GAP")
    lines.append("=" * 70)
    lines.append(
        f"Marginal HIGH_SPEED   : sigma_pb={marginal_a.estimate:.4f}  "
        f"[{marginal_a.lower_bound:.4f}, {marginal_a.upper_bound:.4f}]  "
        f"(n={marginal_a.sample_size} samples)"
    )
    lines.append(
        f"Marginal SHORT_GAP    : sigma_pb={marginal_b.estimate:.4f}  "
        f"[{marginal_b.lower_bound:.4f}, {marginal_b.upper_bound:.4f}]  "
        f"(n={marginal_b.sample_size} samples)"
    )
    lines.append(
        f"Measured intersection : sigma_pb={intersection.estimate:.4f}  "
        f"[{intersection.lower_bound:.4f}, {intersection.upper_bound:.4f}]  "
        f"(n={intersection.sample_size} samples, direct binomial_ci)"
    )
    lines.append("")
    lines.append(
        "sigma_pf (region size; exact by construction under independent "
        "uniform parameter sampling):"
    )
    lines.append(
        f"  sigma_pf(HIGH_SPEED)={sigma_pf_a:.4f}  sigma_pf(SHORT_GAP)={sigma_pf_b:.4f}  "
        f"sigma_pf(intersection)={sigma_pf_intersection:.4f}"
    )
    lines.append("")
    lines.append(
        "sigma_pe = P(condition AND collision) over the full parameter space "
        "(the quantity the FT4D AND-rule upper-bound guarantee is stated in "
        "terms of). Comparisons below use confidence intervals, not point "
        "estimates, since both sides are noisy statistics:"
    )
    lines.append(
        f"  measured intersection sigma_pe : point={measured_sigma_pe_point:.6f}  "
        f"range=[{measured_sigma_pe_bounds[0]:.6f}, {measured_sigma_pe_bounds[1]:.6f}]"
    )
    for rule_name, predicted_bounds in (
        ("min", predicted_sigma_pe_min),
        ("product", predicted_sigma_pe_product),
    ):
        predicted_point = (predicted_bounds[0] + predicted_bounds[1]) / 2.0
        verdict = classify_bound_verdict(predicted_bounds, measured_sigma_pe_bounds)
        lines.append(
            f"  {rule_name:<7s} rule predicted sigma_pe: "
            f"range=[{predicted_bounds[0]:.6f}, {predicted_bounds[1]:.6f}]  "
            f"(point~={predicted_point:.6f})"
        )
        lines.append(f"    verdict: {verdict}")
    lines.append("")
    total_marginal_samples = marginal_a.sample_size + marginal_b.sample_size
    lines.append("Sample efficiency:")
    lines.append(
        f"  marginal route (HIGH_SPEED + SHORT_GAP): {total_marginal_samples} samples"
    )
    lines.append(
        f"  direct intersection route              : {intersection.sample_size} samples"
    )
    more_efficient = (
        "direct measurement" if intersection.sample_size < total_marginal_samples
        else "marginal composition"
    )
    lines.append(f"  fewer total samples used by: {more_efficient}")
    lines.append("")
    lines.append(
        "note: each interval above only carries its nominal confidence level "
        "if the source binomial_ci run did not rely on optional stopping "
        "validity (i.e. was run with --binomial-anytime-valid, or the "
        "repeated-peeking caveat documented in README.md is accepted)."
    )
    lines.append("=" * 70)
    return "\n".join(lines)


def run_validate_ft4d_and_rule(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)

    try:
        marginal_a = read_measured_binomial_result(args.marginal_a_history)
        marginal_b = read_measured_binomial_result(args.marginal_b_history)
        intersection = read_measured_binomial_result(args.intersection_history)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1

    predicted_sigma_pe_min = predict_and_rule_sigma_pe_interval(
        sigma_pf_a=args.sigma_pf_a,
        sigma_pb_a_bounds=(marginal_a.lower_bound, marginal_a.upper_bound),
        sigma_pf_b=args.sigma_pf_b,
        sigma_pb_b_bounds=(marginal_b.lower_bound, marginal_b.upper_bound),
        tree_config_path=args.tree_config,
        and_rule="min",
    )
    predicted_sigma_pe_product = predict_and_rule_sigma_pe_interval(
        sigma_pf_a=args.sigma_pf_a,
        sigma_pb_a_bounds=(marginal_a.lower_bound, marginal_a.upper_bound),
        sigma_pf_b=args.sigma_pf_b,
        sigma_pb_b_bounds=(marginal_b.lower_bound, marginal_b.upper_bound),
        tree_config_path=args.tree_config,
        and_rule="product",
    )

    rendered = render_and_rule_comparison(
        marginal_a=marginal_a,
        marginal_b=marginal_b,
        intersection=intersection,
        sigma_pf_a=args.sigma_pf_a,
        sigma_pf_b=args.sigma_pf_b,
        predicted_sigma_pe_min=predicted_sigma_pe_min,
        predicted_sigma_pe_product=predicted_sigma_pe_product,
    )
    print(rendered)

    if args.output_csv:
        sigma_pf_intersection = args.sigma_pf_a * args.sigma_pf_b
        measured_sigma_pe_bounds = (
            sigma_pf_intersection * intersection.lower_bound,
            sigma_pf_intersection * intersection.upper_bound,
        )
        output_path = Path(args.output_csv).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "sigma_pb_high_speed",
                    "sample_size_high_speed",
                    "sigma_pb_short_gap",
                    "sample_size_short_gap",
                    "sigma_pb_intersection_measured",
                    "sample_size_intersection",
                    "predicted_sigma_pe_min_lower",
                    "predicted_sigma_pe_min_upper",
                    "predicted_sigma_pe_product_lower",
                    "predicted_sigma_pe_product_upper",
                    "measured_sigma_pe_intersection_lower",
                    "measured_sigma_pe_intersection_upper",
                    "min_rule_verdict",
                    "product_rule_verdict",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "sigma_pb_high_speed": marginal_a.estimate,
                    "sample_size_high_speed": marginal_a.sample_size,
                    "sigma_pb_short_gap": marginal_b.estimate,
                    "sample_size_short_gap": marginal_b.sample_size,
                    "sigma_pb_intersection_measured": intersection.estimate,
                    "sample_size_intersection": intersection.sample_size,
                    "predicted_sigma_pe_min_lower": predicted_sigma_pe_min[0],
                    "predicted_sigma_pe_min_upper": predicted_sigma_pe_min[1],
                    "predicted_sigma_pe_product_lower": predicted_sigma_pe_product[0],
                    "predicted_sigma_pe_product_upper": predicted_sigma_pe_product[1],
                    "measured_sigma_pe_intersection_lower": measured_sigma_pe_bounds[0],
                    "measured_sigma_pe_intersection_upper": measured_sigma_pe_bounds[1],
                    "min_rule_verdict": classify_bound_verdict(
                        predicted_sigma_pe_min, measured_sigma_pe_bounds
                    ),
                    "product_rule_verdict": classify_bound_verdict(
                        predicted_sigma_pe_product, measured_sigma_pe_bounds
                    ),
                }
            )

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run_validate_ft4d_and_rule(argv)


if __name__ == "__main__":
    raise SystemExit(main())
