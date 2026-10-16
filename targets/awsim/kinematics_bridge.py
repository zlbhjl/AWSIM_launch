from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


DEFAULT_EXTRACTOR_DIR = Path(__file__).resolve().parents[2] / "AW_Kinematics_Extractor"
SUPPORTED_KINEMATICS_KEYS = (
    "min_ttc",
    "min_distance",
    "min_ttb",
    "z_margin",
    "c_collision",
)


class KinematicsPipeline(Protocol):
    def get_metrics(self, filepath: str) -> Mapping[str, object]:
        ...


class KinematicsBridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class KinematicsBridgeConfig:
    extractor_dir: Path = DEFAULT_EXTRACTOR_DIR
    mode: str = "cvm"
    target_npcs: tuple[str, ...] = ("npc1",)
    allow_non_finite_metrics: bool = True


@dataclass
class KinematicsBridge:
    config: KinematicsBridgeConfig = field(default_factory=KinematicsBridgeConfig)
    pipeline_factory: Any | None = None

    def extract(self, trace_json_path: str | Path) -> dict[str, object]:
        resolved_path = Path(trace_json_path).expanduser().resolve()
        if not resolved_path.exists():
            raise FileNotFoundError(f"AWSIM trace JSON not found: {resolved_path}")

        pipeline = self._build_pipeline()
        raw_metrics = pipeline.get_metrics(str(resolved_path))
        if not isinstance(raw_metrics, Mapping):
            raise KinematicsBridgeError("AWKinematicsPipeline.get_metrics must return a mapping")

        normalized = _normalize_metrics(
            raw_metrics,
            allow_non_finite_metrics=self.config.allow_non_finite_metrics,
        )
        if not normalized:
            raise KinematicsBridgeError("AW kinematics metrics were empty after normalization")
        return normalized

    def _build_pipeline(self) -> KinematicsPipeline:
        if self.pipeline_factory is not None:
            return self.pipeline_factory(
                self.config.mode,
                list(self.config.target_npcs),
            )

        pipeline_cls = _load_pipeline_class(self.config.extractor_dir)
        return pipeline_cls(
            mode=self.config.mode,
            target_npcs=list(self.config.target_npcs),
        )


def extract_kinematics_metrics(
    trace_json_path: str | Path,
    *,
    mode: str = "cvm",
    target_npcs: Sequence[str] | None = None,
    extractor_dir: str | Path | None = None,
    allow_non_finite_metrics: bool = True,
) -> dict[str, object]:
    config = KinematicsBridgeConfig(
        extractor_dir=Path(extractor_dir).expanduser().resolve()
        if extractor_dir is not None
        else DEFAULT_EXTRACTOR_DIR,
        mode=mode,
        target_npcs=tuple(target_npcs or ("npc1",)),
        allow_non_finite_metrics=allow_non_finite_metrics,
    )
    return KinematicsBridge(config=config).extract(trace_json_path)


def _load_pipeline_class(extractor_dir: Path) -> type[KinematicsPipeline]:
    resolved_dir = extractor_dir.expanduser().resolve()
    if not resolved_dir.exists():
        raise FileNotFoundError(f"AW_Kinematics_Extractor directory not found: {resolved_dir}")

    if str(resolved_dir) not in sys.path:
        sys.path.insert(0, str(resolved_dir))

    try:
        from AW_Kinematics_Extractor.main import AWKinematicsPipeline
    except ImportError as exc:
        raise KinematicsBridgeError(
            f"failed to import AWKinematicsPipeline from {resolved_dir}: {exc}",
        ) from exc

    return AWKinematicsPipeline


def _normalize_metrics(
    metrics: Mapping[str, object],
    *,
    allow_non_finite_metrics: bool,
) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key in SUPPORTED_KINEMATICS_KEYS:
        if key not in metrics:
            continue
        value = metrics[key]
        if isinstance(value, bool):
            normalized[key] = int(value)
            continue
        if isinstance(value, int):
            normalized[key] = value
            continue
        if isinstance(value, float):
            if not allow_non_finite_metrics and not math.isfinite(value):
                continue
            normalized[key] = value
            continue
    return normalized
