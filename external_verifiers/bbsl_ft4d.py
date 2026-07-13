from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, List

from .base import (
    ExternalVerificationRequest,
    ExternalVerificationResult,
    ExternalVerifier,
)


class BBSLFT4DVerifier(ExternalVerifier):
    name = "bbsl_ft4d"

    def run(self, request: ExternalVerificationRequest) -> ExternalVerificationResult:
        params = self._normalized_params(request.parameters)
        target_repo = os.path.abspath(request.target_repo)
        script_path = os.path.join(target_repo, "examples", "run_full_experiment_all.py")
        if not os.path.exists(script_path):
            raise FileNotFoundError(
                f"BBSL-test runner not found: {script_path}"
            )

        command = self._build_command(script_path, params)
        completed = subprocess.run(
            command,
            cwd=target_repo,
            check=False,
        )
        raw_result_path = os.path.join(
            target_repo,
            "output",
            "experiment_all_result_mini.json" if params["mini"] else "experiment_all_result.json",
        )
        summary = self._load_summary(raw_result_path)
        return ExternalVerificationResult(
            verifier_name=self.name,
            target_name=request.target_name,
            command=command,
            workdir=target_repo,
            returncode=completed.returncode,
            raw_result_path=raw_result_path if os.path.exists(raw_result_path) else None,
            summary=summary,
        )

    def _normalized_params(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        params = {
            "tree": raw.get("tree", "basic"),
            "sigma_pf_source": raw.get("sigma_pf_source", "dataset"),
            "sigma_pb_mode": raw.get("sigma_pb_mode", "delta-clean"),
            "and_rule": raw.get("and_rule", "min"),
            "mini": bool(raw.get("mini", False)),
            "max_images": raw.get("max_images"),
            "detect_timeout": raw.get("detect_timeout"),
        }
        return params

    def _build_command(self, script_path: str, params: Dict[str, Any]) -> List[str]:
        command = [
            "python3",
            script_path,
            "--tree",
            str(params["tree"]),
            "--sigma-pf-source",
            str(params["sigma_pf_source"]),
            "--sigma-pb-mode",
            str(params["sigma_pb_mode"]),
            "--and-rule",
            str(params["and_rule"]),
        ]
        if params["mini"]:
            command.append("--mini")
        if params["max_images"] is not None:
            command.extend(["--max-images", str(params["max_images"])])
        if params["detect_timeout"] is not None:
            command.extend(["--detect-timeout", str(params["detect_timeout"])])
        return command

    def _load_summary(self, raw_result_path: str) -> Dict[str, Any]:
        if not os.path.exists(raw_result_path):
            return {"status": "missing-result"}

        with open(raw_result_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)

        return {
            "status": "ok",
            "tree_mode": data.get("tree_mode"),
            "active_conditions": data.get("active_conditions"),
            "sigma_pf_source": data.get("sigma_pf_source"),
            "sigma_pb_mode": data.get("sigma_pb_mode"),
            "and_rule": data.get("and_rule"),
            "top_sigma_pe": data.get("top_sigma_pe"),
            "bbsl_results": data.get("bbsl_results"),
            "raw_output_path": raw_result_path,
        }
