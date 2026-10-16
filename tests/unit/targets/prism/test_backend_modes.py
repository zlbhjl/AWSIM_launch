from contracts.execution import RunStatus, TestCase
import targets.prism.backend as backend_module
from targets.prism.backend import PrismBackend, PrismBackendConfig
from targets.prism.runner import (
    PrismExecution,
    PrismModelCheckArtifacts,
    PrismSamplePathArtifacts,
)


def _execution(command: str) -> PrismExecution:
    return PrismExecution(
        command=(command,),
        returncode=0,
        stdout="",
        stderr="",
        elapsed_sec=0.01,
    )


def test_backend_model_check_does_not_generate_sample_path(monkeypatch, tmp_path) -> None:
    calls = {"model_check": 0, "sample": 0}

    def fake_model_check(_definition, **kwargs):
        calls["model_check"] += 1
        result_csv = kwargs["output_dir"] / "property_results.csv"
        result_csv.write_text("0.8\n0.2\n", encoding="utf-8")
        return PrismModelCheckArtifacts(
            property_results={"eventual_failure": 0.8, "bounded_failure": 0.2},
            execution=_execution("properties"),
            result_csv=result_csv,
        )

    def fake_sample(*_args, **_kwargs):
        calls["sample"] += 1
        raise AssertionError("sample path must not run during exact model check")

    monkeypatch.setattr(backend_module, "run_prism_model_check", fake_model_check)
    monkeypatch.setattr(backend_module, "run_prism_sample_path", fake_sample)
    backend = PrismBackend(PrismBackendConfig(output_root=tmp_path))

    result = backend.run(
        TestCase(
            case_id="exact",
            target="prism",
            case_kind="simple_reliability_dtmc",
            input={"execution_kind": "model_check"},
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert calls == {"model_check": 1, "sample": 0}
    assert "property_results_csv" in result.evidence


def test_backend_sample_path_does_not_run_exact_model_check(monkeypatch, tmp_path) -> None:
    calls = {"model_check": 0, "sample": 0}

    def fake_model_check(*_args, **_kwargs):
        calls["model_check"] += 1
        raise AssertionError("exact model check must not run for each sample")

    def fake_sample(_definition, **kwargs):
        calls["sample"] += 1
        trace_csv = kwargs["output_dir"] / "trace.csv"
        trace_csv.write_text("step,state\n0,0\n1,2\n", encoding="utf-8")
        return PrismSamplePathArtifacts(
            execution=_execution("simpath"),
            trace_csv=trace_csv,
        )

    monkeypatch.setattr(backend_module, "run_prism_model_check", fake_model_check)
    monkeypatch.setattr(backend_module, "run_prism_sample_path", fake_sample)
    backend = PrismBackend(PrismBackendConfig(output_root=tmp_path))

    result = backend.run(
        TestCase(
            case_id="sample",
            target="prism",
            case_kind="simple_reliability_dtmc",
            input={"execution_kind": "sample_path", "steps": 5},
        )
    )

    assert result.status is RunStatus.SUCCESS
    assert calls == {"model_check": 0, "sample": 1}
    assert "trace_csv" in result.evidence
