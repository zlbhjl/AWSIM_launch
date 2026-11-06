"""Named regions select the rows of earlier results (evaluation/region_filter.py)."""

import pandas as pd

from evaluation.region_filter import filter_rows
from targets.awsim.statistical import AWSIM_REGION_VOCABULARY


def _rows(text_dtype: object) -> pd.DataFrame:
    return pd.DataFrame({
        "dx0": [10.5, 12.0, 20.0],
        "c_collision": [0, 1, 0],
        "status": pd.Series(["success", "success", "success"], dtype=text_dtype),
        "reason": pd.Series(["prefill (1)", "prefill (2)", "prefill (3)"], dtype=text_dtype),
    })


def test_a_named_region_keeps_its_rows_with_text_columns() -> None:
    for dtype in (object, "string"):
        inside = filter_rows(_rows(dtype), region="emp_safe", vocabulary=AWSIM_REGION_VOCABULARY)
        assert inside["dx0"].tolist() == [10.5, 20.0], dtype


def test_a_named_region_works_with_the_default_text_dtype() -> None:
    """pandas 3 reads text as its string dtype, which refuses numbers written into it: the
    in-place numeric conversion raised, and the region sampling silently drew from the full
    ranges instead of the region (found by the public CI on Python 3.11/3.12, 2026-11-06)."""
    rows = pd.DataFrame({"dx0": [10.5, 12.0], "c_collision": [0, 1], "status": ["success", "success"]})

    inside = filter_rows(rows, region="emp_safe", vocabulary=AWSIM_REGION_VOCABULARY,
                         bounds={"dx0": (10.0, 11.0)})

    assert inside["dx0"].tolist() == [10.5]
