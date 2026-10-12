# Environmental AI Scientist — End-to-End Query Lifecycle & Guardrail Flow

This document details the exact, step-by-step technical lifecycle of a user query in the **Environmental AI Scientist** system, incorporating:
1. **The 500-Word / 3,000-Character & 16 KB Network Constraints**
2. **Pre-Flight Deterministic Security Guardrails (<0.1ms latency, 0 tokens spent)**
3. **Google ADK Agent & Streamable HTTP Remote Tavily MCP Web Search**
4. **Custom FastMCP BigQuery Vector Search & Cosine Grounding**
5. **Incremental Token Streaming via Server-Sent Events (SSE)**
6. **Egress Sanitization & Client Presentation**

---

## 1. Executive Summary

| Question | Architectural Answer |
| :--- | :--- |
| **Does the query touch the LLM model first?** | **No.** Queries pass through HTTP size checks (16 KB), input validation (500 words / 3,000 chars), IP rate limiters, and the deterministic `SecurityGuardian` before touching the LLM or agent runtime. |
| **How do the Guardrails inspect queries?** | Using **compiled Regular Expressions (Regex) and word-count tokenizers** executed in local Python memory. An LLM is intentionally **not** used here. |
| **Why not use an LLM for the guardrails?** | **$0.00 Token Cost & Sub-millisecond Execution (<0.1ms).** Calling an LLM to check another LLM introduces latency (500–1500ms), wastes billable quota on attacks, and remains vulnerable to jailbreaks. Regex provides deterministic, instantaneous immunity. |
| **If a query is unsafe or oversized, does the LLM respond?** | **No.** Gemini is never called. A predefined domain refusal or 422/413 error is returned directly with **0 tokens consumed**. |
| **How are tools executed?** | **BigQuery RAG** runs via an in-process FastMCP server (Stdio). **Web Research** runs via Tavily's remote MCP server over **Streamable HTTP**. |
| **How are responses streamed?** | Live tool execution events, intermediate reasoning statuses, and Gemini 3.5 Flash synthesis tokens stream incrementally to the browser via **Server-Sent Events (SSE)**. |

---

## 2. End-to-End Query Lifecycle Diagram

