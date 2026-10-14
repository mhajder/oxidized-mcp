"""Tests for fetching and searching node configurations."""

import asyncio
from urllib.parse import parse_qs

import httpx2
import pytest

from tests.conftest import call

CONFIGS = {
    "/node/fetch/core/sw1": "hostname sw1\nsnmp-server community public RO\nline vty 0 4\n transport input telnet\n",
    "/node/fetch/core/sw2": "hostname sw2\nSNMP-SERVER COMMUNITY private RO\n",
    "/node/fetch/edge/rtr1": "hostname rtr1\nsnmp-server community public RO\n",
    "/node/fetch/core/lonely": "hostname lonely\n",
}


def config_handler(request: httpx2.Request) -> httpx2.Response:
    path = request.url.path
    if path == "/nodes/conf_search.json":
        return httpx2.Response(
            200,
            json=[
                {"node": "sw1", "full_name": "core/sw1"},
                {"node": "sw2", "full_name": "core/sw2"},
                {"node": "rtr1", "full_name": "edge/rtr1"},
            ],
        )
    if path in CONFIGS:
        return httpx2.Response(200, text=CONFIGS[path])
    if path == "/node/show/sw1.json":
        return httpx2.Response(200, json={"name": "sw1", "full_name": "core/sw1"})
    if path == "/node/show/lonely.json":
        return httpx2.Response(200, json={"name": "lonely", "full_name": "lonely"})
    if path == "/node/fetch/lonely":
        return httpx2.Response(200, text="hostname lonely\n")
    if path.startswith("/node/show/"):
        return httpx2.Response(500, text="Oxidized::NodeNotFound: unable to find")
    if path.startswith("/node/fetch/"):
        return httpx2.Response(200, text="unable to find 'missing'")
    return httpx2.Response(404)


@pytest.fixture
def server(make_server):
    return make_server(config_handler)


@pytest.mark.asyncio
async def test_get_node_config_requests_text(server):
    mcp, requests = server
    result = await call(mcp, "get_node_config", {"node": "sw1", "group": "core"})
    assert result["content"].startswith("hostname sw1")
    assert result["total_lines"] == 4
    assert result["truncated"] is False
    assert requests[0].url.params["format"] == "text"


@pytest.mark.asyncio
async def test_get_node_config_pages_lines(server):
    mcp, _ = server
    result = await call(
        mcp,
        "get_node_config",
        {"node": "sw1", "group": "core", "offset": 1, "max_lines": 2},
    )
    assert result["content"] == "snmp-server community public RO\nline vty 0 4"
    assert result["truncated"] is True
    assert result["next_offset"] == 3


@pytest.mark.asyncio
async def test_get_node_config_reports_missing_node(server):
    mcp, _ = server
    # Without a group the node is looked up first; /node/show raises
    # NodeNotFound (a 500).
    with pytest.raises(Exception, match="Node not found"):
        await call(mcp, "get_node_config", {"node": "missing"})
    # With a group /node/fetch answers 200 and a message, not an error.
    with pytest.raises(Exception, match="unable to find"):
        await call(mcp, "get_node_config", {"node": "missing", "group": "core"})


@pytest.mark.asyncio
async def test_get_node_config_resolves_the_group(server):
    mcp, requests = server
    # single_repo stores grouped nodes under <group>/<name>, so the group must
    # be in the fetch path (verified against a live Oxidized).
    result = await call(mcp, "get_node_config", {"node": "sw1"})
    assert [r.url.path for r in requests] == [
        "/node/show/sw1.json",
        "/node/fetch/core/sw1",
    ]
    assert result["full_name"] == "core/sw1"
    assert result["group"] == "core"


