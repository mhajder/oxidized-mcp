import pytest

from oxidized_mcp.oxidized_client import get_oxidized_config_from_env
from oxidized_mcp.oxidized_client import get_transport_config_from_env

TRANSPORT_ENV_VARS = (
    "MCP_TRANSPORT",
    "MCP_HTTP_HOST",
    "MCP_HTTP_PORT",
    "MCP_HTTP_BEARER_TOKEN",
)


# Isolates each test from the ambient environment and any local .env file.
@pytest.fixture(autouse=True)
def _clean_transport_env(monkeypatch):
    for name in TRANSPORT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults():
    config = get_transport_config_from_env()

    assert config.transport_type == "stdio"
    assert config.http_host == "127.0.0.1"
    assert config.http_port == 8000
    assert config.http_bearer_token is None


def test_http_settings_are_read(monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", "HTTP")
    monkeypatch.setenv("MCP_HTTP_HOST", "192.0.2.10")
    monkeypatch.setenv("MCP_HTTP_PORT", "9000")
    monkeypatch.setenv("MCP_HTTP_BEARER_TOKEN", "  secret-token  ")

    config = get_transport_config_from_env()

    assert config.transport_type == "http"
    assert config.http_host == "192.0.2.10"
    assert config.http_port == 9000
    assert config.http_bearer_token == "secret-token"


# stdio binds no port, so a leftover value must not stop it or `fastmcp run`.
def test_invalid_port_is_ignored_under_stdio(monkeypatch):
    monkeypatch.setenv("MCP_HTTP_PORT", "eighty")

    assert get_transport_config_from_env().http_port == 8000


# A declared-but-empty port must fall back to the default, not crash startup.
def test_blank_port_uses_the_default(monkeypatch):
    monkeypatch.setenv("MCP_HTTP_PORT", "  ")

    assert get_transport_config_from_env().http_port == 8000


def test_invalid_port_names_the_variable(monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    monkeypatch.setenv("MCP_HTTP_PORT", "eighty")

    with pytest.raises(ValueError, match="MCP_HTTP_PORT must be an integer"):
        get_transport_config_from_env()


def test_blank_oxidized_timeout_uses_the_default(monkeypatch):
    monkeypatch.setenv("OXIDIZED_TIMEOUT", "")
    monkeypatch.setenv("OXIDIZED_SEARCH_TIMEOUT", " ")

    config = get_oxidized_config_from_env()
    assert config.timeout == 30
    assert config.search_timeout == 300


# A declared-but-empty token must not become an empty (always matching) secret.
@pytest.mark.parametrize("value", ["", "   "])
def test_blank_bearer_token_counts_as_unset(monkeypatch, value):
    monkeypatch.setenv("MCP_HTTP_BEARER_TOKEN", value)

    assert get_transport_config_from_env().http_bearer_token is None


@pytest.mark.parametrize(
    "name",
    [
        "READ_ONLY_MODE",
        "OXIDIZED_VERIFY_SSL",
        "OXIDIZED_REDACT_NODE_VARS",
        "RATE_LIMIT_ENABLED",
        "TOOL_SEARCH_ENABLED",
    ],
)
def test_unknown_bool_setting_stops_startup(monkeypatch, name):
    """A typo must not turn read-only mode, TLS checks or redaction off."""
    monkeypatch.setenv(name, "enabled")

    with pytest.raises(ValueError, match=f"{name} must be one of"):
        get_oxidized_config_from_env()


# Whitespace-only must count as missing, so main() reports it at startup.
def test_blank_url_and_username_count_as_unset(monkeypatch):
    monkeypatch.setenv("OXIDIZED_URL", "   ")
    monkeypatch.setenv("OXIDIZED_USERNAME", "  ")

    config = get_oxidized_config_from_env()
    assert config.oxidized_url == ""
    assert config.username is None


FEATURE_ENV_VARS = (
    "RATE_LIMIT_ENABLED",
    "RATE_LIMIT_MAX_REQUESTS",
    "RATE_LIMIT_WINDOW_MINUTES",
    "TOOL_SEARCH_ENABLED",
    "TOOL_SEARCH_STRATEGY",
    "TOOL_SEARCH_MAX_RESULTS",
)


@pytest.fixture
def _clean_feature_env(monkeypatch):
    for name in FEATURE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# Leftover values of a disabled feature must not stop the server.
@pytest.mark.usefixtures("_clean_feature_env")
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("RATE_LIMIT_MAX_REQUESTS", "0"),
        ("RATE_LIMIT_WINDOW_MINUTES", "lots"),
        ("TOOL_SEARCH_MAX_RESULTS", "-1"),
        ("TOOL_SEARCH_STRATEGY", "regx"),
    ],
)
def test_disabled_feature_ignores_bad_values(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    config = get_oxidized_config_from_env()
    assert config.rate_limit_max_requests == 60
    assert config.rate_limit_window_minutes == 1
    assert config.tool_search_max_results == 5
    assert config.tool_search_strategy == "bm25"


@pytest.mark.usefixtures("_clean_feature_env")
@pytest.mark.parametrize(
    ("enable", "name"),
    [
        ("RATE_LIMIT_ENABLED", "RATE_LIMIT_MAX_REQUESTS"),
        ("RATE_LIMIT_ENABLED", "RATE_LIMIT_WINDOW_MINUTES"),
        ("TOOL_SEARCH_ENABLED", "TOOL_SEARCH_MAX_RESULTS"),
    ],
)
def test_enabled_feature_rejects_zero(monkeypatch, enable, name):
    monkeypatch.setenv(enable, "true")
    monkeypatch.setenv(name, "0")

    with pytest.raises(ValueError, match=f"must be at least 1: {name}=0"):
        get_oxidized_config_from_env()


@pytest.mark.usefixtures("_clean_feature_env")
def test_enabled_feature_reads_its_settings(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "10")
    monkeypatch.setenv("TOOL_SEARCH_ENABLED", "true")
    monkeypatch.setenv("TOOL_SEARCH_STRATEGY", " Regex ")
    monkeypatch.setenv("TOOL_SEARCH_MAX_RESULTS", "3")

    config = get_oxidized_config_from_env()
    assert config.rate_limit_max_requests == 10
    assert config.tool_search_strategy == "regex"
    assert config.tool_search_max_results == 3


@pytest.mark.usefixtures("_clean_feature_env")
def test_strategy_typo_stops_startup_when_tool_search_is_on(monkeypatch):
    monkeypatch.setenv("TOOL_SEARCH_ENABLED", "true")
    monkeypatch.setenv("TOOL_SEARCH_STRATEGY", "regx")

    with pytest.raises(
        ValueError, match="TOOL_SEARCH_STRATEGY must be 'bm25' or 'regex'"
    ):
        get_oxidized_config_from_env()


@pytest.mark.parametrize("name", ["OXIDIZED_TIMEOUT", "OXIDIZED_SEARCH_TIMEOUT"])
def test_zero_timeout_is_rejected(monkeypatch, name):
    monkeypatch.setenv(name, "0")

    with pytest.raises(ValueError, match="greater than 0"):
        get_oxidized_config_from_env()
