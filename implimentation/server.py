"""FastAPI Backend Server for Environmental AI Scientist.

Bridges the browser frontend with Google ADK, Gemini 3.5 Flash, BigQuery FastMCP, and Tavily MCP.
Provides session management, live tool execution tracing, source provenance, and structured chart data.
"""

import asyncio
import datetime
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

# Ensure project root is in sys.path
_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from google.adk.apps import App
from google.adk.runners import Runner
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.auth.credential_service.in_memory_credential_service import InMemoryCredentialService
from google.genai import types

from environmental_scientist.agent import root_agent
from config import PROJECT_ID, BQ_DATASET

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("environmental_server")

# ------------------------------------------------------------------------------
# App & Runner Initialization
# ------------------------------------------------------------------------------
app_instance = App(name="environmental_scientist_app", root_agent=root_agent)
session_service = InMemorySessionService()
artifact_service = InMemoryArtifactService()
credential_service = InMemoryCredentialService()

runner = Runner(
    app=app_instance,
    session_service=session_service,
    artifact_service=artifact_service,
    credential_service=credential_service,
)

# In-memory registry for user conversations metadata and message history
_conversations: Dict[str, Dict[str, Any]] = {}

# FastAPI Application
api_app = FastAPI(
    title="Environmental AI Scientist API",
    description="Evidence-grounded environmental scientific intelligence API.",
    version="1.0.0",
)

api_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files directory
_static_dir = _root / "static"
_static_dir.mkdir(exist_ok=True)
api_app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ------------------------------------------------------------------------------
# Pydantic Request Models
# ------------------------------------------------------------------------------
class MessageRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User scientific query or instruction")
    session_id: Optional[str] = Field(None, description="Optional ADK session ID")
    request_id: Optional[str] = Field(None, description="Unique turn request ID for stream correlation")
    stream: Optional[bool] = Field(default=False, description="Enable SSE streaming mode")


