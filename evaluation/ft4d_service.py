from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from contracts.verification import FT4DResult, VerificationInput
from verification_core.ft4d import (
    FT4DCalculator,
    FaultTree,
    RecognitionTestResult,
    basic_error_rate,
)


@dataclass(frozen=True)
class FT4DServiceConfig:
    tree_path: str | Path | None = None
    sigma_pf_source: str = "dataset"
    and_rule: str = "min"
    source_module: str = "evaluation.ft4d_service"


class FT4DService:
    def __init__(self, config: FT4DServiceConfig | None = None):
        self.config = config or FT4DServiceConfig()

    def run(self, verification_input: VerificationInput) -> FT4DResult:
        tree_path = self._resolve_tree_path(verification_input)
        sigma_pf_source = str(
            verification_input.assumptions.get(
                "sigma_pf_source",
                self.config.sigma_pf_source,
            )
        )
        and_rule = str(
            verification_input.assumptions.get("and_rule", self.config.and_rule)
        )

        tree = FaultTree.from_json(str(tree_path))
        calculator = FT4DCalculator(
            tree,
            sigma_pf_source=sigma_pf_source,
            and_rule=and_rule,
        )
        calculator.set_universal_dataset(verification_input.universal_dataset)

        sigma_pf_assumptions = verification_input.assumptions.get(
            "sigma_pf_assumptions",
            {},
        )
        if not isinstance(sigma_pf_assumptions, Mapping):
            raise TypeError("sigma_pf_assumptions must be a mapping when provided")

        for event_id, event_payload in verification_input.events.items():
            self._apply_event_payload(
                calculator,
                tree,
                event_id,
                event_payload,
                sigma_pf_assumptions=sigma_pf_assumptions,
            )

        report = calculator.calculate()
        return FT4DResult(
            tree_mode=verification_input.tree_mode,
            top_sigma_pe=report["tree"]["sigma_pe"],
            confidence=report["tree"].get("confidence"),
            node_summaries=_flatten_node_summaries(report["tree"]),
            raw_result={
                "tree_path": str(tree_path),
                "sigma_pf_source": sigma_pf_source,
                "and_rule": and_rule,
                "tree_report": report,
                "meta": {
                    **verification_input.meta,
                    "source_module": self.config.source_module,
                },
            },
        )

    def _resolve_tree_path(self, verification_input: VerificationInput) -> Path:
        candidate = (
            self.config.tree_path
            or verification_input.meta.get("tree_path")
            or verification_input.assumptions.get("tree_path")
        )
        if candidate is None:
            raise ValueError("tree_path must be provided via config, meta, or assumptions")
        return Path(candidate).expanduser().resolve()

    def _apply_event_payload(
        self,
        calculator: FT4DCalculator,
        tree: FaultTree,
        event_id: str,
        event_payload: Mapping[str, object],
        *,
        sigma_pf_assumptions: Mapping[str, object],
    ) -> None:
        if event_id not in tree.collect_basic_events():
            raise ValueError(f"Unknown basic event in tree: {event_id}")

        dataset_d = set(event_payload.get("dataset_d", set()))
        dataset_e = set(event_payload.get("dataset_e", set()))
        total_count = int(event_payload.get("total_count", len(dataset_d)))
        error_count = int(event_payload.get("error_count", len(dataset_e)))
        sigma_pf = float(
            event_payload.get(
                "sigma_pf",
                sigma_pf_assumptions.get(
                    event_id,
                    tree.params.get(event_id, {}).get("sigma_pf", 0.0),
                ),
            )
        )
        sigma_pb = float(
            event_payload.get(
                "sigma_pb",
                basic_error_rate(error_count, total_count),
            )
        )

        calculator.set_basic_event(event_id, sigma_pf=sigma_pf, sigma_pb=sigma_pb)
        calculator.set_basic_event_datasets(event_id, dataset_d, dataset_e)

        recognition_test = event_payload.get("recognition_test")
        if recognition_test is not None:
            if isinstance(recognition_test, Mapping):
                recognition_test = RecognitionTestResult(**recognition_test)
            calculator.set_basic_event_test(event_id, recognition_test)


def run_ft4d(
    verification_input: VerificationInput,
    config: FT4DServiceConfig | None = None,
) -> FT4DResult:
    return FT4DService(config=config).run(verification_input)


def _flatten_node_summaries(tree_node: Mapping[str, object]) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []

    def walk(node: Mapping[str, object]) -> None:
        summaries.append(
            {
                "node_id": node["id"],
                "type": node.get("type", "gate"),
                "gate": node.get("gate"),
                "sigma_pf": node.get("sigma_pf"),
                "sigma_pe": node.get("sigma_pe"),
                "confidence": node.get("confidence"),
                "confidence_delta": node.get("confidence_delta"),
            }
        )
        for child in node.get("children", []):
            walk(child)

    walk(tree_node)
    return summaries
