import pytest

from oxidized_mcp.utils import env_bool
from oxidized_mcp.utils import env_float
from oxidized_mcp.utils import env_int
from oxidized_mcp.utils import env_str
from oxidized_mcp.utils import http_host_from_env
from oxidized_mcp.utils import http_port_from_env
from oxidized_mcp.utils import parse_bool
from oxidized_mcp.utils import parse_float
from oxidized_mcp.utils import parse_int
from oxidized_mcp.utils import quote_path_segment
from oxidized_mcp.utils import transport_from_env


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


@pytest.mark.parametrize("val", ["random", "enabled", "enable", "y", "2"])
@pytest.mark.parametrize("default", [True, False])
def test_parse_bool_rejects_unknown_values(val, default):
    """A typo must not read as False: READ_ONLY_MODE=enable would expose writes."""
    with pytest.raises(ValueError, match=f"got {val!r}"):
        parse_bool(val, default)


def test_env_bool_names_the_variable(monkeypatch):
    monkeypatch.setenv("OXIDIZED_VERIFY_SSL", "enabled")
    with pytest.raises(ValueError, match="OXIDIZED_VERIFY_SSL must be one of"):
        env_bool("OXIDIZED_VERIFY_SSL", True)


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


@pytest.fixture
def _clean_transport_env(monkeypatch):
    for name in ("MCP_TRANSPORT", "MCP_HTTP_HOST", "MCP_HTTP_PORT"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("_clean_transport_env")
@pytest.mark.parametrize(
    ("val", "expected"),
    [
        (None, "stdio"),
        ("http", "http"),
        ("SSE", "sse"),
        # FastMCP's name for the streamable HTTP transport.
        ("streamable-http", "http"),
        ("", ""),
    ],
)
def test_transport_from_env(monkeypatch, val, expected):
    if val is not None:
        monkeypatch.setenv("MCP_TRANSPORT", val)
    assert transport_from_env() == expected


@pytest.mark.usefixtures("_clean_transport_env")
@pytest.mark.parametrize(
    ("val", "expected"),
    [
        (None, "127.0.0.1"),
        # Blank counts as unset; binding every interface must be explicit.
        ("", "127.0.0.1"),
        ("   ", "127.0.0.1"),
        (" 0.0.0.0 ", "0.0.0.0"),  # noqa: S104
        ("::", "::"),
        # The URL form of an IPv6 literal is accepted; uvicorn needs it bare.
        ("[::]", "::"),
        ("[fd00::5]", "fd00::5"),
    ],
)
def test_http_host_from_env(monkeypatch, val, expected):
    if val is not None:
        monkeypatch.setenv("MCP_HTTP_HOST", val)
    assert http_host_from_env() == expected


@pytest.mark.usefixtures("_clean_transport_env")
@pytest.mark.parametrize(
    ("val", "expected"), [(None, 8000), (" ", 8000), ("9000", 9000)]
)
def test_http_port_from_env(monkeypatch, val, expected):
    if val is not None:
        monkeypatch.setenv("MCP_HTTP_PORT", val)
    assert http_port_from_env() == expected


def test_env_int_names_the_variable(monkeypatch):
    monkeypatch.setenv("OXIDIZED_TIMEOUT", "thirty")
    with pytest.raises(ValueError, match="OXIDIZED_TIMEOUT must be an integer"):
        env_int("OXIDIZED_TIMEOUT", 30)


def test_env_float_names_the_variable(monkeypatch):
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "half")
    with pytest.raises(ValueError, match="SENTRY_TRACES_SAMPLE_RATE must be a number"):
        env_float("SENTRY_TRACES_SAMPLE_RATE", 1.0)


@pytest.mark.parametrize(("val", "expected"), [(None, 1.0), ("", 1.0), (" ", 1.0)])
def test_env_float_treats_blank_as_unset(monkeypatch, val, expected):
    if val is None:
        monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
    else:
        monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", val)
    assert env_float("SENTRY_TRACES_SAMPLE_RATE", 1.0) == expected


@pytest.mark.parametrize(
    ("val", "expected"),
    [(None, None), ("", None), ("   ", None), (" value ", "value")],
)
def test_env_str(monkeypatch, val, expected):
    if val is None:
        monkeypatch.delenv("OXIDIZED_TEST_STR", raising=False)
    else:
        monkeypatch.setenv("OXIDIZED_TEST_STR", val)
    assert env_str("OXIDIZED_TEST_STR") == expected
