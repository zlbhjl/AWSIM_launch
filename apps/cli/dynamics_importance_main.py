#!/usr/bin/env python3
"""Compatible entry point: importance sampling for target=dynamics.

Same options as before; the experiment is assembled by the dynamics plugin
(``targets/dynamics/importance_sampling.py``). The generic entry point is
``python3 -m apps.cli.importance_main``, which selects the target from the plan.
"""

from __future__ import annotations

from typing import Sequence

from apps.cli.importance_main import build_parser as _build_parser
from apps.cli.importance_main import run_for_target, support_for_target

TARGET = "dynamics"


def build_parser():
    return _build_parser(support_for_target(TARGET), target=TARGET)


def main(argv: Sequence[str] | None = None) -> int:
    return run_for_target(TARGET, argv)


if __name__ == "__main__":
    raise SystemExit(main())
