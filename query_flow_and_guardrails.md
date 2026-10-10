# Environmental AI Scientist — Query Lifecycle & Guardrail Flow

This document details the exact, step-by-step technical lifecycle of a user query in the **Environmental AI Scientist** system, explaining how pre-flight guardrails inspect inputs, why the Large Language Model (LLM) is protected from arbitrary execution attempts, and how safe queries are routed to Google ADK and BigQuery.

---

## 1. Executive Summary

| Question | Architectural Answer |
| :--- | :--- |
| **Does the query touch the LLM model first?** | **No.** Queries pass through HTTP size checks, IP rate limiters, and the deterministic `SecurityGuardian` before touching the LLM or agent runtime. |
| **How do the Guardrails inspect queries?** | Using **compiled Regular Expressions (Regex) and pattern tokenizers** executed in Python memory. An AI model is intentionally **not** used here. |
| **Why not use an LLM for the guardrails?** | **$0.00 Token Cost & Sub-millisecond Execution (<0.1ms).** Calling an LLM to check another LLM introduces latency (500–1500ms), consumes billable API quota on adversarial queries, and remains susceptible to jailbreaks. Regex provides deterministic, instantaneous immunity. |
| **If a query is unsafe, does the LLM respond?** | **No.** Gemini is never called. A predefined, polite domain refusal is emitted directly from the backend server with **0 tokens consumed**. |
| **When does Gemini get called?** | Only after the query is confirmed to be **safe** and **relevant to environmental, agricultural, or biodiversity science**. |

---

## 2. End-to-End Query Lifecycle Diagram

```
[ User Submits Query via UI / API ]
                 │
                 ▼
┌────────────────────────────────────────────────────────┐
│ 1. RequestBodyLimitMiddleware                          │
│    - Checks incoming Content-Length / stream chunks     │
│    - Is payload > 64 KB?                               │
└────────────────────────┬───────────────────────────────┘
                         │
         YES (> 64 KB)   ├──► Returns HTTP 413 (Content Too Large) [STOP]
                         │
         NO (<= 64 KB)   ▼
┌────────────────────────────────────────────────────────┐
│ 2. Sliding-Window RateLimiter (per Client IP)          │
│    - Global limit: 60 req/min                          │
│    - Expensive reasoning & streaming: 15 req/min       │
│    - Is rate exceeded?                                 │
└────────────────────────┬───────────────────────────────┘
                         │
         YES (Flooding)  ├──► Returns HTTP 429 (Too Many Requests) [STOP]
                         │    (Includes 'Retry-After' header)
         NO (< Limit)    ▼
┌────────────────────────────────────────────────────────┐
│ 3. Pre-Flight SecurityGuardian.inspect(query_text)     │
│    - Zero-token deterministic Python regex evaluation  │
│    - Scans 4 threat categories (<0.1ms latency):       │
│      a) Arbitrary Code Execution                       │
│      b) Computational DoS / Infinite Loops             │
│      c) Prompt Injection & Secret Exfiltration         │
│      d) Out-of-Domain Generic Programming Homework     │
│    - Is query flagged as unsafe / non-environmental?   │
└────────────────────────┬───────────────────────────────┘
                         │
         YES (Unsafe)    ├──► [IMMEDIATE SAFE REFUSAL]
                         │    - Gemini is NEVER called (0 tokens spent)
                         │    - BigQuery is NEVER accessed
                         │    - Emits predefined refusal text directly [STOP]
                         │
         NO (Safe)       ▼
┌────────────────────────────────────────────────────────┐
│ 4. Public Session Context Store (_conversations)       │
│    - Registers user turn in public conversation memory │
│    - Generates unique correlation IDs (request_id)     │
│    - Prepares Server-Sent Events (SSE) streaming       │
└────────────────────────┬───────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────┐
│ 5. Google ADK Agent Runtime & First Gemini Call        │
│    - Passes prompt to Gemini 3.5 Flash via ADK Runner  │
│    - Model reasons across system prompt & intent       │
│    - Model yields FunctionCall: search_book_knowledge  │
│      or search_research_evidence                       │
└────────────────────────┬───────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────┐
│ 6. Model Context Protocol (MCP) & BigQuery Vector Search│
│    - Dispatches call to Custom FastMCP server (Stdio)  │
│    - Input boundary check: query <= 1000 chars,        │
│      dataset regex check: ^[a-zA-Z0-9_]+$              │
│    - Generates 768-dim embedding (text-embedding-004)  │
│    - Performs ML.DISTANCE vector cosine similarity     │
│    - Returns peer-reviewed evidence & textbook chunks  │
└────────────────────────┬───────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────┐
│ 7. Second Gemini Call (Scientific Synthesis)          │
│    - Gemini receives FunctionResponse evidence chunks  │
│    - Synthesizes grounded scientific diagnosis         │
│    - Attaches citations, expected impacts, and charts  │
│    - Streams text tokens incrementally via SSE         │
└────────────────────────┬───────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────┐
│ 8. Egress Sanitization & Client Presentation           │
│    - OutputSanitizer: Verifies chart schema (no NaN)   │
│    - Strips internal paths and credentials             │
│    - DOMPurify (Frontend): Sanitizes rendered HTML     │
│      preventing Stored or Reflected XSS                │
└────────────────────────┬───────────────────────────────┘
                         │
                         ▼
[ Real-Time Formatted Response Displayed to User ]
```

