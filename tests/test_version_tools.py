"""Tests for the configuration version history tools."""

import httpx2
import pytest

from tests.conftest import call

OID_NEW = "c" * 40
OID_MID = "b" * 40
OID_OLD = "a" * 40

VERSIONS = [
    {
        "oid": oid,
        "date": date,
        "time": date,
        "author": {"name": "oxidized", "email": "o@example.com", "time": date},
        "message": "update sw1",
    }
    for oid, date in [
        (OID_NEW, "2024-03-14 16:41:27 +0000"),
        (OID_MID, "2024-03-09 08:12:55 +0000"),
        (OID_OLD, "2024-02-27 11:03:40 +0000"),
    ]
]

CONTENT = {
    OID_OLD: "hostname sw1\nvlan 10\n",
    OID_MID: "hostname sw1\nvlan 10\nvlan 20\n",
    OID_NEW: "hostname sw1\nvlan 20\nvlan 30\n",
}


def version_handler(request: httpx2.Request) -> httpx2.Response:
    path, params = request.url.path, request.url.params
    if path == "/node/show/sw1.json":
        return httpx2.Response(
            200, json={"name": "sw1", "full_name": "core/sw1", "group": "core"}
        )
    if path == "/node/show/lonely.json":
        # Ungrouped nodes report group "default" but full_name without a group.
        return httpx2.Response(
            200, json={"name": "lonely", "full_name": "lonely", "group": "default"}
        )
    if path == "/node/version.json":
        if params["node_full"] in {"core/sw1", "lonely"}:
            return httpx2.Response(200, json=VERSIONS)
        return httpx2.Response(200, json=["node not found"])
    if path == "/node/version/view":
        oid = params["oid"]
        if oid in CONTENT:
            return httpx2.Response(200, text=CONTENT[oid])
        return httpx2.Response(200, text="version not found")
    return httpx2.Response(404)


@pytest.fixture
def server(make_server):
    return make_server(version_handler)


def paths(requests):
    return [request.url.path for request in requests]


@pytest.mark.asyncio
async def test_list_versions_resolves_group_from_node(server):
    mcp, requests = server
    result = await call(mcp, "list_node_versions", {"node": "sw1", "limit": 2})
    assert result["full_name"] == "core/sw1"
    assert result["group"] == "core"
    assert result["total_versions"] == 3
    assert [v["oid"] for v in result["versions"]] == [OID_NEW, OID_MID]
    assert paths(requests) == ["/node/show/sw1.json", "/node/version.json"]
    assert requests[1].url.params["node_full"] == "core/sw1"


@pytest.mark.asyncio
async def test_list_versions_with_group_skips_lookup(server):
    mcp, requests = server
    await call(mcp, "list_node_versions", {"node": "sw1", "group": "core"})
    assert paths(requests) == ["/node/version.json"]


@pytest.mark.asyncio
async def test_list_versions_unknown_node(server):
    mcp, _ = server
    with pytest.raises(Exception, match="node not found"):
        await call(mcp, "list_node_versions", {"node": "sw9", "group": "core"})


@pytest.mark.asyncio
async def test_get_node_version_resolves_prefix(server):
    mcp, requests = server
    result = await call(mcp, "get_node_version", {"node": "sw1", "oid": "bbbb"})
    assert result["version"]["oid"] == OID_MID
    assert result["content"] == "hostname sw1\nvlan 10\nvlan 20"
    view = requests[-1]
    assert view.url.params["oid"] == OID_MID
    assert view.url.params["group"] == "core"
    assert view.url.params["format"] == "text"


@pytest.mark.asyncio
async def test_get_node_version_full_oid_has_metadata(server):
    mcp, _ = server
    result = await call(mcp, "get_node_version", {"node": "sw1", "oid": OID_NEW})
    assert result["version"]["date"] == "2024-03-14 16:41:27 +0000"
    assert result["version"]["author"]["name"] == "oxidized"


@pytest.mark.asyncio
async def test_get_node_version_full_oid_outside_history_is_viewed(make_server):
    other = "e" * 40

    def handler(request: httpx2.Request) -> httpx2.Response:
        if (
            request.url.path == "/node/version/view"
            and request.url.params["oid"] == other
        ):
            return httpx2.Response(200, text="hostname sw1\n")
        return version_handler(request)

    mcp, _ = make_server(handler)
    # A commit that did not change this node's file is not in its history.
    result = await call(mcp, "get_node_version", {"node": "sw1", "oid": other})
    assert result["version"] == {
        "oid": other,
        "date": None,
        "author": None,
        "message": None,
    }
    assert result["content"] == "hostname sw1"


@pytest.mark.asyncio
async def test_get_node_version_without_git_output(make_server):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/node/version.json":
            return httpx2.Response(200, json=[])
        return version_handler(request)

    mcp, _ = make_server(handler)
    with pytest.raises(Exception, match="requires the 'git'"):
        await call(mcp, "get_node_version", {"node": "sw1", "oid": OID_NEW})


