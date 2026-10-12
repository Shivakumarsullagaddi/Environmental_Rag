"""Tavily MCP Toolset integration for external scientific web research."""

import logging
import os
from typing import Any, Optional
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from config import is_tavily_configured, TAVILY_API_KEY, get_masked_tavily_key

logger = logging.getLogger(__name__)


def get_tavily_tool() -> Optional[Any]:
    """Connects to the remote Tavily MCP server via Streamable HTTP.

    Uses Tavily's official remote MCP endpoint:
    https://mcp.tavily.com/mcp/?tavilyApiKey=TAVILY_API_KEY

    Returns:
        An ADK McpToolset instance connected to Tavily's remote MCP endpoint,
        or None if TAVILY_API_KEY is not configured.
    """
    if not is_tavily_configured():
        logger.warning("[MCP][TAVILY] TAVILY_API_KEY is not configured in .env. External web search tool disabled.")
        return None

    tavily_key = os.getenv("TAVILY_API_KEY", TAVILY_API_KEY).strip()
    remote_url = f"https://mcp.tavily.com/mcp/?tavilyApiKey={tavily_key}"

    logger.info(
        "[MCP][TAVILY] Initializing remote Tavily MCP toolset via Streamable HTTP: "
        "https://mcp.tavily.com/mcp/?tavilyApiKey=%s",
        get_masked_tavily_key(),
    )

    try:
        connection_params = StreamableHTTPConnectionParams(
            url=remote_url,
            timeout=60.0,
        )
        toolset = McpToolset(
            connection_params=connection_params,
        )
        return toolset
    except Exception as e:
        logger.error("[MCP][TAVILY] Failed to initialize Tavily remote McpToolset: %s", e)
        return None

