import json
from pathlib import Path

import pytest

from contracts.evaluation import EvaluationRecord
from contracts.execution import RunStatus
from runtime.cluster.result_sink import (
    CompositeResultSink,
    JsonlResultSink,
    OptionalResultSink,
    SharedStoreResultSink,
)
from runtime.repository.dataset_csv import DatasetCsvRepository
from runtime.repository.shared_store import SharedStore


def _meta(**extra: object) -> dict[str, object]:
    meta = {
        "schema_version": 1,
        "source_module": "tests.unit.runtime.test_result_sink",
    }
    meta.update(extra)
    return meta


def test_jsonl_result_sink_serializes_status_and_relativizes_evidence(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "records.jsonl"
    fixture_path = tmp_path / "fixtures" / "trace.json"
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text("{}", encoding="utf-8")

    sink = JsonlResultSink(output_path, path_root=tmp_path)
    sink.save(
        EvaluationRecord(
            case_id="case_1",
            target="awsim",
            case_kind="uturn",
            status=RunStatus.SUCCESS,
            evidence={"trace_json": str(fixture_path.resolve())},
            meta=_meta(verifier_name="maude"),
        )
    )

    line = output_path.read_text(encoding="utf-8").strip()
    payload = json.loads(line)

    assert payload["status"] == "success"
    assert payload["evidence"]["trace_json"] == "fixtures/trace.json"
    assert payload["meta"]["path_root"] == str(tmp_path.resolve())


def test_shared_store_result_sink_merges_record_into_dataset_csv(tmp_path: Path) -> None:
    dataset_csv_path = tmp_path / "uturn_dataset.csv"
    repository = DatasetCsvRepository(dataset_csv_path)
    sink = SharedStoreResultSink(SharedStore(repository))

    sink.save(
        EvaluationRecord(
            case_id="case_2",
            target="awsim",
            case_kind="uturn",
            status=RunStatus.TIMEOUT,
            input={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
            output={"min_ttc": -1.0},
            evidence={"trace_json": "/tmp/trace_case_2.json"},
            meta=_meta(
                task_reason="boundary_explore",
                worker_id="worker-21",
                verifier_name="maude",
                config_module="targets.awsim.case_kinds.uturn",
            ),
        )
    )

    rows = repository.read_rows()
    assert len(rows) == 1
    assert rows[0]["loop_num"] == "1"
    assert rows[0]["dx0"] == "15.0000"
    assert rows[0]["worker_id"] == "worker-21"
    assert rows[0]["status"] == "timeout"
    assert rows[0]["reason"] == "boundary_explore [ERROR: TIMEOUT]"
    assert rows[0]["meta_worker_id"] == "worker-21"
    assert rows[0]["evidence_trace_json"] == "/tmp/trace_case_2.json"
    assert rows[0]["theory_zone_a"] in {"A", "B", "C", "D"}
    assert rows[0]["theory_margin_a_ai"].count(".") == 1
    assert len(rows[0]["theory_margin_a_ai"].split(".", maxsplit=1)[1]) == 4


def test_composite_result_sink_writes_jsonl_and_dataset_csv(tmp_path: Path) -> None:
    output_path = tmp_path / "records.jsonl"
    dataset_csv_path = tmp_path / "uturn_dataset.csv"
    repository = DatasetCsvRepository(dataset_csv_path)
    sink = CompositeResultSink(
        [
            JsonlResultSink(output_path),
            SharedStoreResultSink(SharedStore(repository)),
        ]
    )

    sink.save(
        EvaluationRecord(
            case_id="case_3",
            target="awsim",
            case_kind="uturn",
            status=RunStatus.SUCCESS,
            input={"dx0": 20.0},
            output={"c_collision": 0},
            evidence={"trace_json": "/tmp/trace_case_3.json"},
            meta=_meta(task_reason="smoke_case"),
        )
    )

    json_payload = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert json_payload["case_id"] == "case_3"
    rows = repository.read_rows()
    assert rows[0]["case_id"] == "case_3"
    assert rows[0]["reason"] == "smoke_case"


def test_shared_store_result_sink_skips_theory_columns_for_non_awsim(tmp_path: Path) -> None:
    dataset_csv_path = tmp_path / "full_all_dataset.csv"
    repository = DatasetCsvRepository(dataset_csv_path)
    sink = SharedStoreResultSink(SharedStore(repository))

    sink.save(
        EvaluationRecord(
            case_id="case_4",
            target="bbsl",
            case_kind="full_all",
            status=RunStatus.SUCCESS,
            input={"dx0": 15.0, "ego_speed": 35.0, "npc_speed": 14.0},
            output={"status_reason": "ok"},
            meta=_meta(),
        )
    )

    rows = repository.read_rows()
    assert "theory_zone_a" not in rows[0]


def test_shared_store_result_sink_uses_case_kind_theory_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_csv_path = tmp_path / "cutin_dataset.csv"
    repository = DatasetCsvRepository(dataset_csv_path)
    sink = SharedStoreResultSink(SharedStore(repository))

    monkeypatch.setattr(
        "runtime.cluster.result_sink.build_theory_metrics",
        lambda **kwargs: {
            "theory_zone_a": "CUTIN",
            "theory_margin_a_human": float(kwargs["values"]["cutin_vy"]),
        },
    )

    sink.save(
        EvaluationRecord(
            case_id="case_cutin_1",
            target="awsim",
            case_kind="cutin",
            status=RunStatus.SUCCESS,
            input={
                "dx0": 12.0,
                "ego_speed": 30.0,
                "npc_speed": 10.0,
                "cutin_vy": 1.4,
            },
            output={"c_collision": 0},
            meta=_meta(config_module="targets.awsim.case_kinds.cutin"),
        )
    )

    rows = repository.read_rows()
    assert rows[0]["theory_zone_a"] == "CUTIN"
    assert rows[0]["theory_margin_a_human"] == "1.4000"


def test_shared_store_result_sink_computes_actual_cutin_theory_columns(
    tmp_path: Path,
) -> None:
    dataset_csv_path = tmp_path / "cutin_dataset.csv"
    repository = DatasetCsvRepository(dataset_csv_path)
    sink = SharedStoreResultSink(SharedStore(repository))

    sink.save(
        EvaluationRecord(
            case_id="case_cutin_2",
            target="awsim",
            case_kind="cutin",
            status=RunStatus.SUCCESS,
            input={
                "dx0": 12.0,
                "ego_speed": 30.0,
                "npc_speed": 10.0,
                "cutin_vy": 1.4,
            },
            output={"c_collision": 0},
            meta=_meta(config_module="targets.awsim.case_kinds.cutin"),
        )
    )

    rows = repository.read_rows()
    assert rows[0]["theory_zone_a"] in {"A", "B", "C", "D"}
    assert float(rows[0]["theory_d_total_human"]) > 0.0
    assert float(rows[0]["theory_d_total_ai"]) < float(rows[0]["theory_d_total_human"])


def test_composite_result_sink_continues_when_optional_sink_fails(tmp_path: Path) -> None:
    output_path = tmp_path / "records.jsonl"
    warning_messages: list[str] = []

    class FailingSink:
        def save(self, _record):
            raise RuntimeError("shared store unavailable")

    sink = CompositeResultSink(
        [
            JsonlResultSink(output_path),
            OptionalResultSink(
                FailingSink(),
                label="shared_store",
                warning_writer=warning_messages.append,
            ),
        ]
    )

    sink.save(
        EvaluationRecord(
            case_id="case_5",
            target="awsim",
            case_kind="uturn",
            status=RunStatus.SUCCESS,
            output={"c_collision": 0},
            evidence={"trace_json": "/tmp/trace_case_5.json"},
            meta=_meta(),
        )
    )

    payload = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert payload["case_id"] == "case_5"
    assert len(warning_messages) == 1
    assert "shared store unavailable" in warning_messages[0]
    assert sink.warnings == warning_messages


def test_jsonl_result_sink_rejects_missing_required_meta(tmp_path: Path) -> None:
    sink = JsonlResultSink(tmp_path / "records.jsonl")

    with pytest.raises(ValueError, match="missing required keys"):
        sink.save(
            EvaluationRecord(
                case_id="case_6",
                target="awsim",
                case_kind="uturn",
                status=RunStatus.SUCCESS,
                meta={"verifier_name": "maude"},
            )
        )
