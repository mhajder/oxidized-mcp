"""Authentication provider selection for the MCP server.

Kept separate from server.py so the decision here is a pure function of the
transport config and can be tested without importing the server module.
"""

from typing import Any

from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

from oxidized_mcp.models import TransportConfig


def build_auth_provider(config: TransportConfig) -> Any | None:
    """Build the auth provider for the configured transport.

    A static bearer token protects the HTTP transports; without one the
    server runs unauthenticated (fine for STDIO).
    """
    if config.http_bearer_token:
        return StaticTokenVerifier(
            tokens={
                config.http_bearer_token: {
                    "client_id": "authenticated-client",
                    "scopes": ["read", "write"],
                }
            }
        )

    return None
