from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Iterable

from contracts.verification import VerificationInput
from evaluation.ft4d_service import run_ft4d
from targets.bbsl.dataset_adapter import BBSLExperimentAdapter
from targets.bbsl.event_builder import BBSLEventSetBuilder
from targets.bbsl.result_interpreter import ResultInterpreter
from targets.bbsl.runner import run_bbsl_experiment
from targets.bbsl.verification_input import VerificationInputBuilder


BASIC_EVENT_MAPPING = {
    "SALT_PEPPER": "salt_pepper",
    "OCCLUSION": "occlusion",
    "BLUR": "blur",
}

COMBINED_EVENT_MAPPING = {
    **BASIC_EVENT_MAPPING,
    "SP_OCC": "sp_occ",
    "SP_BLUR": "sp_blur",
    "OCC_BLUR": "occ_blur",
    "ALL_THREE": "all_three",
}


def evaluate_ft4d_from_bbsl_output(
    output_json_path: str | Path,
    *,
    tree_mode: str = "basic",
    sigma_pf_source: str | None = None,
    and_rule: str | None = None,
    sigma_pf_assumption_overrides: Mapping[str, float] | None = None,
    result_interpreter: ResultInterpreter | None = None,
    verification_input_builder: VerificationInputBuilder | None = None,
    ft4d_runner=None,
) -> dict[str, object]:
    interpreter = result_interpreter or ResultInterpreter()
    builder = verification_input_builder or VerificationInputBuilder(
        interpreter=interpreter
    )
    runner = ft4d_runner or run_ft4d

    record = interpreter.interpret_fixture(output_json_path)
    sigma_pf_assumptions_used = _merge_sigma_pf_assumptions(
        record.input.get("sigma_pf_assumptions", {}),
        sigma_pf_assumption_overrides,
    )
    effective_sigma_pf_source = sigma_pf_source or str(
        record.input.get("sigma_pf_source", "dataset")
    )
    effective_and_rule = and_rule or str(record.input.get("and_rule", "min"))

    result = {
        "source_output_json": str(Path(output_json_path).expanduser().resolve()),
        "tree_mode": tree_mode,
        "active_conditions": record.input.get("active_conditions", []),
        "sigma_pf_source": effective_sigma_pf_source,
        "sigma_pf_assumptions_used": sigma_pf_assumptions_used,
        "sigma_pb_mode": record.input.get("sigma_pb_mode", "raw"),
        "and_rule": effective_and_rule,
    }

    if tree_mode == "all":
        local_runs = {}
        top_sigma_pe = {}
        confidence = {}
        for sub_tree_mode in ("basic", "combined"):
            verification_input = builder.build(
                record,
                tree_mode=sub_tree_mode,
                assumptions={
                    "sigma_pf_source": effective_sigma_pf_source,
                    "sigma_pf_assumptions": sigma_pf_assumptions_used,
                    "and_rule": effective_and_rule,
                },
            )
            run_payload = _run_ft4d_from_verification_input(
                verification_input,
                ft4d_runner=runner,
            )
            local_runs[sub_tree_mode] = run_payload
            top_sigma_pe[sub_tree_mode] = run_payload["top_sigma_pe"]
            confidence[sub_tree_mode] = run_payload["confidence"]
        result["local_runs"] = local_runs
        result["top_sigma_pe"] = top_sigma_pe
        result["confidence"] = confidence
        return result

    verification_input = builder.build(
        record,
        tree_mode=tree_mode,
        assumptions={
            "sigma_pf_source": effective_sigma_pf_source,
            "sigma_pf_assumptions": sigma_pf_assumptions_used,
            "and_rule": effective_and_rule,
        },
    )
    result.update(_run_ft4d_from_verification_input(verification_input, ft4d_runner=runner))
    return result


