from __future__ import annotations

from apps.cli.prism_importance_main import build_parser


def test_cli_accepts_suggest_without_max_samples() -> None:
    args = build_parser().parse_args(
        [
            "--sampling-plan", "plan.yaml",
            "--output-root", "out",
            "--proposal-mode", "suggest",
        ]
    )

    assert args.proposal_mode == "suggest"
    assert args.max_samples is None


def test_cli_defaults_to_fixed_sample_mode() -> None:
    args = build_parser().parse_args(
        ["--sampling-plan", "plan.yaml", "--output-root", "out", "--max-samples", "10"]
    )

    assert args.proposal_mode == "fixed_samples"
    assert args.max_samples == 10


def test_cli_accepts_weighted_sequential_thresholds() -> None:
    args = build_parser().parse_args(
        [
            "--sampling-plan", "plan.yaml",
            "--output-root", "out",
            "--max-samples", "100",
            "--stop-mode", "weighted_sequential",
            "--null-upper", "0.03",
            "--alternative-lower", "0.05",
        ]
    )

    assert args.stop_mode == "weighted_sequential"
    assert args.null_upper == 0.03
    assert args.alternative_lower == 0.05
