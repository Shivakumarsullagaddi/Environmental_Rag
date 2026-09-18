"""FastMCP server providing controlled BigQuery environmental scientific knowledge retrieval."""

import asyncio
import json
import sys
import time
from pathlib import Path

# Add implementation root to sys.path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from mcp.server.fastmcp import FastMCP
from retrieval.bigquery_retriever import BigQueryRetriever

mcp = FastMCP("Environmental_BigQuery_Knowledge")
retriever = BigQueryRetriever()


def _mcp_log(tag: str, tool_name: str, **kwargs):
    """Structured stderr logger for MCP subprocess (no request_id available here — correlate via timestamp)."""
    parts = [f"[{tag}] tool_name={tool_name}"]
    for k, v in kwargs.items():
        parts.append(f"{k}={v}")
    sys.stderr.write(" ".join(parts) + "\n")
    sys.stderr.flush()


def _search_books_sync(query: str, top_k: int) -> str:
    t0 = time.time()
    _mcp_log("MCP][TOOL][START", "search_book_knowledge",
             query_preview=f"'{query[:60]}'",
             top_k=top_k,
             tables_accessed="[book_embeddings, book_chunks]",
             timestamp=time.strftime("%H:%M:%S"))
    try:
        results = retriever.search_books(query=query, top_k=top_k)
        duration_ms = (time.time() - t0) * 1000
        result_count = len(results) if results else 0
        _mcp_log("MCP][TOOL][END", "search_book_knowledge",
                 duration_ms=f"{duration_ms:.0f}",
                 success=True,
                 result_count=result_count,
                 tables_accessed="[book_embeddings, book_chunks]",
                 timestamp=time.strftime("%H:%M:%S"))
        if not results:
            return f"No textbook knowledge chunks found matching query: '{query}'."
        return json.dumps(results, indent=2)
    except Exception as e:
        duration_ms = (time.time() - t0) * 1000
        _mcp_log("MCP][TOOL][END", "search_book_knowledge",
                 duration_ms=f"{duration_ms:.0f}",
                 success=False,
                 error=str(e),
                 timestamp=time.strftime("%H:%M:%S"))
        return f"Error executing textbook knowledge retrieval: {str(e)}"


def _search_research_sync(query: str, top_k: int) -> str:
    t0 = time.time()
    _mcp_log("MCP][TOOL][START", "search_research_evidence",
             query_preview=f"'{query[:60]}'",
             top_k=top_k,
             tables_accessed="[research_embeddings, research_evidence, research_papers]",
             timestamp=time.strftime("%H:%M:%S"))
    try:
        results = retriever.search_research(query=query, top_k=top_k)
        duration_ms = (time.time() - t0) * 1000
        result_count = len(results) if results else 0
        _mcp_log("MCP][TOOL][END", "search_research_evidence",
                 duration_ms=f"{duration_ms:.0f}",
                 success=True,
                 result_count=result_count,
                 tables_accessed="[research_embeddings, research_evidence, research_papers]",
                 timestamp=time.strftime("%H:%M:%S"))
        if not results:
            return f"No peer-reviewed evidence units found matching query: '{query}'."
        return json.dumps(results, indent=2)
    except Exception as e:
        duration_ms = (time.time() - t0) * 1000
        _mcp_log("MCP][TOOL][END", "search_research_evidence",
                 duration_ms=f"{duration_ms:.0f}",
                 success=False,
                 error=str(e),
                 timestamp=time.strftime("%H:%M:%S"))
        return f"Error executing research evidence retrieval: {str(e)}"


def _get_paper_meta_sync(paper_id_or_title: str) -> str:
    t0 = time.time()
    _mcp_log("MCP][TOOL][START", "get_paper_metadata",
             paper=f"'{paper_id_or_title}'",
             tables_accessed="[research_papers, research_evidence]",
             timestamp=time.strftime("%H:%M:%S"))
    try:
        results = retriever.get_paper_metadata(paper_id_or_title=paper_id_or_title)
        duration_ms = (time.time() - t0) * 1000
        _mcp_log("MCP][TOOL][END", "get_paper_metadata",
                 duration_ms=f"{duration_ms:.0f}",
                 success=True,
                 result_count=len(results) if results else 0,
                 tables_accessed="[research_papers, research_evidence]",
                 timestamp=time.strftime("%H:%M:%S"))
        if not results:
            return f"No paper found matching '{paper_id_or_title}'."
        return json.dumps(results, indent=2)
    except Exception as e:
        duration_ms = (time.time() - t0) * 1000
        _mcp_log("MCP][TOOL][END", "get_paper_metadata",
                 duration_ms=f"{duration_ms:.0f}",
                 success=False,
                 error=str(e),
                 timestamp=time.strftime("%H:%M:%S"))
        return f"Error retrieving paper metadata: {str(e)}"


@mcp.tool()
async def search_book_knowledge(query: str, top_k: int = 5) -> str:
    """Searches foundational environmental and agricultural textbook knowledge in BigQuery.

    Use this tool for conceptual foundations, textbook definitions, soil science basics,
    climatic principles, and general ecological mechanics.
    DOES NOT return raw vectors. Returns clean scientific text, chapters, and page numbers.

    Args:
        query: Semantic search query describing the foundational concept.
        top_k: Maximum number of relevant book chunks to return (1-20, default: 5).
    """
    return await asyncio.to_thread(_search_books_sync, query, top_k)


@mcp.tool()
async def search_research_evidence(query: str, top_k: int = 5) -> str:
    """Searches peer-reviewed scientific research paper evidence in BigQuery.

    Use this tool for empirical findings, quantified effect sizes, ecological relationships,
    statistical outcomes, study locations, methods, and documented limitations.
    DOES NOT return raw vectors. Returns clean peer-reviewed findings and metadata.

    Args:
        query: Semantic search query describing the research question or relationship.
        top_k: Maximum number of relevant evidence units to return (1-20, default: 5).
    """
    return await asyncio.to_thread(_search_research_sync, query, top_k)


@mcp.tool()
async def get_paper_metadata(paper_id_or_title: str) -> str:
    """Retrieves metadata for research papers stored in the BigQuery knowledge base.

    Provides paper ID, title, authors, year, journal, DOI, study type, abstract, and
    total approved evidence units.

    Args:
        paper_id_or_title: Paper identifier (e.g. 'paper_01') or title keyword.
    """
    return await asyncio.to_thread(_get_paper_meta_sync, paper_id_or_title)


if __name__ == "__main__":
    mcp.run(transport="stdio")


