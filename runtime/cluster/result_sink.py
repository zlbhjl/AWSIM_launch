from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

from contracts.evaluation import EvaluationRecord, validate_evaluation_meta
from contracts.execution import RunStatus
from runtime.cluster.ray_client import RayActorLocator, RayConnectionConfig
from runtime.repository.dataset_csv import DatasetCsvRepository
from runtime.repository.shared_store import SharedStore
from targets.awsim.theory import build_theory_metrics


class JsonlResultSink:
    def __init__(
        self,
        output_path: str | Path,
        *,
        path_root: str | Path | None = None,
    ):
        self.output_path = Path(output_path).expanduser().resolve()
        self.path_root = (
            Path(path_root).expanduser().resolve() if path_root is not None else None
        )

    def save(self, record: EvaluationRecord) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.serialize_record(record)
        with self.output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def serialize_record(self, record: EvaluationRecord) -> dict[str, object]:
        return serialize_evaluation_record(record, path_root=self.path_root)


class SharedStoreResultSink:
    def __init__(
        self,
        shared_store: SharedStore,
        *,
        loop_field: str = "global_loop_num",
        local_loop_start: int = 1,
    ):
        self.shared_store = shared_store
        self.loop_field = loop_field
        self.next_local_loop_num = local_loop_start

    @classmethod
    def from_dataset_csv(
        cls,
        dataset_csv_path: str | Path,
        *,
        loop_field: str = "global_loop_num",
        local_loop_start: int = 1,
    ) -> "SharedStoreResultSink":
        repository = DatasetCsvRepository(dataset_csv_path)
        return cls(
            SharedStore(repository),
            loop_field=loop_field,
            local_loop_start=local_loop_start,
        )

    def save(self, record: EvaluationRecord) -> None:
        validate_evaluation_meta(record.meta)
        loop_num = self._resolve_loop_num(record)
        input_row = self._build_input_row(record)
        reason = str(record.meta.get("task_reason", ""))
        result_row = self._build_result_row(record, loop_num)
        if record.status is RunStatus.TIMEOUT:
            self.shared_store.flush_timeout(
                loop_num,
                timeout_row=result_row,
                input_row=input_row,
                reason=self._build_timeout_reason(reason),
            )
            return

        self.shared_store.buffer_parameters(loop_num, input_row, reason=reason)
        self.shared_store.merge_result(result_row)

    def _resolve_loop_num(self, record: EvaluationRecord) -> int:
        loop_value = record.meta.get(self.loop_field)
        if isinstance(loop_value, int):
            return loop_value
        if isinstance(loop_value, str) and loop_value.isdigit():
            return int(loop_value)

        loop_num = self.next_local_loop_num
        self.next_local_loop_num += 1
        return loop_num

    def _build_input_row(self, record: EvaluationRecord) -> dict[str, object]:
        input_row = dict(record.input)
        input_row.setdefault("case_id", record.case_id)
        input_row.setdefault("target", record.target)
        input_row.setdefault("case_kind", record.case_kind)
        worker_id = record.meta.get("worker_id")
        if worker_id is not None:
            input_row.setdefault("worker_id", worker_id)
        input_row.update(_build_theoretical_metrics(record, input_row))
        return input_row

    def _build_result_row(
        self,
        record: EvaluationRecord,
        loop_num: int,
    ) -> dict[str, object]:
        row: dict[str, object] = {
            "loop_num": loop_num,
            "case_id": record.case_id,
            "target": record.target,
            "case_kind": record.case_kind,
            "status": record.status.value,
        }
        row.update(record.output)

        for key, value in record.evidence.items():
            row[f"evidence_{key}"] = value

        for key, value in record.meta.items():
            if key in {self.loop_field, "task_reason", "task_tags"}:
                continue
            row[f"meta_{key}"] = value

        return row

    @staticmethod
    def _build_timeout_reason(reason: str) -> str:
        stripped_reason = reason.strip()
        if stripped_reason:
            return f"{stripped_reason} [ERROR: TIMEOUT]"
        return "[ERROR: TIMEOUT]"


