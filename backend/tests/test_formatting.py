"""Tests for number formatting in explanation texts."""

from causaltwin.utils.formatting import format_value


def test_format_value_avoids_exponent_notation() -> None:
    """Explanation texts show 4,300 rather than 4.3e+03, and keep small values precise."""
    assert format_value(4300.0) == "4,300"
    assert format_value(3440.4) == "3,440"
    assert format_value(0.75) == "0.75"
    assert format_value(13.0) == "13"
