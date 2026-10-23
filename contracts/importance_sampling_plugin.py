"""Optional target-plugin hook for fixed-P/fixed-Q importance sampling.

A target supports importance sampling by giving its plugin an attribute
``importance_sampling`` that implements :class:`ImportanceSamplingSupport`.
The generic CLI (``python3 -m apps.cli.importance_main``) reads the plan's
``target``, adds the target's own options, and calls ``run``. Nothing outside
``targets/<name>/`` has to change.

The hook is read with ``getattr`` and is deliberately not part of
``TargetPlugin``: a ``runtime_checkable`` protocol would reject every target
that does not support importance sampling.

Options shared by every target (added by the CLI): ``--sampling-plan``,
``--output-root`` (a Path), ``--max-samples``, ``--sampling-seed``,
``--metric`` (None: the target chooses) and ``--confidence``. A target checks
the ones it requires and raises ``ValueError`` with a message for the user.
"""

from __future__ import annotations

import argparse
from typing import Protocol


class ImportanceSamplingSupport(Protocol):
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Add the target's own options to the CLI parser."""

    def run(self, args: argparse.Namespace) -> dict[str, object]:
        """Run the experiment of ``args.sampling_plan``; return the runner's result."""


def importance_sampling_support(plugin: object) -> ImportanceSamplingSupport | None:
    return getattr(plugin, "importance_sampling", None)


__all__ = ["ImportanceSamplingSupport", "importance_sampling_support"]