def evaluate_ft4d_from_bbsl_batch_outputs(
    clean_output_json_path: str | Path,
    batch_output_paths: Iterable[str | Path],
    *,
    tree_mode: str = "basic",
    sigma_pf_source: str = "dataset",
    sigma_pb_mode: str = "delta-clean",
    and_rule: str = "min",
    sigma_pf_assumption_overrides: Mapping[str, float] | None = None,
    adapter: BBSLExperimentAdapter | None = None,
    event_builder: BBSLEventSetBuilder | None = None,
    ft4d_runner=None,
) -> dict[str, object]:
    bridge_adapter = adapter or BBSLExperimentAdapter()
    builder = event_builder or BBSLEventSetBuilder(bridge_adapter)
    runner = ft4d_runner or run_ft4d

    clean_path = Path(clean_output_json_path).expanduser().resolve()
    normalized_batch_paths = [
        str(Path(path).expanduser().resolve()) for path in batch_output_paths
    ]
    clean_output = bridge_adapter.load_output(str(clean_path))
    built = builder.build_event_inputs_from_batch_outputs(
        clean_output,
        _iter_bbsl_batch_outputs(bridge_adapter, normalized_batch_paths),
        sigma_pf_source=sigma_pf_source,
        sigma_pb_mode=sigma_pb_mode,
        and_rule=and_rule,
    )
    sigma_pf_assumptions_used = _merge_sigma_pf_assumptions(
        built.get("sigma_pf_assumptions", {}),
        sigma_pf_assumption_overrides,
    )
    built = dict(built)
    built["sigma_pf_assumptions"] = sigma_pf_assumptions_used

    result = {
        "clean_baseline_json": str(clean_path),
        "source_batch_jsons": normalized_batch_paths,
        "tree_mode": tree_mode,
        "active_conditions": built["active_conditions"],
        "sigma_pf_source": sigma_pf_source,
        "sigma_pf_assumptions_used": sigma_pf_assumptions_used,
        "sigma_pb_mode": sigma_pb_mode,
        "and_rule": and_rule,
        "batch_count": built.get("batch_count", len(normalized_batch_paths)),
    }

    if tree_mode == "all":
        local_runs = {}
        top_sigma_pe = {}
        confidence = {}
        for sub_tree_mode in ("basic", "combined"):
            verification_input = _verification_input_from_built(
                built,
                tree_mode=sub_tree_mode,
                sigma_pf_source=sigma_pf_source,
                and_rule=and_rule,
            )
            run_payload = _run_ft4d_from_verification_input(
                verification_input,
                ft4d_runner=runner,
            )
            local_runs[sub_tree_mode] = run_payload
            top_sigma_pe[sub_tree_mode] = run_payload["top_sigma_pe"]
            confidence[sub_tree_mode] = run_payload["confidence"]
        result["local_runs"] = local_runs
        result["top_sigma_pe"] = top_sigma_pe
        result["confidence"] = confidence
        return result

    verification_input = _verification_input_from_built(
        built,
        tree_mode=tree_mode,
        sigma_pf_source=sigma_pf_source,
        and_rule=and_rule,
    )
    result.update(_run_ft4d_from_verification_input(verification_input, ft4d_runner=runner))
    return result


def run_bbsl_and_evaluate_ft4d(
    *,
    target_repo: str,
    mini: bool = False,
    max_images: int | None = None,
    tree: str = "basic",
    sigma_pf_source: str = "dataset",
    sigma_pb_mode: str = "delta-clean",
    and_rule: str = "min",
    detect_timeout: int | None = None,
    reuse_existing_output: bool = False,
    sigma_pf_assumption_overrides: Mapping[str, float] | None = None,
    adapter: BBSLExperimentAdapter | None = None,
) -> dict[str, object]:
    bridge_adapter = adapter or BBSLExperimentAdapter()
    if reuse_existing_output:
        output_json_path = bridge_adapter.default_output_path(target_repo, mini)
    else:
        output_json_path = run_bbsl_experiment(
            target_repo,
            mini=mini,
            max_images=max_images,
            tree=tree,
            sigma_pf_source=sigma_pf_source,
            sigma_pb_mode=sigma_pb_mode,
            and_rule=and_rule,
            detect_timeout=detect_timeout,
        )

    return evaluate_ft4d_from_bbsl_output(
        output_json_path,
        tree_mode=tree,
        sigma_pf_source=sigma_pf_source,
        and_rule=and_rule,
        sigma_pf_assumption_overrides=sigma_pf_assumption_overrides,
    )


