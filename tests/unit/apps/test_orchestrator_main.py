import json
from pathlib import Path

import pytest

import apps.cli.orchestrator_main as orchestrator_main_module
from apps.cli.orchestrator_main import (
    build_parser,
    build_orchestrator_config,
    normalize_args,
    run_orchestrator,
    validate_args,
)
from runtime.cluster.ray_queue import TaskQueue


def test_validate_args_rejects_strategy_mode_without_dataset_csv() -> None:
    class Args:
        fixture = None
        params = []
        target = "awsim"
        headless = False
        dataset_csv = None
        mode = "explore"

    with pytest.raises(ValueError):
        validate_args(Args())


def test_validate_args_rejects_mixed_fixture_and_param_modes() -> None:
    class Args:
        fixture = "tests/fixtures/awsim/timeout_trace.txt"
        params = ["dx0=15.0"]
        target = "awsim"
        headless = False
        dataset_csv = None
        mode = "explore"

    Args.fixture = "tests/fixtures/awsim/timeout_trace.txt"
    with pytest.raises(ValueError):
        validate_args(Args())


def test_validate_args_allows_direct_param_mode() -> None:
    class Args:
        fixture = None
        params = ["dx0=15.0"]
        target = "awsim"
        headless = False
        dataset_csv = None
        mode = "explore"

    validate_args(Args())


def test_prism_binomial_sampling_requires_dataset_and_max_samples() -> None:
    parser = build_parser()
    missing_dataset = parser.parse_args(
        [
            "--output",
            "/tmp/prism.jsonl",
            "--target",
            "prism",
            "--mode",
            "binomial_ci",
            "--param",
            "steps=20",
            "--max-samples",
            "20",
        ]
    )
    missing_max = parser.parse_args(
        [
            "--output",
            "/tmp/prism.jsonl",
            "--dataset-csv",
            "/tmp/prism.csv",
            "--target",
            "prism",
            "--mode",
            "binomial_ci",
            "--param",
            "steps=20",
        ]
    )

    with pytest.raises(ValueError, match="--dataset-csv"):
        validate_args(missing_dataset)
    with pytest.raises(ValueError, match="--max-samples"):
        validate_args(missing_max)


def test_prism_rejects_modes_with_no_live_execution_path() -> None:
    # boundary_gap (and verify_consistency/etc.) have no branch in
    # orchestration/orchestrator.py::_build_strategy for target=prism, so without
    # this check they would silently fall through to a single one-off case with
    # no statistical evaluation at all instead of failing loudly.
    parser = build_parser()
    args = parser.parse_args(
        [
            "--output",
            "/tmp/prism.jsonl",
            "--dataset-csv",
            "/tmp/prism.csv",
            "--target",
            "prism",
            "--mode",
            "boundary_gap",
            "--param",
            "steps=20",
        ]
    )

    with pytest.raises(ValueError, match="no live execution path|has no branch"):
        validate_args(args)


def test_prism_allows_explore_binomial_ci_sprt_ebstop_dkw_modes() -> None:
    parser = build_parser()
    for mode in ("explore", "binomial_ci", "sprt", "ebstop", "dkw", "dkw_fixed"):
        extra = (
            ["--max-samples", "20"]
            if mode in {"binomial_ci", "sprt", "ebstop", "dkw", "dkw_fixed"}
            else []
        )
        args = parser.parse_args(
            [
                "--output",
                "/tmp/prism.jsonl",
                "--dataset-csv",
                "/tmp/prism.csv",
                "--target",
                "prism",
                "--mode",
                mode,
                "--param",
                "steps=20",
                *extra,
            ]
        )
        validate_args(args)


def test_prism_binomial_sampling_uses_failure_metric_and_experiment_id() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--output",
            "/tmp/prism.jsonl",
            "--dataset-csv",
            "/tmp/prism.csv",
            "--target",
            "prism",
            "--mode",
            "binomial_ci",
            "--param",
            "steps=20",
            "--max-samples",
            "20",
            "--experiment-id",
            "batch-a",
        ]
    )

    validate_args(args)
    normalized = normalize_args(args, argv=[])
    config = build_orchestrator_config(normalized)

    assert config.binomial_target == "c_failure"
    assert config.experiment_id == "batch-a"


