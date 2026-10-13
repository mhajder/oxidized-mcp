import os
from typing import Any
from urllib.parse import quote

TRUTHY_VALUES = ("true", "1", "yes", "on")
FALSY_VALUES = ("false", "0", "no", "off")
# "true/false, 1/0, yes/no, on/off", for error messages.
BOOL_HELP = ", ".join(
    f"{t}/{f}" for t, f in zip(TRUTHY_VALUES, FALSY_VALUES, strict=True)
)
REDACTED = "<redacted>"

# MCP_TRANSPORT values that serve HTTP. Anything else, including an unknown
# value, runs the STDIO transport.
HTTP_TRANSPORTS = frozenset({"sse", "http"})
# FastMCP calls the streamable HTTP transport "streamable-http"; accept that
# spelling for the "http" transport this server documents.
TRANSPORT_ALIASES = {"streamable-http": "http"}

# Liveness endpoint served on the HTTP transports and probed by the container
# healthcheck.
HEALTH_PATH = "/health"


def parse_bool(val, default=True):
    """
    Convert a value to boolean.

    A blank or whitespace-only value counts as unset and yields the default,
    so an env var declared without a value (``OXIDIZED_VERIFY_SSL=``) cannot
    silently flip a setting off. Any other value must be a recognized spelling:
    a typo such as ``READ_ONLY_MODE=enable`` raises instead of reading as False,
    which would expose the write tools or disable TLS verification.

    Args:
        val: The value to convert.
        default (bool, optional): The default value to return if val is None or blank. Defaults to True.

    Returns:
        bool: True for "true", "1", "yes", "on"; False for "false", "0", "no",
        "off" (case-insensitive).

    Raises:
        ValueError: If val is set to anything else.
    """
    if val is None:
        return default
    normalized = str(val).strip().casefold()
    if not normalized:
        return default
    if normalized in TRUTHY_VALUES:
        return True
    if normalized in FALSY_VALUES:
        return False
    raise ValueError(f"Expected a boolean ({BOOL_HELP}), got {val!r}")


def env_bool(name: str, default: bool) -> bool:
    """Read a boolean env var; blank means unset, garbage names the variable."""
    try:
        return parse_bool(os.getenv(name), default)
    except ValueError:
        raise ValueError(
            f"{name} must be one of {BOOL_HELP}, got {os.getenv(name)!r}"
        ) from None


def parse_int(val, default: int) -> int:
    """
    Convert a value to an integer.

    Like :func:`parse_bool`, a blank or whitespace-only value counts as unset
    and yields the default, so ``OXIDIZED_TIMEOUT=`` does not crash startup.

    Args:
        val: The value to convert.
        default (int): The value to return if val is None or blank.

    Returns:
        int: The parsed integer, or the default.

    Raises:
        ValueError: If the value is set but is not an integer.
    """
    if val is None:
        return default
    text = str(val).strip()
    if not text:
        return default
    return int(text)


def env_int(name: str, default: int) -> int:
    """Read an integer env var; blank means unset, garbage names the variable."""
    try:
        return parse_int(os.getenv(name), default)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {os.getenv(name)!r}") from exc


def env_float(name: str, default: float) -> float:
    """Read a float env var; blank means unset, garbage names the variable."""
    try:
        return parse_float(os.getenv(name), default)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {os.getenv(name)!r}") from None


def env_str(name: str) -> str | None:
    """Read a string env var, stripped; blank or whitespace-only yields None."""
    return (os.getenv(name) or "").strip() or None


def transport_from_env() -> str:
    """
    Return MCP_TRANSPORT stripped and lowercased; unset or blank means 'stdio'.

    The server and the container healthcheck both read the transport here, so
    they always agree on whether an HTTP transport is running.

    'streamable-http' is read as 'http', FastMCP's name for that transport.
    """
    transport = (env_str("MCP_TRANSPORT") or "stdio").lower()
    return TRANSPORT_ALIASES.get(transport, transport)


