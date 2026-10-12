import ipaddress
import logging
import os
from typing import Any

import httpx2

from oxidized_mcp.models import OxidizedConfig
from oxidized_mcp.models import TransportConfig
from oxidized_mcp.utils import env_int
from oxidized_mcp.utils import http_host_from_env
from oxidized_mcp.utils import http_port_from_env
from oxidized_mcp.utils import parse_bool
from oxidized_mcp.utils import quote_path_segment
from oxidized_mcp.utils import transport_from_env

logger = logging.getLogger(__name__)

# Response bodies are attacker/server controlled and can be arbitrarily large
# (e.g. an HTML error page from Sinatra or a misconfigured reverse proxy);
# truncate what we fold into an exception message.
_MAX_ERROR_BODY_LENGTH = 2000

# oxidized-web answers some "not found" cases with HTTP 200 and a short
# message instead of an error status: /node/fetch returns "unable to find 'x'"
# or "node not found", /node/version/view returns "version not found" and
# /node/version returns "node not found" (as ["node not found"] in JSON).
_NOT_FOUND_MARKERS = ("unable to find", "node not found", "version not found")
_NOT_SUPPORTED_MARKER = "not supported"
# Only short, single-line bodies are checked for the markers above, so a real
# configuration that happens to start with the same words is never rejected.
_MAX_MARKER_LENGTH = 200

# oxidized-web reports nodes without a group as group "default". It is a
# display placeholder, not a real group: sending it back (e.g. in
# /node/fetch/default/<node>) addresses a git path that does not exist.
DEFAULT_GROUP = "default"


class OxidizedError(Exception):
    """Base error for problems reported by the Oxidized web API."""


class OxidizedNotFoundError(OxidizedError):
    """The requested node or version does not exist in Oxidized."""


def _annotate_with_response_body(
    exc: httpx2.HTTPStatusError, hint: str | None = None
) -> httpx2.HTTPStatusError:
    """Fold the response body into an HTTPStatusError's message.

    httpx2's own message (e.g. "Server error '500 Internal Server Error' for
    url ...") omits the body, which is where oxidized-web puts the actual
    reason. Re-raising a new instance with the same request/response keeps
    ``exc.response.status_code`` usable by callers that inspect it while making
    the body visible wherever the exception is stringified.
    """
    body = exc.response.text.strip()
    if len(body) > _MAX_ERROR_BODY_LENGTH:
        body = body[:_MAX_ERROR_BODY_LENGTH] + "... (truncated)"
    message = str(exc)
    if hint:
        message = f"{message} | {hint}"
    if body:
        message = f"{message} | response body: {body}"
    return httpx2.HTTPStatusError(message, request=exc.request, response=exc.response)


def _check_status_message(text: str) -> None:
    """Raise if a 200 response body is one of oxidized-web's status messages."""
    message = text.strip()
    if not message or len(message) > _MAX_MARKER_LENGTH or "\n" in message:
        return
    lowered = message.casefold()
    if lowered.startswith(_NOT_FOUND_MARKERS):
        raise OxidizedNotFoundError(f"Oxidized reported: {message}")
    if lowered == _NOT_SUPPORTED_MARKER:
        raise OxidizedError(
            "Oxidized reported 'not supported' - version history requires the "
            "'git' or 'gitcrypt' output in the Oxidized configuration"
        )


def _is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    return True


def normalize_group(group: str | None) -> str | None:
    """Normalize a caller-supplied group: trim it, map "default"/blank to None.

    Only apply this to groups that come from the caller. Groups parsed from a
    node's ``full_name`` are real, and a group literally named "default"
    must be kept there.
    """
    if group is None:
        return None
    group = group.strip()
    if not group or group == DEFAULT_GROUP:
        return None
    return group


def splat_path(value: str) -> str:
    """Encode a value matched by a splat route (e.g. a group name).

    oxidized-web matches groups with a splat, so group names may contain ``/``.
    Each ``/``-separated component is encoded separately to keep the slashes
    as path separators while escaping everything else.
    """
    return "/".join(quote_path_segment(part) for part in value.split("/"))


def node_path(node: str, group: str | None = None) -> str:
    """Build the ``[<group>/]<node>`` part of an oxidized-web node route.

    The group is matched by a splat (see :func:`splat_path`); the node itself
    is a single segment.
    """
    node_segment = quote_path_segment(node)
    if not group:
        return node_segment
    return f"{splat_path(group)}/{node_segment}"


