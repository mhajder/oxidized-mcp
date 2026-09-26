"""
Oxidized MCP Tools Package
"""

from fastmcp import FastMCP

from oxidized_mcp.models import OxidizedConfig
from oxidized_mcp.tools.configs import register_configs_tools
from oxidized_mcp.tools.nodes import register_nodes_tools
from oxidized_mcp.tools.operations import register_operations_tools
from oxidized_mcp.tools.versions import register_versions_tools


def register_tools(mcp: FastMCP, config: OxidizedConfig) -> None:
    """Register all Oxidized tools with the FastMCP server."""
    register_nodes_tools(mcp, config)
    register_configs_tools(mcp, config)
    register_versions_tools(mcp, config)
    register_operations_tools(mcp, config)
