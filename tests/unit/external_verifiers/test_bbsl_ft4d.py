from pathlib import Path

from contracts.evaluation import EvaluationRecord
from contracts.execution import RawRunResult, RunStatus
from contracts.verification import FT4DResult, VerificationInput
from external_verifiers.base import ExternalVerificationRequest
from external_verifiers.bbsl_ft4d import BBSLFT4DVerifier


def test_build_command_targets_run_bbsl_local_ft4d(tmp_path: Path) -> None:
    verifier = BBSLFT4DVerifier()
    script_path = Path("/tmp/run_bbsl_local_ft4d.py")
    output_json_path = tmp_path / "wrapped.json"

    command = verifier._build_command(
        script_path,
        "/tmp/BBSL-test",
        {
            "tree": "basic",
            "sigma_pf_source": "dataset",
            "sigma_pb_mode": "delta-clean",
            "and_rule": "min",
            "mini": True,
            "max_images": 12,
            "detect_timeout": 60,
            "reuse_existing_output": True,
            "fixture_path": None,
            "raw_result_json": None,
            "case_kind": "full_all",
        },
        output_json_path,
    )

    assert command[:7] == [
        "python3",
        str(script_path),
        "--target-repo",
        "/tmp/BBSL-test",
        "--execution-mode",
        "legacy",
        "--condition-policy",
    ]
    assert "--reuse-existing-output" in command
    assert command[command.index("--output-json") + 1] == str(output_json_path)


def test_run_uses_new_targets_bbsl_pipeline() -> None:
    captured: dict[str, object] = {}

    class FakeBackend:
        def run(self, test_case):
            captured["backend_input"] = dict(test_case.input)
            return RawRunResult(
                case_id=test_case.case_id,
                target=test_case.target,
                case_kind=test_case.case_kind,
                status=RunStatus.SUCCESS,
                evidence={
                    "raw_result_json": "/tmp/BBSL-test/output/experiment_all_raw_result_mini.json"
                },
                meta={"backend_mode": "execution"},
            )

    class FakeResultInterpreter:
        def interpret_raw_run_result(self, raw_run_result):
            captured["raw_run_case_id"] = raw_run_result.case_id
            return EvaluationRecord(
                case_id=raw_run_result.case_id,
                target="bbsl",
                case_kind="full_all",
                status=RunStatus.SUCCESS,
                input={
                    "active_conditions": ["clean", "salt_pepper", "occlusion", "blur"],
                    "sigma_pb_mode": "delta-clean",
                },
                output={
                    "universal_dataset": ["image_0001"],
                    "condition_results": {
                        "salt_pepper": {
                            "dataset_d": [],
                            "dataset_e": ["image_0001"],
                            "total_count": 1,
                            "correct_count": 1,
                            "error_count": 0,
                            "sigma_pb": 0.0,
                            "sigma_pb_mode": "delta-clean",
                        },
                        "occlusion": {
                            "dataset_d": [],
                            "dataset_e": ["image_0001"],
                            "total_count": 1,
                            "correct_count": 1,
                            "error_count": 0,
                            "sigma_pb": 0.0,
                            "sigma_pb_mode": "delta-clean",
                        },
                        "blur": {
                            "dataset_d": [],
                            "dataset_e": ["image_0001"],
                            "total_count": 1,
                            "correct_count": 1,
                            "error_count": 0,
                            "sigma_pb": 0.0,
                            "sigma_pb_mode": "delta-clean",
                        },
                    },
                },
                evidence=dict(raw_run_result.evidence),
                meta={"schema_version": 1},
            )

    class FakeVerificationInputBuilder:
        def build(self, record, *, tree_mode=None, assumptions=None, meta=None):
            captured["verification_tree_mode"] = tree_mode
            captured["verification_assumptions"] = dict(assumptions or {})
            captured["verification_meta"] = dict(meta or {})
            return VerificationInput(
                tree_mode=tree_mode or "basic",
                universal_dataset={"image_0001"},
                events={},
                assumptions=dict(assumptions or {}),
                meta=dict(meta or {}),
            )

    def fake_ft4d_runner(verification_input):
        captured["ft4d_tree_mode"] = verification_input.tree_mode
        return FT4DResult(
            tree_mode=verification_input.tree_mode,
            top_sigma_pe=0.0,
            confidence=1.0,
            node_summaries=[],
            raw_result={"tree_report": {"tree": {"id": "TOP", "sigma_pe": 0.0}}},
        )

    verifier = BBSLFT4DVerifier(
        backend=FakeBackend(),
        result_interpreter=FakeResultInterpreter(),
        verification_input_builder=FakeVerificationInputBuilder(),
        ft4d_runner=fake_ft4d_runner,
    )

    result = verifier.run(
        ExternalVerificationRequest(
            verifier_name="bbsl_ft4d",
            target_name="BBSL-test",
            target_repo="/tmp/BBSL-test",
            parameters={"mini": True, "reuse_existing_output": True},
        )
    )

    assert result.returncode == 0
    assert result.command[1].endswith("run_bbsl_local_ft4d.py")
    assert result.summary["integration_mode"] == "legacy-adapter-to-targets-bbsl"
    assert result.summary["recommended_path"] == "targets/bbsl/* + evaluation/ft4d_service.py"
    assert result.summary["raw_output_path"].endswith("experiment_all_raw_result_mini.json")
    assert result.summary["top_sigma_pe"] == 0.0
    assert captured["backend_input"]["target_repo"] == str(Path("/tmp/BBSL-test").resolve())
    assert captured["backend_input"]["reuse_existing_output"] is True
    assert captured["verification_tree_mode"] == "basic"
    assert captured["ft4d_tree_mode"] == "basic"
