from __future__ import annotations

import argparse

from contracts.target_plugin import TargetComponents
from targets.bernoulli.backend import BernoulliBackend
from targets.bernoulli.result_interpreter import BernoulliResultInterpreter


class BernoulliPlugin:
    name = "bernoulli"
    default_case_kind = "bernoulli"

    def build_components(self, args: argparse.Namespace) -> TargetComponents:
        del args
        return TargetComponents(backend=BernoulliBackend(), result_interpreter=BernoulliResultInterpreter())


PLUGIN = BernoulliPlugin()
