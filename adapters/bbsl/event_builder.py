from __future__ import annotations

from typing import Any

from .dataset_adapter import BBSLExperimentAdapter


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
