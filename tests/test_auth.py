from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

from oxidized_mcp.auth import build_auth_provider
from oxidized_mcp.models import TransportConfig


def test_build_auth_provider_returns_none_without_auth():
    assert build_auth_provider(TransportConfig()) is None


def test_build_auth_provider_uses_bearer_token():
    provider = build_auth_provider(TransportConfig(http_bearer_token="secret-token"))

    assert isinstance(provider, StaticTokenVerifier)
