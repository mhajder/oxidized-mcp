import asyncio
import logging
import re
from collections import deque
from typing import Annotated
from typing import Any

import httpx2
from fastmcp import FastMCP
from pydantic import Field

from oxidized_mcp.models import OxidizedConfig
from oxidized_mcp.oxidized_client import DEFAULT_GROUP
from oxidized_mcp.oxidized_client import OxidizedClient
from oxidized_mcp.oxidized_client import OxidizedError
from oxidized_mcp.oxidized_client import OxidizedNotFoundError
from oxidized_mcp.oxidized_client import get_oxidized_client
from oxidized_mcp.oxidized_client import node_path
from oxidized_mcp.oxidized_client import normalize_group
from oxidized_mcp.oxidized_client import split_full_name
from oxidized_mcp.utils import page_lines

logger = logging.getLogger(__name__)

# Configurations are fetched one request per node; cap the fan-out so a broad
# search does not flood oxidized-web.
_FETCH_CONCURRENCY = 5


async def _conf_search(client: OxidizedClient, pattern: str, timeout: int) -> Any:
    """Run oxidized-web's server-side search, explaining its failure modes."""
    try:
        return await client.post_form(
            "/nodes/conf_search.json",
            {"search_in_conf_textbox": pattern},
            timeout=timeout,
        )
    except httpx2.TimeoutException as exc:
        raise OxidizedError(
            f"The search timed out after {timeout}s: oxidized-web reads every "
            "stored configuration to answer it. Raise OXIDIZED_SEARCH_TIMEOUT for "
            "large inventories."
        ) from exc
    except httpx2.HTTPStatusError as exc:
        if exc.response.status_code != 500:
            raise
        raise OxidizedError(
            "oxidized-web failed to run the search (HTTP 500). It evaluates the "
            "pattern as a Ruby regular expression, so Python-only syntax such as "
            "(?P<name>...) is rejected; with the 'file' output, a node without a "
            f"stored configuration also breaks the search. Details: {exc}"
        ) from exc


def _in_group(node_group: str | None, group: str | None) -> bool:
    """Exact group filter, matching list_nodes and oxidized-web.

    "default" selects nodes without a group (and a group literally named
    "default"), like oxidized-web's /nodes/group/default.
    """
    if group is None or not group.strip():
        return True
    wanted = normalize_group(group)
    if wanted is None:
        return node_group in {None, DEFAULT_GROUP}
    return node_group == wanted


def _grep(
    text: str, regex: re.Pattern[str], context_lines: int, limit: int
) -> tuple[list[dict[str, Any]], bool]:
    """Return up to ``limit`` matching lines with optional surrounding context.

    The flag is True when more matching lines exist beyond ``limit``.
    """
    lines = text.splitlines()
    matches: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not regex.search(line):
            continue
        if len(matches) >= limit:
            return matches, True
        match: dict[str, Any] = {"line_number": index + 1, "line": line}
        if context_lines:
            match["context_before"] = lines[max(index - context_lines, 0) : index]
            match["context_after"] = lines[index + 1 : index + 1 + context_lines]
        matches.append(match)
    return matches, False


