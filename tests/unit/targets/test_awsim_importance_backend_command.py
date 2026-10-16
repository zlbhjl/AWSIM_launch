from __future__ import annotations

from contracts.execution import TestCase
from targets.awsim.backend import AWSIMBackend


def test_awsim_sampling_bookkeeping_is_not_passed_to_scenario_cli() -> None:
    command = AWSIMBackend()._build_command(
        TestCase(
            case_id="is-1",
            target="awsim",
            case_kind="uturn",
            input={
                "dx0": 15.0,
                "ego_speed": 35.0,
                "npc_speed": 14.0,
                "execution_kind": "sample_path",
                "record_kind": "sample",
                "sample_index": 1,
                "sampling_seed": 7,
                "sampling_plan_path": "/tmp/plan.yaml",
            },
        )
    )
    assert "--dx0" in command
    assert "--ego_speed" in command
    assert "--sample_index" not in command
    assert "--sampling_plan_path" not in command
