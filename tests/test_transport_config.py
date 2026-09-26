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


# A declared-but-empty port must fall back to the default, not crash startup.
def test_blank_port_uses_the_default(monkeypatch):
    monkeypatch.setenv("MCP_HTTP_PORT", "  ")

    assert get_transport_config_from_env().http_port == 8000


def test_invalid_port_names_the_variable(monkeypatch):
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
