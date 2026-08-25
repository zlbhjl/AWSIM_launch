from orchestration.final_report import build_final_report, format_final_report


def test_format_final_report_matches_legacy_layout() -> None:
    report = build_final_report(
        num_samples=12,
        target="Binomial CI",
        reason="Binomial CI Complete: c_collision CI width 0.01000",
    )

    rendered = format_final_report(report)

    assert "🎉 [検証完了] Binomial CI Complete: c_collision CI width 0.01000" in rendered
    assert "・総実行回数: 12回" in rendered
    assert "・主要ターゲット: Binomial CI" in rendered
    assert rendered.count("=" * 60) == 3
