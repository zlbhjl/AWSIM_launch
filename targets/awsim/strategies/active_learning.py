"""AWSIM's GP exploration: the shared engine with AWSIM's spec (targets/awsim/exploration.py)."""

from __future__ import annotations

from orchestration.exploration.active_learning import (  # noqa: F401  (compatible names)
    EXTRACTOR_FOCUS_MODES,
    ActiveLearningStrategist as GPExplorationStrategist,
)
from targets.awsim.exploration import AWSIM_EXPLORATION


class ActiveLearningStrategist(GPExplorationStrategist):
    """The shared GP exploration with AWSIM's training rules, failed-run recovery and extractors."""

    def __init__(self, *args, spec=None, **kwargs):
        super().__init__(*args, spec=spec or AWSIM_EXPLORATION, **kwargs)


__all__ = ["EXTRACTOR_FOCUS_MODES", "ActiveLearningStrategist", "GPExplorationStrategist"]