def _run_ft4d_from_verification_input(
    verification_input: VerificationInput,
    *,
    ft4d_runner=None,
) -> dict[str, object]:
    runner = ft4d_runner or run_ft4d
    ft4d_result = runner(verification_input)
    tree_report = ft4d_result.raw_result["tree_report"]
    return {
        "tree_mode": verification_input.tree_mode,
        "event_inputs": _serialize_event_inputs(verification_input.events),
        "local_ft4d_report": tree_report,
        "rendered_tree": _render_tree_report(tree_report),
        "top_sigma_pe": ft4d_result.top_sigma_pe,
        "confidence": ft4d_result.confidence,
        "node_summaries": ft4d_result.node_summaries,
        "raw_result": ft4d_result.raw_result,
    }


def _verification_input_from_built(
    built: Mapping[str, Any],
    *,
    tree_mode: str,
    sigma_pf_source: str,
    and_rule: str,
) -> VerificationInput:
    mapping = _event_mapping(tree_mode)
    event_inputs: dict[str, dict[str, object]] = {}
    for event_id, condition_name in mapping.items():
        metrics = built["events"][condition_name]
        event_inputs[event_id] = {
            "dataset_d": set(metrics.get("dataset_d", [])),
            "dataset_e": set(metrics.get("dataset_e", [])),
            "total_count": int(metrics.get("total_count", 0) or 0),
            "correct_count": int(metrics.get("correct_count", 0) or 0),
            "error_count": int(metrics.get("error_count", 0) or 0),
            "sigma_pb": float(metrics.get("sigma_pb", 0.0) or 0.0),
            "sigma_pb_mode": metrics.get("sigma_pb_mode", built.get("sigma_pb_mode", "raw")),
            "recognition_test": metrics.get("recognition_test"),
        }

    assumptions = {
        "tree_path": str(_tree_path_for_mode(tree_mode)),
        "sigma_pf_source": sigma_pf_source,
        "sigma_pf_assumptions": dict(built.get("sigma_pf_assumptions", {})),
        "sigma_pb_mode": built.get("sigma_pb_mode", "raw"),
        "and_rule": and_rule,
    }
    if built.get("statistical_test_config"):
        assumptions["statistical_test_config"] = dict(
            built.get("statistical_test_config", {})
        )

    return VerificationInput(
        tree_mode=tree_mode,
        universal_dataset=set(built.get("universal_dataset", set())),
        events=event_inputs,
        assumptions=assumptions,
        meta={
            "source_module": "targets.bbsl.ft4d_bridge",
            "target": "bbsl",
            "tree_mode": tree_mode,
        },
    )


def _iter_bbsl_batch_outputs(
    adapter: BBSLExperimentAdapter,
    batch_output_paths: Iterable[str],
):
    for path in batch_output_paths:
        payload = adapter.load_output(path)
        payload["source_output_json"] = path
        yield payload


def _tree_path_for_mode(tree_mode: str) -> Path:
    if tree_mode not in {"basic", "combined"}:
        raise ValueError(f"Unsupported tree mode: {tree_mode!r}")
    filename = "tree_basic.json" if tree_mode == "basic" else "tree_bbsl.json"
    return (
        Path(__file__).resolve().parents[2]
        / "verification_core"
        / "ft4d"
        / "config"
        / filename
    ).resolve()


def _event_mapping(tree_mode: str) -> dict[str, str]:
    if tree_mode == "basic":
        return BASIC_EVENT_MAPPING
    if tree_mode == "combined":
        return COMBINED_EVENT_MAPPING
    raise ValueError(f"Unsupported tree mode: {tree_mode!r}")


def _merge_sigma_pf_assumptions(
    base: Mapping[str, Any] | None,
    overrides: Mapping[str, float] | None,
) -> dict[str, object]:
    merged = dict(base or {})
    if overrides:
        merged.update(overrides)
    return merged


def _serialize_event_inputs(event_inputs: Mapping[str, Mapping[str, object]]) -> dict[str, dict[str, object]]:
    serialized: dict[str, dict[str, object]] = {}
    for event_id, payload in event_inputs.items():
        serialized[event_id] = {
            **{
                key: value
                for key, value in payload.items()
                if key not in {"dataset_d", "dataset_e"}
            },
            "dataset_d": sorted(payload.get("dataset_d", [])),
            "dataset_e": sorted(payload.get("dataset_e", [])),
        }
    return serialized


def _render_tree_report(report: Mapping[str, Any]) -> str:
    labels = report.get("labels", {})
    lines: list[str] = []

    def build(node: Mapping[str, Any], indent: str = "", is_last: bool = True) -> None:
        node_id = node["id"]
        label = labels.get(node_id, node_id)
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
