from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BBSLExecutionProfile:
    target_repo: str
    mini: bool = False
    max_images: int | None = None
    tree_mode: str = "basic"
    sigma_pf_source: str = "dataset"
    sigma_pb_mode: str = "delta-clean"
    and_rule: str = "min"
    detect_timeout: int | None = None
    reuse_existing_output: bool = False

    @classmethod
    def from_input(
        cls,
        payload: Mapping[str, object],
        *,
        default_target_repo: str,
    ) -> "BBSLExecutionProfile":
        if not payload:
            raise ValueError("BBSL execution profile requires explicit input parameters")

        target_repo = payload.get("target_repo", default_target_repo)
        tree_mode = payload.get("tree_mode", payload.get("tree", "basic"))
        return cls(
            target_repo=str(Path(str(target_repo)).expanduser().resolve()),
            mini=_coerce_bool(payload.get("mini", False)),
            max_images=_optional_int(payload.get("max_images")),
            tree_mode=_normalize_string(tree_mode, default="basic"),
            sigma_pf_source=_normalize_string(
                payload.get("sigma_pf_source"),
                default="dataset",
            ),
            sigma_pb_mode=_normalize_string(
                payload.get("sigma_pb_mode"),
                default="delta-clean",
            ),
            and_rule=_normalize_string(payload.get("and_rule"), default="min"),
            detect_timeout=_optional_int(payload.get("detect_timeout")),
            reuse_existing_output=_coerce_bool(
                payload.get("reuse_existing_output", False)
            ),
        )

    def runner_kwargs(self) -> dict[str, object]:
        return {
            "mini": self.mini,
            "max_images": self.max_images,
            "tree": self.tree_mode,
            "sigma_pf_source": self.sigma_pf_source,
            "sigma_pb_mode": self.sigma_pb_mode,
            "and_rule": self.and_rule,
            "detect_timeout": self.detect_timeout,
        }

    def to_meta(self) -> dict[str, object]:
        return {
            "target_repo": self.target_repo,
            "mini": self.mini,
            "max_images": self.max_images,
            "tree_mode": self.tree_mode,
            "sigma_pf_source": self.sigma_pf_source,
            "sigma_pb_mode": self.sigma_pb_mode,
            "and_rule": self.and_rule,
            "detect_timeout": self.detect_timeout,
            "reuse_existing_output": self.reuse_existing_output,
        }


def build_execution_profile(
    payload: Mapping[str, object],
    *,
    default_target_repo: str,
) -> BBSLExecutionProfile:
    return BBSLExecutionProfile.from_input(
        payload,
        default_target_repo=default_target_repo,
    )


def _normalize_string(value: object, *, default: str) -> str:
    if value is None:
        return default
    normalized = str(value).strip()
    return normalized or default


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    return int(str(value))


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    return bool(value)
