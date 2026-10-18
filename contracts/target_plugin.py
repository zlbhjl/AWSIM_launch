"""Contract between the shared code and each verification target.

Each target package ``targets/<name>/`` provides ``plugin.py`` with a module
level ``PLUGIN`` object. The shared code finds plugins by scanning the
``targets`` package (``targets.registry``), so adding a target does not
require editing the registry or the CLI choices.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TargetComponents:
    backend: object
    result_interpreter: object


@runtime_checkable
class TargetPlugin(Protocol):
    """What a target provides to the shared code."""

    name: str

    def build_components(self, args: argparse.Namespace) -> TargetComponents:
        """Build the backend (runs the target) and the result interpreter (judges a run)."""


__all__ = ["TargetComponents", "TargetPlugin"]
