"""ADK Agent Definition for Environmental Scientist."""

import logging
import sys
from pathlib import Path

# Ensure root implementation folder is in sys.path
_pkg_root = Path(__file__).resolve().parent.parent
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))

from google.adk.agents import Agent
from config import GEMINI_MODEL
from environmental_scientist.prompt import ENVIRONMENTAL_SCIENTIST_SYSTEM_INSTRUCTION
from tools.bigquery_tools import get_bigquery_mcp_toolset
from tools.tavily_tool import get_tavily_tool

logger = logging.getLogger(__name__)

# Assemble MCP tools:
# 1. Custom FastMCP Server over Stdio for BigQuery Books & Research retrieval
# 2. Tavily MCP Server over Stdio for external web search
_tools = [get_bigquery_mcp_toolset()]
_tavily_tool = get_tavily_tool()
if _tavily_tool is not None:
    _tools.append(_tavily_tool)

# Define the root ADK agent
root_agent = Agent(
    name="environmental_scientist",
    model=GEMINI_MODEL,
    description=(
        "Evidence-grounded AI Environmental Scientist providing multi-metric scientific reasoning "
        "powered by BigQuery FastMCP vector search and Tavily web research."
    ),
    instruction=ENVIRONMENTAL_SCIENTIST_SYSTEM_INSTRUCTION,
    tools=_tools,
)
