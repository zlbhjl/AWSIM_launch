from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from contracts.evaluation import EvaluationRecord
from targets.registry import default_case_kind, get_target_plugin, list_target_names


def interpretable_targets() -> list[str]:
    """Targets whose plugin interprets a saved result file (``build_result_interpreter``)."""
    return [
        name
        for name in list_target_names()
        if callable(getattr(get_target_plugin(name), "build_result_interpreter", None))
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Interpret a target raw trace/result JSON into a normalized "
            "EvaluationRecord without running the full worker pipeline."
        )
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the target trace JSON or raw result JSON.",
    )
    parser.add_argument(
        "--target",
        default="awsim",
        choices=interpretable_targets(),
        help="Target name used to choose the appropriate interpreter.",
    )
    parser.add_argument(
        "--case-id",
        default=None,
        help="Optional case id override. Defaults to the input file stem.",
    )
    parser.add_argument(
        "--case-kind",
        default=None,
        help="Optional case kind override. Defaults to the interpreter default.",
    )
    parser.add_argument(
        "--config-module",
        default=None,
        help=(
            "Rule-spec module used by the interpreter. Defaults to the target's own "
            "(AWSIM: targets.awsim.case_kinds.uturn)."
        ),
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path where the normalized EvaluationRecord JSON is saved.",
    )
    return parser


def build_interpreter(args: argparse.Namespace):
    builder = getattr(get_target_plugin(args.target), "build_result_interpreter", None)
    if not callable(builder):
        raise ValueError(f"Unsupported target: {args.target}")
    return builder(
        case_kind=args.case_kind or default_case_kind(args.target),
        config_module=args.config_module,
    )


def serialize_evaluation_record(record: EvaluationRecord) -> dict[str, object]:
    return _json_ready(asdict(record))


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json_ready(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_ready(item) for item in value)
    return value


def save_payload(output_path: Path, payload: dict[str, object]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def print_summary(record: EvaluationRecord, output_path: Path) -> None:
    print("=" * 60)
    print("AWSIM_launch result interpreter finished")
    print(f"target       : {record.target}")
    print(f"case id      : {record.case_id}")
    print(f"case kind    : {record.case_kind}")
    print(f"status       : {record.status.value}")
    print(f"result json  : {output_path}")
    if record.meta.get("verifier_name"):
        print(f"verifier     : {record.meta['verifier_name']}")
    if record.meta.get("source_module"):
        print(f"source       : {record.meta['source_module']}")
    print("=" * 60)


def run_result_interpreter(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    interpreter = build_interpreter(args)
    record = interpreter.interpret_path(args.input)
    if args.case_id:
        record.case_id = args.case_id
    if args.case_kind:
        record.case_kind = args.case_kind

    payload = serialize_evaluation_record(record)
    if args.output_json:
        output_path = Path(args.output_json).expanduser().resolve()
        save_payload(output_path, payload)
        print_summary(record, output_path)
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if record.status.value == "success" else 1


def main(argv: Sequence[str] | None = None) -> int:
    return run_result_interpreter(argv)