def test_validate_args_allows_awsim_strategy_mode_with_dataset_csv() -> None:
    class Args:
        fixture = None
        params = []
        target = "awsim"
        headless = False
        dataset_csv = "/tmp/uturn_dataset.csv"
        mode = "focus"

    validate_args(Args())


def test_replay_mode_requires_source_and_isolated_run_id() -> None:
    parser = build_parser()
    valid_args = parser.parse_args(
        [
            "--output",
            "/tmp/replay_records.jsonl",
            "--dataset-csv",
            "/tmp/replay_dataset.csv",
            "--mode",
            "replay",
            "--replay-csv",
            "/tmp/source.csv",
            "--replay-expected-count",
            "9066",
            "--run-id",
            "autoware180_replay_20260911",
        ]
    )

    validate_args(valid_args)
    config = build_orchestrator_config(valid_args)

    assert config.run_mode == "replay"
    assert config.replay_csv == "/tmp/source.csv"
    assert config.replay_expected_count == 9066


def test_replay_mode_rejects_missing_run_id() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--output",
            "/tmp/replay_records.jsonl",
            "--dataset-csv",
            "/tmp/replay_dataset.csv",
            "--mode",
            "replay",
            "--replay-csv",
            "/tmp/source.csv",
        ]
    )

    with pytest.raises(ValueError, match="--run-id is required"):
        validate_args(args)


def test_validate_args_allows_verify_consistency_mode() -> None:
    class Args:
        fixture = None
        params = []
        target = "awsim"
        headless = False
        dataset_csv = "/tmp/uturn_dataset.csv"
        mode = "verify_consistency"

    validate_args(Args())


def test_validate_args_allows_ebstop_mode() -> None:
    class Args:
        fixture = None
        params = []
        target = "awsim"
        headless = False
        dataset_csv = "/tmp/uturn_dataset.csv"
        mode = "ebstop"

    validate_args(Args())


def test_validate_args_allows_sprt_mode() -> None:
    class Args:
        fixture = None
        params = []
        target = "awsim"
        headless = False
        dataset_csv = "/tmp/uturn_dataset.csv"
        mode = "sprt"

    validate_args(Args())


def test_validate_args_allows_jama_edge_mode() -> None:
    class Args:
        fixture = None
        params = []
        target = "awsim"
        headless = False
        dataset_csv = "/tmp/uturn_dataset.csv"
        mode = "jama_edge"

    validate_args(Args())


def test_validate_args_allows_ttc_edge_mode() -> None:
    class Args:
        fixture = None
        params = []
        target = "awsim"
        headless = False
        dataset_csv = "/tmp/uturn_dataset.csv"
        mode = "ttc_edge"

    validate_args(Args())


def test_validate_args_allows_worst_ttc_mode() -> None:
    class Args:
        fixture = None
        params = []
        target = "awsim"
        headless = False
        dataset_csv = "/tmp/uturn_dataset.csv"
        mode = "worst_ttc"

    validate_args(Args())


def test_build_orchestrator_config_accepts_direct_simulation_params() -> None:
    class Args:
        fixture = None
        params = ["dx0=15.0", "ego_speed=35.0", "npc_speed=14.0"]
        output = "/tmp/records.jsonl"
        case_id = None
        case_kind = "uturn"
        mode = "explore"
        target = "awsim"
        worker_id = "worker_v2_local"
        reason = "manual"
        tags = ["direct"]
        config_module = "targets.awsim.case_kinds.uturn"
        focus_points = None
        path_root = None
        dataset_csv = None
        history_path = None
        refresh_interval = None
        max_strategy_cases = None
        resume_from = None
        resume_current_only = False
        scenario_type = None
        simulation_output_dir = "/tmp/awsim-output"
        expected_trace_path = None
        local_loop_num = 3
        headless = True
        cache_size = 5

    config = build_orchestrator_config(Args())

    assert config.fixture is None
    assert config.params["dx0"] == 15.0
    assert config.simulation_output_dir == "/tmp/awsim-output"
    assert config.local_loop_num == 3
    assert config.headless is True
    assert config.cache_size == 5


