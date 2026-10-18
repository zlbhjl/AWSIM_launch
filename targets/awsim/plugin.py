from __future__ import annotations

import argparse
import os

from contracts.target_plugin import TargetComponents
from runtime.container.profile import build_runtime_profile
from targets.awsim.backend import AWSIMBackend, AWSIMBackendConfig
from targets.awsim.result_interpreter import InterpretationContext, ResultInterpreter


class AWSIMPlugin:
    name = "awsim"
    default_case_kind = "uturn"

    def default_config_module(self, case_kind: str) -> str:
        return f"targets.awsim.case_kinds.{case_kind}"

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


PLUGIN = AWSIMPlugin()