class RaySharedStoreResultSink(SharedStoreResultSink):
    def __init__(
        self,
        actor: object,
        *,
        ray_get=None,
        loop_field: str = "global_loop_num",
        local_loop_start: int = 1,
    ):
        super().__init__(
            SharedStore(DatasetCsvRepository("/dev/null")),
            loop_field=loop_field,
            local_loop_start=local_loop_start,
        )
        self.actor = actor
        self.ray_get = ray_get

    @classmethod
    def from_actor_name(
        cls,
        actor_name: str,
        *,
        address: str | None = None,
        namespace: str | None = None,
        loop_field: str = "global_loop_num",
        local_loop_start: int = 1,
        actor_locator: RayActorLocator | None = None,
    ) -> "RaySharedStoreResultSink":
        locator = actor_locator or RayActorLocator()
        ray_module, actor = locator.connect_and_get_actor(
            actor_name,
            config=RayConnectionConfig(
                address=address,
                namespace=namespace,
            ),
        )
        return cls(
            actor,
            ray_get=ray_module.get,
            loop_field=loop_field,
            local_loop_start=local_loop_start,
        )

    def save(self, record: EvaluationRecord) -> None:
        validate_evaluation_meta(record.meta)
        loop_num = self._resolve_loop_num(record)
        input_row = self._build_input_row(record)
        reason = str(record.meta.get("task_reason", ""))
        result_row = self._build_result_row(record, loop_num)
        if record.status is RunStatus.TIMEOUT:
            self._invoke(
                "flush_timeout",
                loop_num,
                result_row,
                input_row,
                self._build_timeout_reason(reason),
                loop_num=loop_num,
                timeout_row=result_row,
                input_row=input_row,
                reason=self._build_timeout_reason(reason),
            )
            self._append_evaluation_record(record)
            return

        self._invoke(
            "buffer_parameters",
            loop_num,
            input_row,
            reason,
            loop_num=loop_num,
            input_row=input_row,
            reason=reason,
        )
        self._invoke("merge_result", result_row, result_row=result_row)
        self._append_evaluation_record(record)

    def _append_evaluation_record(self, record: EvaluationRecord) -> None:
        record_payload = serialize_evaluation_record(record)
        try:
            self._invoke(
                "append_evaluation_record",
                record_payload,
                record_payload=record_payload,
            )
        except AttributeError:
            # Older shared-store actors only provide the merged CSV methods.
            # Keep them usable while the v2 actor adds central JSONL persistence.
            return

    def _invoke(self, method_name: str, *args: object, **kwargs: object) -> object:
        method = getattr(self.actor, method_name)
        if hasattr(method, "remote"):
            remote_result = method.remote(**kwargs) if kwargs else method.remote(*args)
            if self.ray_get is not None:
                return self.ray_get(remote_result)
            import ray  # type: ignore

            return ray.get(remote_result)
        return method(*args) if args else method(**kwargs)


class OptionalResultSink:
    def __init__(
        self,
        sink: object,
        *,
        label: str = "optional_sink",
        warning_writer=None,
    ):
        self.sink = sink
        self.label = label
        self.warning_writer = warning_writer or self._default_warning_writer
        self.warnings: list[str] = []

    def save(self, record: EvaluationRecord) -> None:
        try:
            if hasattr(self.sink, "save"):
                self.sink.save(record)
                return
            if callable(self.sink):
                self.sink(record)
                return
            raise TypeError("optional sink member must expose save() or be callable")
        except Exception as exc:
            message = f"[Worker] Optional sink '{self.label}' is unavailable: {exc}"
            self.warnings.append(message)
            self.warning_writer(message)

    @staticmethod
    def _default_warning_writer(message: str) -> None:
        print(message, file=sys.stderr)


class CompositeResultSink:
    def __init__(self, sinks: Iterable[object]):
        self.sinks = list(sinks)
        self.warnings: list[str] = []

    def save(self, record: EvaluationRecord) -> None:
        for sink in self.sinks:
            if hasattr(sink, "save"):
                sink.save(record)
                if hasattr(sink, "warnings"):
                    for warning in getattr(sink, "warnings", []):
                        if warning not in self.warnings:
                            self.warnings.append(warning)
                continue
            if callable(sink):
                sink(record)
                continue
            raise TypeError("composite sink member must expose save() or be callable")


def _build_theoretical_metrics(
    record: EvaluationRecord,
    input_row: dict[str, object],
) -> dict[str, object]:
    if record.target != "awsim":
        return {}
    return build_theory_metrics(
        case_kind=record.case_kind,
        values=input_row,
        config_module_name=record.meta.get("config_module")
        if isinstance(record.meta.get("config_module"), str)
        else None,
    )


def serialize_evaluation_record(
    record: EvaluationRecord,
    *,
    path_root: str | Path | None = None,
) -> dict[str, object]:
    validate_evaluation_meta(record.meta)
    resolved_root = (
        Path(path_root).expanduser().resolve() if path_root is not None else None
    )
    meta = dict(record.meta)
    if resolved_root is not None:
        meta.setdefault("path_root", str(resolved_root))
    return {
        "case_id": record.case_id,
        "target": record.target,
        "case_kind": record.case_kind,
        "status": record.status.value,
        "input": dict(record.input),
        "output": dict(record.output),
        "evidence": {
            key: _normalize_evidence_path(value, path_root=resolved_root)
            for key, value in record.evidence.items()
        },
        "meta": meta,
    }


def _normalize_evidence_path(value: str, *, path_root: Path | None) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        return str(path)

    resolved_path = path.resolve()
    if path_root is None:
        return str(resolved_path)

    try:
        return str(resolved_path.relative_to(path_root))
    except ValueError:
        return str(resolved_path)