def test_validate_args_rejects_non_positive_cache_size() -> None:
    class Args:
        fixture = None
        params = []
        target = "awsim"
        headless = False
        dataset_csv = "/tmp/uturn_dataset.csv"
        mode = "explore"
        refresh_interval = None
        max_strategy_cases = None
        worker_count = None
        cache_size = 0
        queue_high_water = None
        queue_low_water = None
        poll_interval_sec = 2.0
        max_samples = None
        binomial_confidence = 0.95
        binomial_target_width = 0.02
        binomial_min_samples = None
        scenario_type = None
        simulation_output_dir = None
        expected_trace_path = None
        local_loop_num = None
        focus_points = None

    with pytest.raises(ValueError, match="cache-size"):
        validate_args(Args())


def test_normalize_args_loads_focus_points_from_case_kind_module() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--output",
            "/tmp/records.jsonl",
            "--dataset-csv",
            "/tmp/uturn_dataset.csv",
            "--mode",
            "focus",
            "--case-kind",
            "uturn",
        ]
    )

    normalized = normalize_args(args, argv=["--mode", "focus", "--case-kind", "uturn"])

    assert normalized.config_module == "targets.awsim.case_kinds.uturn"
    assert isinstance(normalized.focus_points, list)
    assert normalized.focus_points
    assert normalized.focus_points[0]["dx0"] == 10.09


def test_runtime_monitor_sync_is_enabled_by_default_and_can_be_disabled() -> None:
    parser = build_parser()
    default_args = parser.parse_args(["--output", "/tmp/records.jsonl"])
    disabled_args = parser.parse_args(
        [
            "--output",
            "/tmp/records.jsonl",
            "--no-sync-aw-runtime-monitor",
        ]
    )

    assert default_args.sync_aw_runtime_monitor is True
    assert disabled_args.sync_aw_runtime_monitor is False


def test_normalize_args_does_not_force_unsupported_scenario_profile_from_container_profile() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--output",
            "/tmp/records.jsonl",
            "--dataset-csv",
            "/tmp/uturn_dataset.csv",
            "--container-profile",
            "autoware180",
        ]
    )

    normalized = normalize_args(args, argv=["--container-profile", "autoware180"])

    assert normalized.scenario_profile is None
    assert normalized.config_module == "targets.awsim.case_kinds.uturn"


def test_build_orchestrator_config_keeps_strategy_fields() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--output",
            "/tmp/records.jsonl",
            "--dataset-csv",
            "/tmp/uturn_dataset.csv",
            "--mode",
            "dkw_fixed",
            "--max-strategy-cases",
            "2",
            "--max-samples",
            "12",
            "--focus_points",
            '[{"dx0": 15.0}]',
            "--dkw_bounds",
            '{"dx0": [15.0, 20.0]}',
            "--dkw_region",
            "intersect_safe",
            "--dkw_simultaneous",
            "--dkw_pure_smc",
            "--binomial_target",
            "c_ttc_1.1",
            "--binomial_method",
            "clopper-pearson",
            "--binomial_confidence",
            "0.9",
            "--binomial_target_width",
            "0.05",
            "--binomial_min_samples",
            "40",
            "--binomial_anytime_valid",
        ]
    )
    normalized = normalize_args(
        args,
        argv=[
            "--mode", "dkw_fixed",
            "--max-strategy-cases", "2",
            "--max-samples", "12",
            "--focus_points", '[{"dx0": 15.0}]',
            "--dkw_bounds", '{"dx0": [15.0, 20.0]}',
            "--dkw_region", "intersect_safe",
            "--dkw_simultaneous",
            "--dkw_pure_smc",
            "--binomial_target", "c_ttc_1.1",
            "--binomial_method", "clopper-pearson",
            "--binomial_confidence", "0.9",
            "--binomial_target_width", "0.05",
            "--binomial_min_samples", "40",
            "--binomial_anytime_valid",
        ],
    )

    config = build_orchestrator_config(normalized)

    assert config.run_mode == "dkw_fixed"
    assert config.focus_points == [{"dx0": 15.0}]
    assert config.dataset_csv == "/tmp/uturn_dataset.csv"
    assert config.max_strategy_cases == 2
    assert config.max_samples == 12
    assert config.dkw_bounds == {"dx0": [15.0, 20.0]}
    assert config.dkw_region == "intersect_safe"
    assert config.dkw_simultaneous is True
    assert config.dkw_pure_smc is True
    assert config.binomial_target == "c_ttc_1.1"
    assert config.binomial_method == "clopper-pearson"
    assert config.binomial_confidence == 0.9
    assert config.binomial_target_width == 0.05
    assert config.binomial_min_samples == 40
    assert config.binomial_anytime_valid is True


