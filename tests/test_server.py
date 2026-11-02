"""In-process tests for server.py's module-level setup and main().

server.py configures everything at import time, so each test imports a fresh
copy under a controlled environment. The subprocess tests in
test_healthcheck.py cover the same module end to end, but coverage does not
see them.
"""

import importlib
import importlib.metadata
import logging
import sys
from unittest.mock import MagicMock

import dotenv
import pytest
from fastmcp.server.middleware.rate_limiting import SlidingWindowRateLimitingMiddleware
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.server.transforms.search import RegexSearchTransform

MODULE = "oxidized_mcp.server"

SERVER_ENV_VARS = (
    "OXIDIZED_URL",
    "OXIDIZED_USERNAME",
    "OXIDIZED_PASSWORD",
    "OXIDIZED_DISABLED_TAGS",
    "READ_ONLY_MODE",
    "RATE_LIMIT_ENABLED",
    "TOOL_SEARCH_ENABLED",
    "TOOL_SEARCH_STRATEGY",
    "LOG_LEVEL",
    "MCP_TRANSPORT",
    "MCP_HTTP_HOST",
    "MCP_HTTP_PORT",
    "MCP_HTTP_BEARER_TOKEN",
    "SENTRY_DSN",
)


@pytest.fixture
def load_server(monkeypatch):
    """Import a fresh server module with only the given environment."""
    for name in SERVER_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # A developer's .env must not leak into the tests.
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_args, **_kwargs: False)

    def factory(**env: str):
        env.setdefault("OXIDIZED_URL", "https://oxidized.test")
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        sys.modules.pop(MODULE, None)
        module = importlib.import_module(MODULE)
        monkeypatch.setattr(module.mcp, "run", MagicMock())
        return module

    yield factory
    sys.modules.pop(MODULE, None)


async def tool_names(module) -> set[str]:
    return {tool.name for tool in await module.mcp.list_tools()}


@pytest.mark.asyncio
async def test_health_route_answers_ok(load_server):
    server = load_server()

    response = await server.health(MagicMock())

    assert response.status_code == 200
    assert response.body == b"ok"


@pytest.mark.asyncio
async def test_all_tools_are_exposed_by_default(load_server):
    names = await tool_names(load_server())

    assert {"trigger_node_backup", "list_nodes", "search_configs"} <= names
    assert "search_tools" not in names


@pytest.mark.asyncio
async def test_read_only_mode_hides_write_tools(load_server, caplog):
    caplog.set_level(logging.INFO)
    names = await tool_names(load_server(READ_ONLY_MODE="true"))

    assert "list_nodes" in names
    assert "trigger_node_backup" not in names
    assert "Read-only mode is enabled" in caplog.text


@pytest.mark.asyncio
async def test_disabled_tags_hide_matching_tools(load_server):
    names = await tool_names(load_server(OXIDIZED_DISABLED_TAGS="version, backup"))

    assert "list_node_versions" not in names
    assert "trigger_node_backup" not in names
    assert "list_nodes" in names


@pytest.mark.parametrize(
    ("strategy", "transform"),
    [("bm25", BM25SearchTransform), ("regex", RegexSearchTransform)],
)
@pytest.mark.asyncio
async def test_tool_search_replaces_the_tool_list(load_server, strategy, transform):
    server = load_server(TOOL_SEARCH_ENABLED="true", TOOL_SEARCH_STRATEGY=strategy)

    assert any(isinstance(t, transform) for t in server.mcp.transforms)
    assert await tool_names(server) == {"search_tools", "call_tool"}


def test_rate_limiting_adds_middleware(load_server):
    server = load_server(RATE_LIMIT_ENABLED="true")

    assert any(
        isinstance(m, SlidingWindowRateLimitingMiddleware)
        for m in server.mcp.middleware
    )


def test_rate_limiting_is_off_by_default(load_server):
    server = load_server()

    assert not any(
        isinstance(m, SlidingWindowRateLimitingMiddleware)
        for m in server.mcp.middleware
    )


def test_unknown_log_level_falls_back_to_info(load_server, caplog):
    load_server(LOG_LEVEL="chatty")

    assert "Unknown LOG_LEVEL 'CHATTY'" in caplog.text


def test_version_falls_back_when_package_is_not_installed(load_server, monkeypatch):
    def missing(_name):
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", missing)

    assert load_server().__version__ == "0.0.1"


def test_invalid_configuration_is_logged_and_raised(load_server, caplog):
    with pytest.raises(ValueError, match="TOOL_SEARCH_STRATEGY"):
        load_server(TOOL_SEARCH_ENABLED="true", TOOL_SEARCH_STRATEGY="regx")

    assert "Invalid configuration" in caplog.text


def test_main_runs_stdio_by_default(load_server):
    server = load_server()

    server.main()

    server.mcp.run.assert_called_once_with()


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_main_runs_http_transports_on_the_configured_address(
    load_server, caplog, transport
):
    caplog.set_level(logging.INFO)
    server = load_server(
        MCP_TRANSPORT=transport,
        MCP_HTTP_HOST="192.0.2.10",
        MCP_HTTP_PORT="9001",
        MCP_HTTP_BEARER_TOKEN="secret",
    )

    server.main()

    server.mcp.run.assert_called_once_with(
        transport=transport, host="192.0.2.10", port=9001
    )
    assert "Bearer token authentication enabled" in caplog.text
    assert "WITHOUT authentication" not in caplog.text


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_main_warns_when_http_runs_without_a_token(load_server, caplog, transport):
    server = load_server(MCP_TRANSPORT=transport)

    server.main()

    assert "WITHOUT authentication" in caplog.text
    assert "Bearer token authentication enabled" not in caplog.text


def test_main_rejects_an_unknown_transport(load_server, caplog):
    server = load_server(MCP_TRANSPORT="websocket")

    with pytest.raises(SystemExit) as exc:
        server.main()

    assert exc.value.code == 1
    assert "Unknown MCP_TRANSPORT 'websocket'" in caplog.text
    server.mcp.run.assert_not_called()


def test_main_requires_the_oxidized_url(load_server, caplog):
    server = load_server(OXIDIZED_URL="")

    with pytest.raises(SystemExit) as exc:
        server.main()

    assert exc.value.code == 1
    assert "OXIDIZED_URL" in caplog.text
    server.mcp.run.assert_not_called()


def test_main_logs_the_url_without_credentials(load_server, caplog):
    caplog.set_level(logging.INFO)
    server = load_server(OXIDIZED_URL="https://admin:hunter2@oxidized.test")

    server.main()

    assert "hunter2" not in caplog.text
    assert "Starting Oxidized MCP Server" in caplog.text
