from contracts.statistics import StatisticalReport, StatisticalRequest


def test_statistical_request_defaults_are_stable() -> None:
    request = StatisticalRequest(method="dkw", metric="c_collision")

    assert request.bounds is None
    assert request.confidence == 0.95
    assert request.target_width is None
    assert request.options == {}


def test_statistical_request_accepts_bounds_and_options() -> None:
    request = StatisticalRequest(
        method="binomial_ci",
        metric="c_collision",
        bounds={"dx0": (20.0, 25.0)},
        options={"region": "intersect_safe"},
    )

    assert request.bounds == {"dx0": (20.0, 25.0)}
    assert request.options["region"] == "intersect_safe"


def test_statistical_request_normalizes_values() -> None:
    request = StatisticalRequest(
        method="dkw",
        metric="min_ttc",
        bounds={"dx0": [20, 25]},
        confidence="0.9",
        target_width="0.15",
    )

    assert request.bounds == {"dx0": (20.0, 25.0)}
    assert request.confidence == 0.9
    assert request.target_width == 0.15


def test_statistical_request_rejects_invalid_confidence() -> None:
    try:
        StatisticalRequest(method="dkw", metric="min_ttc", confidence=1.0)
    except ValueError as exc:
        assert str(exc) == "confidence must satisfy 0.0 < confidence < 1.0"
    else:
        raise AssertionError("Expected ValueError for invalid confidence")


def test_statistical_request_rejects_inverted_bounds() -> None:
    try:
        StatisticalRequest(
            method="dkw",
            metric="min_ttc",
            bounds={"dx0": (25.0, 20.0)},
        )
    except ValueError as exc:
        assert str(exc) == "Bounds for dx0 must satisfy lower <= upper"
    else:
        raise AssertionError("Expected ValueError for inverted bounds")


def test_statistical_report_defaults_and_required_fields() -> None:
    report = StatisticalReport(
        method="dkw",
        metric="c_collision",
        sample_count=128,
        estimate=0.01,
        interval=(0.0, 0.03),
        sufficient=False,
        next_action="collect_more_samples",
    )

    assert report.method == "dkw"
    assert report.metric == "c_collision"
    assert report.sample_count == 128
    assert report.estimate == 0.01
    assert report.interval == (0.0, 0.03)
    assert report.sufficient is False
    assert report.next_action == "collect_more_samples"
    assert report.diagnostics == {}
    assert report.interval_width == 0.03


def test_statistical_report_normalizes_values() -> None:
    report = StatisticalReport(
        method="binomial_ci",
        metric="c_collision",
        sample_count="32",
        estimate="0.125",
        interval=[0, 0.25],
        sufficient=1,
        next_action="stop",
    )

    assert report.sample_count == 32
    assert report.estimate == 0.125
    assert report.interval == (0.0, 0.25)
    assert report.sufficient is True
    assert report.interval_width == 0.25


def test_statistical_report_rejects_invalid_values() -> None:
    try:
        StatisticalReport(
            method="dkw",
            metric="min_ttc",
            sample_count=-1,
            estimate=None,
            interval=None,
            sufficient=False,
            next_action="collect_more_samples",
        )
    except ValueError as exc:
        assert str(exc) == "sample_count must be non-negative"
    else:
        raise AssertionError("Expected ValueError for negative sample_count")
