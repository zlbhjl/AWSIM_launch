"""Strategies shared by every target: one fixed case, or one case built from parameters.

The GP-based exploration strategist of AWSIM lives in
targets/awsim/strategies/active_learning.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from contracts.execution import TestCase


def direct_case_input(target: str, params: Mapping[str, object], source: object, *, role: str) -> dict[str, object]:
    """The input of a single --param case; a target may add fields (plugin ``direct_case_input``)."""
    from targets.registry import get_target_plugin

    try:
        hook = getattr(get_target_plugin(target), "direct_case_input", None)
    except ValueError:
        hook = None
    return dict(hook(params, source, role=role)) if callable(hook) else dict(params)


@dataclass(frozen=True)
class FixedCaseStrategyConfig:
    fixture: str
    case_id: str | None = None
    case_kind: str = "uturn"
    target: str = "awsim"
    reason: str = "manual_orchestrator_run"
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParameterCaseStrategyConfig:
    params: Mapping[str, object]
    case_id: str | None = None
    case_kind: str = "uturn"
    target: str = "awsim"
    reason: str = "manual_orchestrator_run"
    tags: list[str] = field(default_factory=list)
    scenario_type: str | None = None
    simulation_output_dir: str | None = None
    expected_trace_path: str | None = None
    local_loop_num: int | None = None


class FixedCaseStrategy:
    def __init__(self, config: FixedCaseStrategyConfig):
        self.config = config
        self._consumed = False

    def next_test_case(self) -> TestCase | None:
        if self._consumed:
            return None
        self._consumed = True

        fixture_path = Path(self.config.fixture).expanduser().resolve()
        case_id = self.config.case_id or fixture_path.stem
        return TestCase(
            case_id=case_id,
            target=self.config.target,
            case_kind=self.config.case_kind,
            input={"fixture_path": str(fixture_path)},
            tags=list(self.config.tags),
            reason=self.config.reason,
            meta={"source_module": "orchestration.strategy"},
        )


class ParameterCaseStrategy:
    def __init__(self, config: ParameterCaseStrategyConfig):
        self.config = config
        self._consumed = False

    def next_test_case(self) -> TestCase | None:
        if self._consumed:
            return None
        self._consumed = True

        case_id = self.config.case_id or f"{self.config.case_kind}_direct"
        input_payload = direct_case_input(self.config.target, self.config.params, self.config, role="orchestrator")

        return TestCase(
            case_id=case_id,
            target=self.config.target,
            case_kind=self.config.case_kind,
            input=input_payload,
            tags=list(self.config.tags),
            reason=self.config.reason,
            meta={"source_module": "orchestration.strategy"},
        )


