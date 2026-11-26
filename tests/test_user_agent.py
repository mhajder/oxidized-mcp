"""Tests for outbound User-Agent configuration and header injection."""

import httpx2
import pytest

from oxidized_mcp import oxidized_client as client_module
from oxidized_mcp.models import OxidizedConfig
from oxidized_mcp.oxidized_client import DEFAULT_USER_AGENT
from oxidized_mcp.oxidized_client import OxidizedClient
from oxidized_mcp.oxidized_client import get_oxidized_config_from_env


def test_env_user_agent_defaults_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OXIDIZED_URL", "https://oxidized.test")
    monkeypatch.delenv("OXIDIZED_USER_AGENT", raising=False)

    assert get_oxidized_config_from_env().user_agent is None

    monkeypatch.setenv("OXIDIZED_USER_AGENT", "  custom-oxidized/1.0  ")
    assert get_oxidized_config_from_env().user_agent == "custom-oxidized/1.0"

    monkeypatch.setenv("OXIDIZED_USER_AGENT", "   ")
    assert get_oxidized_config_from_env().user_agent is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (None, DEFAULT_USER_AGENT),
        ("custom-oxidized/1.0", "custom-oxidized/1.0"),
    ],
)
async def test_outbound_requests_send_user_agent(
    monkeypatch: pytest.MonkeyPatch, configured: str | None, expected: str
) -> None:
    seen: list[str | None] = []
    real_async_client = httpx2.AsyncClient

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers.get("User-Agent"))
        return httpx2.Response(200, json=[])

    def patched_async_client(*args, **kwargs):
        kwargs["transport"] = httpx2.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", patched_async_client)

    client_module._oxidized_client_singleton = None
    try:
        client = OxidizedClient(
            OxidizedConfig(
                oxidized_url="https://oxidized.test",
                verify_ssl=False,
                user_agent=configured,
            )
        )
        async with client:
            await client.get_json("/nodes.json")
        await client.close()
    finally:
        client_module._oxidized_client_singleton = None

    assert seen == [expected]