def split_full_name(full_name: str) -> tuple[str | None, str]:
    """Split an Oxidized ``full_name`` (``group/name`` or ``name``) like the server.

    oxidized-web splits ``node_full`` on the *last* slash, so group names may
    themselves contain slashes.
    """
    if "/" in full_name:
        group, name = full_name.rsplit("/", 1)
        return group or None, name
    return None, full_name


class OxidizedClient:
    """Async client for the Oxidized web (oxidized-web) REST API.

    Use :func:`get_oxidized_client` to share one instance (and its connection
    pool) across tools.
    """

    def __init__(self, config: OxidizedConfig):
        """Initialize the OxidizedClient."""
        self.config = config
        self.base_url = config.oxidized_url.rstrip("/")
        self.client: httpx2.AsyncClient | None = None

    def build_auth(self) -> httpx2.BasicAuth | None:
        """HTTP Basic auth for a reverse proxy in front of oxidized-web, if set."""
        if self.config.username and self.config.password:
            return httpx2.BasicAuth(self.config.username, self.config.password)
        return None

    async def __aenter__(self):
        """Enter the async context manager."""
        if self.client is None:
            self.client = httpx2.AsyncClient(
                verify=self.config.verify_ssl,
                timeout=self.config.timeout,
                base_url=self.base_url,
                auth=self.build_auth(),
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the async context manager."""
        # Keep client for reuse
        pass

    async def close(self):
        """Close the HTTP client session."""
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> httpx2.Response:
        """Perform a request to an oxidized-web path and return the raw response.

        Callers assemble ``path`` themselves and must encode any caller-supplied
        segment with :func:`node_path` or ``utils.quote_path_segment``.
        ``timeout`` overrides the client-wide timeout for this request.
        """
        if self.client is None:
            raise RuntimeError(
                "Client not initialized - use 'async with OxidizedClient(config)' or call __aenter__"
            )

        extra: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
        resp = await self.client.request(
            method, path, params=params, data=data, json=json, **extra
        )
        try:
            resp.raise_for_status()
        except httpx2.HTTPStatusError as exc:
            # oxidized-web raises Oxidized::NodeNotFound for unknown nodes,
            # which Sinatra turns into a 500 rather than a 404.
            if resp.status_code == 500 and "NodeNotFound" in resp.text:
                raise OxidizedNotFoundError(
                    f"Node not found in Oxidized (request: {method} {path})"
                ) from exc
            # When oxidized-web runs with RACK_ENV=production the exception
            # name is not in the body, so an unknown node is a bare 500 that
            # cannot be told apart from a real server error.
            hint = None
            if resp.status_code == 500 and path.startswith("/node/"):
                hint = (
                    "oxidized-web also answers unknown node names with HTTP 500 - "
                    "if this persists, check the name with find_nodes"
                )
            raise _annotate_with_response_body(exc, hint) from exc
        return resp

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a JSON route (``.json`` suffix)."""
        resp = await self.request("GET", path, params=params)
        return self._decode_json(resp)

    async def get_text(self, path: str, params: dict[str, Any] | None = None) -> str:
        """GET a route as plain text (``format=text``)."""
        query = {**(params or {}), "format": "text"}
        resp = await self.request("GET", path, params=query)
        # With the 'file' output a missing configuration is nil, which
        # oxidized-web serves as an empty 200 response.
        if not resp.text.strip():
            raise OxidizedNotFoundError(
                "Oxidized returned an empty response - no configuration is stored "
                "for this node (has it been backed up yet?)"
            )
        _check_status_message(resp.text)
        return resp.text

    async def post_form(
        self, path: str, data: dict[str, Any], timeout: float | None = None
    ) -> Any:
        """POST form fields to a JSON route."""
        resp = await self.request("POST", path, data=data, timeout=timeout)
        return self._decode_json(resp)

    async def put_json(self, path: str, body: dict[str, Any]) -> Any:
        """PUT a JSON body to a JSON route."""
        resp = await self.request("PUT", path, json=body)
        return self._decode_json(resp)

    @staticmethod
    def _decode_json(resp: httpx2.Response) -> Any:
        """Decode a JSON response, surfacing oxidized-web status messages.

        oxidized-web serialises string results as a JSON array of lines, so a
        status message such as "node not found" arrives as ``["node not found"]``.
        """
        if not resp.content:
            return None
        result = resp.json()
        if isinstance(result, list) and len(result) == 1 and isinstance(result[0], str):
            _check_status_message(result[0])
        return result

    async def resolve_node(
        self, node: str, group: str | None = None
    ) -> tuple[str, str | None, str]:
        """Resolve a node to ``(name, group, full_name)``.

        Version routes need the group (``node_full`` or a separate ``group``
        parameter). When the caller does not supply it, look the node up via
        ``/node/show``, which also accepts an IP address. The group is taken
        from ``full_name`` because ``group`` is reported as "default" for nodes
        that have none (and a caller passing "default" back is treated the same).

        A given group skips the lookup - unless ``node`` is an IP address:
        fetch and version routes find nodes by name only, so an IP is always
        resolved to the node's name first.
        """
        group = normalize_group(group)
        if group and not _is_ip_address(node):
            return node, group, f"{group}/{node}"

        info = await self.get_json(f"/node/show/{quote_path_segment(node)}.json")
        if not isinstance(info, dict):
            raise OxidizedError(f"Unexpected response for node {node!r}: {info!r}")
        full_name = str(info.get("full_name") or info.get("name") or node)
        resolved_group, name = split_full_name(full_name)
        return str(info.get("name") or name), resolved_group, full_name


def get_oxidized_config_from_env() -> OxidizedConfig:
    """Get Oxidized configuration from environment variables."""
    # Parse disabled tags from comma-separated string
    disabled_tags_str = os.getenv("OXIDIZED_DISABLED_TAGS", "")
    disabled_tags = set()
    if disabled_tags_str.strip():
        # Split by comma and strip whitespace from each tag
        disabled_tags = {
            tag.strip() for tag in disabled_tags_str.split(",") if tag.strip()
        }

    return OxidizedConfig(
        oxidized_url=os.getenv("OXIDIZED_URL", ""),
        username=os.getenv("OXIDIZED_USERNAME", "").strip() or None,
        password=os.getenv("OXIDIZED_PASSWORD") or None,
        verify_ssl=parse_bool(os.getenv("OXIDIZED_VERIFY_SSL"), default=True),
        timeout=env_int("OXIDIZED_TIMEOUT", 30),
        search_timeout=env_int("OXIDIZED_SEARCH_TIMEOUT", 300),
        redact_node_vars=parse_bool(
            os.getenv("OXIDIZED_REDACT_NODE_VARS"), default=True
        ),
        read_only_mode=parse_bool(os.getenv("READ_ONLY_MODE"), default=False),
        disabled_tags=disabled_tags,
        rate_limit_enabled=parse_bool(os.getenv("RATE_LIMIT_ENABLED"), default=False),
        rate_limit_max_requests=env_int("RATE_LIMIT_MAX_REQUESTS", 60),
        rate_limit_window_minutes=env_int("RATE_LIMIT_WINDOW_MINUTES", 1),
        tool_search_enabled=parse_bool(os.getenv("TOOL_SEARCH_ENABLED"), default=False),
        tool_search_strategy=(
            "regex"
            if os.getenv("TOOL_SEARCH_STRATEGY", "bm25").lower() == "regex"
            else "bm25"
        ),
        tool_search_max_results=env_int("TOOL_SEARCH_MAX_RESULTS", 5),
    )


def get_transport_config_from_env() -> TransportConfig:
    """Get transport configuration from environment variables."""
    http_bearer_token = os.getenv("MCP_HTTP_BEARER_TOKEN")
    if http_bearer_token is not None:
        http_bearer_token = http_bearer_token.strip() or None

    return TransportConfig(
        transport_type=transport_from_env(),
        http_host=http_host_from_env(),
        http_port=http_port_from_env(),
        http_bearer_token=http_bearer_token,
    )


_oxidized_client_singleton: OxidizedClient | None = None


def get_oxidized_client(config: OxidizedConfig | None = None) -> OxidizedClient:
    """Get the singleton Oxidized client instance."""
    global _oxidized_client_singleton
    if _oxidized_client_singleton is None:
        if config is None:
            raise ValueError(
                "Oxidized config must be provided for first initialization"
            )
        _oxidized_client_singleton = OxidizedClient(config)
    return _oxidized_client_singleton
