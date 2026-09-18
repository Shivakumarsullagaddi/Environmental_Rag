"""Tavily MCP Toolset integration for external scientific web research."""

import logging
import os
import shutil
from typing import Any
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from mcp import StdioServerParameters
from config import is_tavily_configured, TAVILY_API_KEY, get_masked_tavily_key

logger = logging.getLogger(__name__)


def _create_tavily_fallback_tool():
    """Returns fallback tools when TAVILY_API_KEY is not yet provided by the user."""
    def tavily_search(query: str) -> str:
        """Searches external web sources for recent scientific studies, reports, or data."""
        return (
            "External web research is currently unavailable because TAVILY_API_KEY is not "
            "configured in .env. Please provide a valid Tavily API key from https://app.tavily.com "
            "in the .env file. Foundational textbook and peer-reviewed research evidence in "
            "BigQuery remain fully accessible."
        )
    return tavily_search


def get_tavily_tool() -> Any:
    """Connects to the Tavily MCP server or provides a graceful fallback.

    Returns:
        An ADK McpToolset instance connected via Stdio if configured,
        or a helpful fallback function tool explaining key setup.
    """
    if not is_tavily_configured():
        logger.info("Tavily MCP: TAVILY_API_KEY not set or placeholder. Using fallback tool.")
        return _create_tavily_fallback_tool()

    npx_path = shutil.which("npx") or "/usr/local/nvm/versions/node/v24.20.0/bin/npx"
    path_env = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    if "/usr/local/nvm/versions/node/v24.20.0/bin" not in path_env:
        path_env = f"/usr/local/nvm/versions/node/v24.20.0/bin:{path_env}"

    logger.info(f"Connecting to Tavily MCP server via {npx_path} (API Key: {get_masked_tavily_key()})")

    tavily_key = os.getenv("TAVILY_API_KEY", TAVILY_API_KEY).strip()
    try:
        connection_params = StdioConnectionParams(
            server_params=StdioServerParameters(
                command=npx_path,
                args=["-y", "tavily-mcp"],
                env={
                    "TAVILY_API_KEY": tavily_key,
                    "PATH": path_env,
                },
            ),
            timeout=120.0,
        )
        toolset = McpToolset(
            connection_params=connection_params,
            tool_filter=["tavily_search", "tavily_research", "tavily_extract"],
        )
        return toolset
    except Exception as e:
        logger.error(f"Failed to initialize Tavily McpToolset: {e}")
        return _create_tavily_fallback_tool()
