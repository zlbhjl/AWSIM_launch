from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable, Sequence

from evaluation.ft4d_service import run_ft4d
from targets.awsim.result_interpreter import interpret_path
from targets.awsim.verification_input import build_verification_input


LAUNCH_DIR = Path(__file__).resolve().parents[2]
DEFAULT_TREE_PATH = (
    LAUNCH_DIR / "verification_core" / "ft4d" / "config" / "awsim_demo_tree.json"
)
DEFAULT_TRACE_PATH = (
    LAUNCH_DIR / "tests" / "fixtures" / "awsim" / "normal_trace_maude.json"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the AWSIM smoke path through the new pipeline: "
            "result_interpreter -> verification_input -> ft4d_service."
        )
    )
    parser.add_argument(
        "--tree",
        default=str(DEFAULT_TREE_PATH),
        help="Path to the FT4D JSON tree config inside AWSIM_launch.",
    )
    parser.add_argument(
        "--trace",
        default=str(DEFAULT_TRACE_PATH),
        help="Path to the AWSIM trace JSON used by the smoke run.",
    )
    parser.add_argument(
        "--sigma-pf-source",
        choices=["dataset", "assumption"],
        default="dataset",
        help="sigma_pf source passed to the FT4D service.",
    )
    parser.add_argument(
        "--and-rule",
        choices=["min", "product"],
        default="min",
        help="AND combination rule for the FT4D smoke run.",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path to save the FT4D smoke result JSON.",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(list(argv) if argv is not None else None)


def build_demo_event_definitions() -> dict[str, dict[str, str | None]]:
    return {
        "HIGH_SPEED": {
            "dataset_filter": None,
            "error_filter": "output.c_collision",
            "target_column": "c_collision",
        },
        "SHORT_GAP": {
            "dataset_filter": None,
            "error_filter": "output.c_ttc_1.5",
            "target_column": "c_ttc_1.5",
        },
        "LOW_TTC": {
            "dataset_filter": None,
            "error_filter": "output.c_ttc_0.9",
            "target_column": "c_ttc_0.9",
        },
    }


def run_smoke(
    trace_path: str | Path,
    tree_path: str | Path,
    sigma_pf_source: str,
    and_rule: str,
    *,
    interpret_path_fn: Callable[[str | Path], object] = interpret_path,
    build_verification_input_fn: Callable[..., object] = build_verification_input,
    ft4d_runner: Callable[[object], object] = run_ft4d,
) -> dict[str, object]:
    resolved_trace_path = Path(trace_path).expanduser().resolve()
    resolved_tree_path = Path(tree_path).expanduser().resolve()

    record = interpret_path_fn(resolved_trace_path)
    verification_input = build_verification_input_fn(
        record,
        build_demo_event_definitions(),
        assumptions={
            "tree_path": str(resolved_tree_path),
            "sigma_pf_source": sigma_pf_source,
            "and_rule": and_rule,
        },
        meta={"tree_path": str(resolved_tree_path)},
    )
    ft4d_result = ft4d_runner(verification_input)
    tree_report = ft4d_result.raw_result["tree_report"]

    return {
        "trace_path": str(resolved_trace_path),
        "tree_path": str(resolved_tree_path),
        "record_status": record.status.value,
        "record_output": record.output,
        "verification_input": {
            "tree_mode": verification_input.tree_mode,
            "universal_dataset": sorted(verification_input.universal_dataset),
            "events": {
                event_id: {
                    **{
                        key: value
                        for key, value in metrics.items()
                        if key not in {"dataset_d", "dataset_e"}
                    },
                    "dataset_d": sorted(metrics["dataset_d"]),
                    "dataset_e": sorted(metrics["dataset_e"]),
                }
                for event_id, metrics in verification_input.events.items()
            },
            "assumptions": verification_input.assumptions,
            "meta": verification_input.meta,
        },
        "sigma_pf_source": ft4d_result.raw_result["sigma_pf_source"],
        "and_rule": ft4d_result.raw_result["and_rule"],
        "tree_report": tree_report,
        "rendered_tree": render_tree_report(tree_report),
        "top_sigma_pe": ft4d_result.top_sigma_pe,
        "confidence": ft4d_result.confidence,
        "node_summaries": ft4d_result.node_summaries,
    }


def render_tree_report(report: dict[str, object]) -> str:
    labels = report.get("labels", {})
    lines: list[str] = []

    def build(node: dict[str, object], indent: str = "", is_last: bool = True) -> None:
        node_id = str(node["id"])
        label = labels.get(node_id, node_id) if isinstance(labels, dict) else node_id
        sigma_pf = float(node.get("sigma_pf", 0.0))
        sigma_pe = float(node.get("sigma_pe", 0.0))
        connector = "└── " if is_last else "├── "

        if node.get("type") == "basic":
            sigma_pb = float(node.get("sigma_pb", 0.0))
            line = (
                f"{indent}{connector}■ {label}  "
                f"(σpf={sigma_pf:.4f}, σpb={sigma_pb:.4f}, σpe={sigma_pe:.6f})"
            )
        else:
            gate = node.get("gate", "")
            line = (
                f"{indent}{connector}□ {label}  [{gate}]  "
                f"(σpf={sigma_pf:.4f}, σpe={sigma_pe:.6f})"
            )
        lines.append(line)

        children = node.get("children", [])
        child_indent = indent + ("    " if is_last else "│   ")
        for index, child in enumerate(children):
            build(child, child_indent, index == len(children) - 1)

    build(report["tree"])
    return "\n".join(lines)


def save_output_json(output_json: str | Path, result: dict[str, object]) -> Path:
    output_path = Path(output_json).expanduser().resolve()
    output_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return output_path


def print_summary(result: dict[str, object]) -> None:
    print("=" * 60)
    print("AWSIM_launch internal FT4D smoke run")
    print(f"trace path    : {result['trace_path']}")
    print(f"tree path     : {result['tree_path']}")
    print(f"record status : {result['record_status']}")
    print(f"sigma_pf      : {result['sigma_pf_source']}")
    print(f"and rule      : {result['and_rule']}")
    print(f"top sigma_pe  : {result['top_sigma_pe']:.6f}")
    if result["confidence"] is not None:
        print(f"confidence    : {result['confidence']:.6f}")
    print()
    print(result["rendered_tree"])
    print("=" * 60)


def run_ft4d_smoke(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_smoke(
        trace_path=args.trace,
        tree_path=args.tree,
        sigma_pf_source=args.sigma_pf_source,
        and_rule=args.and_rule,
    )
    print_summary(result)
    if args.output_json:
        output_path = save_output_json(args.output_json, result)
        print(f"Saved smoke result to {output_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run_ft4d_smoke(argv)
