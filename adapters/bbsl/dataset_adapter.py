from __future__ import annotations

import json
import os
from typing import Any


CONDITION_ORDER = [
    "clean",
    "salt_pepper",
    "occlusion",
    "blur",
    "sp_occ",
    "sp_blur",
    "occ_blur",
    "all_three",
]


class BBSLExperimentAdapter:
    def default_output_path(
        self,
        target_repo: str,
        mini: bool,
        prefer_raw: bool = True,
    ) -> str:
        target_repo = os.path.abspath(target_repo)
        output_dir = os.path.join(target_repo, "output")
        raw_filename = (
            "experiment_all_raw_result_mini.json"
            if mini else "experiment_all_raw_result.json"
        )
        local_filename = (
            "experiment_all_result_mini.json"
            if mini else "experiment_all_result.json"
        )
        if prefer_raw:
            raw_path = os.path.join(output_dir, raw_filename)
            if os.path.exists(raw_path):
                return raw_path
        return os.path.join(output_dir, local_filename)

    def load_output(self, output_json_path: str) -> dict[str, Any]:
        with open(output_json_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def active_conditions(self, output: dict[str, Any]) -> list[str]:
        active = output.get("active_conditions")
        if active:
            return active

        bbsl_results = output.get("bbsl_results", {})
        available = set(bbsl_results.keys())
        return [cond for cond in CONDITION_ORDER if cond in available]

    def universal_dataset(self, output: dict[str, Any]) -> set[str]:
        return set(output.get("universal_dataset", []))

    def sigma_pf_assumptions(self, output: dict[str, Any]) -> dict[str, float]:
        return output.get("sigma_pf_assumptions", {})