```
                                  [ User Submits Query via Web UI / API ]
                                                     │
                                                     ▼
    ┌─────────────────────────────────────────────────────────────────────────────────┐
    │ 0. Frontend Live Client Guard (Browser)                                         │
    │    - Live word counter badge: displays "N / 500 words"                          │
    │    - Warns amber at >= 450 words; turns red and disables Send button at > 500   │
    └────────────────────────────────────────┬────────────────────────────────────────┘
                                             │
                                             ▼
    ┌─────────────────────────────────────────────────────────────────────────────────┐
    │ 1. RequestBodyLimitMiddleware (Network Gateway Layer)                           │
    │    - Reads incoming Content-Length header and raw stream chunk bytes            │
    │    - Is payload > 16 KB (16,384 bytes)?                                         │
    └────────────────────────────────────────┬────────────────────────────────────────┘
                                             │
              YES (> 16 KB)                  ├──► Returns HTTP 413 (Payload Too Large) [DROP & STOP]
                                             │
              NO (<= 16 KB)                  ▼
    ┌─────────────────────────────────────────────────────────────────────────────────┐
    │ 2. Sliding-Window RateLimiter (per Client IP Address)                           │
    │    - Global limit: 60 req/min                                                   │
    │    - Expensive reasoning & streaming limit: 15 req/min                          │
    │    - Is client IP rate exceeded?                                                │
    └────────────────────────────────────────┬────────────────────────────────────────┘
                                             │
              YES (Flooding)                 ├──► Returns HTTP 429 (Too Many Requests with Retry-After) [STOP]
                                             │
              NO (< Limit)                   ▼
    ┌─────────────────────────────────────────────────────────────────────────────────┐
    │ 3. Pydantic Message Length & Word Count Validation                              │
    │    - Validates message schema: min_length=1, max_length=3,000 characters        │
    │    - Is character length > 3,000 or word count > 500 words?                     │
    └────────────────────────────────────────┬────────────────────────────────────────┘
                                             │
              YES (> 500 words / 3,000 chars)├──► Returns HTTP 422 (Unprocessable Entity) [STOP]
                                             │
              NO (Within bounds)             ▼
    ┌─────────────────────────────────────────────────────────────────────────────────┐
    │ 4. Pre-Flight SecurityGuardian.inspect(query_text)                              │
    │    - Zero-token deterministic Python regex evaluation (<0.1ms latency)          │
    │    - Scans 4 threat categories:                                                 │
    │      a) Arbitrary Code Execution (exec, eval, os.system, def palindrome...)     │
    │      b) Computational DoS (e^1000000, 100,000 iteration loops...)              │
    │      c) Prompt Injection & Secret Exfiltration (ignore rules, dump .env...)     │
    │      d) Non-Environmental Programming Homework (binary search, sorting...)     │
    │    - Is query flagged as unsafe / out-of-domain?                                │
    └────────────────────────────────────────┬────────────────────────────────────────┘
                                             │
              YES (Unsafe / Refusal)         ├──► [IMMEDIATE SAFE REFUSAL]
                                             │    - Gemini is NEVER called (0 tokens spent)
                                             │    - Database is NEVER accessed
                                             │    - Emits clean domain refusal message directly [STOP]
                                             │
              NO (Safe Scientific Query)     ▼
    ┌─────────────────────────────────────────────────────────────────────────────────┐
    │ 5. Public Session Context Store & Message Registration                          │
    │    - Registers user message and assistant placeholder in public session store   │
    │    - Generates unique turn correlation ID (request_id) and timestamps           │
    │    - Opens Server-Sent Events (SSE) connection (media_type: text/event-stream)  │
    └────────────────────────────────────────┬────────────────────────────────────────┘
                                             │
                                             ▼
    ┌─────────────────────────────────────────────────────────────────────────────────┐
    │ 6. Fast Path Check (Pure Greetings / Social Chit-Chat)                          │
    │    - Is query a pure greeting (hi, hello, thanks, goodbye)?                     │
    └────────────────────────────────────────┬────────────────────────────────────────┘
                                             │
              YES (Greeting)                 ├──► Emits 1-2 sentence friendly response via SSE (0 ADK tokens) [STOP]
                                             │
              NO (Scientific Query)          ▼
    ┌─────────────────────────────────────────────────────────────────────────────────┐
    │ 7. Google ADK Agent Runner & Gemini 3.5 Flash Reasoning Turn                   │
    │    - Dispatches Content(role="user", parts=[query]) to ADK Runner               │
    │    - Model evaluates 4 compact Invariant Rules in prompt (~55 lines)            │
    │    - Model selects minimal necessary tool(s) based on query type                │
    └────────────────────────────────────────┬────────────────────────────────────────┘
                                             │
                         ┌───────────────────┴───────────────────┐
                         │                                       │
                         ▼                                       ▼
    ┌────────────────────────────────────────┐ ┌────────────────────────────────────────┐
    │ 8A. Internal Scientific Retrieval (MCP) │ │ 8B. External Web Research (Remote MCP) │
    │     - Custom FastMCP Server over Stdio │ │     - Remote Tavily MCP (Streamable    │
    │     - Generates 768-dim query embedding│ │       HTTP: https://mcp.tavily.com/mcp)│
    │       (text-embedding-004)             │ │     - Queries: tavily-search / extract │
    │     - Performs BigQuery ML.DISTANCE    │ │     - Gathers real-time web news,      │
    │       cosine vector search             │ │       recent policy targets & URL text │
    │     - Returns peer-reviewed evidence & │ └───────────────────┬────────────────────┘
    │       textbook chunks                  │                     │
    └────────────────────┬───────────────────┘                     │
                         │                                         │
                         └───────────────────┬─────────────────────┘
                                             │
                                             ▼
    ┌─────────────────────────────────────────────────────────────────────────────────┐
    │ 9. Tool Completion SSE Event Dispatch                                           │
    │    - Server yields 'tool_complete' event: tool name, duration_ms, sources       │
    │    - Right Trace Panel updates execution graph and active tool badges in real-time│
    └────────────────────────────────────────┬────────────────────────────────────────┘
                                             │
                                             ▼
    ┌─────────────────────────────────────────────────────────────────────────────────┐
    │ 10. Gemini 3.5 Flash Evidence Synthesis Turn                                    │
    │     - Gemini receives FunctionResponse parts containing retrieved evidence      │
    │     - Synthesizes rigorous, multi-variable scientific answer                    │
    │     - Attaches standardized citations: **Book**, **Research**, **External**    │
    │     - Generates structured chart JSON if quantitative comparisons are present   │
    │     - Streams incremental text tokens via SSE ('type': 'token')                 │
    └────────────────────────────────────────┬────────────────────────────────────────┘
                                             │
                                             ▼
    ┌─────────────────────────────────────────────────────────────────────────────────┐
    │ 11. Egress Output Sanitization & Client Presentation                            │
    │     - OutputSanitizer: Verifies chart data (no NaN, Infinity, mismatched lists) │
    │     - Redacts any sensitive API keys or internal database paths                 │
    │     - DOMPurify (Frontend): Sanitizes rendered markdown against XSS             │
    │     - Emits final 'done' SSE event with complete answer, sources, and latency   │
    └────────────────────────────────────────┬────────────────────────────────────────┘
                                             │
                                             ▼
                     [ Real-Time Evidence-Grounded Scientific Report Rendered ]
```

