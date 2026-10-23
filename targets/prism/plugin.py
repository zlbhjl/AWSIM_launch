from __future__ import annotations

import argparse
from pathlib import Path

from contracts.target_plugin import TargetComponents
from targets.prism.backend import PrismBackend, PrismBackendConfig
from targets.prism.result_interpreter import PrismResultInterpreter


class PrismPlugin:
    name = "prism"
    fixed_parameter_reason_prefix = "PRISM_FIXED_SAMPLING"
    # Each experiment first computes the exact probability by model checking.
    run_model_check_once = True
    # The exact model check (orchestration/fixed_parameter_sampling.py).
    exact_probability_columns = {
        "eventual_failure_probability": "prism_eventual_failure_probability",
        "bounded_failure_probability": "prism_bounded_failure_probability",
    }
    exact_probability_metric = "c_failure"
    exact_model_check_failure_reason = "PRISM exact model check failed"
    # Cluster runs need a profile from targets/prism/container_profiles/.
    requires_container_profile = True
    supported_parameter_modes = frozenset({"explore", "binomial_ci", "sprt", "ebstop", "dkw", "dkw_fixed"})
    default_case_kind = "uturn"

    def default_config_module(self, case_kind: str) -> str:
        return f"targets.awsim.case_kinds.{case_kind}"

    def build_components(self, args: argparse.Namespace) -> TargetComponents:
        return TargetComponents(
            backend=PrismBackend(
                PrismBackendConfig(
                    output_root=Path(getattr(args, "prism_output_root", "artifacts/prism")),
                    prism_executable=getattr(args, "prism_executable", "prism"),
                    timeout_sec=float(getattr(args, "prism_timeout_sec", 30.0)),
                )
            ),
            result_interpreter=PrismResultInterpreter(),
        )


    def select_strategy(self, config) -> str | None:
        """With --param, statistical modes repeat the fixed parameters; explore runs one case."""
        if not config.params:
            return None
        if config.run_mode in {"binomial_ci", "sprt", "ebstop"}:
            return "fixed_parameter_sampling"
        if config.run_mode in {"dkw", "dkw_fixed"}:
            return "fixed_parameter_dkw"
        return None

    def validate_orchestrator_args(self, args) -> None:
        """CLI checks of a PRISM run (moved from apps/cli/orchestrator_main.py)."""
        simulation_mode = bool(getattr(args, "params", []))
        mode = getattr(args, "mode", "explore")
        if simulation_mode and mode in {"binomial_ci", "sprt", "ebstop", "dkw", "dkw_fixed"}:
            if getattr(args, "max_samples", None) is None:
                raise ValueError("PRISM binomial sampling requires --max-samples")
            if getattr(args, "dataset_csv", None) is None:
                raise ValueError("PRISM binomial sampling requires --dataset-csv")
        if simulation_mode and mode not in self.supported_parameter_modes:
            raise ValueError(
                "target=prism with --param only supports "
                f"--mode {'/'.join(sorted(self.supported_parameter_modes))}; "
                f"'{mode}' has no live PRISM execution path yet "
                "(orchestration/orchestrator.py::_build_strategy has no branch for it, "
                "so it would silently fall through to a single one-off case)"
            )

    def normalize_cli_args(self, args, raw_argv, *, role: str) -> None:
        """binomial_ci reads PRISM's binary metric unless --binomial-target was changed."""
        del raw_argv
        if role != "orchestrator":
            return
        if getattr(args, "mode", "") == "binomial_ci" and getattr(args, "binomial_target", "c_collision") == "c_collision":
            from targets.prism.statistical import STATISTICAL_TARGET_PROFILE

            args.binomial_target = STATISTICAL_TARGET_PROFILE.binary_metric

    def check_strategy(self, config) -> None:
        if config.params and config.run_mode not in self.supported_parameter_modes:
            raise ValueError(
                f"target=prism has no live execution path for --mode {config.run_mode!r} "
                "(only explore/binomial_ci/sprt/ebstop/dkw/dkw_fixed are wired to a real "
                "PRISM strategy); refusing to silently fall back to a single one-off case"
            )

    @property
    def importance_sampling(self):
        """Importance-sampling hook (contracts/importance_sampling_plugin.py); imported on use."""
        from targets.prism.importance_sampling import PrismImportanceSamplingSupport

        return PrismImportanceSamplingSupport()


PLUGIN = PrismPlugin()
