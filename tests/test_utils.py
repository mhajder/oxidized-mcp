import pytest

from oxidized_mcp.utils import parse_bool
from oxidized_mcp.utils import parse_float
from oxidized_mcp.utils import parse_int
from oxidized_mcp.utils import quote_path_segment


@pytest.mark.parametrize(
    ("val", "default", "expected"),
    [
        (None, True, True),
        (None, False, False),
        ("1", False, True),
        ("true", False, True),
        ("yes", False, True),
        ("on", False, True),
        ("TrUe", False, True),
        ("YES", False, True),
        ("ON", False, True),
        ("1 ", False, True),
        ("0", True, False),
        ("false", True, False),
        ("no", True, False),
        ("random", True, False),
        ("False", True, False),
        # Blank means "unset", so the default wins rather than False.
        ("", True, True),
        ("", False, False),
        ("  ", True, True),
        ("  ", False, False),
    ],
)
def test_parse_bool(val, default, expected):
    assert parse_bool(val, default) is expected


@pytest.mark.parametrize("value", ["", " ", "\t", "\n  "])
def test_quote_path_segment_rejects_empty(value):
    # An empty segment collapses into the trailing slash and turns an item
    # request into a collection one, so there is no safe request to make.
    with pytest.raises(ValueError, match="must not be empty"):
        quote_path_segment(value)


@pytest.mark.parametrize(
    ("val", "expected"),
    [(None, 30), ("", 30), ("   ", 30), ("45", 45), (" 7 ", 7), ("0", 0)],
)
def test_parse_int(val, expected):
    assert parse_int(val, 30) == expected


def test_parse_int_rejects_garbage():
    with pytest.raises(ValueError, match="invalid literal"):
        parse_int("thirty", 30)


@pytest.mark.parametrize(
    ("val", "expected"),
    [(None, 1.0), ("", 1.0), ("  ", 1.0), ("0.25", 0.25), (" 0 ", 0.0)],
)
def test_parse_float(val, expected):
    assert parse_float(val, 1.0) == expected
