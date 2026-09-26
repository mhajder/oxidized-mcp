"""Tests for the backup trigger and reload tools, and read-only mode."""

import json

import httpx2
import pytest
from fastmcp import FastMCP

from oxidized_mcp.models import OxidizedConfig
from oxidized_mcp.tools import register_tools
from tests.conftest import call

WRITE_TOOLS = {"trigger_node_backup", "reload_nodes"}


def ops_handler(request: httpx2.Request) -> httpx2.Response:
    if request.url.path.startswith("/node/next/"):
        return httpx2.Response(200, json=["ok"])
    if request.url.path == "/reload.json":
        node = request.url.params.get("node")
        message = f"reloaded {node}" if node else "reloaded list of nodes"
        return httpx2.Response(200, json=[message])
    return httpx2.Response(404)


@pytest.fixture
def server(make_server):
    return make_server(ops_handler)


@pytest.mark.asyncio
async def test_trigger_backup_uses_get_without_metadata(server):
    mcp, requests = server
    result = await call(mcp, "trigger_node_backup", {"node": "sw1"})
    assert result["queued"] is True
    assert result["response"] == ["ok"]
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/node/next/sw1.json"


@pytest.mark.asyncio
async def test_trigger_backup_sends_commit_metadata(server):
    mcp, requests = server
    await call(
        mcp,
        "trigger_node_backup",
        {"node": "sw1", "message": "after change CHG-42", "user": "alice"},
    )
    assert requests[0].method == "PUT"
    assert json.loads(requests[0].content) == {
        "from": "oxidized-mcp",
        "user": "alice",
        "msg": "after change CHG-42",
    }


@pytest.mark.asyncio
async def test_trigger_backup_unknown_node(make_server):
    mcp, _ = make_server(
        lambda _request: httpx2.Response(
            500, text="<title>Oxidized::NodeNotFound at /node/next/x.json</title>"
        )
    )
    with pytest.raises(Exception, match="Node not found"):
        await call(mcp, "trigger_node_backup", {"node": "x"})


@pytest.mark.asyncio
async def test_other_server_errors_include_the_body(make_server):
    mcp, _ = make_server(lambda _request: httpx2.Response(502, text="Bad gateway"))
    with pytest.raises(Exception, match="response body: Bad gateway"):
        await call(mcp, "trigger_node_backup", {"node": "x"})


@pytest.mark.asyncio
async def test_reload_all_nodes(server):
    mcp, requests = server
    result = await call(mcp, "reload_nodes")
    assert result["message"] == "reloaded list of nodes"
    assert "node" not in requests[0].url.params


@pytest.mark.asyncio
async def test_reload_never_sends_a_node_filter(server):
    mcp, requests = server
    # /reload?node=X would replace Oxidized's whole node list with the matches
    # (verified against a live Oxidized), so the tool does not accept a node.
    with pytest.raises(Exception, match="node"):
        await call(mcp, "reload_nodes", {"node": "sw1"})
    assert requests == []


@pytest.mark.asyncio
async def test_read_only_mode_hides_write_tools():
    mcp = FastMCP(name="test")
    register_tools(mcp, OxidizedConfig(oxidized_url="https://oxidized.test"))
    all_tools = {tool.name for tool in await mcp.list_tools()}
    assert all_tools >= WRITE_TOOLS

    # Same transform server.configure_component_visibility applies.
    mcp.enable(tags={"read-only"}, only=True)
    read_only_tools = {tool.name for tool in await mcp.list_tools()}
    assert read_only_tools == all_tools - WRITE_TOOLS
    assert len(read_only_tools) == 9