def http_host_from_env() -> str:
    """
    Return MCP_HTTP_HOST stripped and without IPv6 brackets, defaulting to
    loopback when unset or blank.

    The server and the container healthcheck both read the bind address here,
    so they always agree on it. A blank value used to reach uvicorn as is and
    bind every interface; listening beyond loopback now takes an explicit
    '0.0.0.0' or '::'.
    """
    host = env_str("MCP_HTTP_HOST") or "127.0.0.1"
    # Accept the URL form of an IPv6 literal ('[::]'); uvicorn needs it bare.
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def http_port_from_env() -> int:
    """Return MCP_HTTP_PORT, defaulting to 8000 when unset or blank."""
    return env_int("MCP_HTTP_PORT", 8000)


def parse_float(val, default: float) -> float:
    """
    Convert a value to a float; a blank value counts as unset, like parse_int.

    Args:
        val: The value to convert.
        default (float): The value to return if val is None or blank.

    Returns:
        float: The parsed number, or the default.

    Raises:
        ValueError: If the value is set but is not a number.
    """
    if val is None:
        return default
    text = str(val).strip()
    if not text:
        return default
    return float(text)


def quote_path_segment(value: str) -> str:
    """
    Percent-encode a value for use as a single URL path segment.

    Oxidized path parameters are caller-supplied (node names and groups come
    from the router.db source), so they can contain characters that carry
    meaning in a URL. Interpolating them raw silently sends the request
    somewhere else: ``#`` starts a fragment and truncates the path, ``?``
    starts the query string, and ``/`` adds a path segment. For an action such
    as queueing a backup that means acting on a *different* node - and getting
    a success response for it.

    Nothing is treated as safe, so ``/`` becomes ``%2F`` and stays inside the
    segment. Values made up of unreserved characters (the common case, e.g.
    ``core-sw01``, ``sw1.example.com`` or ``ios``) come back unchanged, and httpx2 does not
    re-encode the escapes this produces.

    Two cases percent-encoding alone does not cover, because ``.`` is an
    unreserved character:

    - A dot-only segment (``.``, ``..``) is removed by RFC 3986 dot-segment
      normalisation, which walks the request up to a different endpoint. The
      dots are escaped so the value stays a value.
    - An empty segment collapses into the trailing slash, turning an
      item request into a different route (``/node/fetch/`` plus an empty
      node name addresses nothing sensible). There is no correct request to
      make for an empty identifier, so this raises.

    Args:
        value: The path segment value to encode.

    Returns:
        str: The percent-encoded segment.

    Raises:
        ValueError: If the value is empty or whitespace-only.
    """
    segment = str(value)
    if not segment.strip():
        raise ValueError(
            "URL path segment must not be empty - a node name or group is required"
        )
    encoded = quote(segment, safe="")
    if set(encoded) == {"."}:
        encoded = encoded.replace(".", "%2E")
    return encoded


def redact_node_vars(node: dict[str, Any]) -> dict[str, Any]:
    """
    Return a copy of an Oxidized node with its ``vars`` values redacted.

    Node vars come from the router.db source and commonly carry per-device
    credentials (``username``, ``password``, ``enable``). oxidized-web's
    ``hide_node_vars`` only applies to ``/node/show``; ``/nodes.json`` returns
    them in full. Keys are kept so the caller can see *which* vars are set,
    and unset (``None``) values stay ``None``.

    Args:
        node: A node object as returned by oxidized-web.

    Returns:
        dict: A shallow copy with every non-null ``vars`` value replaced.
    """
    node_vars = node.get("vars")
    if not isinstance(node_vars, dict):
        return node
    return {
        **node,
        "vars": {
            key: None if value is None else REDACTED for key, value in node_vars.items()
        },
    }


def page_lines(text: str, offset: int = 0, max_lines: int = 0) -> dict[str, Any]:
    """
    Slice a text document by lines so large configurations can be paged.

    Args:
        text: The full document.
        offset: Zero-based index of the first line to return.
        max_lines: Maximum number of lines to return; 0 or less means all.

    Returns:
        dict: Size information plus the selected ``content``. ``next_offset``
        is the offset to request next, or None when the end was reached.
    """
    lines = text.splitlines()
    total = len(lines)
    start = max(offset, 0)
    selected = lines[start:] if max_lines <= 0 else lines[start : start + max_lines]
    end = start + len(selected)
    has_more = end < total
    return {
        "total_lines": total,
        "total_bytes": len(text.encode()),
        "offset": start,
        "returned_lines": len(selected),
        "truncated": has_more,
        "next_offset": end if has_more else None,
        "content": "\n".join(selected),
    }
