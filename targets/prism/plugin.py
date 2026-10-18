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

    def check_strategy(self, config) -> None:
        if config.params and config.run_mode not in self.supported_parameter_modes:
            raise ValueError(
                f"target=prism has no live execution path for --mode {config.run_mode!r} "
                "(only explore/binomial_ci/sprt/ebstop/dkw/dkw_fixed are wired to a real "
                "PRISM strategy); refusing to silently fall back to a single one-off case"
            )


PLUGIN = PrismPlugin()