@pytest.mark.asyncio
async def test_search_configs_returns_matching_lines(server):
    mcp, requests = server
    result = await call(mcp, "search_configs", {"pattern": "snmp-server community"})

    form = parse_qs(requests[0].content.decode())
    assert form["search_in_conf_textbox"] == ["(?i)snmp-server community"]

    assert result["matched_nodes_total"] == 3
    assert result["total_matches"] == 3
    assert [r["full_name"] for r in result["results"]] == [
        "core/sw1",
        "core/sw2",
        "edge/rtr1",
    ]
    assert result["results"][0]["matches"] == [
        {"line_number": 2, "line": "snmp-server community public RO"}
    ]


@pytest.mark.asyncio
async def test_search_configs_case_sensitive_and_group(server):
    mcp, requests = server
    result = await call(
        mcp,
        "search_configs",
        {"pattern": "snmp-server", "case_sensitive": True, "group": "core"},
    )
    form = parse_qs(requests[0].content.decode())
    assert form["search_in_conf_textbox"] == ["snmp-server"]
    # edge/rtr1 is filtered out; sw2 only has the upper-case spelling.
    assert [(r["full_name"], r["match_count"]) for r in result["results"]] == [
        ("core/sw1", 1),
        ("core/sw2", 0),
    ]


@pytest.mark.asyncio
async def test_search_configs_literal_and_context(server):
    mcp, requests = server
    result = await call(
        mcp,
        "search_configs",
        {"pattern": "vty 0 4", "literal": True, "context_lines": 1, "group": "core"},
    )
    form = parse_qs(requests[0].content.decode())
    assert form["search_in_conf_textbox"] == ["(?i)vty\\ 0\\ 4"]
    assert result["results"][0]["matches"] == [
        {
            "line_number": 3,
            "line": "line vty 0 4",
            "context_before": ["snmp-server community public RO"],
            "context_after": [" transport input telnet"],
        }
    ]


@pytest.mark.asyncio
async def test_search_configs_limits_matches_and_nodes(server):
    mcp, _ = server
    result = await call(
        mcp, "search_configs", {"pattern": "hostname", "max_matches": 2}
    )
    assert result["total_matches"] == 2
    assert result["truncated"] is True

    result = await call(mcp, "search_configs", {"pattern": "hostname", "max_nodes": 1})
    assert result["nodes_searched"] == 1
    assert result["matched_nodes_total"] == 3
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_search_configs_exact_limit_is_not_truncated(server):
    mcp, _ = server
    # Exactly 3 lines match and max_matches is 3: nothing was cut off.
    result = await call(
        mcp, "search_configs", {"pattern": "hostname", "max_matches": 3}
    )
    assert result["total_matches"] == 3
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_search_configs_stops_fetching_once_the_limit_is_reached(make_server):
    names = [f"sw{i:02d}" for i in range(12)]

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/nodes/conf_search.json":
            return httpx2.Response(
                200, json=[{"node": n, "full_name": f"core/{n}"} for n in names]
            )
        if not request.url.path.endswith("/sw00"):
            await asyncio.sleep(0.05)
        return httpx2.Response(200, text="hostname x\n")

    mcp, requests = make_server(handler)
    result = await call(
        mcp, "search_configs", {"pattern": "hostname", "max_matches": 1}
    )
    fetches = [r for r in requests if r.url.path.startswith("/node/fetch/")]
    # The first config fills the limit; the rest are cancelled, not fetched.
    assert len(fetches) < len(names)
    assert result["nodes_searched"] == 1
    assert result["total_matches"] == 1
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_search_configs_keeps_candidate_order(make_server):
    names = ["sw01", "sw02", "sw03"]

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/nodes/conf_search.json":
            return httpx2.Response(
                200, json=[{"node": n, "full_name": f"core/{n}"} for n in names]
            )
        # The first node answers last.
        if request.url.path.endswith("/sw01"):
            await asyncio.sleep(0.05)
        return httpx2.Response(200, text="hostname x\n")

    mcp, _ = make_server(handler)
    result = await call(mcp, "search_configs", {"pattern": "hostname"})
    assert [r["node"] for r in result["results"]] == names


