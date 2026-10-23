from __future__ import annotations

import argparse
import os

from contracts.target_plugin import TargetComponents
from runtime.container.profile import build_runtime_profile
from targets.awsim.backend import AWSIMBackend, AWSIMBackendConfig
from targets.awsim.result_interpreter import InterpretationContext, ResultInterpreter
from targets.awsim.theory import build_theory_metrics


class AWSIMPlugin:
    name = "awsim"
    default_case_kind = "uturn"
    # The worker checks the GPU before taking each case (--worker-gpu-health-check).
    requires_gpu_health_check = True
    # Options of the shared CLIs that only AWSIM accepts, with the message for other targets.
    exclusive_options = (
        (("headless",), "--headless is only supported for target=awsim"),
        (("replay_csv",), "--replay-csv is only supported for target=awsim"),
        (
            ("scenario_type", "simulation_output_dir", "expected_trace_path", "local_loop_num",
             "legacy_type", "focus_points"),
            "AWSIM-only direct execution options are not supported for non-awsim targets",
        ),
    )
    # Replay provenance carried from a case's meta into the queue payload.
    task_payload_meta_keys = (
        "replay_source_loop_num",
        "replay_source_case_id",
        "replay_source_collision",
        "replay_source_reason",
        "replay_source_csv",
    )

    def default_config_module(self, case_kind: str) -> str:
        return f"targets.awsim.case_kinds.{case_kind}"

    @property
    def scenario_profiles(self) -> tuple[str, ...]:
        from targets.awsim.case_kinds import SUPPORTED_SCENARIO_PROFILES

        return tuple(SUPPORTED_SCENARIO_PROFILES)

    def config_module_for(self, case_kind: str, scenario_profile: str | None) -> str:
        from targets.awsim.cli import config_module_for

        return config_module_for(case_kind, scenario_profile)

    def normalize_cli_args(self, args, raw_argv, *, role: str) -> None:
        from targets.awsim.cli import normalize_cli_args

        normalize_cli_args(args, raw_argv, role=role)

    def validate_orchestrator_args(self, args) -> None:
        from targets.awsim.cli import validate_orchestrator_args

        validate_orchestrator_args(args)

    def direct_case_input(self, params, source, *, role: str) -> dict[str, object]:
        """Input of a single --param case: the parameters plus what the AWSIM backend needs.

        ``source`` is the orchestrator config or the worker arguments. The
        worker also passes the trajectory extractor mode and absolute paths.
        """
        from pathlib import Path

        payload = dict(params)
        payload["scenario_type"] = source.scenario_type or source.case_kind
        if role == "worker":
            payload["ext_mode"] = getattr(source, "ext_mode", "cvm")
            if source.simulation_output_dir:
                payload["output_dir"] = str(Path(source.simulation_output_dir).expanduser().resolve())
            if source.expected_trace_path:
                payload["expected_trace_path"] = str(Path(source.expected_trace_path).expanduser().resolve())
        else:
            if source.simulation_output_dir is not None:
                payload["output_dir"] = source.simulation_output_dir
            if source.expected_trace_path is not None:
                payload["expected_trace_path"] = source.expected_trace_path
        if source.local_loop_num is not None:
            payload["local_loop_num"] = source.local_loop_num
        return payload

    def derived_input_columns(self, record, input_row) -> dict[str, object]:
        """Theory columns (JAMA margins and zones) stored with each AWSIM input row."""
        config_module = record.meta.get("config_module")
        return build_theory_metrics(
            case_kind=record.case_kind,
            values=input_row,
            config_module_name=config_module if isinstance(config_module, str) else None,
        )

    def select_strategy(self, config) -> str | None:
        """Replay a CSV, or explore with the GP strategist; --param runs the shared single case."""
        if config.params:
            return None
        return "replay" if config.replay_csv is not None else "active_learning"

    @property
    def strategies(self):
        """AWSIM's own strategies (orchestration/strategy_registry.StrategySpec); imported on use."""
        from orchestration.strategy_registry import StrategySpec
        from targets.awsim.strategies.builders import (
            active_learning_target_total,
            build_active_learning_strategy,
            build_replay_strategy,
            replay_target_total,
        )

        return {
            "active_learning": StrategySpec(build_active_learning_strategy, target_total=active_learning_target_total),
            "replay": StrategySpec(build_replay_strategy, target_total=replay_target_total),
        }

    def build_components(self, args: argparse.Namespace) -> TargetComponents:
        ros_domain_id = os.environ.get("ROS_DOMAIN_ID", "0")
        machine_role = "master" if ros_domain_id == "21" else "local"
        host_mode = os.environ.get("EXEC_MODE") == "host"
        backend = AWSIMBackend(
            config=AWSIMBackendConfig(
                runtime_profile=build_runtime_profile(
                    case_kind=args.case_kind,
                    headless=getattr(args, "headless", False),
                    host_mode=host_mode,
                    machine_role=machine_role,
                    container_profile=getattr(args, "container_profile", None),
                    scenario_profile=getattr(args, "scenario_profile", None),
                ),
                manage_infra=True,
                # Normal AWSIM workflows retain their existing reuse default.
                # The importance-sampling CLI opts into a fresh simulation
                # world per record to prevent a rejected trace from affecting
                # the next proposal sample.
                reuse_infra_between_runs=not bool(
                    getattr(args, "fresh_infra_per_sample", False)
                ),
                initial_warmup_sec=40.0,
                extra_refresh_warmup_sec=15.0,
                infra_ext_mode=getattr(args, "ext_mode", "cvm"),
                include_awchecker=False,
                startup_probe_timeout_sec=30.0,
                refresh_probe_timeout_sec=45.0,
                probe_service_timeout_sec=2.0,
                startup_probe_stability_checks=2,
                refresh_probe_stability_checks=3,
                probe_stability_interval_sec=1.0,
                max_refresh_probe_retries=1,
            )
        )
        interpreter = ResultInterpreter(
            context=InterpretationContext(
                target=args.target,
                case_kind=args.case_kind,
                config_module=args.config_module,
                kinematics_mode=getattr(args, "ext_mode", "cvm"),
            )
        )
        return TargetComponents(backend=backend, result_interpreter=interpreter)

    def build_result_interpreter(self, *, case_kind: str, config_module: str | None) -> ResultInterpreter:
        """The interpreter of a saved trace (apps/cli/result_interpreter_main.py).

        Without --config-module the U-turn rule spec is used, whatever the case kind.
        """
        return ResultInterpreter(
            context=InterpretationContext(
                target=self.name,
                case_kind=case_kind,
                config_module=config_module or self.default_config_module(self.default_case_kind),
            )
        )

    @property
    def importance_sampling(self):
        """Importance-sampling hook (contracts/importance_sampling_plugin.py); imported on use."""
        from targets.awsim.importance_sampling import AWSIMImportanceSamplingSupport

        return AWSIMImportanceSamplingSupport()


PLUGIN = AWSIMPlugin()