---

## 3. Defense-in-Depth: The 5 Gatekeeper Layers

### Layer 0: Browser-Level Word Counter & Form Enforcement
- **Location:** [`implimentation/static/app.js`](file:///home/shivakumarullagaddi855/agriculture_rag/implimentation/static/app.js)
- **Mechanism:** Live input event listener counts words dynamically (`\s+` regex tokenizer).
- **Behavior:**
  - Displays `<span id="word-counter">0</span> / 500 words` in the input footer.
  - At $\ge 450$ words: turns amber (`warning` state).
  - At $> 500$ words: turns red (`exceeded` state), alerts the user, and automatically disables the Submit button.

### Layer 1: Raw HTTP Payload Size Guard
- **Location:** [`implimentation/security.py`](file:///home/shivakumarullagaddi855/agriculture_rag/implimentation/security.py) (`RequestBodyLimitMiddleware`)
- **Mechanism:** Inspects the raw socket stream and `Content-Length` header before JSON deserialization.
- **Threshold:** **16 KB (16,384 bytes)**.
- **Behavior:** If a request exceeds 16 KB, the connection is aborted immediately with **HTTP 413 (Payload Too Large)**, preventing memory exhaustion and DoS attacks on Cloud Run's single-instance deployment (`concurrency = 1`).

### Layer 2: Sliding-Window IP Rate Limiting
- **Location:** [`implimentation/security.py`](file:///home/shivakumarullagaddi855/agriculture_rag/implimentation/security.py) (`RateLimiter`)
- **Mechanism:** In-memory sliding 60-second window tracking client IP addresses.
- **Limits:**
  - Global endpoints: 60 requests/minute.
  - Heavy reasoning/streaming endpoints: 15 requests/minute.
- **Behavior:** Exceeding requests receive **HTTP 429 (Too Many Requests)** with a mandatory `Retry-After: <seconds>` header.

### Layer 3: Pydantic Schema & Word Count Validation
- **Location:** [`implimentation/server.py`](file:///home/shivakumarullagaddi855/agriculture_rag/implimentation/server.py) (`MessageRequest`)
- **Limits:**
  - `min_length = 1`
  - `max_length = 3000` characters (~500 words at 6 chars/word).
- **Behavior:** Rejects oversized or malformed payloads with **HTTP 422 (Unprocessable Entity)** before invoking business logic.

### Layer 4: Pre-Flight Deterministic Security Guardian
- **Location:** [`implimentation/security.py`](file:///home/shivakumarullagaddi855/agriculture_rag/implimentation/security.py) (`SecurityGuardian`)
- **Execution Cost:** **$0.00 (Zero LLM tokens spent), <0.1ms execution time**.
- **Threat Categories Scanned:**
  1. **Arbitrary Code Execution:** Matches `exec(`, `eval(`, `os.system`, `subprocess.`, `__import__`, `def palindrome`, `fork bomb`, `while True:`.
  2. **Computational Denial of Service (DoS):** Matches `e raised to the power of a million`, `2**1000000`, `execute/loop 100,000 times`.
  3. **Prompt Injection & Secret Exfiltration:** Matches `ignore previous instructions`, `reveal system prompt`, `dump .env`, `api_key`, `/etc/passwd`.
  4. **Non-Environmental Generic Coding Homework:** Matches requests like `write a python program for binary search` that contain zero environmental/ecological keywords.
- **Behavior:** Returns `(is_safe=False, refusal_reason)`. Gemini is **never invoked**, BigQuery is **never queried**, and the user receives an immediate polite refusal.

---

## 4. Why Use Regex Instead of an AI Agent for Guardrails?

| Metric | AI Agent Guardrail (Llama-Guard / Gemini Flash) | Deterministic Regex Guardrail (Our Architecture) |
| :--- | :--- | :--- |
| **Execution Latency** | +600ms to +1,500ms added to *every* request | **<0.1ms (instantaneous CPU check)** |
| **Token Cost** | Billable API quota consumed on *every* attack | **$0.00 (Zero tokens consumed)** |
| **Adversarial Asymmetry** | An attacker sending 50,000 requests drains your billing quota | Server drops all 50,000 attacks with 0 quota impact |
| **Jailbreak Vulnerability** | Susceptible to adversarial hypothetical roleplay jailbreaks | **100% immune** to semantic jailbreaks; matches exact tokens |

---

## 5. Dual MCP Tool Architecture: BigQuery + Tavily

When a safe environmental question passes all guardrails, Google ADK coordinates two distinct Model Context Protocol (MCP) integrations:

```
                                      [ Google ADK Agent Runner ]
                                                   │
                         ┌─────────────────────────┴─────────────────────────┐
                         ▼                                                   ▼
         [ Internal Knowledge RAG ]                              [ Live External Web ]
         FastMCP Server (Stdio)                                  Remote Tavily MCP (Streamable HTTP)
         Target: BigQuery Vector DB                              Target: https://mcp.tavily.com/mcp/
         Tools:                                                  Tools:
         - search_book_knowledge                                 - tavily-search
         - search_research_evidence                              - tavily-extract
         - get_paper_metadata                                    - tavily-research
```

1. **Internal BigQuery FastMCP Server (`bq_mcp_server.py`)**:
   - Runs as a secure local subprocess communicating over JSON-RPC Stdio.
   - Computes 768-dimensional vector embeddings with Vertex AI `text-embedding-004`.
   - Executes BigQuery `ML.DISTANCE` cosine similarity queries against peer-reviewed literature and environmental textbooks.
2. **External Tavily Remote MCP Server (`tavily_tool.py`)**:
   - Connects directly to `https://mcp.tavily.com/mcp/?tavilyApiKey=...` over **Streamable HTTP**.
   - No local Node.js or `npx` dependencies required inside the Cloud Run container.
   - Provides real-time web discovery, article extraction, and policy research.

---

## 6. Output Sanitization & Safe Client Rendering

Before displaying results in the browser, the output passes through two final safety layers:
1. **`OutputSanitizer` (Python)**:
   - Validates structured chart objects: ensures labels and values have matching lengths, rejects `NaN` and `Infinity` values, and escapes HTML inside chart titles.
   - Redacts any leaked API keys (`AIza...`, `tvly-...`) or database identifiers.
2. **`DOMPurify` (Browser)**:
   - Sanitizes rendered markdown from `marked.js`.
   - Strips dangerous HTML tags (`<script>`, `<iframe>`, `onerror=`, `<svg onload=>`), preventing Reflected and Stored Cross-Site Scripting (XSS).

---

## 7. Lifecycle Summary

1. **Before the LLM:** Queries are inspected by **5 consecutive deterministic gatekeepers** (Browser Word Counter $\rightarrow$ 16 KB Body Middleware $\rightarrow$ Rate Limiter $\rightarrow$ 500-Word Schema $\rightarrow$ Security Guardian).
2. **If Blocked:** The query halts immediately at **$0.00 cost** without wasting CPU, RAM, or LLM tokens.
3. **If Approved:** The query flows to Google ADK and Gemini 3.5 Flash, which queries internal BigQuery FastMCP and remote Tavily MCP, streaming evidence-grounded scientific responses back via SSE.
