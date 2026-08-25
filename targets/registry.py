from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

from runtime.container.profile import build_runtime_profile
from targets.awsim.backend import AWSIMBackend, AWSIMBackendConfig
from targets.awsim.result_interpreter import (
    InterpretationContext as AWSIMInterpretationContext,
)
from targets.awsim.result_interpreter import ResultInterpreter as AWSIMResultInterpreter
from targets.bbsl.backend import BBSLBackend
from targets.bbsl.result_interpreter import ResultInterpreter as BBSLResultInterpreter


@dataclass(frozen=True)
class TargetComponents:
    backend: object
    result_interpreter: object


def build_target_components(
    args: argparse.Namespace,
    *,
    backend: object | None = None,
    result_interpreter: object | None = None,
) -> TargetComponents:
    if backend is not None and result_interpreter is not None:
        return TargetComponents(backend=backend, result_interpreter=result_interpreter)

    if args.target == "awsim":
        ros_domain_id = os.environ.get("ROS_DOMAIN_ID", "0")
        machine_role = "master" if ros_domain_id == "21" else "local"
        host_mode = os.environ.get("EXEC_MODE") == "host"
        backend_impl = backend or AWSIMBackend(
            config=AWSIMBackendConfig(
                runtime_profile=build_runtime_profile(
                    case_kind=args.case_kind,
                    headless=getattr(args, "headless", False),
                    host_mode=host_mode,
                    machine_role=machine_role,
                ),
                manage_infra=True,
                reuse_infra_between_runs=True,
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
        interpreter_impl = result_interpreter or AWSIMResultInterpreter(
            context=AWSIMInterpretationContext(
                target=args.target,
                case_kind=args.case_kind,
                config_module=args.config_module,
            )
        )
        return TargetComponents(
            backend=backend_impl,
            result_interpreter=interpreter_impl,
        )

    if args.target == "bbsl":
        return TargetComponents(
            backend=backend or BBSLBackend(),
            result_interpreter=result_interpreter or BBSLResultInterpreter(),
        )

    raise ValueError(f"Unsupported target: {args.target}")
