import asyncio
import difflib
import logging
import re
from collections import OrderedDict
from typing import Annotated
from typing import Any

from fastmcp import FastMCP
from pydantic import Field

from oxidized_mcp.models import OxidizedConfig
from oxidized_mcp.oxidized_client import OxidizedClient
from oxidized_mcp.oxidized_client import OxidizedError
from oxidized_mcp.oxidized_client import OxidizedNotFoundError
from oxidized_mcp.oxidized_client import get_oxidized_client
from oxidized_mcp.utils import page_lines

logger = logging.getLogger(__name__)

_VERSION_FIELDS = ("oid", "date", "author", "message")
_FULL_OID = re.compile(r"[0-9a-fA-F]{40}")
# Diffs kept for paging with diff_node_versions' offset. Each holds a whole
# diff, so the cache is kept small.
_DIFF_CACHE_SIZE = 8


def _is_full_oid(oid: str) -> bool:
    return _FULL_OID.fullmatch(oid) is not None


def _version_summary(version: dict[str, Any]) -> dict[str, Any]:
    return {field: version.get(field) for field in _VERSION_FIELDS}


def _resolve_oid(versions: list[dict[str, Any]], oid: str) -> int:
    """Return the index of the version matching a full oid or unique prefix."""
    needle = oid.strip().lower()
    if not needle:
        raise ValueError("Version oid must not be blank")
    matches = [
        index
        for index, version in enumerate(versions)
        if str(version.get("oid", "")).lower().startswith(needle)
    ]
    if not matches:
        raise OxidizedNotFoundError(f"Version {oid!r} not found for this node")
    if len(matches) > 1:
        raise ValueError(
            f"Version prefix {oid!r} is ambiguous ({len(matches)} matches) - use a longer oid"
        )
    return matches[0]


def _pick_version(
    versions: list[dict[str, Any]], oid: str
) -> tuple[int | None, dict[str, Any]]:
    """Find a version by full oid or unique prefix.

    Returns its index in the node's history and its metadata. A full oid of a
    commit that did not change this node's file (single_repo) is not in the
    history but can still be viewed, so it is returned with index None.
    """
    oid = oid.strip()
    try:
        index = _resolve_oid(versions, oid)
    except OxidizedNotFoundError:
        if not _is_full_oid(oid):
            raise
        return None, {"oid": oid}
    return index, versions[index]


async def _list_versions(
    client: OxidizedClient, full_name: str
) -> list[dict[str, Any]]:
    """Fetch a node's version list (newest first)."""
    versions = await client.get_json(
        "/node/version.json", params={"node_full": full_name}
    )
    if not isinstance(versions, list):
        return []
    return [version for version in versions if isinstance(version, dict)]


async def _view_version(
    client: OxidizedClient, name: str, group: str | None, oid: str
) -> str:
    """Fetch the configuration stored at a given version."""
    params = {"node": name, "oid": oid}
    # A blank group is truthy in Ruby and would change the git path, so only
    # send the parameter for nodes that actually have a group.
    if group:
        params["group"] = group
    return await client.get_text("/node/version/view", params=params)


