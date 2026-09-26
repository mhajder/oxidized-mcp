"""Shared fixtures: a FastMCP server wired to a mocked oxidized-web."""

from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from fastmcp import FastMCP

from oxidized_mcp import oxidized_client as client_module
from oxidized_mcp.models import OxidizedConfig
from oxidized_mcp.tools import register_tools

BASE_URL = "https://oxidized.test"

Handler = Callable[[httpx2.Request], httpx2.Response]


def _reset_singletons() -> None:
    # The client is a module-level singleton; reset it to give each test its
    # own transport.
    client_module._oxidized_client_singleton = None


@pytest.fixture
def make_server():
    """Build a server whose Oxidized client answers through ``handler``.

    Returns ``(mcp, requests)``; every request that reached the mock transport
    is appended to ``requests``.
    """

    def factory(
        handler: Handler, **config_overrides: Any
    ) -> tuple[FastMCP, list[httpx2.Request]]:
        config = OxidizedConfig(oxidized_url=BASE_URL, **config_overrides)
        mcp = FastMCP(name="test")
        register_tools(mcp, config)

        requests: list[httpx2.Request] = []

        def recording_handler(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            return handler(request)

        _reset_singletons()
        client = client_module.get_oxidized_client(config)
        client.client = httpx2.AsyncClient(
            base_url=client.base_url,
            auth=client.build_auth(),
            transport=httpx2.MockTransport(recording_handler),
        )
        return mcp, requests

    yield factory
    _reset_singletons()


async def call(mcp: FastMCP, name: str, arguments: dict | None = None) -> Any:
    """Run a registered tool and return its structured result."""
    tool = await mcp.get_tool(name)
    assert tool is not None, f"tool {name!r} is not registered"
    result = await tool.run(arguments or {})
    return result.structured_content


def raw_path(request: httpx2.Request) -> str:
    """The request path exactly as sent, without the query string."""
    return request.url.raw_path.decode().split("?", 1)[0]
