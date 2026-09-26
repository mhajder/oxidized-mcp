import logging
from typing import Annotated
from typing import Any

from fastmcp import FastMCP
from pydantic import Field

from oxidized_mcp.models import OxidizedConfig
from oxidized_mcp.oxidized_client import get_oxidized_client
from oxidized_mcp.utils import quote_path_segment

logger = logging.getLogger(__name__)

# Recorded as the commit origin when a backup is queued with metadata.
_REQUEST_ORIGIN = "oxidized-mcp"


def register_operations_tools(mcp: FastMCP, config: OxidizedConfig) -> None:
    """Register Oxidized operations (backup trigger, source reload) with the FastMCP server."""

    @mcp.tool(
        tags={"node", "backup"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def trigger_node_backup(
        node: Annotated[
            str,
            Field(description="Node name or IP address (e.g. 'core-sw01')"),
        ],
        message: Annotated[
            str | None,
            Field(
                default=None,
                description="Commit message recorded if this backup produces a new version",
            ),
        ] = None,
        user: Annotated[
            str | None,
            Field(
                default=None,
                description="Author name recorded if this backup produces a new version",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """
        Queue an immediate configuration backup of a node.

        Oxidized moves the node to the head of its queue; the backup itself
        runs asynchronously. Check get_node afterwards for the run result, and
        list_node_versions for a new version if the configuration changed.
        Oxidized picks the node by name or IP only (no group), so node names
        must be unique - otherwise the first node with that name is queued.

        Args:
            node: Node name or IP address
            message: Commit message recorded if a new version is created
            user: Author name recorded if a new version is created

        Returns:
            Dictionary confirming the node was queued
        """
        # oxidized-web accepts /node/next/<group>/<node> but ignores the group.
        path = f"/node/next/{quote_path_segment(node)}.json"
        async with get_oxidized_client(config) as client:
            if message or user:
                body: dict[str, Any] = {"from": _REQUEST_ORIGIN}
                if user:
                    body["user"] = user
                if message:
                    body["msg"] = message
                result = await client.put_json(path, body)
            else:
                result = await client.get_json(path)
        return {
            "node": node,
            "queued": True,
            "response": result,
        }

    @mcp.tool(
        tags={"node", "reload"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def reload_nodes() -> dict[str, Any]:
        """
        Reload the Oxidized node list from its source (router.db, SQL, HTTP, ...).

        Use after devices were added, removed or changed in the source so
        Oxidized picks up the change without waiting for its next refresh.
        Always reloads every node: oxidized-web's per-node reload
        (/reload?node=X) replaces the whole in-memory node list with just the
        matching nodes, so it is deliberately not offered.

        Returns:
            Dictionary with Oxidized's response message
        """
        async with get_oxidized_client(config) as client:
            result = await client.get_json("/reload.json")
        if isinstance(result, list):
            result = "".join(str(line) for line in result).strip()
        return {"reloaded": True, "message": result}
