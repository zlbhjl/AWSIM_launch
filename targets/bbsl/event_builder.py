from __future__ import annotations

from typing import Any, Iterable

from targets.bbsl.dataset_adapter import BBSLExperimentAdapter


EVENT_ID_TO_CONDITION = {
    "SALT_PEPPER": "salt_pepper",
    "OCCLUSION": "occlusion",
    "BLUR": "blur",
    "SP_OCC": "sp_occ",
    "SP_BLUR": "sp_blur",
    "OCC_BLUR": "occ_blur",
    "ALL_THREE": "all_three",
}


class BBSLEventSetBuilder:
    def __init__(self, adapter: BBSLExperimentAdapter | None = None):
        self.adapter = adapter or BBSLExperimentAdapter()

    @staticmethod
    def _record_slot_key(record: dict[str, Any]) -> str:
        return f"{record['image_name']}#obj{record['object_index']}"

    def _collect_tree_recognition_tests(self, node, collected):
        if node.get("type") == "basic":
            recognition_test = node.get("recognition_test")
            condition_name = EVENT_ID_TO_CONDITION.get(node["id"])
            if condition_name is not None and recognition_test is not None:
                collected.setdefault(condition_name, recognition_test)
        for child in node.get("children", []):
            self._collect_tree_recognition_tests(child, collected)

    def _recognition_tests_by_tree(self, output: dict[str, Any]) -> dict[str, dict[str, Any]]:
        ft4d_reports = output.get("ft4d_reports", {})
        recognition_tests = {}
        for tree_mode, report in ft4d_reports.items():
            root = report.get("tree")
            if not root:
                continue
            collected = {}
            self._collect_tree_recognition_tests(root, collected)
            recognition_tests[tree_mode] = collected
        return recognition_tests

    def build_event_inputs(self, output: dict[str, Any]) -> dict[str, Any]:
        sigma_pb_mode = output.get("sigma_pb_mode", "raw")
        condition_datasets = output.get("condition_datasets", {})
        bbsl_results = output.get("bbsl_results", {})
        recognition_tests_by_tree = self._recognition_tests_by_tree(output)

        event_inputs: dict[str, dict[str, Any]] = {}
        for condition_name in self.adapter.active_conditions(output):
            raw_dataset = condition_datasets.get(condition_name, {})
            metrics = bbsl_results.get(condition_name, {})

            if sigma_pb_mode == "delta-clean":
                dataset_d = set(metrics.get("dataset_d_effective", []))
                dataset_e = set(metrics.get("dataset_e_effective", []))
                total_count = metrics.get("dataset_size_effective", len(dataset_d))
                correct_count = metrics.get("T_effective", total_count - len(dataset_e))
                error_count = metrics.get("F_effective", len(dataset_e))
            else:
                dataset_d = set(raw_dataset.get("dataset_d", metrics.get("dataset_d", [])))
                dataset_e = set(raw_dataset.get("dataset_e", metrics.get("dataset_e", [])))
                total_count = metrics.get("dataset_size", len(dataset_d))
                correct_count = metrics.get("T", total_count - len(dataset_e))
                error_count = metrics.get("F", len(dataset_e))

            event_inputs[condition_name] = {
                "dataset_d": dataset_d,
                "dataset_e": dataset_e,
                "total_count": total_count,
                "correct_count": correct_count,
                "error_count": error_count,
                "sigma_pb": metrics.get("sigma_pb", 0.0),
                "sigma_pb_mode": sigma_pb_mode,
                "recognition_test": metrics.get("statistical_test"),
            }

        return {
            "tree_mode": output.get("tree_mode", "all"),
            "active_conditions": self.adapter.active_conditions(output),
            "sigma_pf_source": output.get("sigma_pf_source", "dataset"),
            "sigma_pb_mode": sigma_pb_mode,
            "and_rule": output.get("and_rule", "min"),
            "universal_dataset": self.adapter.universal_dataset(output),
            "sigma_pf_assumptions": self.adapter.sigma_pf_assumptions(output),
            "statistical_test_config": output.get("statistical_test_config", {}),
            "recognition_tests_by_tree": recognition_tests_by_tree,
            "events": event_inputs,
        }

    def build_event_inputs_from_batch_outputs(
        self,
        clean_output: dict[str, Any],
        batch_outputs: Iterable[dict[str, Any]],
        *,
        sigma_pf_source: str,
        sigma_pb_mode: str,
        and_rule: str,
    ) -> dict[str, Any]:
        active_conditions: list[str] | None = None
        sigma_pf_assumptions = clean_output.get("sigma_pf_assumptions", {})
        statistical_test_config = clean_output.get("statistical_test_config", {})
        batch_paths: list[str] = []
        aggregated: dict[str, dict[str, Any]] = {}
        batch_count = 0

        for batch_output in batch_outputs:
            if active_conditions is None:
                active_conditions = list(batch_output.get("active_conditions", []))
                if not active_conditions:
                    raise ValueError("No active_conditions found in batch output.")
                aggregated = {
                    condition_name: {
                        "total_count": 0,
                        "correct_count": 0,
                        "error_count": 0,
                        "recognition_test": None,
                    }
                    for condition_name in active_conditions
                }

            sigma_pf_assumptions = (
                batch_output.get("sigma_pf_assumptions")
                or sigma_pf_assumptions
            )
            statistical_test_config = (
                batch_output.get("statistical_test_config")
                or statistical_test_config
            )
            batch_count += 1

            source_output_json = batch_output.get("source_output_json")
            if source_output_json:
                batch_paths.append(source_output_json)

            for condition_name in active_conditions:
                metrics = batch_output.get("bbsl_results", {}).get(condition_name, {})
                dataset_payload = batch_output.get("condition_datasets", {}).get(
                    condition_name,
                    {},
                )
                total_count = int(
                    metrics.get(
                        "dataset_size",
                        dataset_payload.get("dataset_size", 0),
                    )
                )
                correct_count = int(
                    metrics.get(
                        "T",
                        dataset_payload.get("correct_count", 0),
                    )
                )
                error_count = int(
                    metrics.get(
                        "F",
                        dataset_payload.get("error_count", 0),
                    )
                )

                if total_count <= 0 and dataset_payload.get("sample_records"):
                    # Backward-compatible fallback for old detailed batch JSONs.
                    for record in dataset_payload.get("sample_records", []):
                        total_count += 1
                        if record.get("is_correct", False):
                            correct_count += 1
                        else:
                            error_count += 1

                aggregate = aggregated[condition_name]
                aggregate["total_count"] += total_count
                aggregate["correct_count"] += correct_count
                aggregate["error_count"] += error_count
                if aggregate["recognition_test"] is None:
                    aggregate["recognition_test"] = metrics.get("statistical_test")

        if active_conditions is None:
            raise ValueError("At least one noisy batch output is required.")

        event_inputs: dict[str, dict[str, Any]] = {}
        universal_size = sum(
            int(aggregated[condition_name]["total_count"])
            for condition_name in active_conditions
        )
        universal_dataset = set(range(universal_size))
        offset = 0

        for condition_name in active_conditions:
            total_count = int(aggregated[condition_name]["total_count"])
            correct_count = int(aggregated[condition_name]["correct_count"])
            error_count = int(aggregated[condition_name]["error_count"])
            dataset_d = set(range(offset, offset + total_count))
            dataset_e = set(range(offset, offset + error_count))
            offset += total_count

            event_inputs[condition_name] = {
                "dataset_d": dataset_d,
                "dataset_e": dataset_e,
                "total_count": total_count,
                "correct_count": correct_count,
                "error_count": error_count,
                "sigma_pb": (error_count / total_count) if total_count > 0 else 0.0,
                "sigma_pb_mode": sigma_pb_mode,
                "recognition_test": aggregated[condition_name]["recognition_test"],
            }

        return {
            "tree_mode": "basic",
            "active_conditions": active_conditions,
            "sigma_pf_source": sigma_pf_source,
            "sigma_pb_mode": sigma_pb_mode,
            "and_rule": and_rule,
            "universal_dataset": universal_dataset,
            "sigma_pf_assumptions": sigma_pf_assumptions,
            "statistical_test_config": statistical_test_config,
            "recognition_tests_by_tree": {},
            "events": event_inputs,
            "clean_success_image_ids": clean_output.get("clean_success_image_ids", []),
            "batch_count": batch_count,
            "batch_output_paths": batch_paths,
        }
