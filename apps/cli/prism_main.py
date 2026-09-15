#!/usr/bin/env python3
"""Run the self-contained PRISM + Maude statistical-verification demonstration."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import uuid

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from contracts.statistics import StatisticalRequest
from evaluation.dkw import DKWService
from evaluation.ft4d_service import run_ft4d
from orchestration.orchestrator import Orchestrator, OrchestratorConfig
from targets.prism.verification_input import build_verification_input
from targets.prism.dataset_adapter import PrismDatasetAdapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PRISM DTMC sampling, Maude verdicts, and existing statistical services")
    parser.add_argument("--samples", type=int, default=20, help="Maximum independent PRISM paths")
    parser.add_argument("--min-samples", type=int, default=10)
    parser.add_argument("--target-width", type=float, default=0.30, help="Wilson CI total width required to stop early")
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--p-normal-degrade", type=float, default=0.10)
    parser.add_argument("--p-normal-failure", type=float, default=0.01)
    parser.add_argument("--p-degraded-normal", type=float, default=0.30)
    parser.add_argument("--p-degraded-failure", type=float, default=0.10)
    parser.add_argument("--prism-executable", default="prism")
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/prism"))
    parser.add_argument("--report", type=Path, default=Path("artifacts/prism/report.json"))
    parser.add_argument("--timeout-sec", type=float, default=30.0)
    parser.add_argument("--experiment-id", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.samples < 1 or args.min_samples < 1 or args.min_samples > args.samples:
        raise SystemExit("samples must be >= min-samples >= 1")
    experiment_id = args.experiment_id or f"prism-{uuid.uuid4().hex[:12]}"
    output_root = args.output_root.expanduser().resolve()
    records_path = output_root / f"{experiment_id}.records.jsonl"
    dataset_path = output_root / f"{experiment_id}.samples.csv"
    summary = Orchestrator().run(
        OrchestratorConfig(
            output=str(records_path),
            dataset_csv=str(dataset_path),
            target="prism",
            case_kind="simple_reliability_dtmc",
            run_mode="binomial_ci",
            params={
                "model": "simple_reliability_dtmc",
                "steps": args.steps,
                "p_normal_degrade": args.p_normal_degrade,
                "p_normal_failure": args.p_normal_failure,
                "p_degraded_normal": args.p_degraded_normal,
                "p_degraded_failure": args.p_degraded_failure,
                "prism_executable": args.prism_executable,
                "timeout_sec": args.timeout_sec,
                "output_root": str(output_root),
            },
            experiment_id=experiment_id,
            max_samples=args.samples,
            binomial_target="c_failure",
            binomial_method="wilson",
            binomial_confidence=0.95,
            binomial_target_width=args.target_width,
            binomial_min_samples=args.min_samples,
            queue_high_water=1,
            queue_low_water=0,
            refresh_interval=None,
            container_profile="prism_maude",
        )
    )
    all_records = _load_records(records_path)
    records = PrismDatasetAdapter.sample_records(all_records)
    report_payload = summary.get("statistical_report")
    if not isinstance(report_payload, dict):
        raise RuntimeError("PRISM sampling finished without a statistical report")

    dkw_request = StatisticalRequest(method="dkw", metric="steps_to_failure_capped", confidence=0.95, target_width=float(args.steps), options={"q": 0.5, "region": "custom", "use_kde_weighting": False, "minimum_value": 0.0})
    dkw = DKWService().evaluate_request(records, dkw_request)
    ft4d = run_ft4d(build_verification_input(records[-1])) if records and records[-1].status.value == "success" else None
    payload = {
        "target": "prism", "model": "simple_reliability_dtmc", "sample_count": len(records),
        "experiment_id": experiment_id,
        "sampling_signature": report_payload.get("sampling_signature"),
        "binomial_ci": report_payload, "dkw": _report_payload(dkw),
        "exact_model_check": report_payload.get("exact_model_check"),
        "ft4d": None if ft4d is None else {"tree_mode": ft4d.tree_mode, "top_sigma_pe": ft4d.top_sigma_pe, "confidence": ft4d.confidence},
        "records": [_record_payload(record) for record in records],
        "execution_records": [_record_payload(record) for record in all_records],
        "orchestrator": summary,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
    print(json.dumps({"report": str(args.report.resolve()), "sample_count": len(records), "binomial_ci": payload["binomial_ci"], "dkw": payload["dkw"]}, ensure_ascii=False, indent=2, default=_json_default))
    return 0


def _load_records(path: Path) -> list[EvaluationRecord]:
    records: list[EvaluationRecord] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        records.append(
            EvaluationRecord(
                case_id=str(payload["case_id"]),
                target=str(payload["target"]),
                case_kind=str(payload["case_kind"]),
                status=RunStatus(str(payload["status"])),
                input=dict(payload.get("input", {})),
                output=dict(payload.get("output", {})),
                evidence={str(k): str(v) for k, v in dict(payload.get("evidence", {})).items()},
                meta=dict(payload.get("meta", {})),
            )
        )
    return records


def _report_payload(report) -> dict[str, object]:
    diagnostics = {key: value for key, value in report.diagnostics.items() if key != "filtered_df"}
    return {"method": report.method, "metric": report.metric, "sample_count": report.sample_count, "estimate": report.estimate, "interval": report.interval, "sufficient": report.sufficient, "next_action": report.next_action, "diagnostics": diagnostics}


def _record_payload(record) -> dict[str, object]:
    return {"case_id": record.case_id, "status": record.status.value, "input": record.input, "output": record.output, "evidence": record.evidence, "meta": record.meta}


def _json_default(value):
    """Convert scalar values returned by NumPy/pandas without losing numbers."""
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")


if __name__ == "__main__":
    raise SystemExit(main())