def register_configs_tools(mcp: FastMCP, config: OxidizedConfig) -> None:
    """Register configuration-related Oxidized tools with the FastMCP server."""

    @mcp.tool(
        tags={"config", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_node_config(
        node: Annotated[
            str,
            Field(description="Node name or IP address (e.g. 'core-sw01')"),
        ],
        group: Annotated[
            str | None,
            Field(
                default=None,
                description="Node group, to disambiguate nodes with the same name in different groups",
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
        Get the latest backed-up configuration of a node.

        Large configurations are paged by lines: when 'truncated' is true, call
        again with offset set to 'next_offset' to read the rest.

        Args:
            node: Node name or IP address
            group: Node group; looked up automatically when omitted
            offset: Zero-based first line to return
            max_lines: Maximum lines to return (0 = whole configuration)

        Returns:
            Dictionary with size information and the configuration 'content'
        """
        async with get_oxidized_client(config) as client:
            # With the git output's single_repo the group is part of the file
            # path, so /node/fetch/<node> without it reports "node not found"
            # for grouped nodes. Resolve it like the version tools do.
            name, resolved_group, full_name = await client.resolve_node(node, group)
            text = await client.get_text(
                f"/node/fetch/{node_path(name, resolved_group)}"
            )
        return {
            "node": name,
            "group": resolved_group,
            "full_name": full_name,
            **page_lines(text, offset, max_lines),
        }

    @mcp.tool(
        tags={"config", "search", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def search_configs(
        pattern: Annotated[
            str,
            Field(
                min_length=1,
                description="Regular expression (or literal text with literal=true) to search for, e.g. 'snmp-server community'",
            ),
        ],
        literal: Annotated[
            bool,
            Field(default=False, description="Treat the pattern as literal text"),
        ] = False,
        case_sensitive: Annotated[
            bool,
            Field(default=False, description="Match case-sensitively"),
        ] = False,
        group: Annotated[
            str | None,
            Field(
                default=None,
                description="Only search nodes in this group - exact, case-sensitive name as shown by list_nodes; 'default' selects nodes without a group",
            ),
        ] = None,
        context_lines: Annotated[
            int,
            Field(
                default=0,
                ge=0,
                le=10,
                description="Lines of context to include before and after each match",
            ),
        ] = 0,
        max_matches: Annotated[
            int,
            Field(
                default=200,
                ge=1,
                le=5000,
                description="Maximum matching lines to return in total",
            ),
        ] = 200,
        max_nodes: Annotated[
            int,
            Field(
                default=50,
                ge=1,
                le=500,
                description="Maximum matching nodes whose configurations are searched (nodes without a stored configuration are skipped and not counted)",
            ),
        ] = 50,
    ) -> dict[str, Any]:
        """
        Search the latest configurations of all nodes for a pattern.

        oxidized-web first finds the nodes whose configuration matches, then
        those configurations are fetched to return the matching lines with
        line numbers. Use this to answer questions such as "which devices
        still have telnet enabled" or "where is VLAN 42 configured".

        The pattern must be valid in both Ruby (oxidized-web) and Python
        regular expressions - stick to common syntax. Lines are matched one at
        a time, so patterns spanning several lines find nodes but no lines.

        Args:
            pattern: Regular expression or literal text to search for
            literal: Treat the pattern as literal text
            case_sensitive: Match case-sensitively
            group: Only search nodes in this group
            context_lines: Lines of context before and after each match
            max_matches: Maximum matching lines to return in total
            max_nodes: Maximum matching nodes whose configurations are fetched

        Returns:
            Dictionary with match counts and per-node matching lines
        """
        expression = re.escape(pattern) if literal else pattern
        try:
            regex = re.compile(expression, 0 if case_sensitive else re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"Invalid regular expression {pattern!r}: {exc}") from exc

        # oxidized-web evaluates the pattern as a Ruby regexp, which accepts the
        # same inline flag for case-insensitivity.
        server_pattern = expression if case_sensitive else f"(?i){expression}"

        async with get_oxidized_client(config) as client:
            found = await _conf_search(client, server_pattern, config.search_timeout)

            candidates: list[tuple[str, str | None, str]] = []
            for item in found if isinstance(found, list) else []:
                if not isinstance(item, dict):
                    continue
                full_name = str(item.get("full_name") or item.get("node") or "")
                node_group, name = split_full_name(full_name)
                if not name or not _in_group(node_group, group):
                    continue
                candidates.append(
                    (str(item.get("node") or name), node_group, full_name)
                )
            candidates.sort(key=lambda candidate: candidate[2])

            results: list[dict[str, Any]] = []
            errors: list[dict[str, str]] = []
            total_matches = 0
            nodes_searched = 0
            skipped = 0
            truncated = False
            next_index = 0
            pending: deque[tuple[tuple[str, str | None, str], asyncio.Task[str]]] = (
                deque()
            )

            def refill() -> None:
                """Keep up to _FETCH_CONCURRENCY fetches running.

                The max_nodes budget counts configurations actually searched
                (pending ones included). Nodes without a stored configuration
                do not use it, so the search moves past them.
                """
                nonlocal next_index
                while (
                    next_index < len(candidates)
                    and sum(not task.done() for _, task in pending) < _FETCH_CONCURRENCY
                    and nodes_searched + len(pending) < max_nodes
                ):
                    candidate = candidates[next_index]
                    next_index += 1
                    name, node_group, _ = candidate
                    task = asyncio.ensure_future(
                        client.get_text(f"/node/fetch/{node_path(name, node_group)}")
                    )
                    pending.append((candidate, task))

            try:
                refill()
                # Consume results in candidate order; while the next one is
                # still running, keep the window full as others complete.
                while pending:
                    if total_matches >= max_matches:
                        break
                    head = pending[0][1]
                    while not head.done():
                        await asyncio.wait(
                            [task for _, task in pending if not task.done()],
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        refill()
                    (name, _, full_name), task = pending.popleft()
                    try:
                        text = task.result()
                    except OxidizedNotFoundError:
                        # conf_search also matches the "node not found"
                        # placeholder it gets for nodes never backed up.
                        skipped += 1
                    except (OxidizedError, httpx2.HTTPError) as exc:
                        nodes_searched += 1
                        errors.append({"full_name": full_name, "error": str(exc)})
                    else:
                        nodes_searched += 1
                        matches, has_more = _grep(
                            text, regex, context_lines, max_matches - total_matches
                        )
                        truncated = truncated or has_more
                        total_matches += len(matches)
                        results.append(
                            {
                                "node": name,
                                "full_name": full_name,
                                "match_count": len(matches),
                                "matches": matches,
                            }
                        )
                    refill()
            finally:
                for _, task in pending:
                    task.cancel()
                await asyncio.gather(
                    *(task for _, task in pending), return_exceptions=True
                )
            # Candidates matched on the server but left unsearched mean the
            # results may be incomplete.
            truncated = truncated or bool(pending) or next_index < len(candidates)

        response: dict[str, Any] = {
            "pattern": pattern,
            "literal": literal,
            "case_sensitive": case_sensitive,
            "matched_nodes_total": len(candidates),
            "nodes_searched": nodes_searched,
            "total_matches": total_matches,
            "truncated": truncated,
            "results": results,
        }
        if skipped:
            response["skipped_without_config"] = skipped
        if errors:
            response["errors"] = errors
        return response