def test_build_orchestrator_config_binomial_anytime_valid_defaults_to_false() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["--output", "/tmp/records.jsonl", "--dataset-csv", "/tmp/uturn_dataset.csv"]
    )
    normalized = normalize_args(args, argv=[])

    config = build_orchestrator_config(normalized)

    assert config.binomial_anytime_valid is False


def test_normalize_args_rejects_invalid_dkw_bounds_json() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--output",
            "/tmp/records.jsonl",
            "--dataset-csv",
            "/tmp/uturn_dataset.csv",
            "--dkw_bounds",
            "{broken",
        ]
    )

    with pytest.raises(ValueError, match="dkw-bounds"):
        normalize_args(args, argv=["--dkw_bounds", "{broken"])


def test_validate_args_rejects_non_positive_max_strategy_cases() -> None:
    class Args:
        fixture = None
        params = []
        target = "awsim"
        headless = False
        dataset_csv = "/tmp/uturn_dataset.csv"
        mode = "explore"
        max_strategy_cases = 0
        refresh_interval = None
        scenario_type = None
        simulation_output_dir = None
        expected_trace_path = None
        local_loop_num = None
        focus_points = None

    with pytest.raises(ValueError, match="max-strategy-cases"):
        validate_args(Args())


def test_validate_args_rejects_non_positive_max_samples() -> None:
    class Args:
        fixture = None
        params = []
        target = "awsim"
        headless = False
        dataset_csv = "/tmp/uturn_dataset.csv"
        mode = "dkw_fixed"
        max_strategy_cases = None
        max_samples = 0
        binomial_confidence = 0.95
        binomial_target_width = 0.02
        binomial_min_samples = None
        refresh_interval = None
        scenario_type = None
        simulation_output_dir = None
        expected_trace_path = None
        local_loop_num = None
        focus_points = None

    with pytest.raises(ValueError, match="max-samples"):
        validate_args(Args())


def test_run_orchestrator_enqueues_and_runs_one_worker_case(tmp_path: Path) -> None:
    output_path = tmp_path / "records.jsonl"
    queue = TaskQueue()

    exit_code = run_orchestrator(
        [
            "--fixture",
            "tests/fixtures/awsim/timeout_trace.txt",
            "--output",
            str(output_path),
            "--worker-id",
            "worker-orchestrator",
        ],
        queue=queue,
    )

    assert exit_code == 0
    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["case_id"] == "timeout_trace"
    assert payload["status"] == "timeout"

    queue_size, completed_count, worker_statuses = queue.get_status()
    assert queue_size == 0
    assert completed_count == 1
    assert worker_statuses["worker-orchestrator"] == "waiting"


