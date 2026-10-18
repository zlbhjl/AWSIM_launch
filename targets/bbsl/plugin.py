from __future__ import annotations

import argparse

from contracts.target_plugin import TargetComponents
from targets.bbsl.backend import BBSLBackend
from targets.bbsl.result_interpreter import ResultInterpreter


class BBSLPlugin:
    name = "bbsl"
    default_case_kind = "uturn"

    def default_config_module(self, case_kind: str) -> str:
        return f"targets.awsim.case_kinds.{case_kind}"

    def build_components(self, args: argparse.Namespace) -> TargetComponents:
        del args
        return TargetComponents(backend=BBSLBackend(), result_interpreter=ResultInterpreter())


PLUGIN = BBSLPlugin()
