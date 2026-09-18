# Environmental Scientist — Google ADK Prototype

The **Environmental Scientist** is an evidence-grounded scientific AI agent built using the **Google Agent Development Kit (ADK)**, **Gemini 3.5 Flash**, **FastMCP**, and **Tavily MCP**.

It interfaces with:
1. **Curated BigQuery Knowledge Base (`developer-491706.darukaa_kb`)**:
   - **Books Layer**: `book_chunks`, `book_embeddings` (615 chunks, foundational environmental & soil science).
   - **Research Evidence Layer**: `research_papers`, `research_evidence`, `research_embeddings` (196 clean evidence units, global meta-analyses from PNAS, iScience, STOTEN, Nature Communications, etc.).
   - **Vector Search**: Semantic vector search via `text-embedding-004` directly in BigQuery ML (`ML.GENERATE_EMBEDDING` and `ML.DISTANCE`).
2. **FastMCP Server (`mcp_servers/bq_mcp_server.py`)**:
   - Implements the MCP standard over Stdio. Exposes controlled retrieval tools: `search_book_knowledge`, `search_research_evidence`, and `get_paper_metadata`.
   - Reuses query embeddings across searches and calculates explicit cosine distances and similarities.
3. **External Web Research (Tavily MCP)**:
   - Connected via ADK's `McpToolset` over Stdio using `npx -y tavily-mcp`.
   - Real live web search tool for current international targets, environmental policies, and external verification.
   - Gracefully falls back when API key is not yet provided.

---

## 1. Directory Structure

```
implementation/
├── .env                              # Secure configuration (protected by .gitignore)
├── .gitignore                         # Excludes secrets, pycache, and session files
├── README.md                          # Project documentation
├── config.py                          # Unified configuration loader
├── environmental_scientist/           # ADK Agent Directory
│   ├── __init__.py                    # Exports root_agent for ADK AgentLoader
│   ├── agent.py                       # Root ADK LlmAgent definition (FastMCP + Tavily MCP)
│   └── prompt.py                      # Multi-metric, evidence-grounded scientific prompt
├── retrieval/                         # Core BigQuery RAG Engine
│   ├── __init__.py
│   └── bigquery_retriever.py          # BigQuery vector search with embedding reuse
├── tools/                             # Modular Tool Definitions
│   ├── __init__.py
│   ├── bigquery_tools.py              # FastMCP Toolset wrapper (stdio)
│   └── tavily_tool.py                 # Tavily McpToolset wrapper & fallback
├── mcp_servers/                       # Standalone MCP Servers
│   ├── __init__.py
│   └── bq_mcp_server.py               # FastMCP server for BigQuery retrieval
└── tests/                             # Verification & Test Suite
    ├── test_environmental_scientist.py # Automated runner for the 6 evaluation tests
    └── test_replay.json               # Replay configuration for adk run
```

---

## 2. Environment Variables (`.env`)

The project uses a local `.env` file for configuration. **Secrets are never committed or printed in logs.**

```ini
# Google Cloud Project & BigQuery Knowledge Base
GOOGLE_CLOUD_PROJECT=developer-491706
GOOGLE_CLOUD_LOCATION=global
GOOGLE_GENAI_USE_VERTEXAI=1
BIGQUERY_LOCATION=US
BIGQUERY_DATASET=developer-491706.darukaa_kb
BIGQUERY_EMBEDDING_MODEL=developer-491706.darukaa_kb.embedding_model

# Gemini LLM Configuration
# Exactly gemini-3.5-flash
GEMINI_MODEL=gemini-3.5-flash

# Tavily External Web Research MCP API Key
# Replace the placeholder below with your actual Tavily API key from https://app.tavily.com
TAVILY_API_KEY=<VALUE WILL BE PROVIDED BY USER>
```

---

## 3. Running the Agent

### Option A: Interactive CLI with `adk run`
Navigate to the `implementation` directory and launch the agent:
```bash
cd /home/shivakumarullagaddi855/agriculture_rag/implementation
adk run environmental_scientist
```
Type any environmental or scientific question at the prompt (e.g. *"How does deforestation impact soil fungal communities?"*). Type `exit` to quit.

### Option B: Non-Interactive Evaluation with `--replay`
Run an automated query sequence using a JSON replay file:
```bash
cd /home/shivakumarullagaddi855/agriculture_rag/implementation
adk run environmental_scientist --replay tests/test_replay.json
```

### Option C: Web UI with `adk web`
Start the FastAPI web server with built-in chat UI:
```bash
cd /home/shivakumarullagaddi855/agriculture_rag/implementation
adk web --host 127.0.0.1 --port 8080 .
```
Then open your browser at:
`http://127.0.0.1:8080`

### Option D: Automated 6-Test Suite
To execute the comprehensive verification test suite:
```bash
cd /home/shivakumarullagaddi855/agriculture_rag/implementation
python3 tests/test_environmental_scientist.py
```

---

## 4. Controlled Knowledge Retrieval (BigQuery FastMCP)

The agent does **not** have arbitrary database access. It accesses curated knowledge through three controlled tools exposed via our custom FastMCP server:

1. `search_book_knowledge(query, top_k)`:
   - Vectors matched against `developer-491706.darukaa_kb.book_embeddings`.
   - Returns book title, domain, chapter, section, page numbers, chunk text, and similarity score.
   - Raw embedding vectors are **strictly hidden** from the LLM.
2. `search_research_evidence(query, top_k)`:
   - Vectors matched against `developer-491706.darukaa_kb.research_embeddings`.
   - Returns paper title, journal, year, section, page numbers, evidence text, environmental variables, relationship, direction, outcome, magnitude, study location, study design, and limitations.
3. `get_paper_metadata(paper_id_or_title)`:
   - Retrieves paper details, DOI, authors, abstract, and approved evidence counts.

---

## 5. Structured Response Standard

All substantive responses from the agent adhere to the mandatory structure:
- **Assessment**: What the peer-reviewed evidence and textbooks state.
- **Recommendation**: Concrete, scientifically sound management interventions.
- **Why**: The ecological and biophysical mechanisms underlying the recommendation.
- **Impacted Metrics**: Specific environmental variables affected.
- **Evidence**: Distinct citations for internal book chunks, research evidence units, and external web evidence.
- **Source Type**: Categorized as internal book knowledge, research paper evidence, or external web research.
- **Time Horizon**: Immediate, short-term (<1 yr), medium-term (1-5 yrs), or long-term (>5 yrs).
- **Limitations**: Missing data, regional bias, or observational constraints.
- **Confidence**: High / Medium / Low with explicit scientific justification.

---

## 6. Session Management Architecture

- **In-Process Session State**: Multi-turn conversation state is managed via ADK's `InMemorySessionService` as **in-process session state** (not durable disk/database persistence).
- **Context Preservation**: Passing the same ADK `session_id` preserves conversational context, enabling the agent to resolve pronouns and perform comparative analysis without redundant database retrieval.

---

## 7. Environmental Measurement Policy

- **Curated Internal Knowledge**: The BigQuery knowledge base contains textbooks and peer-reviewed research papers; it contains **no** sensor-level environmental measurement datasets (such as plot soil pH, soil moisture, or station rainfall).
- **External Verification**: The agent does not automatically refuse measurement questions. It first determines whether reliable measurements can be retrieved via Tavily.
- **Strict Grounding**: If verified externally, it returns the value cited as **External Web Evidence**. If no reliable source is found, it states that the measurement could not be verified. Under no circumstances does the agent fabricate a measurement.
