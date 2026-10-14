import logging
from collections import Counter
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Annotated
from typing import Any

from fastmcp import FastMCP
from pydantic import Field

from oxidized_mcp.models import OxidizedConfig
from oxidized_mcp.oxidized_client import get_oxidized_client
from oxidized_mcp.oxidized_client import splat_path
from oxidized_mcp.oxidized_models import canonical_model
from oxidized_mcp.utils import quote_path_segment
from oxidized_mcp.utils import redact_node_vars

logger = logging.getLogger(__name__)

# Statuses reported by Oxidized for a node's last run. Anything that is neither
# a success nor "never" (no_connection, fail, ...) counts as a failure.
_STATUS_SUCCESS = "success"
_STATUS_NEVER = "never"

_SUMMARY_FIELDS = ("name", "full_name", "ip", "group", "model", "status", "time")


def _summary(node: dict[str, Any]) -> dict[str, Any]:
    """Compact view of a node for lists of search/stats results."""
    return {field: node.get(field) for field in _SUMMARY_FIELDS}


def _node_status(node: dict[str, Any]) -> str:
    """A node's last-run status; a node without one has never run."""
    return str(node.get("status") or _STATUS_NEVER)


def _equals(value: Any, expected: str | None) -> bool:
    """Case-insensitive equality; a missing filter matches everything."""
    if expected is None:
        return True
    return str(value or "").casefold() == expected.casefold()


