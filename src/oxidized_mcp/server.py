#!/usr/bin/env python3
"""
Oxidized MCP Server

Provides a Model Context Protocol (MCP) server exposing tools that interact with the Oxidized web API.
"""

import logging
import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp import settings
from fastmcp.server.middleware.rate_limiting import SlidingWindowRateLimitingMiddleware
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.server.transforms.search import RegexSearchTransform
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from oxidized_mcp.auth import build_auth_provider
from oxidized_mcp.oxidized_client import get_oxidized_config_from_env
from oxidized_mcp.oxidized_client import get_transport_config_from_env
from oxidized_mcp.sentry_init import init_sentry
from oxidized_mcp.tools import register_tools
from oxidized_mcp.utils import HEALTH_PATH
from oxidized_mcp.utils import HTTP_TRANSPORTS

# Load environment variables
load_dotenv()

# Configure FastMCP defaults
settings.show_server_banner = False
settings.check_for_updates = "off"

# Configure logging. An unknown or lowercase LOG_LEVEL must not take the server
# down, so resolve it leniently and fall back to INFO.
_requested_log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
_resolved_log_level = logging.getLevelNamesMapping().get(_requested_log_level)
logging.basicConfig(
    level=_resolved_log_level if _resolved_log_level is not None else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Initialize optional Sentry monitoring. This runs after logging is configured
# so that its "enabled" message is not dropped.
init_sentry()

if _resolved_log_level is None:
    logger.warning(
        "Unknown LOG_LEVEL %r - falling back to INFO. Valid values: %s",
        _requested_log_level,
        ", ".join(logging.getLevelNamesMapping()),
    )

# Get package version
try:
    __version__ = version("oxidized-mcp")
except PackageNotFoundError:
    __version__ = "0.0.1"

try:
    OXIDIZED_CONFIG = get_oxidized_config_from_env()
    TRANSPORT_CONFIG = get_transport_config_from_env()
except Exception as e:
    logger.error(f"Invalid configuration: {e}")
    raise

auth_provider = build_auth_provider(TRANSPORT_CONFIG)

# Initialize FastMCP server
mcp = FastMCP(
    name="Oxidized MCP Server",
    version=__version__,
    instructions=(
        "You are an assistant with access to the Oxidized MCP Server, exposing tools to "
        "interact with Oxidized, a network device configuration backup tool, through its "
        "oxidized-web REST API.\n\n"
        "Available Capabilities:\n"
        "1. Nodes: List and filter managed devices (by group, model, last run status), find devices "
        "by partial name or IP, and inspect a single device's last backup run.\n"
        "2. Backup Health: Summarise backup status across all devices - failures, devices never "
        "backed up and devices whose last run is stale.\n"
        "3. Configurations: Read the latest backed-up configuration of a device (paged by lines) and "
        "search all configurations for a regular expression or literal text.\n"
        "4. Version History: List a device's stored configuration versions, read the configuration at "
        "a given version and diff two versions (requires the Oxidized git output).\n"
        "5. Operations: Queue an immediate backup of a device and reload the node list from its source.\n\n"
        "Guidelines:\n"
        "- Node names are case-sensitive. When a name is ambiguous or unknown, use find_nodes first.\n"
        "- For large configurations, page with offset/next_offset instead of requesting everything at once.\n"
        "- trigger_node_backup only queues the backup; check get_node afterwards for the result."
    ),
    auth=auth_provider,
)


@mcp.custom_route(HEALTH_PATH, methods=["GET"], include_in_schema=False)
async def health(_request: Request) -> PlainTextResponse:
    """Liveness probe for the HTTP transports.

    Answering proves the server handles requests, which a TCP connect does not:
    the kernel completes the handshake even while the event loop is stuck. It
    deliberately does not call Oxidized, so an outage there does not get a
    healthy MCP server restarted. FastMCP serves custom routes outside the
    bearer-token auth, so the container healthcheck needs no token.
    """
    return PlainTextResponse("ok")


# Register all tools
register_tools(mcp, OXIDIZED_CONFIG)


def configure_component_visibility() -> None:
    """Apply server-level visibility transforms for read-only and disabled tags."""

    if OXIDIZED_CONFIG.read_only_mode:
        logger.info("Read-only mode is enabled - restricting to read-only components")
        mcp.enable(tags={"read-only"}, only=True)

    if OXIDIZED_CONFIG.disabled_tags:
        logger.info(
            "Disabled tags configured: %s - disabling matching components",
            OXIDIZED_CONFIG.disabled_tags,
        )
        mcp.disable(tags=OXIDIZED_CONFIG.disabled_tags)


def configure_tool_search() -> None:
    """Apply the optional FastMCP tool-search transform."""

    if not OXIDIZED_CONFIG.tool_search_enabled:
        return

    strategy = OXIDIZED_CONFIG.tool_search_strategy
    max_results = OXIDIZED_CONFIG.tool_search_max_results

    if strategy == "regex":
        mcp.add_transform(RegexSearchTransform(max_results=max_results))
    else:
        mcp.add_transform(BM25SearchTransform(max_results=max_results))

    logger.info(
        "Tool search is enabled - strategy=%s, max_results=%s",
        strategy,
        max_results,
    )


configure_component_visibility()
configure_tool_search()

# Optional rate limiting
if OXIDIZED_CONFIG.rate_limit_enabled:
    logger.info("Rate limiting is enabled - applying middleware")
    mcp.add_middleware(
        SlidingWindowRateLimitingMiddleware(
            max_requests=OXIDIZED_CONFIG.rate_limit_max_requests,
            window_minutes=OXIDIZED_CONFIG.rate_limit_window_minutes,
        )
    )


def main():
    # Basic validation
    if not OXIDIZED_CONFIG.oxidized_url:
        logger.error(
            "Missing required Oxidized configuration (OXIDIZED_URL). Check your .env file."
        )
        raise SystemExit(1)

    if (
        TRANSPORT_CONFIG.transport_type in HTTP_TRANSPORTS
        and not TRANSPORT_CONFIG.http_bearer_token
    ):
        logger.warning(
            "WARNING: MCP_HTTP_BEARER_TOKEN is not set. The MCP server will run WITHOUT authentication. "
            "Ensure the server is not exposed to untrusted networks (e.g. bind to 127.0.0.1 instead of 0.0.0.0)."
        )

    logger.info(f"Starting Oxidized MCP Server at {OXIDIZED_CONFIG.oxidized_url} ...")

    # Choose transport based on configuration
    if TRANSPORT_CONFIG.transport_type == "sse":
        logger.info(
            f"Using HTTP SSE transport on {TRANSPORT_CONFIG.http_host}:{TRANSPORT_CONFIG.http_port}"
        )
        if TRANSPORT_CONFIG.http_bearer_token:
            logger.info("Bearer token authentication enabled for SSE transport")

        # Run with HTTP SSE transport
        mcp.run(
            transport="sse",
            host=TRANSPORT_CONFIG.http_host,
            port=TRANSPORT_CONFIG.http_port,
        )
    elif TRANSPORT_CONFIG.transport_type == "http":
        logger.info(
            f"Using HTTP Streamable transport on {TRANSPORT_CONFIG.http_host}:{TRANSPORT_CONFIG.http_port}"
        )
        if TRANSPORT_CONFIG.http_bearer_token:
            logger.info("Bearer token authentication enabled for Streamable transport")

        # Run with HTTP Streamable transport
        mcp.run(
            transport="http",
            host=TRANSPORT_CONFIG.http_host,
            port=TRANSPORT_CONFIG.http_port,
        )
    else:
        # Default to STDIO transport
        logger.info("Using STDIO transport")
        mcp.run()


if __name__ == "__main__":
    main()
