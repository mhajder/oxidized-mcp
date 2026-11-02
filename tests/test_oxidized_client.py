"""Tests for OxidizedClient's lifecycle and response handling."""

import httpx2
import pytest

from oxidized_mcp import oxidized_client as client_module
from oxidized_mcp.models import OxidizedConfig
from oxidized_mcp.oxidized_client import _MAX_ERROR_BODY_LENGTH
from oxidized_mcp.oxidized_client import OxidizedClient
from oxidized_mcp.oxidized_client import OxidizedError
from oxidized_mcp.oxidized_client import _annotate_with_response_body
from oxidized_mcp.oxidized_client import get_oxidized_client

BASE_URL = "https://oxidized.test"


def mocked_client(handler) -> OxidizedClient:
    client = OxidizedClient(OxidizedConfig(oxidized_url=BASE_URL))
    client.client = httpx2.AsyncClient(
        base_url=client.base_url, transport=httpx2.MockTransport(handler)
    )
    return client


@pytest.mark.asyncio
async def test_context_manager_creates_a_reusable_client_and_close_drops_it():
    client = OxidizedClient(OxidizedConfig(oxidized_url=BASE_URL))

    async with client as entered:
        assert entered is client
        first = client.client
        assert isinstance(first, httpx2.AsyncClient)
    async with client:
        assert client.client is first

    await client.close()
    assert client.client is None
    # Closing twice is harmless.
    await client.close()


@pytest.mark.asyncio
async def test_request_without_a_client_explains_how_to_open_one():
    client = OxidizedClient(OxidizedConfig(oxidized_url=BASE_URL))

    with pytest.raises(RuntimeError, match="Client not initialized"):
        await client.request("GET", "/nodes.json")


def test_unparsable_url_is_kept_without_moving_credentials():
    client = OxidizedClient(OxidizedConfig(oxidized_url="http://[::1"))

    assert client.base_url == "http://[::1"
    assert client.build_auth() is None


@pytest.mark.asyncio
async def test_empty_json_response_is_none():
    client = mocked_client(lambda _request: httpx2.Response(200, content=b""))

    assert await client.get_json("/nodes.json") is None


@pytest.mark.asyncio
async def test_resolve_node_rejects_a_non_object_response():
    client = mocked_client(lambda _request: httpx2.Response(200, json=["a", "b"]))

    with pytest.raises(OxidizedError, match="Unexpected response for node 'sw1'"):
        await client.resolve_node("sw1")


def test_long_error_bodies_are_truncated():
    request = httpx2.Request("GET", f"{BASE_URL}/nodes.json")
    response = httpx2.Response(
        502, text="x" * (_MAX_ERROR_BODY_LENGTH + 50), request=request
    )
    original = httpx2.HTTPStatusError("Bad gateway", request=request, response=response)

    message = str(_annotate_with_response_body(original))

    assert message.endswith("x" * _MAX_ERROR_BODY_LENGTH + "... (truncated)")


def test_singleton_needs_a_config_on_first_use(monkeypatch):
    monkeypatch.setattr(client_module, "_oxidized_client_singleton", None)

    with pytest.raises(ValueError, match="config must be provided"):
        get_oxidized_client()


def test_singleton_is_reused_without_a_config(monkeypatch):
    monkeypatch.setattr(client_module, "_oxidized_client_singleton", None)
    first = get_oxidized_client(OxidizedConfig(oxidized_url=BASE_URL))

    assert get_oxidized_client() is first