@pytest.mark.asyncio
async def test_search_configs_group_filter_is_exact_and_knows_default(make_server):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/nodes/conf_search.json":
            return httpx2.Response(
                200,
                json=[
                    {"node": "sw1", "full_name": "core/sw1"},
                    {"node": "sw2", "full_name": "Core/sw2"},
                    {"node": "lonely", "full_name": "lonely"},
                ],
            )
        return httpx2.Response(200, text="hostname x\n")

    mcp, _ = make_server(handler)
    # Same semantics as list_nodes: groups differing only by case are distinct.
    result = await call(mcp, "search_configs", {"pattern": "hostname", "group": "core"})
    assert [r["full_name"] for r in result["results"]] == ["core/sw1"]
    result = await call(
        mcp, "search_configs", {"pattern": "hostname", "group": "default"}
    )
    assert [r["full_name"] for r in result["results"]] == ["lonely"]


@pytest.mark.asyncio
async def test_search_configs_keeps_a_real_default_group_in_the_path(make_server):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/nodes/conf_search.json":
            return httpx2.Response(
                200, json=[{"node": "sw9", "full_name": "default/sw9"}]
            )
        return httpx2.Response(200, text="hostname x\n")

    mcp, requests = make_server(handler)
    await call(mcp, "search_configs", {"pattern": "hostname"})
    # The group comes from full_name, so it is a real group named "default".
    assert requests[-1].url.path == "/node/fetch/default/sw9"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("group", "path", "echoed"),
    [
        (" core ", "/node/fetch/core/lonely", "core"),
        ("default ", "/node/fetch/lonely", None),
    ],
)
async def test_get_node_config_normalizes_the_group(server, group, path, echoed):
    mcp, requests = server
    result = await call(mcp, "get_node_config", {"node": "lonely", "group": group})
    assert requests[-1].url.path == path
    assert result["group"] == echoed


@pytest.mark.asyncio
async def test_get_node_config_empty_response_is_not_an_empty_config(make_server):
    # The 'file' output serves a missing configuration as an empty 200.
    mcp, _ = make_server(lambda _request: httpx2.Response(200, text=""))
    with pytest.raises(Exception, match="no configuration is stored"):
        await call(mcp, "get_node_config", {"node": "sw1", "group": "core"})


@pytest.mark.asyncio
async def test_search_configs_reports_per_node_errors(make_server):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/nodes/conf_search.json":
            return httpx2.Response(
                200,
                json=[
                    {"node": "sw1", "full_name": "core/sw1"},
                    {"node": "gone", "full_name": "core/gone"},
                ],
            )
        return config_handler(request)

    mcp, _ = make_server(handler)
    result = await call(mcp, "search_configs", {"pattern": "hostname"})
    assert [r["full_name"] for r in result["results"]] == ["core/sw1"]
    # "gone" answers "unable to find": a node without a stored config, which is
    # skipped rather than reported as an error.
    assert result["skipped_without_config"] == 1
    assert "errors" not in result


@pytest.mark.asyncio
async def test_search_configs_reports_real_fetch_errors(make_server):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/nodes/conf_search.json":
            return httpx2.Response(
                200,
                json=[
                    {"node": "sw1", "full_name": "core/sw1"},
                    {"node": "broken", "full_name": "core/broken"},
                ],
            )
        if request.url.path == "/node/fetch/core/broken":
            return httpx2.Response(502, text="Bad gateway")
        return config_handler(request)

    mcp, _ = make_server(handler)
    result = await call(mcp, "search_configs", {"pattern": "hostname"})
    assert [r["full_name"] for r in result["results"]] == ["core/sw1"]
    assert result["errors"][0]["full_name"] == "core/broken"