def parse_oxidized_time(value: Any) -> datetime | None:
    """Parse a time as rendered by Oxidized (Ruby ``Time#to_s``).

    Oxidized reports times like ``2024-11-18 07:32:14 +0100`` or
    ``2024-06-30 22:15:03 UTC``, and uses "never"/"unknown" placeholders.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text in {_STATUS_NEVER, "unknown"}:
        return None
    if text.endswith(" UTC"):
        text = f"{text[:-4]} +0000"
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def _fetch_nodes(
    config: OxidizedConfig, server_filter: tuple[str, str] | None = None
) -> list[dict[str, Any]]:
    """Fetch nodes, redacting vars when configured.

    ``server_filter`` is a ``(field, value)`` pair sent as
    ``/nodes/<field>/<value>.json`` so oxidized-web returns only matching
    nodes instead of the whole inventory. Without it, ``/nodes.json`` is used.
    """
    path = "/nodes.json"
    if server_filter is not None:
        field, value = server_filter
        path = f"/nodes/{quote_path_segment(field)}/{splat_path(value)}.json"
    async with get_oxidized_client(config) as client:
        nodes = await client.get_json(path)
    if not isinstance(nodes, list):
        return []
    # Report a node without a status as "never", the status the filters and
    # backup stats already treat it as.
    return [
        {
            **(redact_node_vars(node) if config.redact_node_vars else node),
            "status": _node_status(node),
        }
        for node in nodes
        if isinstance(node, dict)
    ]


async def _fetch_nodes_by(
    config: OxidizedConfig, group: str | None = None, model: str | None = None
) -> list[dict[str, Any]]:
    """Fetch nodes matching an optional group and model.

    The group (or, failing that, the model) is filtered by oxidized-web, so
    large inventories are never transferred in full. oxidized-web matches
    exactly and case-sensitively:

    - Groups are user-defined and may differ only by case ("core" vs "Core"),
      so the group is matched exactly. "default" selects nodes without a
      group, which oxidized-web supports natively.
    - Models are Ruby class names ("IOS", "JunOS"), easy to guess wrong
      ("ios"). Built-in names are mapped to their exact spelling first;
      unknown (custom) models are sent as given.
    """
    # A blank model is no filter, with or without a group.
    model = canonical_model(model) if model and model.strip() else None
    if group and group.strip():
        nodes = await _fetch_nodes(config, ("group", group.strip()))
        # Same exact semantics as the server-side model filter below.
        return [node for node in nodes if model is None or node.get("model") == model]
    if model:
        return await _fetch_nodes(config, ("model", model))
    return await _fetch_nodes(config)


def register_nodes_tools(mcp: FastMCP, config: OxidizedConfig) -> None:
    """Register node-related Oxidized tools with the FastMCP server."""

    @mcp.tool(
        tags={"node", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def list_nodes(
        group: Annotated[
            str | None,
            Field(
                default=None,
                description="Only nodes in this group - exact, case-sensitive name as shown by list_nodes (e.g. 'core'); 'default' selects nodes without a group",
            ),
        ] = None,
        model: Annotated[
            str | None,
            Field(
                default=None,
                description="Only nodes of this Oxidized model (e.g. 'IOS', 'JunOS', 'RouterOS'); built-in model names are case-insensitive",
            ),
        ] = None,
        status: Annotated[
            str | None,
            Field(
                default=None,
                description="Only nodes whose last run had this status (e.g. 'success', 'no_connection', 'fail', 'never')",
            ),
        ] = None,
        name_contains: Annotated[
            str | None,
            Field(
                default=None,
                description="Only nodes whose name, full name or IP contains this text (case-insensitive)",
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(default=100, ge=1, le=1000, description="Maximum nodes to return"),
        ] = 100,
        offset: Annotated[
            int,
            Field(default=0, ge=0, description="Number of matching nodes to skip"),
        ] = 0,
    ) -> dict[str, Any]:
        """
        List nodes (network devices) managed by Oxidized.

        Returns each node's name, full name (group/name), IP, group, model,
        last run status and time, and last run details. Node vars are redacted
        unless redaction is disabled in the server configuration. Pass a group
        or model on large installations: they are filtered by Oxidized itself,
        so only matching nodes are transferred. The group must match exactly;
        use find_nodes when unsure of the name.

        Args:
            group: Only nodes in this group
            model: Only nodes of this Oxidized model
            status: Only nodes whose last run had this status
            name_contains: Only nodes whose name, full name or IP contains this text
            limit: Maximum nodes to return
            offset: Number of matching nodes to skip

        Returns:
            Dictionary with the total match count, paging info and the nodes
        """
        nodes = await _fetch_nodes_by(config, group, model)
        needle = name_contains.casefold() if name_contains else None
        # A blank status is no filter, like a blank group or model.
        status = (status or "").strip() or None
        matched = [
            node
            for node in nodes
            if _equals(node["status"], status)
            and (
                needle is None
                or any(
                    needle in str(node.get(field) or "").casefold()
                    for field in ("name", "full_name", "ip")
                )
            )
        ]
        page = matched[offset : offset + limit]
        return {
            "total": len(matched),
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(page) < len(matched),
            "nodes": page,
        }

    @mcp.tool(
        tags={"node", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_node(
        node: Annotated[
            str,
            Field(description="Node name or IP address (e.g. 'core-sw01')"),
        ],
    ) -> dict[str, Any]:
        """
        Get details of a single Oxidized node.

        Returns the node's name, full name, IP, group, model, vars and details
        of its last backup run (start, end, status, duration).

        Args:
            node: Node name or IP address

        Returns:
            Dictionary containing the node details
        """
        async with get_oxidized_client(config) as client:
            result = await client.get_json(
                f"/node/show/{quote_path_segment(node)}.json"
            )
        if isinstance(result, dict) and config.redact_node_vars:
            return redact_node_vars(result)
        return result

    @mcp.tool(
        tags={"node", "search", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def find_nodes(
        query: Annotated[
            str,
            Field(
                min_length=1,
                description="Part of a node name, full name (group/name) or IP address",
            ),
        ],
        limit: Annotated[
            int,
            Field(default=20, ge=1, le=200, description="Maximum nodes to return"),
        ] = 20,
    ) -> dict[str, Any]:
        """
        Find nodes by partial name, full name or IP address.

        Useful to resolve an ambiguous device name before calling other tools.
        Exact matches are listed first, then prefix matches, then other
        substring matches (all case-insensitive).

        Args:
            query: Part of a node name, full name or IP address
            limit: Maximum nodes to return

        Returns:
            Dictionary with the total match count and compact node summaries
        """
        needle = query.strip().casefold()
        ranked: list[tuple[int, str, dict[str, Any]]] = []
        for node in await _fetch_nodes(config):
            values = [
                str(node.get(field) or "").casefold()
                for field in ("name", "full_name", "ip")
            ]
            if any(value == needle for value in values):
                rank = 0
            elif any(value.startswith(needle) for value in values):
                rank = 1
            elif any(needle in value for value in values):
                rank = 2
            else:
                continue
            ranked.append((rank, str(node.get("full_name") or ""), node))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return {
            "query": query,
            "total_matches": len(ranked),
            "nodes": [_summary(node) for _, _, node in ranked[:limit]],
        }

    @mcp.tool(
        tags={"node", "stats", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_backup_stats(
        stale_hours: Annotated[
            int,
            Field(
                default=24,
                ge=1,
                description="Report nodes whose last backup attempt (successful or not) is older than this many hours",
            ),
        ] = 24,
        max_listed: Annotated[
            int,
            Field(
                default=50,
                ge=0,
                le=1000,
                description="Maximum nodes listed in each of the failed/never/stale lists",
            ),
        ] = 50,
        group: Annotated[
            str | None,
            Field(
                default=None,
                description="Only include nodes in this group (exact, case-sensitive name)",
            ),
        ] = None,
        model: Annotated[
            str | None,
            Field(
                default=None,
                description="Only include nodes of this Oxidized model (e.g. 'IOS')",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """
        Summarise backup health across Oxidized nodes.

        Computed from the node list (oxidized-web's own stats JSON is not
        usable): counts per last-run status, per group and per model, the
        success rate, and lists of failed nodes, nodes never backed up and
        stale nodes. Optionally limited to one group or model, filtered by
        Oxidized itself.

        "Stale" means Oxidized has not *attempted* a backup within the
        threshold (e.g. it is not polling the node); nodes that are attempted
        but keep failing are listed under failed_nodes instead. Oxidized does
        not expose the time of the last *successful* backup, and its run
        history is kept in memory only (reset on restart). Right after
        trigger_node_backup a node briefly shows as never backed up, because
        Oxidized clears its last run while it is queued.

        Args:
            stale_hours: Report nodes whose last attempt is older than this many hours
            max_listed: Maximum nodes listed in each list
            group: Only include nodes in this group
            model: Only include nodes of this Oxidized model

        Returns:
            Dictionary containing the backup statistics
        """
        nodes = await _fetch_nodes_by(config, group, model)
        threshold = datetime.now(UTC) - timedelta(hours=stale_hours)

        status_counts: Counter[str] = Counter()
        model_counts: Counter[str] = Counter()
        groups: dict[str, Counter[str]] = {}
        failed: list[dict[str, Any]] = []
        never: list[dict[str, Any]] = []
        stale: list[dict[str, Any]] = []

        for node in nodes:
            status = node["status"]
            node_group = str(node.get("group") or "default")
            status_counts[status] += 1
            model_counts[str(node.get("model") or "unknown")] += 1
            groups.setdefault(node_group, Counter())[status] += 1

            if status == _STATUS_NEVER:
                never.append(_summary(node))
                continue
            if status != _STATUS_SUCCESS:
                failed.append(_summary(node))
            last_run = parse_oxidized_time(node.get("time"))
            if last_run is not None and last_run < threshold:
                stale.append(_summary(node))

        attempted = len(nodes) - status_counts[_STATUS_NEVER]
        success_rate = (
            round(100 * status_counts[_STATUS_SUCCESS] / attempted, 1)
            if attempted
            else None
        )
        return {
            "total_nodes": len(nodes),
            "status_counts": dict(status_counts),
            "success_rate_percent": success_rate,
            "groups": {
                name: {"total": sum(counts.values()), "status_counts": dict(counts)}
                for name, counts in sorted(groups.items())
            },
            "models": dict(model_counts.most_common()),
            "stale_threshold_hours": stale_hours,
            "failed_count": len(failed),
            "never_backed_up_count": len(never),
            "stale_count": len(stale),
            "failed_nodes": failed[:max_listed],
            "never_backed_up": never[:max_listed],
            "stale_nodes": stale[:max_listed],
        }
