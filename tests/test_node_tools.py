"""Tests for the node listing, lookup and backup statistics tools."""

import base64
from datetime import UTC
from datetime import datetime
from datetime import timedelta

import httpx2
import pytest

from oxidized_mcp.tools.nodes import parse_oxidized_time
from tests.conftest import call

RECENT = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S UTC")
OLD = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S +0000")


def node(name, group, *, model="IOS", status="success", time=RECENT, ip=None):
    return {
        "name": name,
        "full_name": f"{group}/{name}",
        "ip": ip or f"10.0.0.{len(name)}",
        "group": group,
        "model": model,
        "last": {"start": time, "end": time, "status": status, "time": 1.2},
        "vars": {"password": "s3cret", "enable": None},
        "mtime": "unknown",
        "status": status,
        "time": time,
    }


NODES = [
    node("core-sw01", "core", ip="10.0.0.1"),
    node("core-sw02", "core", model="JunOS", status="no_connection"),
    node("edge-rtr01", "edge", model="RouterOS", time=OLD, ip="192.0.2.1"),
    node("new-sw", "access", status="never", time="never"),
]


def filter_nodes(path: str) -> list[dict]:
    """Mimic oxidized-web's ``get '/nodes/:filter/*'``: exact, case-sensitive."""
    field, _, value = path.removeprefix("/nodes/").partition("/")
    value = value.removesuffix(".json")
    return [
        n
        for n in NODES
        if n.get(field) == value
        or (field == "group" and value == "default" and not n["group"])
    ]


def nodes_handler(request: httpx2.Request) -> httpx2.Response:
    if request.url.path == "/nodes.json":
        return httpx2.Response(200, json=NODES)
    if request.url.path.startswith("/nodes/"):
        return httpx2.Response(200, json=filter_nodes(request.url.path))
    if request.url.path == "/node/show/core-sw01.json":
        return httpx2.Response(200, json=NODES[0])
    if request.url.path.startswith("/node/show/"):
        return httpx2.Response(
            500,
            text="Oxidized::NodeNotFound: unable to find 'missing'",
        )
    return httpx2.Response(404)


@pytest.fixture
def server(make_server):
    return make_server(nodes_handler)


@pytest.mark.asyncio
async def test_list_nodes_redacts_vars(server):
    mcp, _ = server
    result = await call(mcp, "list_nodes")
    assert result["total"] == 4
    assert result["nodes"][0]["vars"] == {"password": "<redacted>", "enable": None}


@pytest.mark.asyncio
async def test_list_nodes_can_skip_redaction(make_server):
    mcp, _ = make_server(nodes_handler, redact_node_vars=False)
    result = await call(mcp, "list_nodes")
    assert result["nodes"][0]["vars"]["password"] == "s3cret"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"model": "junos"}, ["core-sw02"]),
        ({"status": "never"}, ["new-sw"]),
        ({"name_contains": "RTR"}, ["edge-rtr01"]),
        ({"name_contains": "10.0.0.1"}, ["core-sw01"]),
        ({"group": "core", "status": "success"}, ["core-sw01"]),
    ],
)
async def test_list_nodes_filters(server, arguments, expected):
    mcp, _ = server
    result = await call(mcp, "list_nodes", arguments)
    assert [n["name"] for n in result["nodes"]] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "expected_path", "expected_names"),
    [
        ({"group": "core"}, "/nodes/group/core.json", ["core-sw01", "core-sw02"]),
        ({"model": "JunOS"}, "/nodes/model/JunOS.json", ["core-sw02"]),
        # The group goes to the server; the model is applied to its result.
        (
            {"group": "core", "model": "IOS"},
            "/nodes/group/core.json",
            ["core-sw01"],
        ),
        # Built-in model names are mapped to their exact spelling in both
        # paths, so group + model behaves like model alone.
        ({"group": "core", "model": "junos"}, "/nodes/group/core.json", ["core-sw02"]),
        ({"group": "core", "model": "IOS-custom"}, "/nodes/group/core.json", []),
        (
            {"group": "core", "status": "no_connection"},
            "/nodes/group/core.json",
            ["core-sw02"],
        ),
    ],
)
async def test_list_nodes_filters_on_the_server(
    server, arguments, expected_path, expected_names
):
    mcp, requests = server
    result = await call(mcp, "list_nodes", arguments)
    # Only the matching nodes are transferred - no /nodes.json request.
    assert [r.url.path for r in requests] == [expected_path]
    assert [n["name"] for n in result["nodes"]] == expected_names