@pytest.mark.asyncio
async def test_search_configs_placeholders_do_not_use_the_node_budget(make_server):
    # conf_search also matches the "node not found" placeholder of nodes that
    # were never backed up (pattern "not" here).
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/nodes/conf_search.json":
            return httpx2.Response(
                200,
                json=[
                    {"node": "a-never", "full_name": "core/a-never"},
                    {"node": "b-real", "full_name": "core/b-real"},
                ],
            )
        if request.url.path == "/node/fetch/core/a-never":
            return httpx2.Response(200, text="node not found")
        return httpx2.Response(200, text="hostname b\nno shutdown\n")

    mcp, _ = make_server(handler)
    result = await call(mcp, "search_configs", {"pattern": "not", "max_nodes": 1})
    assert [r["full_name"] for r in result["results"]] == ["core/b-real"]
    assert result["skipped_without_config"] == 1
    assert result["nodes_searched"] == 1
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_search_configs_uses_the_search_timeout(make_server):
    mcp, requests = make_server(config_handler, search_timeout=123)
    await call(mcp, "search_configs", {"pattern": "hostname"})
    assert requests[0].extensions["timeout"]["read"] == 123
    # Other requests keep the client-wide timeout.
    assert requests[1].extensions["timeout"]["read"] != 123


@pytest.mark.asyncio
async def test_search_configs_explains_a_server_error(make_server):
    mcp, _ = make_server(lambda _request: httpx2.Response(500, text="RegexpError"))
    with pytest.raises(Exception, match="Ruby regular expression"):
        await call(mcp, "search_configs", {"pattern": "(?P<n>x)"})


@pytest.mark.asyncio
async def test_search_configs_explains_a_timeout(make_server):
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    mcp, _ = make_server(handler)
    with pytest.raises(Exception, match="OXIDIZED_SEARCH_TIMEOUT"):
        await call(mcp, "search_configs", {"pattern": "hostname"})


@pytest.mark.asyncio
async def test_get_node_config_ip_with_group_is_resolved(make_server):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/node/show/10.0.0.1.json":
            return httpx2.Response(200, json={"name": "sw1", "full_name": "core/sw1"})
        return config_handler(request)

    mcp, requests = make_server(handler)
    # fetch finds nodes by name only, so the IP must be resolved even with a group.
    result = await call(mcp, "get_node_config", {"node": "10.0.0.1", "group": "core"})
    assert [r.url.path for r in requests] == [
        "/node/show/10.0.0.1.json",
        "/node/fetch/core/sw1",
    ]
    assert result["node"] == "sw1"


@pytest.mark.asyncio
async def test_search_configs_rejects_invalid_regex(server):
    mcp, requests = server
    with pytest.raises(Exception, match="Invalid regular expression"):
        await call(mcp, "search_configs", {"pattern": "("})
    assert requests == []


@pytest.mark.asyncio
async def test_search_configs_reports_an_unaddressable_node(make_server):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/nodes/conf_search.json":
            return httpx2.Response(
                200,
                json=[
                    # Group "a/" has an empty component and cannot be put in a path.
                    {"node": "sw1", "full_name": "a//sw1"},
                    {"node": "sw1", "full_name": "core/sw1"},
                ],
            )
        return config_handler(request)

    mcp, requests = make_server(handler)
    result = await call(mcp, "search_configs", {"pattern": "hostname"})
    assert [r["full_name"] for r in result["results"]] == ["core/sw1"]
    assert [e["full_name"] for e in result["errors"]] == ["a//sw1"]
    # Counted like a node whose fetch failed.
    assert result["nodes_searched"] == 2
    assert [r.url.path for r in requests] == [
        "/nodes/conf_search.json",
        "/node/fetch/core/sw1",
    ]


@pytest.mark.asyncio
async def test_search_configs_unaddressable_node_uses_the_node_budget(make_server):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/nodes/conf_search.json":
            return httpx2.Response(
                200,
                json=[
                    {"node": "sw1", "full_name": "a//sw1"},
                    {"node": "sw1", "full_name": "core/sw1"},
                ],
            )
        return config_handler(request)

    mcp, requests = make_server(handler)
    result = await call(mcp, "search_configs", {"pattern": "hostname", "max_nodes": 1})
    assert result["nodes_searched"] == 1
    assert result["results"] == []
    assert result["truncated"] is True
    assert [r.url.path for r in requests] == ["/nodes/conf_search.json"]
