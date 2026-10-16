from __future__ import annotations

import json
import os
import re
from glob import glob
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
    _NOISY_BATCH_RE = re.compile(r"noisy_batch_(\d{4})(?:_mini)?\.json$")

    def batch_output_dir(self, target_repo: str) -> str:
        target_repo = os.path.abspath(target_repo)
        return os.path.join(target_repo, "output", "batches")

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

    def clean_baseline_output_path(self, target_repo: str, mini: bool) -> str:
        suffix = "_mini" if mini else ""
        return os.path.join(
            self.batch_output_dir(target_repo),
            f"clean_baseline{suffix}.json",
        )

    def clean_success_path(self, target_repo: str, mini: bool) -> str:
        suffix = "_mini" if mini else ""
        return os.path.join(
            self.batch_output_dir(target_repo),
            f"clean_success_image_ids{suffix}.json",
        )

    def noisy_batch_output_path(
        self,
        target_repo: str,
        batch_id: int,
        mini: bool,
    ) -> str:
        suffix = "_mini" if mini else ""
        return os.path.join(
            self.batch_output_dir(target_repo),
            f"noisy_batch_{batch_id:04d}{suffix}.json",
        )

    def list_noisy_batch_output_paths(self, target_repo: str, mini: bool) -> list[str]:
        suffix = "_mini" if mini else ""
        pattern = os.path.join(
            self.batch_output_dir(target_repo),
            f"noisy_batch_*{suffix}.json",
        )
        paths = []
        for path in glob(pattern):
            match = self._NOISY_BATCH_RE.search(os.path.basename(path))
            if match is None:
                continue
            paths.append((int(match.group(1)), path))
        paths.sort(key=lambda item: item[0])
        return [path for _, path in paths]

    def noisy_batch_id_from_path(self, path: str) -> int | None:
        match = self._NOISY_BATCH_RE.search(os.path.basename(path))
        if match is None:
            return None
        return int(match.group(1))

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
