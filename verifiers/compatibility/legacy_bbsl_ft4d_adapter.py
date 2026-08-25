from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from contracts.execution import RunStatus, TestCase
from evaluation.ft4d_service import run_ft4d
from external_verifiers.base import (
    ExternalVerificationRequest,
    ExternalVerificationResult,
    ExternalVerifier,
)
from targets.bbsl.backend import BBSLBackend
from targets.bbsl.profile import build_execution_profile
from targets.bbsl.result_interpreter import ResultInterpreter
from targets.bbsl.verification_input import VerificationInputBuilder


class BBSLFT4DVerifier(ExternalVerifier):
    """Legacy-compatible adapter backed by the new targets/bbsl pipeline."""

    name = "bbsl_ft4d"

    def __init__(
        self,
        *,
        backend: BBSLBackend | None = None,
        result_interpreter: ResultInterpreter | None = None,
        verification_input_builder: VerificationInputBuilder | None = None,
        ft4d_runner=None,
    ):
        self.backend = backend or BBSLBackend()
        self.result_interpreter = result_interpreter or ResultInterpreter()
        self.verification_input_builder = (
            verification_input_builder or VerificationInputBuilder()
        )
        self.ft4d_runner = ft4d_runner or run_ft4d

    def run(self, request: ExternalVerificationRequest) -> ExternalVerificationResult:
        params = self._normalized_params(request.parameters)
        launch_dir = Path(__file__).resolve().parents[2]
        command = self._build_command(
            launch_dir / "run_bbsl_local_ft4d.py",
            request.target_repo,
            params,
            launch_dir / "verification_results" / "legacy_adapter_in_process.json",
        )

        backend_input = self._build_backend_input(request.target_repo, params)
        raw_run_result = self.backend.run(
            TestCase(
                case_id=f"{request.target_name}_external_verifier",
                target="bbsl",
                case_kind=str(params.get("case_kind", "full_all")),
                input=backend_input,
                reason="legacy_external_verifier",
                meta={
                    "source_module": "verifiers.compatibility.legacy_bbsl_ft4d_adapter",
                    "legacy_verifier_name": self.name,
                },
            )
        )
        record = self.result_interpreter.interpret_raw_run_result(raw_run_result)
        summary = self._build_summary(
            params=params,
            raw_run_result=raw_run_result,
            record=record,
        )
        return ExternalVerificationResult(
            verifier_name=self.name,
            target_name=request.target_name,
            command=command,
            workdir=str(launch_dir),
            returncode=0 if record.status is RunStatus.SUCCESS else 1,
            raw_result_path=raw_run_result.evidence.get("raw_result_json"),
            summary=summary,
        )

    def _normalized_params(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "tree": raw.get("tree", "basic"),
            "sigma_pf_source": raw.get("sigma_pf_source", "dataset"),
            "sigma_pb_mode": raw.get("sigma_pb_mode", "delta-clean"),
            "and_rule": raw.get("and_rule", "min"),
            "mini": bool(raw.get("mini", False)),
            "max_images": raw.get("max_images"),
            "detect_timeout": raw.get("detect_timeout"),
            "reuse_existing_output": bool(raw.get("reuse_existing_output", False)),
            "fixture_path": raw.get("fixture_path"),
            "raw_result_json": raw.get("raw_result_json"),
            "case_kind": raw.get("case_kind", "full_all"),
        }

    def _build_backend_input(
        self,
        target_repo: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        if params.get("fixture_path"):
            return {"fixture_path": params["fixture_path"]}
        if params.get("raw_result_json"):
            return {"raw_result_json": params["raw_result_json"]}

        execution_profile = build_execution_profile(
            {
                "target_repo": target_repo,
                "mini": params["mini"],
                "max_images": params["max_images"],
                "tree_mode": (
                    "basic" if params["tree"] == "all" else params["tree"]
                ),
                "sigma_pf_source": params["sigma_pf_source"],
                "sigma_pb_mode": params["sigma_pb_mode"],
                "and_rule": params["and_rule"],
                "detect_timeout": params["detect_timeout"],
                "reuse_existing_output": params["reuse_existing_output"],
            },
            default_target_repo=target_repo,
        )
        return execution_profile.to_meta()

    def _build_command(
        self,
        script_path: Path,
        target_repo: str,
        params: Dict[str, Any],
        output_json_path: Path,
    ) -> List[str]:
        command = [
            "python3",
            str(script_path),
            "--target-repo",
            os.path.abspath(target_repo),
            "--execution-mode",
            "legacy",
            "--condition-policy",
            "all",
            "--run-mode",
            "resume",
            "--tree",
            str(params["tree"]),
            "--sigma-pf-source",
            str(params["sigma_pf_source"]),
            "--sigma-pb-mode",
            str(params["sigma_pb_mode"]),
            "--and-rule",
            str(params["and_rule"]),
            "--output-json",
            str(output_json_path),
        ]
        if params["mini"]:
            command.append("--mini")
        if params["max_images"] is not None:
            command.extend(["--max-images", str(params["max_images"])])
        if params["detect_timeout"] is not None:
            command.extend(["--detect-timeout", str(params["detect_timeout"])])
        if params["reuse_existing_output"]:
            command.append("--reuse-existing-output")
        return command

    def _build_summary(
        self,
        *,
        params: Dict[str, Any],
        raw_run_result,
        record,
    ) -> Dict[str, Any]:
        summary = {
            "status": "ok" if record.status is RunStatus.SUCCESS else record.status.value,
            "integration_mode": "legacy-adapter-to-targets-bbsl",
            "recommended_path": "targets/bbsl/* + evaluation/ft4d_service.py",
            "recommended_design_path": (
                "targets/bbsl/backend.py -> "
                "targets/bbsl/result_interpreter.py -> "
                "targets/bbsl/verification_input.py -> "
                "evaluation/ft4d_service.py"
            ),
            "tree_mode": params["tree"],
            "active_conditions": record.input.get("active_conditions"),
            "sigma_pf_source": params["sigma_pf_source"],
            "sigma_pb_mode": record.input.get("sigma_pb_mode", params["sigma_pb_mode"]),
            "and_rule": params["and_rule"],
            "raw_output_path": raw_run_result.evidence.get("raw_result_json"),
            "raw_run_status": raw_run_result.status.value,
            "evaluation_status": record.status.value,
        }
        if record.status is not RunStatus.SUCCESS:
            if "error_message" in record.meta:
                summary["error_message"] = record.meta["error_message"]
            return summary

        if params["tree"] == "all":
            local_runs: dict[str, dict[str, object]] = {}
            top_sigma_pe: dict[str, object] = {}
            confidence: dict[str, object] = {}
            for tree_mode in ("basic", "combined"):
                ft4d_result = self._run_ft4d_for_tree_mode(
                    record,
                    tree_mode=tree_mode,
                    sigma_pf_source=params["sigma_pf_source"],
                    sigma_pb_mode=params["sigma_pb_mode"],
                    and_rule=params["and_rule"],
                )
                local_runs[tree_mode] = self._serialize_ft4d_result(ft4d_result)
                top_sigma_pe[tree_mode] = ft4d_result.top_sigma_pe
                confidence[tree_mode] = ft4d_result.confidence
            summary["local_runs"] = local_runs
            summary["top_sigma_pe"] = top_sigma_pe
            summary["confidence"] = confidence
            return summary

        ft4d_result = self._run_ft4d_for_tree_mode(
            record,
            tree_mode=params["tree"],
            sigma_pf_source=params["sigma_pf_source"],
            sigma_pb_mode=params["sigma_pb_mode"],
            and_rule=params["and_rule"],
        )
        summary["top_sigma_pe"] = ft4d_result.top_sigma_pe
        summary["confidence"] = ft4d_result.confidence
        summary["node_summaries"] = ft4d_result.node_summaries
        summary["raw_ft4d_result"] = ft4d_result.raw_result
        return summary

    def _run_ft4d_for_tree_mode(
        self,
        record,
        *,
        tree_mode: str,
        sigma_pf_source: str,
        sigma_pb_mode: str,
        and_rule: str,
    ):
        verification_input = self.verification_input_builder.build(
            record,
            tree_mode=tree_mode,
            assumptions={
                "sigma_pf_source": sigma_pf_source,
                "sigma_pb_mode": sigma_pb_mode,
                "and_rule": and_rule,
            },
            meta={
                "legacy_verifier_name": self.name,
            },
        )
        return self.ft4d_runner(verification_input)

    @staticmethod
    def _serialize_ft4d_result(ft4d_result) -> Dict[str, object]:
        return {
            "tree_mode": ft4d_result.tree_mode,
            "top_sigma_pe": ft4d_result.top_sigma_pe,
            "confidence": ft4d_result.confidence,
            "node_summaries": ft4d_result.node_summaries,
        }
