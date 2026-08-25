from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class FinalReport:
    num_samples: int
    target: str
    reason: str

    def to_payload(self) -> dict[str, object]:
        return asdict(self)


def build_final_report(
    *,
    num_samples: int,
    target: str,
    reason: str,
) -> FinalReport:
    return FinalReport(
        num_samples=max(int(num_samples), 0),
        target=str(target),
        reason=str(reason),
    )


def format_final_report(report: FinalReport) -> str:
    return (
        "\n"
        + "=" * 60
        + f"\n🎉 [検証完了] {report.reason}\n"
        + "=" * 60
        + f"\n・総実行回数: {report.num_samples}回\n・主要ターゲット: {report.target}\n"
        + "=" * 60
        + "\n"
    )


__all__ = [
    "FinalReport",
    "build_final_report",
    "format_final_report",
]
