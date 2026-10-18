from __future__ import annotations

import argparse
from pathlib import Path

from contracts.target_plugin import TargetComponents
from targets.dynamics.backend import DynamicsBackend, DynamicsBackendConfig
from targets.dynamics.result_interpreter import DynamicsResultInterpreter


# Binary metric per dynamics decision mode.  "judgment" uses the physical
# overlap (AWSIM-substitute use); "screening" uses the calibrated clearance
# margin so that candidates are re-validated in AWSIM.
DYNAMICS_DECISION_METRICS = {
    "judgment": "c_collision",
    "screening": "c_screening_candidate",
}


class DynamicsPlugin:
    name = "dynamics"
    default_case_kind = "uturn"

    def default_config_module(self, case_kind: str) -> str:
        del case_kind
        return "scenario_specs.uturn"

    def build_components(self, args: argparse.Namespace) -> TargetComponents:
        output_root = getattr(args, "dynamics_output_root", None) or "artifacts/dynamics"
        return TargetComponents(
            backend=DynamicsBackend(DynamicsBackendConfig(output_root=Path(output_root))),
            result_interpreter=DynamicsResultInterpreter(),
        )


    def iid_statistical_metrics(self, config) -> dict[str, object]:
        binary_metric = DYNAMICS_DECISION_METRICS[config.dynamics_decision_mode]
        return {
            "binary": binary_metric,
            "sprt": binary_metric,
            "dkw": "min_ttc",
            "ebstop": "min_ttc",
            "minimum_dkw_value": 0.0,
        }


PLUGIN = DynamicsPlugin()