def register_versions_tools(mcp: FastMCP, config: OxidizedConfig) -> None:
    """Register configuration version (git history) tools with the FastMCP server."""
    # Keyed by full oids, whose content never changes, so entries never go
    # stale. Paging a long diff then fetches the two versions only once.
    diff_cache: OrderedDict[tuple[str, str, str, int], list[str]] = OrderedDict()

    @mcp.tool(
        tags={"version", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def list_node_versions(
        node: Annotated[
            str,
            Field(description="Node name or IP address (e.g. 'core-sw01')"),
        ],
        group: Annotated[
            str | None,
            Field(
                default=None,
                description="Node group; looked up automatically when omitted",
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(default=20, ge=1, le=1000, description="Maximum versions to return"),
        ] = 20,
    ) -> dict[str, Any]:
        """
        List the stored configuration versions of a node, newest first.

        Each version has its git oid, date, author and commit message. Requires
        the Oxidized 'git' (or 'gitcrypt') output.

        Args:
            node: Node name or IP address
            group: Node group; looked up automatically when omitted
            limit: Maximum versions to return

        Returns:
            Dictionary with the node identity, total version count and versions
        """
        async with get_oxidized_client(config) as client:
            name, resolved_group, full_name = await client.resolve_node(node, group)
            versions = await _list_versions(client, full_name)
        return {
            "node": name,
            "group": resolved_group,
            "full_name": full_name,
            "total_versions": len(versions),
            "versions": versions[:limit],
        }

    @mcp.tool(
        tags={"version", "config", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_node_version(
        node: Annotated[
            str,
            Field(description="Node name or IP address (e.g. 'core-sw01')"),
        ],
        oid: Annotated[
            str,
            Field(
                min_length=4,
                description="Version oid from list_node_versions (full or unique prefix)",
            ),
        ],
        group: Annotated[
            str | None,
            Field(
                default=None,
                description="Node group; looked up automatically when omitted",
            ),
        ] = None,
        offset: Annotated[
            int,
            Field(default=0, ge=0, description="Zero-based first line to return"),
        ] = 0,
        max_lines: Annotated[
            int,
            Field(
                default=1000,
                ge=0,
                description="Maximum lines to return (0 = whole configuration)",
            ),
        ] = 1000,
    ) -> dict[str, Any]:
        """
        Get the configuration of a node as it was at a specific version.

        Large configurations are paged by lines: when 'truncated' is true, call
        again with offset set to 'next_offset' to read the rest.

        Args:
            node: Node name or IP address
            oid: Version oid (full or unique prefix)
            group: Node group; looked up automatically when omitted
            offset: Zero-based first line to return
            max_lines: Maximum lines to return (0 = whole configuration)

        Returns:
            Dictionary with the version, size information and 'content'
        """
        async with get_oxidized_client(config) as client:
            name, resolved_group, full_name = await client.resolve_node(node, group)
            # Resolve through the version list even for a full oid, so the
            # response carries its date, author and message.
            versions = await _list_versions(client, full_name)
            if not versions:
                raise OxidizedError(
                    f"No stored versions for {full_name!r} - version history requires "
                    "the 'git' or 'gitcrypt' output"
                )
            _, version = _pick_version(versions, oid)
            text = await _view_version(
                client, name, resolved_group, str(version["oid"])
            )
        return {
            "node": name,
            "full_name": full_name,
            "version": _version_summary(version),
            **page_lines(text, offset, max_lines),
        }

    @mcp.tool(
        tags={"version", "diff", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def diff_node_versions(
        node: Annotated[
            str,
            Field(description="Node name or IP address (e.g. 'core-sw01')"),
        ],
        oid: Annotated[
            str | None,
            Field(
                default=None,
                min_length=4,
                description="Newer version oid (full or prefix); defaults to the latest version",
            ),
        ] = None,
        base_oid: Annotated[
            str | None,
            Field(
                default=None,
                min_length=4,
                description="Older version oid to compare against; defaults to the version before 'oid'",
            ),
        ] = None,
        group: Annotated[
            str | None,
            Field(
                default=None,
                description="Node group; looked up automatically when omitted",
            ),
        ] = None,
        context_lines: Annotated[
            int,
            Field(
                default=3,
                ge=0,
                le=50,
                description="Unchanged lines of context around each change",
            ),
        ] = 3,
        max_lines: Annotated[
            int,
            Field(
                default=2000,
                ge=0,
                description="Maximum diff lines to return (0 = whole diff)",
            ),
        ] = 2000,
        offset: Annotated[
            int,
            Field(default=0, ge=0, description="Zero-based first diff line to return"),
        ] = 0,
    ) -> dict[str, Any]:
        """
        Show what changed in a node's configuration between two versions.

        Returns a unified diff from 'base_oid' (older) to 'oid' (newer). With no
        arguments besides the node it shows the most recent change.

        Args:
            node: Node name or IP address
            oid: Newer version oid; defaults to the latest version
            base_oid: Older version oid; defaults to the version before 'oid'
            group: Node group; looked up automatically when omitted
            context_lines: Unchanged lines of context around each change
            max_lines: Maximum diff lines to return
            offset: Zero-based first diff line to return; when 'truncated' is
                true, call again with offset set to 'next_offset'

        Returns:
            Dictionary with both versions, change counts and the unified 'content'
        """
        async with get_oxidized_client(config) as client:
            name, resolved_group, full_name = await client.resolve_node(node, group)
            versions = await _list_versions(client, full_name)
            if not versions:
                raise OxidizedError(
                    f"No stored versions for {full_name!r} - version history requires "
                    "the 'git' or 'gitcrypt' output"
                )

            if oid is None:
                target_index, target = 0, versions[0]
            else:
                target_index, target = _pick_version(versions, oid)
            if base_oid is not None:
                _, base = _pick_version(versions, base_oid)
            elif target_index is None:
                raise OxidizedError(
                    f"Version {target['oid']!r} is not in the history of "
                    f"{full_name!r}, so its predecessor is unknown - pass base_oid"
                )
            elif target_index + 1 < len(versions):
                base = versions[target_index + 1]
            else:
                raise OxidizedError(
                    f"Version {target.get('oid')!r} is the oldest stored version "
                    f"of {full_name!r} - there is nothing to compare it to"
                )
            base_oid_full, target_oid_full = str(base["oid"]), str(target["oid"])
            cache_key = (full_name, base_oid_full, target_oid_full, context_lines)
            cacheable = _is_full_oid(base_oid_full) and _is_full_oid(target_oid_full)
            diff_lines = diff_cache.get(cache_key) if cacheable else None
            if diff_lines is None:
                base_text, target_text = await asyncio.gather(
                    _view_version(client, name, resolved_group, base_oid_full),
                    _view_version(client, name, resolved_group, target_oid_full),
                )
                diff_lines = list(
                    difflib.unified_diff(
                        base_text.splitlines(),
                        target_text.splitlines(),
                        fromfile=f"{full_name}@{base_oid_full[:8]}",
                        tofile=f"{full_name}@{target_oid_full[:8]}",
                        n=context_lines,
                        lineterm="",
                    )
                )
                if cacheable:
                    diff_cache[cache_key] = diff_lines
                    if len(diff_cache) > _DIFF_CACHE_SIZE:
                        diff_cache.popitem(last=False)
            else:
                diff_cache.move_to_end(cache_key)
        # Skip the two ---/+++ file headers by position: a changed line whose
        # own text starts with "--" or "++" (e.g. a "-----" banner) would
        # otherwise look like a header and go uncounted.
        body = diff_lines[2:]
        added = sum(1 for line in body if line.startswith("+"))
        removed = sum(1 for line in body if line.startswith("-"))
        return {
            "node": name,
            "full_name": full_name,
            "from_version": _version_summary(base),
            "to_version": _version_summary(target),
            "identical": not diff_lines,
            "lines_added": added,
            "lines_removed": removed,
            **page_lines("\n".join(diff_lines), offset, max_lines),
        }