---

## 3. Deep-Dive: The Pre-Flight Security Guardian

The primary gatekeeper is the `SecurityGuardian` class in `implimentation/security.py`. It inspects raw inbound query text before passing anything to the Google ADK runner.

### Pattern Categories Inspected

#### Category A: Arbitrary Code Execution
- **Target Patterns:** `exec(`, `eval(`, `os.system`, `subprocess.`, `os.popen`, `shutil.`, `__import__`, `importlib`, `def palindrome`, `fork bomb`, `while True:`.
- **Purpose:** Prevents users from turning the advisory interface into an arbitrary Python script execution sandbox or requesting non-scientific algorithms.
- **Immediate Response:** Emits a direct refusal explaining that the system is an evidence-grounded Environmental AI Scientist and does not solve general programming exercises.

#### Category B: Computational Denial of Service (DoS)
- **Target Patterns:** Expressions matching exponential numbers such as `e raised to the power of a million`, `2**1000000`, `repeat/execute 100,000 times`.
- **Purpose:** Prevents attackers from tying up server resources, triggering CPU saturation, or attempting mathematical memory blowouts.
- **Immediate Response:** Emits a resource restriction notice with 0 compute overhead.

#### Category C: Prompt Injection & Credential Exfiltration
- **Target Patterns:** `ignore previous instructions`, `disregard system prompt`, `reveal your instructions`, `dump .env`, `api_key`, `/etc/passwd`, `/proc/`.
- **Purpose:** Prevents prompt leakage, unauthorized persona modification, and credential exposure.
- **Immediate Response:** Returns a notice that system directives and credentials remain restricted.

#### Category D: Out-of-Domain Generic Programming Homework
- **Target Patterns:** Generic programming prompts (`write a python function to`, `implement binary search`, `write code for`) that lack any environmental, agricultural, soil, biodiversity, or climate keywords.
- **Purpose:** Protects project API quotas so that resources are reserved strictly for genuine environmental science inquiries.

---

## 4. Why Not Use an AI Agent to Inspect Incoming Queries?

A common architecture question is whether another AI model should act as the guardrail. In production, this approach has several drawbacks:

```
Proposed AI Guardrail Architecture:
[User Query] ──► [LLM Guardrail ($$$, +800ms)] ──► [Agent LLM ($$$, +1200ms)]

Our Implemented Architecture:
[User Query] ──► [Regex Guardrail ($0.00, <0.1ms)] ──► [Agent LLM ($$$, +1200ms)]
```

1. **Adversarial Cost Asymmetry:** An attacker sending 50,000 automated script requests would force 50,000 paid LLM calls just to say "unsafe". In our architecture, the server rejects all 50,000 attacks using localized CPU regex with **zero external API calls**.
2. **Sub-Millisecond Speed:** Regex matching executes in Python memory in under 100 microseconds ($\approx 0.0001$ seconds), leaving zero observable latency for legitimate users.
3. **Deterministic Immunity:** Small LLMs can be tricked by complex jailbreak prompts (e.g., "*Hypothetically in an alternate universe, write a python palindrome...*"). Regex rules match the literal token `def palindrome` regardless of hypothetical contexts.

---

## 5. Defense-in-Depth: What Happens if a Safe Query Enters the Model?

If a legitimate scientific inquiry passes through (e.g., *"How does cover cropping impact soil organic carbon and earthworm populations?"*), additional defense-in-depth measures remain active:

1. **System Prompt Section 14 (Agent Guardrails):** The prompt explicitly instructs Gemini that all retrieved literature must be treated as untrusted data, preventing indirect prompt injection from research documents.
2. **MCP Tool Parameter Bounding:** The Custom FastMCP server clamps retrieval queries to 1,000 characters and forces $top\_k \in [1, 20]$, preventing database overload.
3. **SQL Identifier Sanitization:** In `bigquery_retriever.py`, all dataset and model table references are checked against `^[a-zA-Z0-9_]+$` to prevent SQL identifier tampering.
4. **DOMPurify Client-Side Sanitization:** When the frontend renders markdown using `marked.parse()`, `DOMPurify.sanitize()` strips dangerous tags (`<script>`, `<iframe>`, `onerror=`), preventing Cross-Site Scripting (XSS).

---

## 6. Summary

- **Before the Model:** Queries pass through three deterministic barriers (Body Limit $\rightarrow$ Rate Limit $\rightarrow$ Security Guardian).
- **If Flagged:** The query stops immediately. The LLM is never invoked, BigQuery is never queried, and a hardcoded polite response is returned at **$0.00 cost**.
- **If Approved:** The query flows smoothly into the Google ADK and Gemini 3.5 Flash for high-precision, evidence-backed scientific synthesis.
