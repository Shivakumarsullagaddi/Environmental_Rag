"""Controlled BigQuery retrieval tools via FastMCP for the Environmental Scientist Agent."""

import logging
import os
import sys
from pathlib import Path
from typing import Any, List
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from mcp import StdioServerParameters

logger = logging.getLogger(__name__)


def get_bigquery_mcp_toolset() -> McpToolset:
    """Creates an ADK McpToolset connecting to the custom FastMCP BigQuery server via stdio.

    Architecture:
    ADK -> McpToolset -> Stdio (FastMCP) -> bq_mcp_server.py -> bigquery_retriever.py -> BigQuery
    """
    pkg_root = Path(__file__).resolve().parent.parent
    server_path = pkg_root / "mcp_servers" / "bq_mcp_server.py"

    env_vars = dict(os.environ)
    if "PYTHONPATH" in env_vars:
        env_vars["PYTHONPATH"] = f"{pkg_root}:{env_vars['PYTHONPATH']}"
    else:
        env_vars["PYTHONPATH"] = str(pkg_root)

    connection_params = StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=[str(server_path)],
            env=env_vars,
        ),
        timeout=120.0,
    )

    toolset = McpToolset(
        connection_params=connection_params,
        tool_filter=["search_book_knowledge", "search_research_evidence", "get_paper_metadata"],
    )
    return toolset


def get_bigquery_tools() -> List[Any]:
    """Returns the FastMCP toolset for BigQuery retrieval."""
    return [get_bigquery_mcp_toolset()]