@pytest.mark.asyncio
async def test_list_nodes_without_group_or_model_fetches_all(server):
    mcp, requests = server
    await call(mcp, "list_nodes", {"status": "never"})
    assert [r.url.path for r in requests] == ["/nodes.json"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("model", "sent"), [("ios", "IOS"), ("IOSXE", "IOS")])
async def test_list_nodes_maps_builtin_model_names(server, model, sent):
    mcp, requests = server
    # oxidized-web matches the class name exactly; "ios" (and the IOSXE alias)
    # are mapped to "IOS" without downloading the inventory.
    result = await call(mcp, "list_nodes", {"model": model})
    assert [r.url.path for r in requests] == [f"/nodes/model/{sent}.json"]
    assert [n["name"] for n in result["nodes"]] == ["core-sw01", "new-sw"]


@pytest.mark.asyncio
async def test_list_nodes_unknown_model_is_one_request(server):
    mcp, requests = server
    result = await call(mcp, "list_nodes", {"model": "jnuos"})
    assert result["total"] == 0
    assert [r.url.path for r in requests] == ["/nodes/model/jnuos.json"]


@pytest.mark.asyncio
async def test_list_nodes_unknown_group_is_empty(server):
    mcp, requests = server
    result = await call(mcp, "list_nodes", {"group": "nope"})
    assert result["total"] == 0
    assert result["nodes"] == []
    # A misspelt group must not download the whole inventory.
    assert [r.url.path for r in requests] == ["/nodes/group/nope.json"]


@pytest.mark.asyncio
async def test_list_nodes_group_is_exact(server):
    mcp, requests = server
    # Groups may legitimately differ only by case, so there is no fallback.
    result = await call(mcp, "list_nodes", {"group": "CORE"})
    assert result["nodes"] == []
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_unknown_node_in_production_mode_gets_a_hint(make_server):
    # With RACK_ENV=production Sinatra hides the exception name, so a bare 500
    # may be an unknown node or a real server error.
    mcp, _ = make_server(
        lambda _request: httpx2.Response(500, text="<h1>Internal Server Error</h1>")
    )
    with pytest.raises(
        httpx2.HTTPStatusError, match=r"unknown node names.*find_nodes"
    ) as exc:
        await call(mcp, "get_node", {"node": "missing"})
    # Still an HTTP error: callers can inspect the status code.
    assert exc.value.response.status_code == 500


@pytest.mark.asyncio
async def test_500_outside_node_routes_has_no_node_hint(make_server):
    mcp, _ = make_server(lambda _request: httpx2.Response(500, text="boom"))
    with pytest.raises(httpx2.HTTPStatusError) as exc:
        await call(mcp, "list_nodes")
    assert "find_nodes" not in str(exc.value)


@pytest.mark.asyncio
async def test_list_nodes_server_filter_redacts_vars(server):
    mcp, _ = server
    result = await call(mcp, "list_nodes", {"group": "core"})
    assert result["nodes"][0]["vars"]["password"] == "<redacted>"


@pytest.mark.asyncio
async def test_list_nodes_pages(server):
    mcp, _ = server
    result = await call(mcp, "list_nodes", {"limit": 2, "offset": 1})
    assert [n["name"] for n in result["nodes"]] == ["core-sw02", "edge-rtr01"]
    assert result["has_more"] is True
    assert result["total"] == 4