@pytest.mark.asyncio
async def test_get_node_version_strips_whitespace(server):
    mcp, requests = server
    await call(mcp, "get_node_version", {"node": "sw1", "oid": f" {OID_MID[:10]} "})
    assert requests[-1].url.params["oid"] == OID_MID


@pytest.mark.asyncio
async def test_default_group_is_resolved_like_no_group(server):
    mcp, requests = server
    result = await call(
        mcp, "list_node_versions", {"node": "lonely", "group": "default"}
    )
    assert result["full_name"] == "lonely"
    assert requests[-1].url.params["node_full"] == "lonely"


@pytest.mark.asyncio
async def test_get_node_version_omits_group_for_ungrouped_node(server):
    mcp, requests = server
    await call(mcp, "get_node_version", {"node": "lonely", "oid": OID_NEW})
    view = requests[-1]
    assert view.url.path == "/node/version/view"
    assert "group" not in view.url.params
    assert view.url.params["node"] == "lonely"


@pytest.mark.asyncio
async def test_get_node_version_unknown_oid(server):
    mcp, _ = server
    with pytest.raises(Exception, match="not found"):
        await call(mcp, "get_node_version", {"node": "sw1", "oid": "d" * 40})
    with pytest.raises(Exception, match="not found"):
        await call(mcp, "get_node_version", {"node": "sw1", "oid": "dddd"})


@pytest.mark.asyncio
async def test_diff_defaults_to_latest_change(server):
    mcp, _ = server
    result = await call(mcp, "diff_node_versions", {"node": "sw1"})
    assert result["from_version"]["oid"] == OID_MID
    assert result["to_version"]["oid"] == OID_NEW
    assert result["lines_added"] == 1
    assert result["lines_removed"] == 1
    assert result["identical"] is False
    lines = result["content"].splitlines()
    assert lines[0] == "--- core/sw1@bbbbbbbb"
    assert lines[1] == "+++ core/sw1@cccccccc"
    assert "-vlan 10" in lines
    assert "+vlan 30" in lines


@pytest.mark.asyncio
async def test_diff_between_explicit_versions(server):
    mcp, _ = server
    result = await call(
        mcp,
        "diff_node_versions",
        {"node": "sw1", "oid": "cccc", "base_oid": "aaaa", "context_lines": 0},
    )
    assert result["from_version"]["oid"] == OID_OLD
    # vlan 10 -> vlan 20, vlan 30
    assert result["lines_added"] == 2
    assert result["lines_removed"] == 1
    assert "+vlan 30" in result["content"]
    assert " hostname sw1" not in result["content"]


@pytest.mark.asyncio
async def test_diff_accepts_a_full_oid_outside_the_history(make_server):
    other = "e" * 40

    def handler(request: httpx2.Request) -> httpx2.Response:
        if (
            request.url.path == "/node/version/view"
            and request.url.params["oid"] == other
        ):
            return httpx2.Response(200, text="hostname sw1\nvlan 99\n")
        return version_handler(request)

    mcp, _ = make_server(handler)
    # Accepted like get_node_version does, as long as the base is explicit.
    result = await call(
        mcp, "diff_node_versions", {"node": "sw1", "oid": other, "base_oid": OID_OLD}
    )
    assert result["to_version"]["oid"] == other
    assert "+vlan 99" in result["content"]
    with pytest.raises(Exception, match="pass base_oid"):
        await call(mcp, "diff_node_versions", {"node": "sw1", "oid": other})


@pytest.mark.asyncio
async def test_version_tools_resolve_an_ip_even_with_a_group(make_server):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/node/show/10.0.0.1.json":
            return httpx2.Response(
                200, json={"name": "sw1", "full_name": "core/sw1", "group": "core"}
            )
        return version_handler(request)

    mcp, requests = make_server(handler)
    result = await call(
        mcp, "list_node_versions", {"node": "10.0.0.1", "group": "core"}
    )
    assert result["full_name"] == "core/sw1"
    assert requests[0].url.path == "/node/show/10.0.0.1.json"


@pytest.mark.asyncio
async def test_diff_oldest_version_has_nothing_to_compare(server):
    mcp, _ = server
    with pytest.raises(Exception, match="oldest stored version"):
        await call(mcp, "diff_node_versions", {"node": "sw1", "oid": "aaaa"})


@pytest.mark.asyncio
async def test_diff_without_git_output(make_server):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/node/version.json":
            return httpx2.Response(200, json=[])
        return version_handler(request)

    mcp, _ = make_server(handler)
    with pytest.raises(Exception, match="requires the 'git'"):
        await call(mcp, "diff_node_versions", {"node": "sw1"})


@pytest.mark.asyncio
async def test_view_not_supported_is_explained(make_server):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/node/version/view":
            return httpx2.Response(200, text="not supported")
        return version_handler(request)

    mcp, _ = make_server(handler)
    with pytest.raises(Exception, match="gitcrypt"):
        await call(mcp, "get_node_version", {"node": "sw1", "oid": OID_NEW})
