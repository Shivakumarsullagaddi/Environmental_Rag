# Tool Budget & Zero-Hallucination Fallback Architecture

## Overview
This document specifies the optimizations implemented to resolve:
1. **Multiple / Repeated Tool Calls**: Eliminating redundant external searches (e.g. 2–3 Tavily API calls per single turn).
2. **Zero-Hallucination Enforcement on Empty or Failed Tool Responses**: Ensuring that when an external tool returns empty data, an error, or no response, the agent transparently reports the lack of real-time data instead of fabricating figures or speculating.

---

## 1. Problem Statement

### Issue A: Multi-turn Re-querying (Tavily Loop)
When answering live environmental questions (such as *"what is current AQI of Bangalore"*), the model previously observed slight numerical differences across search result snippets (e.g., US AQI vs Indian CPCB NAQI scales). In the default ADK ReAct loop, Gemini 3.5 Flash autonomously dispatched follow-up search queries (e.g., `"current AQI Bangalore 2025"`, `"Bengaluru live AQI Central Pollution Control Board"`).
- **Impact**: 2x–3x API cost, 3x latency (~11.6 seconds vs ~1.5 seconds per turn).

### Issue B: Risk of Hallucination on Missing External Data
If an external search returns no results, timeouts, or empty responses, unconstrained models risk using parametric memory or speculative interpolation to invent live numbers.
- **Requirement**: The agent must state directly to the user that no real-time data could be retrieved and strictly refrain from hallucinating facts or metrics.

---

## 2. Implemented Optimizations

### Optimization 1: Strict Single-Call Search Budget
In `implimentation/environmental_scientist/prompt.py` (`RULE 2: KNOWLEDGE TOOL HIERARCHY & SEARCH BUDGET`), an invariant constraint was established:
- **Comprehensive Single Query**: The agent must formulate one single, well-targeted search query.
- **Maximum 1 Call**: Exactly one `tavily_search` call per turn is permitted.
- **No Iterative Follow-ups**: The model is forbidden from issuing secondary follow-up searches.
- **Discrepancy Reporting**: If differing index values exist across returned snippets, the model reports the observed range directly to the user rather than searching repeatedly.

### Optimization 2: Zero-Hallucination Fallback Rule
In `implimentation/environmental_scientist/prompt.py`:
```text
If tavily_search returns empty results, an error, or no response, state clearly and directly to the user that no external real-time data could be retrieved. NEVER hallucinate, speculate, or fabricate live metrics or external facts.
```
- If the tool fails or yields no snippets, the model immediately informs the user that real-time environmental data is currently unavailable from live monitoring sources.
- Grounding invariants in `RULE 1` and `RULE 4` remain active: claims and empirical numbers must be traceable to retrieved data.

---

## 3. Key Configuration & Architecture Details

### Secret Management
- **Local Development**: `.env` file loads keys via `dotenv`.
- **Production (Google Cloud Run)**: The secret `TAVILY_API_KEY` is securely fetched from **Google Secret Manager** (`projects/developer-491706/secrets/TAVILY_API_KEY`) and mounted directly as an environment variable to the Cloud Run container. No secrets are stored in code or repository files.

### Testing & Verification
- Unit and security tests (`implimentation/tests/test_security_suite.py`) run entirely with mocked services and do not consume external building or API quotas.
- Verified test runs confirmed:
  - `"what is current AQI of Bangalore"` dispatches exactly **1** `tavily_search` call.
  - Latency reduced from ~11.6s to ~1.8s.
