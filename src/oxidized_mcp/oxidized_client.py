import ipaddress
import logging
import os
from typing import Any

import httpx2

from oxidized_mcp.models import OxidizedConfig
from oxidized_mcp.models import TransportConfig
from oxidized_mcp.utils import HTTP_TRANSPORTS
from oxidized_mcp.utils import env_bool
from oxidized_mcp.utils import env_int
from oxidized_mcp.utils import env_str
from oxidized_mcp.utils import http_host_from_env
from oxidized_mcp.utils import http_port_from_env
from oxidized_mcp.utils import quote_path_segment
from oxidized_mcp.utils import redact_url_credentials
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
    # httpx2 puts the full request URL in the message, including any
    # user:password@ from OXIDIZED_URL; the message reaches MCP clients/Sentry.
    url = str(exc.request.url)
    message = str(exc).replace(url, redact_url_credentials(url))
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
        # Credentials embedded in OXIDIZED_URL (user:password@) are moved into
        # Basic auth: left in the URL, httpx2 would print them in every
        # "HTTP Request" log line and error message (logs can reach Sentry).
        self._url_auth: tuple[str, str] | None = None
        try:
            url = httpx2.URL(self.base_url)
        except httpx2.InvalidURL:
            url = None
        # A password-only userinfo (":secret@") must be moved out too.
        if url is not None and (url.username or url.password):
            self._url_auth = (url.username, url.password)
            self.base_url = str(url.copy_with(userinfo=b""))
        self.client: httpx2.AsyncClient | None = None

    def build_auth(self) -> httpx2.BasicAuth | None:
        """HTTP Basic auth for a reverse proxy in front of oxidized-web, if set.

        Explicit username/password settings win over credentials in the URL.
        """
        if self.config.username and self.config.password:
            return httpx2.BasicAuth(self.config.username, self.config.password)
        if self._url_auth is not None:
            return httpx2.BasicAuth(*self._url_auth)
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


def _env_setting(name: str, field: str) -> int:
    """Read an integer setting, defaulting to OxidizedConfig's own default."""
    return env_int(name, OxidizedConfig.model_fields[field].default)


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

    # Unknown values used to fall back to bm25 silently, hiding a typo such as
    # "regx"; refuse them - but only when tool search is on, so a leftover
    # value cannot stop a server that never uses it.
    tool_search_enabled = env_bool("TOOL_SEARCH_ENABLED", default=False)
    tool_search_strategy = (env_str("TOOL_SEARCH_STRATEGY") or "bm25").lower()
    if tool_search_enabled and tool_search_strategy not in ("bm25", "regex"):
        raise ValueError(
            "TOOL_SEARCH_STRATEGY must be 'bm25' or 'regex', "
            f"got {os.getenv('TOOL_SEARCH_STRATEGY')!r}"
        )
    rate_limit_enabled = env_bool("RATE_LIMIT_ENABLED", default=False)

    oxidized_url = env_str("OXIDIZED_URL") or ""
    # httpx2 appends request paths after a base URL's query
    # ("/?token=x/nodes.json"), so every call would reach the wrong route. The
    # value is not echoed: the query is likely a token. Any "?" counts, even
    # with an empty query ("http://host/?"): httpx2 keeps it and the request
    # path still lands after it.
    if "?" in oxidized_url.partition("#")[0]:
        raise ValueError(
            "OXIDIZED_URL must not contain a query string (?...); use "
            "OXIDIZED_USERNAME/OXIDIZED_PASSWORD for authentication"
        )
    # Otherwise every tool call fails, and httpx2's own message quotes part of
    # the URL ("Invalid port: 'pa'" for an unencoded "/" in "admin:pa/ss@"),
    # which may be the password. Neither is echoed; "from None" keeps it out
    # of the traceback too.
    if oxidized_url:
        try:
            parsed = httpx2.URL(oxidized_url)
        except httpx2.InvalidURL:
            parsed = None
        if parsed is None or parsed.scheme not in ("http", "https") or not parsed.host:
            raise ValueError(
                "OXIDIZED_URL must be an http:// or https:// URL with a host; "
                "percent-encode special characters in a user name or password "
                "(e.g. '/' as %2F) or use OXIDIZED_USERNAME/OXIDIZED_PASSWORD"
            ) from None

    # Numeric settings of a disabled feature are not read at all: a leftover
    # value must not stop a server that never uses it, and OxidizedConfig's
    # own defaults then apply.
    numbers: dict[str, int] = {}
    if rate_limit_enabled:
        numbers["rate_limit_max_requests"] = _env_setting(
            "RATE_LIMIT_MAX_REQUESTS", "rate_limit_max_requests"
        )
        numbers["rate_limit_window_minutes"] = _env_setting(
            "RATE_LIMIT_WINDOW_MINUTES", "rate_limit_window_minutes"
        )
    if tool_search_enabled:
        numbers["tool_search_max_results"] = _env_setting(
            "TOOL_SEARCH_MAX_RESULTS", "tool_search_max_results"
        )

    return OxidizedConfig(
        # Whitespace-only counts as missing, so main() reports it instead of
        # every call failing with a confusing connection error.
        oxidized_url=oxidized_url,
        username=env_str("OXIDIZED_USERNAME"),
        password=os.getenv("OXIDIZED_PASSWORD") or None,
        verify_ssl=env_bool("OXIDIZED_VERIFY_SSL", default=True),
        timeout=_env_setting("OXIDIZED_TIMEOUT", "timeout"),
        search_timeout=_env_setting("OXIDIZED_SEARCH_TIMEOUT", "search_timeout"),
        redact_node_vars=env_bool("OXIDIZED_REDACT_NODE_VARS", default=True),
        read_only_mode=env_bool("READ_ONLY_MODE", default=False),
        disabled_tags=disabled_tags,
        rate_limit_enabled=rate_limit_enabled,
        tool_search_enabled=tool_search_enabled,
        tool_search_strategy="regex" if tool_search_strategy == "regex" else "bm25",
        **numbers,
    )


def get_transport_config_from_env() -> TransportConfig:
    """Get transport configuration from environment variables."""
    transport_type = transport_from_env()
    # The port is only parsed when an HTTP transport will bind it: a leftover
    # non-numeric MCP_HTTP_PORT must not stop stdio or `fastmcp run`.
    http_port = http_port_from_env() if transport_type in HTTP_TRANSPORTS else 8000

    return TransportConfig(
        transport_type=transport_type,
        http_host=http_host_from_env(),
        http_port=http_port,
        http_bearer_token=env_str("MCP_HTTP_BEARER_TOKEN"),
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
