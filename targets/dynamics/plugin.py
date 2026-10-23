from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path

from contracts.target_plugin import TargetComponents
from targets.dynamics.backend import DynamicsBackend, DynamicsBackendConfig
from targets.dynamics.models.registry import resolve_dynamics_model, statistical_defaults
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
        # Only the U-turn has a scenario spec; the IID statistical path does
        # not import the config module of other models.
        return "scenario_specs.uturn" if case_kind == "uturn" else "targets.dynamics.plugin"

    def build_components(self, args: argparse.Namespace) -> TargetComponents:
        output_root = getattr(args, "dynamics_output_root", None) or "artifacts/dynamics"
        return TargetComponents(
            backend=DynamicsBackend(DynamicsBackendConfig(output_root=Path(output_root))),
            result_interpreter=DynamicsResultInterpreter(),
        )


    def iid_statistical_metrics(self, config) -> dict[str, object]:
        model_id, adapter = resolve_dynamics_model(dict(config.params), config.case_kind, case_kind_explicit=False)
        if model_id == "uturn":
            binary_metric = DYNAMICS_DECISION_METRICS[config.dynamics_decision_mode]
            return {
                "binary": binary_metric,
                "sprt": binary_metric,
                "dkw": "min_ttc",
                "ebstop": "min_ttc",
                "minimum_dkw_value": 0.0,
            }
        if config.dynamics_decision_mode != "judgment":
            raise ValueError(f"--dynamics-decision-mode {config.dynamics_decision_mode} is only for the U-turn")
        defaults = statistical_defaults(adapter)
        return {
            "binary": defaults["binary_metric"],
            "sprt": defaults["binary_metric"],
            "dkw": defaults["continuous_metric"],
            "ebstop": defaults["continuous_metric"],
            "minimum_dkw_value": defaults["minimum_dkw_value"],
        }

    def prepare_statistical_run(
        self,
        *,
        mode: str,
        case_kind: str | None,
        case_kind_explicit: bool,
        params: Mapping[str, object],
        explicit_options: frozenset[str],
        decision_mode: str = "judgment",
    ) -> dict[str, object]:
        """Resolve the model of a statistical run and the inputs it needs.

        Returns the case kind, the fixed inputs to add, and the default input
        distribution. The U-turn keeps its previous defaults and adds nothing.
        Other registered models vary only their Brownian path by default
        (``solver_kind=sde``, a per-sample ``sde_seed``); their fixed inputs
        are checked here by resolving the model once, before any case runs.
        """
        model_id, adapter = resolve_dynamics_model(params, case_kind, case_kind_explicit=case_kind_explicit)
        defaults = statistical_defaults(adapter)
        if model_id == "uturn" and params.get("model_id") is None:
            return {"case_kind": case_kind or model_id, "params": {},
                    "default_input_distribution": defaults["default_input_distribution"]}
        if model_id != "uturn" and decision_mode != "judgment":
            raise ValueError(f"--dynamics-decision-mode {decision_mode} is only for the U-turn")
        extra: dict[str, object] = {"model_id": model_id}
        if not adapter.legacy_execution and "solver_kind" not in params:
            extra["solver_kind"] = "sde"
        if (
            mode == "ebstop"
            and not defaults["continuous_metric_bounded"]
            and not {"ebstop_value_range_min", "ebstop_value_range_max"} <= explicit_options
        ):
            raise ValueError(
                f"ebstop on {model_id} needs --ebstop-value-range-min and --ebstop-value-range-max: "
                f"its metric {defaults['continuous_metric']} has no known range"
            )
        if not adapter.legacy_execution:
            probe = {
                **{name: (float(spec["min"]) + float(spec["max"])) / 2.0
                   for name, spec in defaults["default_input_distribution"].items()},
                **params,
                **extra,
                "sde_seed": 0,
            }
            try:
                adapter.resolve(probe)
            except ValueError as exc:
                raise ValueError(f"dynamics model {model_id}: {exc}") from exc
        return {
            "case_kind": model_id if not case_kind_explicit else case_kind,
            "params": extra,
            "default_input_distribution": defaults["default_input_distribution"],
        }

    def validate_worker_args(self, *, case_kind: str | None, params: Mapping[str, object]) -> None:
        resolve_dynamics_model(params, case_kind, case_kind_explicit=True)

    def statistical_summary_extras(self, args: argparse.Namespace) -> dict[str, object]:
        """Recorded in the summary of a statistical run (orchestrator CLI)."""
        return {"dynamics_decision_mode": args.dynamics_decision_mode}

    @property
    def importance_sampling(self):
        """Importance-sampling hook (contracts/importance_sampling_plugin.py); imported on use."""
        from targets.dynamics.importance_sampling import DynamicsImportanceSamplingSupport

        return DynamicsImportanceSamplingSupport()


PLUGIN = DynamicsPlugin()
