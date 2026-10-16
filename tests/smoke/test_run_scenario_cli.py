import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_run_scenario_cli_smoke_uses_scenario_runner_module(tmp_path: Path) -> None:
    marker_path = tmp_path / "scenario_marker.json"
    sitecustomize_path = tmp_path / "sitecustomize.py"
    sitecustomize_path.write_text(
        f"""
import json
import os
from pathlib import Path

import targets.awsim.scenario_runner as scenario_runner


class FakeManager:
    def __init__(self):
        self.network = "fake-network"

    def run(self, scenarios):
        Path(os.environ["RUN_SCENARIO_MARKER"]).write_text(
            json.dumps({{"scenarios": scenarios}}),
            encoding="utf-8",
        )


def fake_lane_offset(lane_id, offset):
    return {{"lane_id": lane_id, "offset": offset}}


def fake_uturn_builder(**kwargs):
    return kwargs


scenario_runner._default_scenario_manager_factory = lambda: FakeManager()
scenario_runner._default_lane_offset_factory = fake_lane_offset
scenario_runner._load_uturn_builder = lambda: fake_uturn_builder
""".strip()
        + "\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{tmp_path}:{ROOT}"
        if not existing_pythonpath
        else f"{tmp_path}:{ROOT}:{existing_pythonpath}"
    )
    env["RUN_SCENARIO_MARKER"] = str(marker_path)

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_scenario.py"),
            "--type",
            "uturn",
            "--dx0",
            "15.0",
            "--ego_speed",
            "35.0",
            "--npc_speed",
            "14.0",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert marker_path.exists()
    assert "Starting 'uturn' simulation" in completed.stdout

    payload = json.loads(marker_path.read_text(encoding="utf-8"))
    assert len(payload["scenarios"]) == 1
    scenario = payload["scenarios"][0]
    assert scenario["network"] == "fake-network"
    assert scenario["ego_init_laneoffset"] == {"lane_id": "514", "offset": 17}
    assert scenario["ego_goal_laneoffset"] == {"lane_id": "516", "offset": 20}
    assert scenario["npc_init_laneoffset"] == {"lane_id": "521", "offset": 32}
    assert scenario["uturn_next_lane"] == "511"
    assert scenario["_ego_speed"] == 35.0 / 3.6
    assert scenario["_npc_speed"] == 14.0 / 3.6
    assert scenario["dx0"] == 15.0
    assert scenario["acceleration"] == 7.0
