"""End-to-end checks that tool arguments cannot rewrite the request path.

These drive the registered tools with a mocked httpx2 transport and assert on
the raw path that reaches the wire: a node name containing '#', '?' or '/'
must stay inside its path segment, otherwise a request (including queueing a
backup) silently addresses a different node and returns success for it.
"""

import httpx2
import pytest

from tests.conftest import call
from tests.conftest import raw_path


def handler(request: httpx2.Request) -> httpx2.Response:
    path = request.url.path
    if path.startswith("/node/fetch/"):
        return httpx2.Response(200, text="hostname sw\n")
    if path.startswith("/node/show/"):
        return httpx2.Response(200, json={"name": "sw", "full_name": "sw"})
    if path.startswith("/nodes/"):
        # Non-empty, so list_nodes does not fall back to /nodes.json.
        return httpx2.Response(200, json=[{"name": "sw"}])
    return httpx2.Response(200, json=["ok"])


@pytest.fixture
def tools(make_server):
    return make_server(handler)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("get_node", {"node": "a#b"}, "/node/show/a%23b.json"),
        ("get_node", {"node": "a?b"}, "/node/show/a%3Fb.json"),
        # Without a group the node is looked up first (single_repo needs the
        # group in the path), so the name is encoded in both requests.
        (
            "get_node_config",
            {"node": "a#b"},
            ["/node/show/a%23b.json", "/node/fetch/sw"],
        ),
        ("get_node_config", {"node": "a/b", "group": "core"}, "/node/fetch/core/a%2Fb"),
        (
            "get_node_config",
            {"node": "a#b", "group": "core"},
            "/node/fetch/core/a%23b",
        ),
        # Group names may contain '/', which oxidized-web matches with a splat.
        (
            "get_node_config",
            {"node": "sw", "group": "dc1/core"},
            "/node/fetch/dc1/core/sw",
        ),
        (
            "get_node_config",
            {"node": "sw", "group": "dc#1"},
            "/node/fetch/dc%231/sw",
        ),
        # Server-side node filters: the group is a splat, like in fetch.
        ("list_nodes", {"group": "dc1/core"}, "/nodes/group/dc1/core.json"),
        ("list_nodes", {"group": "a#b"}, "/nodes/group/a%23b.json"),
        ("list_nodes", {"model": "A?B"}, "/nodes/model/A%3FB.json"),
        # The write path - where a rewritten path means acting on another node.
        ("trigger_node_backup", {"node": "a#b"}, "/node/next/a%23b.json"),
        # "default" is oxidized-web's label for nodes without a group, not a
        # real group, so it must not end up in the path.
        (
            "get_node_config",
            {"node": "sw", "group": "default"},
            ["/node/show/sw.json", "/node/fetch/sw"],
        ),
    ],
)
async def test_path_segments_are_encoded(tools, name, arguments, expected):
    mcp, requests = tools
    await call(mcp, name, arguments)
    expected = expected if isinstance(expected, list) else [expected]
    assert [raw_path(request) for request in requests] == expected


@pytest.mark.asyncio
async def test_dot_segment_cannot_walk_up_the_path(tools):
    mcp, requests = tools
    await call(mcp, "get_node_config", {"node": "..", "group": ".."})
    # Unescaped this would normalise to /node or /, a different route.
    assert [raw_path(request) for request in requests] == ["/node/fetch/%2E%2E/%2E%2E"]


@pytest.mark.asyncio
async def test_empty_node_is_rejected_before_any_request(tools):
    mcp, requests = tools
    with pytest.raises(Exception, match="must not be empty"):
        await call(mcp, "trigger_node_backup", {"node": ""})
    assert requests == []


@pytest.mark.asyncio
async def test_empty_group_component_is_rejected(tools):
    mcp, requests = tools
    with pytest.raises(Exception, match="must not be empty"):
        await call(mcp, "get_node_config", {"node": "sw", "group": "dc1//core"})
    assert requests == []