@pytest.mark.asyncio
async def test_get_node_redacts_vars(server):
    mcp, requests = server
    result = await call(mcp, "get_node", {"node": "core-sw01"})
    assert result["full_name"] == "core/core-sw01"
    assert result["vars"]["password"] == "<redacted>"
    assert requests[0].url.path == "/node/show/core-sw01.json"


@pytest.mark.asyncio
async def test_get_node_reports_missing_node(server):
    mcp, _ = server
    # oxidized-web raises NodeNotFound, which Sinatra serves as a 500.
    with pytest.raises(Exception, match="Node not found"):
        await call(mcp, "get_node", {"node": "missing"})


@pytest.mark.asyncio
async def test_find_nodes_ranks_exact_then_prefix_then_substring(server):
    mcp, _ = server
    result = await call(mcp, "find_nodes", {"query": "core-sw01"})
    assert [n["name"] for n in result["nodes"]] == ["core-sw01"]

    result = await call(mcp, "find_nodes", {"query": "sw"})
    # No exact or prefix match on name; "new-sw" and "core-sw0x" are substrings.
    assert result["total_matches"] == 3
    assert set(result["nodes"][0]) == {
        "name",
        "full_name",
        "ip",
        "group",
        "model",
        "status",
        "time",
    }

    result = await call(mcp, "find_nodes", {"query": "edge"})
    assert [n["name"] for n in result["nodes"]] == ["edge-rtr01"]


@pytest.mark.asyncio
async def test_get_backup_stats(server):
    mcp, _ = server
    result = await call(mcp, "get_backup_stats", {"stale_hours": 24})
    assert result["total_nodes"] == 4
    assert result["status_counts"] == {"success": 2, "no_connection": 1, "never": 1}
    # 2 successes out of 3 nodes that have run at least once.
    assert result["success_rate_percent"] == 66.7
    assert result["groups"]["core"] == {
        "total": 2,
        "status_counts": {"success": 1, "no_connection": 1},
    }
    assert result["models"]["IOS"] == 2
    assert [n["name"] for n in result["failed_nodes"]] == ["core-sw02"]
    assert [n["name"] for n in result["never_backed_up"]] == ["new-sw"]
    assert [n["name"] for n in result["stale_nodes"]] == ["edge-rtr01"]


@pytest.mark.asyncio
async def test_get_backup_stats_for_one_group(server):
    mcp, requests = server
    result = await call(mcp, "get_backup_stats", {"group": "core"})
    assert [r.url.path for r in requests] == ["/nodes/group/core.json"]
    assert result["total_nodes"] == 2
    assert result["status_counts"] == {"success": 1, "no_connection": 1}
    assert list(result["groups"]) == ["core"]


@pytest.mark.asyncio
async def test_basic_auth_is_sent_when_configured(make_server):
    mcp, requests = make_server(nodes_handler, username="admin", password="pw")
    await call(mcp, "list_nodes")
    expected = base64.b64encode(b"admin:pw").decode()
    assert requests[0].headers["Authorization"] == f"Basic {expected}"


@pytest.mark.asyncio
async def test_no_auth_header_without_credentials(server):
    mcp, requests = server
    await call(mcp, "list_nodes")
    assert "Authorization" not in requests[0].headers


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024-11-18 07:32:14 +0100", datetime(2024, 11, 18, 6, 32, 14, tzinfo=UTC)),
        ("2024-06-30 22:15:03 UTC", datetime(2024, 6, 30, 22, 15, 3, tzinfo=UTC)),
        ("2024-06-30T22:15:03", datetime(2024, 6, 30, 22, 15, 3, tzinfo=UTC)),
        ("never", None),
        ("unknown", None),
        ("", None),
        (None, None),
        ("garbage", None),
    ],
)
def test_parse_oxidized_time(value, expected):
    assert parse_oxidized_time(value) == expected