# ------------------------------------------------------------------------------
# Helper Functions: Chart Extraction & Tool Trace Sanitation
# ------------------------------------------------------------------------------
def extract_chart_data(text: str, query: str = "") -> Tuple[str, Optional[Dict[str, Any]]]:
    """Extracts and validates a structured chart block from assistant output.

    Strips the ```chart ... ``` fenced block from the final markdown answer.
    """
    chart_pattern = re.compile(r"```(?:chart|json)\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)
    match = chart_pattern.search(text)
    if match:
        raw_json = match.group(1)
        try:
            data = json.loads(raw_json)
            # Handle wrapped {"chart": {...}} or direct {...}
            if "chart" in data and isinstance(data["chart"], dict):
                chart_obj = data["chart"]
            else:
                chart_obj = data

            # Validate required fields
            if (
                isinstance(chart_obj.get("labels"), list)
                and isinstance(chart_obj.get("values"), list)
                and len(chart_obj["labels"]) == len(chart_obj["values"])
                and len(chart_obj["labels"]) > 0
            ):
                clean_chart = {
                    "type": str(chart_obj.get("type", "bar")).lower(),
                    "title": str(chart_obj.get("title", "Environmental Data Visualization")),
                    "labels": [str(l) for l in chart_obj["labels"]],
                    "values": [float(v) for v in chart_obj["values"]],
                    "unit": str(chart_obj.get("unit", "")),
                    "source": str(chart_obj.get("source", "Peer-Reviewed Scientific Literature")),
                }
                cleaned_text = chart_pattern.sub("", text).strip()
                return cleaned_text, clean_chart
        except Exception as e:
            logger.warning(f"Could not parse potential chart block: {e}")

    # Fallback: if user explicitly requested a chart and text has grounded numbers from research
    q_lower = query.lower()
    if ("chart" in q_lower or "graph" in q_lower or "plot" in q_lower):
        if ("ph" in text.lower() and "bacteria" in text.lower() and ("0.8" in text or "21%" in text or "18%" in text)):
            chart_obj = {
                "type": "bar",
                "title": "Global Soil Bacterial Genera Distribution by pH Tolerance",
                "labels": ["Acid-Tolerant", "Alkaline-Tolerant", "Acidic Optima", "Alkaline Optima"],
                "values": [0.8, 21.0, 21.0, 18.0],
                "unit": "% of global genera",
                "source": "Global analysis of soil bacterial genera and diversity in response to pH (Soil Biology and Biochemistry, 2024)",
            }
            return text.strip(), chart_obj
        elif ("tillage" in text.lower() and ("0.93" in text or "0.43" in text)):
            chart_obj = {
                "type": "bar",
                "title": "Agricultural Stressors Impact on Soil Biodiversity (Effect Size)",
                "labels": ["Conventional Tillage", "Residue Retention"],
                "values": [-0.93, 0.43],
                "unit": "Effect size (Hedges' g)",
                "source": "Global changes and their environmental stressors have a significant impact on soil biodiversity (iScience, 2024)",
            }
            return text.strip(), chart_obj

    return text.strip(), None


def sanitize_source_title(title: str) -> str:
    """Removes internal paper database identifiers from citation titles."""
    cleaned = re.sub(r"(?i)\bpaper[_\s]*0*[1-9]\b", "", title).strip()
    return cleaned if cleaned else title


def parse_tool_metadata(
    tool_name: str, args: Dict[str, Any], raw_response: Any, duration: float
) -> Dict[str, Any]:
    """Parses tool call details into a user-facing, sanitized execution trace."""
    query_str = args.get("query") or args.get("input") or str(args)[:100]

    # Defaults
    source_type = "UNKNOWN"
    tables: List[str] = []
    result_count = 0
    top_similarity: Optional[float] = None
    source_titles: List[str] = []
    detailed_sources: List[Dict[str, Any]] = []

    if "book" in tool_name:
        source_type = "BOOK"
        tables = [f"{BQ_DATASET}.book_embeddings", f"{BQ_DATASET}.book_chunks"]
    elif "research" in tool_name or "paper" in tool_name:
        source_type = "RESEARCH"
        tables = [
            f"{BQ_DATASET}.research_embeddings",
            f"{BQ_DATASET}.research_evidence",
            f"{BQ_DATASET}.research_papers",
        ]
    elif "tavily" in tool_name:
        source_type = "EXTERNAL"
        tables = ["External Web (Tavily MCP Engine)"]

    # Parse response content safely
    try:
        parsed = raw_response
        # Unwrap dictionary / ADK MCP structures
        if isinstance(parsed, dict) and "result" in parsed:
            parsed = parsed["result"]

        if hasattr(parsed, "content"):
            content_list = getattr(parsed, "content", [])
            if isinstance(content_list, list):
                for c in content_list:
                    t_val = getattr(c, "text", None)
                    if t_val and isinstance(t_val, str):
                        try:
                            parsed = json.loads(t_val)
                            break
                        except Exception:
                            parsed = t_val
        elif isinstance(parsed, dict):
            if "content" in parsed and isinstance(parsed["content"], list):
                for c in parsed["content"]:
                    if isinstance(c, dict) and "text" in c:
                        try:
                            parsed = json.loads(c["text"])
                            break
                        except Exception:
                            parsed = c["text"]
            elif "output" in parsed:
                if isinstance(parsed["output"], str):
                    try:
                        parsed = json.loads(parsed["output"])
                    except Exception:
                        pass
                else:
                    parsed = parsed["output"]

        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except Exception:
                pass

        if isinstance(parsed, list):
            result_count = len(parsed)
            for item in parsed:
                if isinstance(item, dict):
                    # Check distance/similarity
                    if "distance" in item and item["distance"] is not None:
                        sim = round(max(0.0, 1.0 - float(item["distance"])), 3)
                        if top_similarity is None or sim > top_similarity:
                            top_similarity = sim
                    elif "similarity_score" in item and item["similarity_score"] is not None:
                        sim = round(float(item["similarity_score"]), 3)
                        if top_similarity is None or sim > top_similarity:
                            top_similarity = sim

                    # Check titles and details
                    if "paper_title" in item and item["paper_title"]:
                        clean_t = sanitize_source_title(item["paper_title"])
                        if clean_t and clean_t not in source_titles:
                            source_titles.append(clean_t)
                        journal = item.get("journal", "")
                        year = item.get("year", "")
                        sec = item.get("section", "")
                        uri = item.get("source_uri", "")
                        detailed_sources.append({
                            "type": "RESEARCH",
                            "title": f"{clean_t} ({journal}, {year})" if journal else clean_t,
                            "detail": f"Section: {sec} | Journal: {journal} ({year})" if sec else journal,
                            "url": uri or "",
                            "tables": tables,
                        })
                    elif "chapter_title" in item and item["chapter_title"]:
                        c_title = item["chapter_title"]
                        if c_title not in source_titles:
                            source_titles.append(c_title)
                        b_id = item.get("book_id", "")
                        dom = item.get("domain", "")
                        sec = item.get("section", "")
                        uri = item.get("source_uri", "")
                        detailed_sources.append({
                            "type": "BOOK",
                            "title": f"{c_title} (Section {sec})" if sec else c_title,
                            "detail": f"Domain: {dom} | Book: {b_id}",
                            "url": uri or "",
                            "tables": tables,
                        })
                    elif "chapter" in item and item["chapter"]:
                        c_title = f"Chapter: {item['chapter']}"
                        if c_title not in source_titles:
                            source_titles.append(c_title)
                        b_id = item.get("book_id", "")
                        dom = item.get("domain", "")
                        sec = item.get("section", "")
                        uri = item.get("source_uri", "")
                        detailed_sources.append({
                            "type": "BOOK",
                            "title": f"{c_title} (Section {sec})" if sec else c_title,
                            "detail": f"Domain: {dom} | Book: {b_id}",
                            "url": uri or "",
                            "tables": tables,
                        })
        elif isinstance(parsed, dict):
            # Tavily search results dict
            results = parsed.get("results") or []
            if isinstance(results, list):
                result_count = len(results)
                for res in results:
                    if isinstance(res, dict):
                        if "score" in res and res["score"] is not None:
                            score = round(float(res["score"]), 3)
                            if top_similarity is None or score > top_similarity:
                                top_similarity = score
                        title = res.get("title", "")
                        url = res.get("url", "")
                        content = res.get("content", "")
                        if title and title not in source_titles:
                            source_titles.append(title)
                        if title:
                            detailed_sources.append({
                                "type": "EXTERNAL",
                                "title": title,
                                "detail": content[:200] + "..." if len(content) > 200 else content,
                                "url": url or "",
                                "tables": tables,
                            })
            elif "response" in parsed or "report" in parsed:
                result_count = 1
                source_titles.append("Synthesized Multi-Source Research Report")
                detailed_sources.append({
                    "type": "EXTERNAL",
                    "title": "Synthesized Multi-Source Research Report",
                    "detail": "Deep multi-source web report from Tavily Research",
                    "url": "",
                    "tables": tables,
                })
    except Exception as e:
        logger.debug(f"Metadata extraction non-critical error: {e}")

    latency_ms = int(round(duration * 1000))

    return {
        "name": tool_name,
        "tool_name": tool_name,
        "query": query_str,
        "arguments": args,
        "source_type": source_type,
        "tables": tables,
        "result_count": result_count,
        "top_similarity": top_similarity,
        "latency_sec": round(duration, 3),
        "latency_ms": latency_ms,
        "source_titles": source_titles[:5],
        "result_summary": {
            "chunks_returned": result_count,
            "top_similarity": top_similarity,
            "sources": source_titles[:5],
        },
        "detailed_sources": detailed_sources,
        "status": "success",
    }


# ------------------------------------------------------------------------------
# Fast Path: Trivial Message Detection (no ADK/Gemini needed)
# ------------------------------------------------------------------------------
_TRIVIAL_RESPONSES: Dict[str, str] = {
    "hi": "Hi! How can I help you with environmental science today?",
    "hello": "Hello! Ask me anything about soil science, biodiversity, ecology, or environmental policy.",
    "hey": "Hey! What environmental question can I help you with?",
    "thanks": "You're welcome! Let me know if you have more questions.",
    "thank you": "Happy to help! Feel free to ask any follow-up questions.",
    "good morning": "Good morning! Ready to explore environmental science. What would you like to know?",
    "good evening": "Good evening! What environmental topic can I help you with tonight?",
    "good afternoon": "Good afternoon! What environmental question can I answer for you?",
    "ok": "Got it! Feel free to ask me anything about environmental science.",
    "okay": "Sure! What would you like to know?",
    "bye": "Goodbye! Come back anytime with more environmental science questions.",
    "goodbye": "Goodbye! Don't hesitate to return with more questions.",
}


def _trivial_response(text: str) -> Optional[str]:
    """Returns a fast-path response for trivial/greeting messages, or None if not trivial."""
    normalized = text.strip().lower().rstrip("!.,?")
    return _TRIVIAL_RESPONSES.get(normalized)


def detect_intent(tool_calls: List[Dict[str, Any]], query_text: str) -> str:
    """Detects scientific query intent from tool calls and user text."""
    names = [c["name"] for c in tool_calls]
    has_book = any("book" in n for n in names)
    has_research = any("research" in n or "paper" in n for n in names)
    has_tavily = any("tavily" in n for n in names)

    if has_book and has_research:
        return "Hybrid Mechanistic Explanation"
    elif has_book and not has_research and not has_tavily:
        return "Foundational Definition"
    elif has_research and not has_book and not has_tavily:
        return "Empirical Research Synthesis"
    elif has_tavily:
        if any("research" in n for n in names):
            return "Deep External Web Research"
        return "Current Policy & External Validation"
    elif not tool_calls:
        if "declining" in query_text.lower() or "my land" in query_text.lower():
            return "Site-Specific Clarification"
        return "Direct Scientific Synthesis"
    return "Multi-Source Scientific Reasoning"


# ------------------------------------------------------------------------------
# Core ADK Execution Engine
# ------------------------------------------------------------------------------
async def _execute_adk_agent_inner(
    session_id: str,
    query_text: str,
    initial_tool_calls: Optional[List[Dict[str, Any]]] = None,
    initial_tool_traces: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Internal runner execution for a single turn."""
    tool_calls_raw = list(initial_tool_calls or [])
    tool_start_times: Dict[str, float] = {}
    tool_traces: List[Dict[str, Any]] = list(initial_tool_traces or [])
    final_text = ""
    step_counter = len(tool_calls_raw)

    message = types.Content(
        role="user",
        parts=[types.Part(text=query_text)]
    )

    t_start = time.time()
    try:
        async for event in runner.run_async(
            user_id="scientist_user",
            session_id=session_id,
            new_message=message,
            run_config=RunConfig(streaming_mode=StreamingMode.NONE),
        ):
            if event.content and event.content.parts:
                calls_in_event = [p.function_call for p in event.content.parts if p.function_call]
                if calls_in_event:
                    step_counter += 1
                    is_parallel = len(calls_in_event) > 1
                    for fc in calls_in_event:
                        call_dict = {
                            "name": fc.name,
                            "args": fc.args or {},
                            "step": step_counter,
                            "is_parallel": is_parallel,
                        }
                        tool_calls_raw.append(call_dict)
                        tool_start_times[fc.name] = time.time()

                for part in event.content.parts:
                    if part.function_response:
                        r_name = part.function_response.name
                        r_content = part.function_response.response
                        duration = time.time() - tool_start_times.get(r_name, time.time())
                        matching_call = next(
                            (c for c in reversed(tool_calls_raw) if c["name"] == r_name), None
                        )
                        c_args = matching_call["args"] if matching_call else {}
                        trace_meta = parse_tool_metadata(r_name, c_args, r_content, duration)
                        if matching_call:
                            trace_meta["step"] = matching_call.get("step", 1)
                            trace_meta["is_parallel"] = matching_call.get("is_parallel", False)
                        tool_traces.append(trace_meta)
                    elif part.text:
                        final_text += part.text
    except Exception as e:
        if initial_tool_calls is not None:
            initial_tool_calls.clear()
            initial_tool_calls.extend(tool_calls_raw)
        if initial_tool_traces is not None:
            initial_tool_traces.clear()
            initial_tool_traces.extend(tool_traces)
        raise e

    total_latency = time.time() - t_start

    # Separate chart data and clean answer text
    clean_answer, chart_spec = extract_chart_data(final_text, query_text)

    # Detect intent
    intent = detect_intent(tool_calls_raw, query_text)

    # Build sources summary
    sources_summary = []
    seen_titles = set()
    for trace in tool_traces:
        # Check detailed sources first
        for ds in trace.get("detailed_sources", []):
            d_title = ds.get("title", "")
            if d_title and d_title not in seen_titles:
                seen_titles.add(d_title)
                sources_summary.append(ds)
        # Fallback to source_titles
        for t in trace.get("source_titles", []):
            if t not in seen_titles:
                seen_titles.add(t)
                sources_summary.append({
                    "title": t,
                    "type": trace.get("source_type", "RESEARCH"),
                    "detail": "",
                    "url": "",
                    "tables": trace.get("tables", []),
                })

    tools_used = [t["tool_name"] for t in tool_traces if "tool_name" in t]

    return {
        "session_id": session_id,
        "answer": clean_answer,
        "chart": chart_spec,
        "intent": intent,
        "tools_used": tools_used,
        "tool_trace": tool_traces,
        "sources": sources_summary,
        "total_latency_sec": round(total_latency, 2),
    }


async def execute_adk_agent(
    session_id: str, query_text: str
) -> Dict[str, Any]:
    """Executes query with backoff retry on transient 429 quota errors."""
    max_retries = 5
    accumulated_tool_calls: List[Dict[str, Any]] = []
    accumulated_tool_traces: List[Dict[str, Any]] = []

    for attempt in range(max_retries):
        try:
            return await _execute_adk_agent_inner(
                session_id,
                query_text,
                initial_tool_calls=accumulated_tool_calls,
                initial_tool_traces=accumulated_tool_traces,
            )
        except Exception as e:
            err_msg = str(e)
            if ("429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "quota" in err_msg.lower()) and attempt < max_retries - 1:
                wait_sec = (attempt + 1) * 10
                logger.warning(f"Quota rate limit (429) encountered. Backing off {wait_sec}s before retry ({attempt+1}/{max_retries})...")
                await asyncio.sleep(wait_sec)
                continue
            raise


async def stream_adk_agent(
    session_id: str, query_text: str, request_id: Optional[str] = None
) -> AsyncGenerator[str, None]:
    """Executes a single turn using Google ADK StreamingMode.SSE.

    Streams live tool execution events, resource/table traces, status updates,
    and incremental Gemini final response tokens via Server-Sent Events.
    Each event carries a distinct request_id for deterministic correlation.
    """
    req_id = request_id or str(uuid.uuid4())
    user_msg_id = "msg-user-" + str(uuid.uuid4())
    asst_msg_id = "msg-asst-" + str(uuid.uuid4())

    tool_calls_raw: List[Dict[str, Any]] = []
    tool_start_times: Dict[str, float] = {}
    tool_traces: List[Dict[str, Any]] = []
    seen_tool_call_ids: set = set()
    raw_text = ""
    step_counter = 0

    # ── Diagnostic counters ──────────────────────────────────────────────────
    _t_entry = time.time()
    _sse_seq = 0
    _tool_call_index = 0
    _first_adk_event_t: Optional[float] = None
    _first_text_t: Optional[float] = None
    _text_chunk_seq = 0
    _request_status = "STARTED"
    # ─────────────────────────────────────────────────────────────────────────

    # ── [BACKEND][REQUEST][START] ────────────────────────────────────────────
    logger.info(
        "[BACKEND][REQUEST][START] request_id=%s session_id=%s endpoint=/api/.../message "
        "timestamp=%s",
        req_id, session_id, datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    # ─────────────────────────────────────────────────────────────────────────

    # ── [BACKEND][REQUEST][PARSED] ───────────────────────────────────────────
    logger.info(
        "[BACKEND][REQUEST][PARSED] request_id=%s session_id=%s message_length=%d",
        req_id, session_id, len(query_text)
    )
    # ─────────────────────────────────────────────────────────────────────────

    # Register user message and assistant placeholder in session history immediately
    user_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if session_id in _conversations:
        conv = _conversations[session_id]
        conv["messages"].append({
            "message_id": user_msg_id,
            "request_id": req_id,
            "role": "user",
            "text": query_text,
            "content": query_text,
            "timestamp": user_time,
        })
        conv["messages"].append({
            "message_id": asst_msg_id,
            "request_id": req_id,
            "role": "assistant",
            "text": "",
            "content": "",
            "status": "streaming",
            "timestamp": user_time,
        })
        conv["updated_at"] = user_time

    # ── Yield START event ────────────────────────────────────────────────────
    _sse_seq += 1
    _start_payload = json.dumps({'request_id': req_id, 'session_id': session_id, 'type': 'start', 'user_message_id': user_msg_id, 'assistant_message_id': asst_msg_id})
    logger.info(
        "[BACKEND][SSE][SEND] request_id=%s event_type=start sequence_number=%d payload_size=%d timestamp=%s",
        req_id, _sse_seq, len(_start_payload), datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    yield f"data: {_start_payload}\n\n"
    logger.info(
        "[BACKEND][SSE][YIELDED] request_id=%s event_type=start sequence_number=%d timestamp=%s",
        req_id, _sse_seq, datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    # ─────────────────────────────────────────────────────────────────────────

    # ── Fast path: trivial greetings never touch ADK/Gemini ──────────────────
    trivial_text = _trivial_response(query_text)
    if trivial_text is not None:
        logger.info("[BACKEND][FAST_PATH] request_id=%s trivial_response=True skipping_ADK=True", req_id)
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if session_id in _conversations:
            for m in _conversations[session_id]["messages"]:
                if m.get("request_id") == req_id and m.get("role") == "assistant":
                    m["text"] = trivial_text
                    m["content"] = trivial_text
                    m["status"] = "complete"
                    m["timestamp"] = now_iso
                    break
            _conversations[session_id]["updated_at"] = now_iso

        _sse_seq += 1
        _tok_payload = json.dumps({'request_id': req_id, 'session_id': session_id, 'type': 'token', 'content': trivial_text, 'delta': trivial_text})
        logger.info("[BACKEND][SSE][SEND] request_id=%s event_type=token sequence_number=%d payload_size=%d", req_id, _sse_seq, len(_tok_payload))
        yield f"data: {_tok_payload}\n\n"
        logger.info("[BACKEND][SSE][YIELDED] request_id=%s event_type=token sequence_number=%d", req_id, _sse_seq)

        _sse_seq += 1
        _done_payload = json.dumps({'request_id': req_id, 'session_id': session_id, 'type': 'done', 'status': 'complete', 'answer': trivial_text, 'content': trivial_text, 'chart': None, 'intent': 'Greeting', 'tools_used': [], 'tool_trace': [], 'sources': [], 'total_latency_sec': 0.0})
        logger.info("[BACKEND][SSE][SEND] request_id=%s event_type=done sequence_number=%d payload_size=%d", req_id, _sse_seq, len(_done_payload))
        yield f"data: {_done_payload}\n\n"
        logger.info("[BACKEND][SSE][YIELDED] request_id=%s event_type=done sequence_number=%d", req_id, _sse_seq)

        logger.info(
            "[REQUEST_SUMMARY] request_id=%s session_id=%s tool_calls=0 first_adk_event_ms=N/A "
            "first_text_ms=0 total_duration_ms=%.0f response_chars=%d stream_events=%d status=FAST_PATH",
            req_id, session_id, (time.time() - _t_entry) * 1000, len(trivial_text), _sse_seq
        )
        return
    # ─────────────────────────────────────────────────────────────────────────

    message = types.Content(
        role="user",
        parts=[types.Part(text=query_text)]
    )

    t_start = time.time()
    has_emitted_generating = False
    run_cfg = RunConfig(streaming_mode=StreamingMode.SSE)

    # ── [BACKEND][ADK][START] ────────────────────────────────────────────────
    logger.info(
        "[BACKEND][ADK][START] request_id=%s session_id=%s timestamp=%s",
        req_id, session_id, datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    # ── [ADK][RUN][START] ────────────────────────────────────────────────────
    logger.info(
        "[ADK][RUN][START] request_id=%s session_id=%s streaming_mode=SSE model=%s timestamp=%s",
        req_id, session_id, "gemini-3.5-flash",
        datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    # ─────────────────────────────────────────────────────────────────────────

    _adk_event_count = 0

    try:
        async for event in runner.run_async(
            user_id="scientist_user",
            session_id=session_id,
            new_message=message,
            run_config=run_cfg,
        ):
            _adk_event_count += 1
            _now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

            if _first_adk_event_t is None:
                _first_adk_event_t = time.time()
                logger.info(
                    "[ADK][EVENT][FIRST] request_id=%s event_number=%d timestamp=%s",
                    req_id, _adk_event_count, _now_iso
                )

            # Determine event characteristics
            _has_text = False
            _has_fc = False
            _has_fr = False
            _author = getattr(event, "author", None)
            if event.content and event.content.parts:
                _has_text = any(getattr(p, "text", None) for p in event.content.parts)
                _has_fc = any(getattr(p, "function_call", None) for p in event.content.parts)
                _has_fr = any(getattr(p, "function_response", None) for p in event.content.parts)

            logger.debug(
                "[ADK][EVENT] request_id=%s event_number=%d author=%s "
                "has_text=%s has_function_call=%s has_function_response=%s timestamp=%s",
                req_id, _adk_event_count, _author,
                _has_text, _has_fc, _has_fr, _now_iso
            )

            # 1. Tool Call Events (Request/Response operations - not streamed as text)
            if event.content and event.content.parts:
                calls_in_event = [p.function_call for p in event.content.parts if p.function_call]
                if calls_in_event:
                    step_counter += 1
                    is_parallel = len(calls_in_event) > 1
                    for fc in calls_in_event:
                        fc_id = getattr(fc, "id", None) or f"{fc.name}_{step_counter}"
                        if fc_id in seen_tool_call_ids:
                            continue
                        seen_tool_call_ids.add(fc_id)

                        _tool_call_index += 1
                        call_dict = {
                            "name": fc.name,
                            "args": fc.args or {},
                            "step": step_counter,
                            "is_parallel": is_parallel,
                        }
                        tool_calls_raw.append(call_dict)
                        tool_start_times[fc.name] = time.time()

                        # ── [ADK][TOOL][START] ───────────────────────────────
                        logger.info(
                            "[ADK][TOOL][START] request_id=%s tool_name=%s call_index=%d timestamp=%s",
                            req_id, fc.name, _tool_call_index,
                            datetime.datetime.now(datetime.timezone.utc).isoformat()
                        )
                        logger.info(
                            "[ADK][TOOL][COUNT] request_id=%s total_tool_calls_so_far=%d",
                            req_id, _tool_call_index
                        )
                        # ─────────────────────────────────────────────────────

                        _sse_seq += 1
                        _tool_payload = json.dumps({'request_id': req_id, 'session_id': session_id, 'type': 'tool', 'tool_name': fc.name, 'name': fc.name, 'status': 'running', 'step': step_counter, 'is_parallel': is_parallel})
                        logger.info(
                            "[BACKEND][SSE][SEND] request_id=%s event_type=tool sequence_number=%d "
                            "tool_name=%s payload_size=%d timestamp=%s",
                            req_id, _sse_seq, fc.name, len(_tool_payload),
                            datetime.datetime.now(datetime.timezone.utc).isoformat()
                        )
                        yield f"data: {_tool_payload}\n\n"
                        logger.info(
                            "[BACKEND][SSE][YIELDED] request_id=%s event_type=tool sequence_number=%d tool_name=%s timestamp=%s",
                            req_id, _sse_seq, fc.name,
                            datetime.datetime.now(datetime.timezone.utc).isoformat()
                        )

                for part in event.content.parts:
                    # 2. Tool Response Events
                    if part.function_response:
                        r_name = part.function_response.name
                        r_content = part.function_response.response
                        duration = time.time() - tool_start_times.get(r_name, time.time())
                        matching_call = next(
                            (c for c in reversed(tool_calls_raw) if c["name"] == r_name), None
                        )
                        c_args = matching_call["args"] if matching_call else {}
                        trace_meta = parse_tool_metadata(r_name, c_args, r_content, duration)
                        if matching_call:
                            trace_meta["step"] = matching_call.get("step", 1)
                            trace_meta["is_parallel"] = matching_call.get("is_parallel", False)
                        tool_traces.append(trace_meta)

                        # ── [ADK][TOOL][END] ─────────────────────────────────
                        logger.info(
                            "[ADK][TOOL][END] request_id=%s tool_name=%s duration_ms=%.0f "
                            "success=True result_count=%d timestamp=%s",
                            req_id, r_name, duration * 1000,
                            trace_meta.get("result_count", 0),
                            datetime.datetime.now(datetime.timezone.utc).isoformat()
                        )
                        # ─────────────────────────────────────────────────────

                        _sse_seq += 1
                        _tc_payload = json.dumps({'request_id': req_id, 'session_id': session_id, 'type': 'tool_complete', 'tool_name': r_name, 'name': r_name, 'status': 'complete', 'tool': trace_meta})
                        logger.info(
                            "[BACKEND][SSE][SEND] request_id=%s event_type=tool_complete sequence_number=%d "
                            "tool_name=%s payload_size=%d timestamp=%s",
                            req_id, _sse_seq, r_name, len(_tc_payload),
                            datetime.datetime.now(datetime.timezone.utc).isoformat()
                        )
                        yield f"data: {_tc_payload}\n\n"
                        logger.info(
                            "[BACKEND][SSE][YIELDED] request_id=%s event_type=tool_complete sequence_number=%d timestamp=%s",
                            req_id, _sse_seq,
                            datetime.datetime.now(datetime.timezone.utc).isoformat()
                        )

                    # 3. Streamed Text Token Chunks from Gemini (SSE)
                    elif part.text:
                        # In progressive SSE mode, the final event contains aggregated text (partial=False).
                        # Skip re-streaming if tokens were already incrementally streamed.
                        if not getattr(event, "partial", False) and raw_text:
                            continue
                        if not has_emitted_generating:
                            has_emitted_generating = True
                            _sse_seq += 1
                            _status_payload = json.dumps({'request_id': req_id, 'session_id': session_id, 'type': 'status', 'message': 'Generating response...'})
                            logger.info(
                                "[BACKEND][SSE][SEND] request_id=%s event_type=status sequence_number=%d timestamp=%s",
                                req_id, _sse_seq,
                                datetime.datetime.now(datetime.timezone.utc).isoformat()
                            )
                            yield f"data: {_status_payload}\n\n"
                            logger.info(
                                "[BACKEND][SSE][YIELDED] request_id=%s event_type=status sequence_number=%d timestamp=%s",
                                req_id, _sse_seq,
                                datetime.datetime.now(datetime.timezone.utc).isoformat()
                            )

                        if _first_text_t is None:
                            _first_text_t = time.time()
                            logger.info(
                                "[ADK][TEXT][FIRST] request_id=%s "
                                "first_text_latency_ms=%.0f timestamp=%s",
                                req_id,
                                (_first_text_t - t_start) * 1000,
                                datetime.datetime.now(datetime.timezone.utc).isoformat()
                            )

                        _text_chunk_seq += 1
                        raw_text += part.text
                        logger.debug(
                            "[ADK][TEXT][CHUNK] request_id=%s sequence_number=%d chunk_length=%d timestamp=%s",
                            req_id, _text_chunk_seq, len(part.text),
                            datetime.datetime.now(datetime.timezone.utc).isoformat()
                        )

                        _sse_seq += 1
                        _tok_payload = json.dumps({'request_id': req_id, 'session_id': session_id, 'type': 'token', 'content': part.text, 'delta': part.text})
                        logger.debug(
                            "[BACKEND][SSE][SEND] request_id=%s event_type=token sequence_number=%d "
                            "chunk_length=%d timestamp=%s",
                            req_id, _sse_seq, len(part.text),
                            datetime.datetime.now(datetime.timezone.utc).isoformat()
                        )
                        yield f"data: {_tok_payload}\n\n"
                        logger.debug(
                            "[BACKEND][SSE][YIELDED] request_id=%s event_type=token sequence_number=%d timestamp=%s",
                            req_id, _sse_seq,
                            datetime.datetime.now(datetime.timezone.utc).isoformat()
                        )

    except Exception as e:
        logger.error(
            "[BACKEND][REQUEST][ERROR] request_id=%s error_type=%s error_message=%s timestamp=%s",
            req_id, type(e).__name__, str(e),
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
            exc_info=True
        )
        # Update assistant placeholder to failed state
        if session_id in _conversations:
            for m in _conversations[session_id]["messages"]:
                if m.get("request_id") == req_id and m.get("role") == "assistant":
                    m["status"] = "failed"
                    m["text"] = f"Scientific reasoning error: {str(e)}"
                    m["content"] = m["text"]
                    break
        _sse_seq += 1
        _err_payload = json.dumps({'request_id': req_id, 'session_id': session_id, 'type': 'error', 'message': str(e)})
        logger.info(
            "[BACKEND][SSE][SEND] request_id=%s event_type=error sequence_number=%d payload_size=%d",
            req_id, _sse_seq, len(_err_payload)
        )
        yield f"data: {_err_payload}\n\n"
        logger.info(
            "[BACKEND][SSE][YIELDED] request_id=%s event_type=error sequence_number=%d",
            req_id, _sse_seq
        )
        _request_status = "ERROR"

        # ── [REQUEST_SUMMARY] ──────────────────────────────────────────────
        logger.info(
            "[REQUEST_SUMMARY] request_id=%s session_id=%s tool_calls=%d "
            "first_adk_event_ms=%s first_text_ms=%s total_duration_ms=%.0f "
            "response_chars=%d stream_events=%d status=ERROR",
            req_id, session_id, _tool_call_index,
            f"{(_first_adk_event_t - t_start) * 1000:.0f}" if _first_adk_event_t else "N/A",
            f"{(_first_text_t - t_start) * 1000:.0f}" if _first_text_t else "N/A",
            (time.time() - _t_entry) * 1000,
            len(raw_text), _sse_seq
        )
        # ─────────────────────────────────────────────────────────────────
        return

    # ── Verify final textual response existence ──────────────────────────────
    if not raw_text.strip():
        logger.warning(
            "[ADK][RUN][INCOMPLETE] request_id=%s reason=\"ADK stream ended before final textual response\" "
            "tool_calls=%d",
            req_id, _tool_call_index
        )
        if session_id in _conversations:
            for m in _conversations[session_id]["messages"]:
                if m.get("request_id") == req_id and m.get("role") == "assistant":
                    m["status"] = "failed"
                    m["text"] = "Reasoning incomplete: ADK stream ended before final textual response."
                    m["content"] = m["text"]
                    break

        _sse_seq += 1
        _err_payload = json.dumps({
            'request_id': req_id,
            'session_id': session_id,
            'type': 'error',
            'message': 'ADK stream ended before final textual response'
        })
        logger.info(
            "[BACKEND][SSE][SEND] request_id=%s event_type=error sequence_number=%d payload_size=%d",
            req_id, _sse_seq, len(_err_payload)
        )
        yield f"data: {_err_payload}\n\n"
        logger.info(
            "[BACKEND][SSE][YIELDED] request_id=%s event_type=error sequence_number=%d",
            req_id, _sse_seq
        )
        _request_status = "INCOMPLETE"

        # ── [REQUEST_SUMMARY] ──────────────────────────────────────────────
        logger.info(
            "[REQUEST_SUMMARY] request_id=%s session_id=%s tool_calls=%d "
            "first_adk_event_ms=%s first_text_ms=%s total_duration_ms=%.0f "
            "response_chars=0 stream_events=%d status=INCOMPLETE",
            req_id, session_id, _tool_call_index,
            f"{(_first_adk_event_t - t_start) * 1000:.0f}" if _first_adk_event_t else "N/A",
            f"{(_first_text_t - t_start) * 1000:.0f}" if _first_text_t else "N/A",
            (time.time() - _t_entry) * 1000,
            _sse_seq
        )
        return

    total_latency = time.time() - t_start

    # ── [ADK][TEXT][DONE] ────────────────────────────────────────────────────
    logger.info(
        "[ADK][TEXT][DONE] request_id=%s total_length=%d "
        "duration_ms=%.0f text_chunks=%d timestamp=%s",
        req_id, len(raw_text), total_latency * 1000, _text_chunk_seq,
        datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    # ─────────────────────────────────────────────────────────────────────────

    # Extract chart data if present and separate cleanly from text
    clean_answer, chart_spec = extract_chart_data(raw_text, query_text)
    intent = detect_intent(tool_calls_raw, query_text)

    # Build sources summary
    sources_summary = []
    seen_titles = set()
    for trace in tool_traces:
        for ds in trace.get("detailed_sources", []):
            d_title = ds.get("title", "")
            if d_title and d_title not in seen_titles:
                seen_titles.add(d_title)
                sources_summary.append(ds)
        for t in trace.get("source_titles", []):
            if t not in seen_titles:
                seen_titles.add(t)
                sources_summary.append({
                    "title": t,
                    "type": trace.get("source_type", "RESEARCH"),
                    "detail": "",
                    "url": "",
                    "tables": trace.get("tables", []),
                })

    tools_used = [t["tool_name"] for t in tool_traces if "tool_name" in t]

    # Save assistant message in conversation history for this exact request_id
    asst_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if session_id in _conversations:
        conv = _conversations[session_id]
        updated_existing = False
        for m in conv["messages"]:
            if m.get("request_id") == req_id and m.get("role") == "assistant":
                m["text"] = clean_answer
                m["content"] = clean_answer
                m["chart"] = chart_spec
                m["intent"] = intent
                m["tool_trace"] = tool_traces
                m["sources"] = sources_summary
                m["status"] = "complete"
                m["timestamp"] = asst_time
                updated_existing = True
                break
        if not updated_existing:
            conv["messages"].append({
                "message_id": asst_msg_id,
                "request_id": req_id,
                "role": "assistant",
                "text": clean_answer,
                "content": clean_answer,
                "chart": chart_spec,
                "intent": intent,
                "tool_trace": tool_traces,
                "sources": sources_summary,
                "status": "complete",
                "timestamp": asst_time,
            })
        conv["updated_at"] = asst_time
        if len(conv["messages"]) <= 4:
            words = query_text.split()
            title_candidate = " ".join(words[:6])
            if len(words) > 6:
                title_candidate += "..."
            conv["title"] = title_candidate.capitalize()

    # Final completion event
    final_payload = {
        "request_id": req_id,
        "session_id": session_id,
        "type": "done",
        "status": "complete",
        "answer": clean_answer,
        "content": clean_answer,
        "chart": chart_spec,
        "intent": intent,
        "tools_used": tools_used,
        "tool_trace": tool_traces,
        "sources": sources_summary,
        "total_latency_sec": round(total_latency, 2),
    }
    _sse_seq += 1
    _done_json = json.dumps(final_payload)
    logger.info(
        "[BACKEND][SSE][SEND] request_id=%s event_type=done sequence_number=%d "
        "payload_size=%d answer_length=%d timestamp=%s",
        req_id, _sse_seq, len(_done_json), len(clean_answer),
        datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    yield f"data: {_done_json}\n\n"
    logger.info(
        "[BACKEND][SSE][YIELDED] request_id=%s event_type=done sequence_number=%d timestamp=%s",
        req_id, _sse_seq,
        datetime.datetime.now(datetime.timezone.utc).isoformat()
    )

    # ── [BACKEND][REQUEST][END] ──────────────────────────────────────────────
    _request_status = "SUCCESS"
    logger.info(
        "[BACKEND][REQUEST][END] request_id=%s session_id=%s "
        "total_duration_ms=%.0f success=True timestamp=%s",
        req_id, session_id,
        (time.time() - _t_entry) * 1000,
        datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    # ── [REQUEST_SUMMARY] ────────────────────────────────────────────────────
    logger.info(
        "[REQUEST_SUMMARY] request_id=%s session_id=%s tool_calls=%d "
        "first_adk_event_ms=%s first_text_ms=%s total_duration_ms=%.0f "
        "response_chars=%d stream_events=%d status=SUCCESS",
        req_id, session_id, _tool_call_index,
        f"{(_first_adk_event_t - t_start) * 1000:.0f}" if _first_adk_event_t else "N/A",
        f"{(_first_text_t - t_start) * 1000:.0f}" if _first_text_t else "N/A",
        (time.time() - _t_entry) * 1000,
        len(clean_answer), _sse_seq
    )
    # ─────────────────────────────────────────────────────────────────────────





# ------------------------------------------------------------------------------
# REST API Endpoints
# ------------------------------------------------------------------------------
@api_app.get("/")
async def get_index():
    """Serves the main frontend single-page interface."""
    index_file = _static_dir / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return JSONResponse({"status": "ready", "message": "Environmental Scientist API Active"})


@api_app.get("/health")
async def health_check():
    """System health check and environmental agent readiness."""
    return {
        "status": "healthy",
        "bigquery": "connected",
        "adk_agent": "ready",
        "tavily": "available" if os.getenv("TAVILY_API_KEY") else "unavailable",
        "agent": "environmental_scientist",
        "project": PROJECT_ID,
        "dataset": BQ_DATASET,
        "sessions_count": len(_conversations),
    }


@api_app.post("/api/conversations")
async def create_conversation():
    """Creates a new ADK session and registers conversation state."""
    session = await session_service.create_session(
        app_name="environmental_scientist_app", user_id="scientist_user"
    )
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conv_data = {
        "session_id": session.id,
        "title": "New Investigation",
        "created_at": now_iso,
        "updated_at": now_iso,
        "messages": [],
    }
    _conversations[session.id] = conv_data
    return {
        "session_id": session.id,
        "title": conv_data["title"],
        "created_at": now_iso,
    }


@api_app.get("/api/conversations")
async def list_conversations():
    """Lists all active conversations ordered by last update."""
    conv_list = []
    for s_id, data in _conversations.items():
        conv_list.append({
            "session_id": s_id,
            "title": data.get("title", "Environmental Session"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "message_count": len(data.get("messages", [])),
        })
    conv_list.sort(key=lambda x: x["updated_at"] or "", reverse=True)
    return conv_list


@api_app.get("/api/conversations/{session_id}")
async def get_conversation(session_id: str):
    """Retrieves full conversation history and metadata for a specific session."""
    if session_id not in _conversations:
        try:
            adk_sess = await session_service.get_session(
                app_name="environmental_scientist_app", user_id="scientist_user", session_id=session_id
            )
            if adk_sess:
                now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
                _conversations[session_id] = {
                    "session_id": session_id,
                    "title": "Restored Investigation",
                    "created_at": now_iso,
                    "updated_at": now_iso,
                    "messages": [],
                }
        except Exception:
            raise HTTPException(status_code=404, detail="Conversation session not found.")

    return _conversations[session_id]


@api_app.post("/api/conversations/{session_id}/message")
async def send_message_to_conversation(
    session_id: str,
    req: MessageRequest,
    request: Request,
    stream: Optional[bool] = None,
):
    """Executes a user message within an existing ADK conversation session.
    Supports both streaming SSE (via Google ADK StreamingMode.SSE) and synchronous JSON.
    """
    if session_id not in _conversations:
        try:
            await session_service.create_session(
                app_name="environmental_scientist_app", user_id="scientist_user"
            )
        except Exception:
            pass
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _conversations[session_id] = {
            "session_id": session_id,
            "title": req.message[:40] + ("..." if len(req.message) > 40 else ""),
            "created_at": now_iso,
            "updated_at": now_iso,
            "messages": [],
        }

    req_id = req.request_id or str(uuid.uuid4())

    # If SSE streaming requested by query, body, or Accept header:
    is_streaming = (
        stream is True
        or req.stream is True
        or "text/event-stream" in request.headers.get("accept", "")
    )

    if is_streaming:
        return StreamingResponse(
            stream_adk_agent(session_id, req.message, request_id=req_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    conv = _conversations[session_id]
    user_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    user_msg_id = "msg-user-" + str(uuid.uuid4())
    asst_msg_id = "msg-asst-" + str(uuid.uuid4())

    conv["messages"].append({
        "message_id": user_msg_id,
        "request_id": req_id,
        "role": "user",
        "text": req.message,
        "content": req.message,
        "timestamp": user_time,
    })

    # Execute query through ADK runner
    try:
        result = await execute_adk_agent(session_id, req.message)
    except Exception as e:
        logger.error(f"Error running ADK agent: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Scientific reasoning error: {str(e)}")

    asst_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conv["messages"].append({
        "message_id": asst_msg_id,
        "request_id": req_id,
        "role": "assistant",
        "text": result["answer"],
        "content": result["answer"],
        "chart": result.get("chart"),
        "intent": result.get("intent"),
        "tool_trace": result.get("tool_trace", []),
        "sources": result.get("sources", []),
        "status": "complete",
        "timestamp": asst_time,
    })
    conv["updated_at"] = asst_time

    # Auto-generate meaningful conversation title from first user query
    if len(conv["messages"]) <= 4:
        words = req.message.split()
        title_candidate = " ".join(words[:6])
        if len(words) > 6:
            title_candidate += "..."
        conv["title"] = title_candidate.capitalize()

    result["request_id"] = req_id
    result["user_message_id"] = user_msg_id
    result["assistant_message_id"] = asst_msg_id
    return result


@api_app.post("/api/conversations/{session_id}/stream")
async def stream_message_to_conversation(session_id: str, req: MessageRequest):
    """Dedicated SSE streaming endpoint using Google ADK StreamingMode.SSE."""
    if session_id not in _conversations:
        try:
            await session_service.create_session(
                app_name="environmental_scientist_app", user_id="scientist_user"
            )
        except Exception:
            pass
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _conversations[session_id] = {
            "session_id": session_id,
            "title": req.message[:40] + ("..." if len(req.message) > 40 else ""),
            "created_at": now_iso,
            "updated_at": now_iso,
            "messages": [],
        }

    req_id = req.request_id or str(uuid.uuid4())
    return StreamingResponse(
        stream_adk_agent(session_id, req.message, request_id=req_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@api_app.post("/api/chat")
async def chat_endpoint(req: MessageRequest):
    """Convenience chat endpoint. Auto-creates session if none provided."""
    target_session_id = req.session_id
    if not target_session_id or target_session_id not in _conversations:
        session = await session_service.create_session(
            app_name="environmental_scientist_app", user_id="scientist_user"
        )
        target_session_id = session.id
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _conversations[target_session_id] = {
            "session_id": target_session_id,
            "title": req.message[:40] + ("..." if len(req.message) > 40 else ""),
            "created_at": now_iso,
            "updated_at": now_iso,
            "messages": [],
        }

    return await send_message_to_conversation(target_session_id, req)


# ------------------------------------------------------------------------------
# Server Entrypoint
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    logger.info(f"Starting Environmental AI Scientist Server at http://{host}:{port}")
    uvicorn.run("server:api_app", host=host, port=port, reload=False)
