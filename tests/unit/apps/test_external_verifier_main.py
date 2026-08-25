import json
from datetime import datetime
from pathlib import Path

from apps.cli.external_verifier_main import (
    build_request,
    resolve_output_json_path,
    run_external_verifier,
)
from external_verifiers.base import ExternalVerificationResult


def test_build_request_uses_cli_arguments() -> None:
    class Args:
        verifier = "bbsl_ft4d"
        target_name = "BBSL-test"
        target_repo = "/tmp/BBSL-test"
        mini = True
        max_images = 5
        tree = "basic"
        sigma_pf_source = "dataset"
        sigma_pb_mode = "delta-clean"
        and_rule = "min"
        detect_timeout = 60
        reuse_existing_output = True

    request = build_request(Args())

    assert request.verifier_name == "bbsl_ft4d"
    assert request.target_repo == "/tmp/BBSL-test"
    assert request.parameters["mini"] is True
    assert request.parameters["max_images"] == 5
    assert request.parameters["reuse_existing_output"] is True


def test_resolve_output_json_path_builds_default_name() -> None:
    output_path = resolve_output_json_path(
        None,
        verifier_name="bbsl_ft4d",
        now=lambda: datetime(2026, 8, 4, 10, 11, 12),
    )

    assert output_path.name == "bbsl_ft4d_20260804_101112.json"
    assert output_path.parent.name == "verification_results"


def test_run_external_verifier_writes_result_json(tmp_path: Path, capsys) -> None:
    class FakeVerifier:
        def run(self, request):
            assert request.verifier_name == "bbsl_ft4d"
            assert request.parameters["reuse_existing_output"] is True
            return ExternalVerificationResult(
                verifier_name="bbsl_ft4d",
                target_name=request.target_name,
                command=["python3", "run_bbsl_local_ft4d.py"],
                workdir="/tmp",
                returncode=0,
                raw_result_path="/tmp/output/result.json",
                summary={
                    "status": "ok",
                    "integration_mode": "legacy-adapter-to-targets-bbsl",
                    "tree_mode": "basic",
                    "active_conditions": ["clean", "salt_pepper"],
                    "top_sigma_pe": 0.0,
                    "recommended_path": "targets/bbsl/* + evaluation/ft4d_service.py",
                },
            )

    output_path = tmp_path / "external_result.json"
    exit_code = run_external_verifier(
        [
            "--verifier",
            "bbsl_ft4d",
            "--target-repo",
            "/tmp/BBSL-test",
            "--reuse-existing-output",
            "--output-json",
            str(output_path),
        ],
        create_verifier_fn=lambda name: FakeVerifier(),
        verifier_choices=["bbsl_ft4d"],
    )

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["verifier_name"] == "bbsl_ft4d"
    assert payload["summary"]["tree_mode"] == "basic"

    stdout = capsys.readouterr().out
    assert "AWSIM_launch external verifier finished" in stdout
    assert str(output_path) in stdout