def test_run_orchestrator_forwards_history_path_and_skips_duplicate(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "records.jsonl"
    history_path = tmp_path / "processed_loops_history.csv"
    history_path.write_text("1\n", encoding="utf-8")
    queue = TaskQueue()

    exit_code = run_orchestrator(
        [
            "--fixture",
            "tests/fixtures/awsim/timeout_trace.txt",
            "--output",
            str(output_path),
            "--worker-id",
            "worker-orchestrator",
            "--history-path",
            str(history_path),
        ],
        queue=queue,
    )

    assert exit_code == 0
    assert not output_path.exists()

    queue_size, completed_count, worker_statuses = queue.get_status()
    assert queue_size == 0
    assert completed_count == 1
    assert worker_statuses["worker-orchestrator"] == "waiting"


def test_run_orchestrator_accepts_resume_arguments(tmp_path: Path) -> None:
    output_path = tmp_path / "records.jsonl"
    resume_from = tmp_path / "resume"
    queue = TaskQueue()
    resume_from.mkdir()
    (resume_from / "uturn_dataset.csv").write_text(
        "loop_num,status\n1,success\n",
        encoding="utf-8",
    )

    exit_code = run_orchestrator(
        [
            "--fixture",
            "tests/fixtures/awsim/timeout_trace.txt",
            "--output",
            str(output_path),
            "--worker-id",
            "worker-orchestrator",
            "--resume-from",
            str(resume_from),
            "--resume-current-only",
        ],
        queue=queue,
    )

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert payload["case_id"] == "timeout_trace"


def test_run_orchestrator_accepts_direct_simulation_params(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    class FakeOrchestrator:
        def __init__(self, *, queue=None):
            captured["queue"] = queue

        def run(self, config):
            captured["config"] = config
            return {
                "mode": "local_queue",
                "enqueued": 1,
                "worker_exit_code": 0,
                "queue_size": 0,
                "completed_count": 0,
                "worker_statuses": {},
                "output_path": "/tmp/records.jsonl",
            }

    monkeypatch.setattr(orchestrator_main_module, "Orchestrator", FakeOrchestrator)

    exit_code = run_orchestrator(
        [
            "--param",
            "dx0=15.0",
            "--param",
            "ego_speed=35.0",
            "--param",
            "npc_speed=14.0",
            "--output",
            "/tmp/records.jsonl",
            "--simulation-output-dir",
            "/tmp/awsim-output",
            "--local-loop-num",
            "3",
            "--headless",
        ]
    )

    assert exit_code == 0
    config = captured["config"]
    assert config.fixture is None
    assert config.params == {
        "dx0": 15.0,
        "ego_speed": 35.0,
        "npc_speed": 14.0,
    }
    assert config.simulation_output_dir == "/tmp/awsim-output"
    assert config.local_loop_num == 3
    assert config.headless is True

    stdout_payload = json.loads(capsys.readouterr().out.strip())
    assert stdout_payload["worker_exit_code"] == 0


def test_run_orchestrator_dispatches_cluster_flag(monkeypatch) -> None:
    captured = {}

    def fake_cluster_runner(argv):
        captured["argv"] = list(argv)
        return 0

    monkeypatch.setattr(
        "apps.cli.orchestrator_cluster_main.run_cluster_orchestrator",
        fake_cluster_runner,
    )

    exit_code = run_orchestrator(
        [
            "--cluster",
            "--output",
            "/tmp/records.jsonl",
            "--dataset-csv",
            "/tmp/uturn_dataset.csv",
        ]
    )

    assert exit_code == 0
    assert captured["argv"][0] == "--cluster"


def test_validate_args_allows_bbsl_param_mode() -> None:
    class Args:
        fixture = None
        params = ["dx0=15.0"]
        target = "bbsl"
        headless = False
        dataset_csv = None
        mode = "explore"
        scenario_type = None
        simulation_output_dir = None
        expected_trace_path = None
        local_loop_num = None
        focus_points = None

    validate_args(Args())


def test_validate_args_rejects_awsim_only_options_for_bbsl() -> None:
    class Args:
        fixture = None
        params = ["dx0=15.0"]
        target = "bbsl"
        headless = False
        dataset_csv = None
        mode = "explore"
        scenario_type = "uturn"
        simulation_output_dir = None
        expected_trace_path = None
        local_loop_num = None
        focus_points = None

    with pytest.raises(ValueError, match="AWSIM-only"):
        validate_args(Args())
