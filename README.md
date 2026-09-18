# Environmental AI Scientist

An evidence-grounded scientific AI assistant that reasons across soil health, biodiversity, land use, and climate dynamics to deliver peer-reviewed, multi-metric environmental guidance.

---

## Overview

The **Environmental AI Scientist** is an evidence-grounded conversational AI system engineered to emulate the diagnostic and advisory capabilities of a senior environmental scientist. Rather than operating as an ungrounded general-purpose LLM chatbot, the system bridges domain-curated scientific textbooks, peer-reviewed empirical research, and live international data via the **Model Context Protocol (MCP)** and the **Google Agent Development Kit (ADK)**.

Key system characteristics:
- **Understands Complex Environmental Inquiries:** Accurately parses domain terminology, multi-parameter site reports, and foundational queries.
- **Retrieves Grounded Scientific Knowledge:** Performs native vector similarity queries across curated textbook knowledge and peer-reviewed research papers in Google BigQuery.
- **Reasons Across Interdependent Environmental Variables:** Evaluates coupled dynamics (e.g., how soil organic carbon, pH, moisture, and tillage jointly dictate microbial and invertebrate richness).
- **Delivers Evidence-Backed Recommendations:** Produces structured interventions complete with expected impact directions, magnitudes, time horizons, and confidence ratings.
- **Maintains Multi-Turn Conversational Context:** Retains historical turns within an interactive session to clarify underspecified site scenarios and answer contextual follow-up questions.
- **Executes Supplemental Web Research:** Accesses external international policy updates, global databases, and real-time environmental reports using Tavily MCP.

> [!IMPORTANT]
> **Not an LLM-Only Chatbot:** This agent never synthesizes scientific advice in a vacuum or hallucinates domain metrics. Every response is dynamically anchored in retrieved textbook chunks, peer-reviewed meta-analyses, or verified external sources, with full citation traces surfaced in the UI.

---

## Problem

Environmental ecosystems are non-linear, interconnected systems. In agricultural landscapes, forests, and wetlands, land management decisions trigger complex cascade effects across:
- **Soil Health:** Soil organic carbon (SOC), cation-exchange capacity (CEC), pH balance, nutrient retention, and soil microbiota.
- **Biodiversity:** Epigeic and endogeic earthworms, bacterial/fungal diversity, pollinator populations, and functional habitat connectivity.
- **Land Use & Land Cover (LULC):** Crop rotation schemes, tillage regimes, buffer strips, cover cropping, and deforestation.
- **Climate Dynamics:** Drought frequency, precipitation variability, soil temperature shifts, and moisture stress.
- **Human & Management Impacts:** Synthetic chemical usage, irrigation salinization, and monoculture pressures.

**The Challenge:** Traditional agronomic and ecological advisory workflows often analyze these variables in isolation—treating soil chemistry separately from biodiversity or hydrology. Land stewards and researchers need an intelligent partner capable of synthesizing multi-metric evidence across these coupled domains to prevent unintended ecological degradation.

---

## Key Capabilities

- **Conversational Queries:** Provides crisp, accessible definitions and conceptual breakdowns for foundational concepts (e.g., SOC, biodiversity, trophic cascades).
- **Peer-Reviewed Scientific Inquiries:** Explores empirical relationships and meta-analyses (e.g., non-linear relationships between soil pH and bacterial alpha diversity).
- **Site-Specific Diagnostic Reasoning:** Accepts heterogeneous site measurements (e.g., SOC, texture, precipitation, crop history) and reasons across all variables.
- **Clarification of Incomplete Scenarios:** Automatically asks targeted follow-up questions when critical site dimensions (e.g., soil texture or tillage history) are missing.
- **Actionable Evidence-Backed Recommendations:** Outputs step-by-step management interventions with quantitative metric expectations and transparent limitations.
- **Current External Verification:** Seamlessly triggers external search tools via Tavily MCP for international agreements (e.g., Kunming-Montreal Global Biodiversity Framework) or recent regulatory targets.
- **Dynamic Visualizations:** Generates structured chart specifications (e.g., bar charts, metric comparisons) rendered directly in the chat interface.
- **Full Retrieval Observability:** Surfaces transparent tool execution traces, execution latencies, accessed BigQuery tables, and document citations in real time.

---

## Architecture

The system decouples agent orchestration, tool boundaries, knowledge persistence, and user interfaces into an enterprise-grade modular architecture:

<p align="center">
  <img src="architecture.png" alt="System Architecture" width="750" />
</p>

### End-to-End Execution Flow
1. **User Interface:** A responsive browser interface communicates with the FastAPI backend over Server-Sent Events (SSE) for sub-second, real-time response token streaming.
2. **Google ADK Orchestrator:** The `google-adk` runtime maintains session history, coordinates model turns, and governs model-tool interaction.
3. **Model Context Protocol (MCP) Boundary:** Tool execution is mediated by standardized MCP servers running over Stdio, strictly isolating database access and external API calls from the agent runtime.
4. **BigQuery Vector Search:** The custom FastMCP server delegates semantic retrieval directly to BigQuery using Google Cloud's `text-embedding-004` model.
5. **Gemini 3.5 Flash:** Synthesizes the retrieved evidence, evaluates cross-variable relationships, and streams structured, grounded answers.

<p align="center">
  <img src="knowledge_retrieval_architecture.png" alt="Knowledge & Retrieval Pipeline" width="850" />
</p>

---

## Knowledge Corpus

The internal knowledge base is hosted in Google BigQuery (`developer-491706.darukaa_kb`) and comprises two strictly segregated scientific layers:

### 1. Foundational Books Layer (615 Approved Semantic Chunks)
- *Introduction to Environmental Science*
- *Introduction to Soil Science*
- *What is Biodiversity?*
- Remote Sensing, LULC, and Drought Monitoring literature
- Environmental pollution, soil degradation, and water resource materials

### 2. Peer-Reviewed Research Evidence Layer (196 Clean Evidence Units)
- Curated empirical studies and global meta-analyses published in leading journals (*PNAS*, *Nature Communications*, *Science of The Total Environment*, *iScience*, etc.).
- Explicitly models quantitative and qualitative findings across:
  - Soil biodiversity and organic amendment responses
  - Deforestation, selective logging, and tropical canopy disruption
  - Land-use change and agricultural intensification impacts
  - Wetland biodiversity restoration and riparian buffer design
  - Soil pH gradients and bacterial vs. fungal taxon richness
  - Particulate air pollution and beneficial invertebrate communities

> [!CAUTION]
> **Corpus Scope Clarification:** The internal BigQuery knowledge base exclusively houses **textbook knowledge chunks** and **peer-reviewed scientific evidence units**. It does **not** contain raw temporal telemetry from local IoT sensors, private soil probe networks, or real-time weather stations.

---

## Data Processing

To ensure high-precision grounding without retrieval noise, raw PDF documents underwent an automated ingestion pipeline:

```
Raw PDFs ──► Layout Parsing ──► Text Cleaning ──► Semantic Chunking ──► Evidence Filtering ──► Embeddings ──► BigQuery
```

1. **Layout Parsing & Text Extraction:** Scientific PDFs were parsed with layout awareness, isolating body sections from multi-column margins.
2. **Artifact & Noise Filtering:** Reference lists, bibliographies, author affiliations, copyright notices, and publication headers were systematically identified and stripped.
3. **Semantic Chunking:** Text was segmented along natural thematic and section boundaries (optimal chunks of 400–700 tokens) to preserve contextual completeness.
4. **Structured Evidence Unit Extraction:** For research literature, empirical sections were transformed into standardized evidence units capturing variables, relationship directions, study locations, statistical methods, and observed magnitudes.
5. **Vector Generation:** Each approved unit was passed to BigQuery ML's `text-embedding-004` model, producing 768-dimensional normalized dense vectors.

---

## BigQuery Schema

The knowledge store utilizes five structured tables organized by domain layer:

```
[Book Knowledge Layer]                    [Research Evidence Layer]
   book_embeddings                           research_embeddings
         │                                            │
   (chunk_id JOIN)                             (evidence_id JOIN)
         ▼                                            ▼
    book_chunks                               research_evidence
                                                      │
                                                (paper_id JOIN)
                                                      ▼
                                               research_papers
```

| Table | Domain | Purpose | Key Fields |
| :--- | :--- | :--- | :--- |
| **`book_chunks`** | Book | Stores approved textbook semantic content and domain taxonomy. | `chunk_id`, `book_id`, `domain`, `chapter`, `section`, `page_start`, `page_end`, `content`, `topics`, `source_uri` |
| **`book_embeddings`** | Book | Stores 768-dimensional dense vectors for book chunk vector search. | `chunk_id`, `embedding` (ARRAY<FLOAT64>) |
| **`research_papers`** | Research | High-level bibliographic and methodological metadata for scientific studies. | `paper_id`, `title`, `authors`, `year`, `journal`, `doi`, `study_type`, `domain`, `abstract`, `source_uri` |
| **`research_evidence`** | Research | Discrete, standardized empirical evidence units extracted from research papers. | `evidence_id`, `paper_id`, `section`, `evidence_text`, `variables`, `relationship`, `direction`, `outcome`, `magnitude`, `study_location`, `limitations` |
| **`research_embeddings`** | Research | Stores 768-dimensional dense vectors for research evidence units. | `evidence_id`, `embedding` (ARRAY<FLOAT64>) |

*Note: Raw high-dimensional vector arrays are utilized strictly inside BigQuery vector queries and are never transmitted to the client interface.*

---

## Query Retrieval

Retrieval operates directly within Google Cloud BigQuery using native vector operations:

1. **Query Embedding:** The incoming user question is encoded into a 768-dimensional vector via `ML.GENERATE_EMBEDDING` referencing `text-embedding-004`.
2. **Cosine Similarity Calculation:** BigQuery evaluates the cosine distance across embeddings using `ML.DISTANCE(embedding, @query_vector, 'COSINE')`. Cosine similarity is computed as:
   $$\text{Similarity} = 1.0 - \text{Cosine Distance}$$
3. **Top-$k$ Ranking & Filtering:** The engine retrieves the top $k$ candidates (ranked from closest to farthest vector distance) joined against the corresponding semantic content tables (`book_chunks` or `research_evidence` + `research_papers`).
4. **Context Injection:** Formatted evidence units are passed into the model prompt context for synthesis.

*Because BigQuery handles vector indexing and distance computation natively, no external vector database (e.g., Pinecone, Milvus, Chroma) is required.*

---

## MCP Architecture

Rather than having the agent make ad-hoc API queries or direct database network connections, all external interactions are mediated through the **Model Context Protocol (MCP)**:

```
Google ADK Agent ──► ADK McpToolset ──► Stdio Pipe ──► FastMCP Server ──► BigQuery Retriever ──► BigQuery ML
```

### Why MCP Instead of Direct API Calls?
- **Explicit Tool Contracts:** Exposes strict, typed JSON Schema specifications for every tool.
- **Architectural Decoupling:** The agent reasons about *what* information it requires; the FastMCP server governs *how* the data is queried.
- **Enterprise Security Boundary:** Database credentials and BigQuery query logic remain strictly sequestered on the server side of the Stdio transport.
- **Unified Observability:** Tool invocations, durations, arguments, and result counts are intercepted and surfaced deterministically to the user.
- **Zero Lock-In:** Tool implementations can migrate from BigQuery to other backends without altering agent code.

---

## Agent & Tool Layer

Orchestrated by **Google ADK (v1.39.1)** and powered by **Gemini 3.5 Flash**, the agent selects dynamically from internal and external tools:

### Internal Knowledge Tools (Custom FastMCP Server over Stdio)
- `search_book_knowledge(query: str, top_k: int)`: Queries `book_chunks` and `book_embeddings` for fundamental environmental concepts, definitions, and agronomic principles.
- `search_research_evidence(query: str, top_k: int)`: Queries `research_evidence`, `research_embeddings`, and `research_papers` for quantitative empirical findings and meta-analyses.
- `get_paper_metadata(paper_id_or_title: str)`: Inspects complete bibliographic metadata, DOIs, abstracts, and evidence unit inventories for specific papers.

### External Research Tools (Tavily MCP Server over Stdio)
- `tavily_search(query: str)`: Conducts live web research for current international policy targets, real-time climate developments, or recent environmental updates.
- `tavily_research(query: str)`: Performs multi-source investigative web research for complex global topics.
- `tavily_extract(urls: list[str])`: Extracts clean markdown content from specific external reference URLs.

---

## External Web Research

External web search via **Tavily MCP** serves as a targeted complement—never a replacement—for the internal BigQuery knowledge base:

| Scenario | Internal BigQuery Knowledge | External Tavily MCP |
| :--- | :---: | :---: |
| Foundational principles (e.g., CEC, soil organic matter, water retention) | **Primary** | *Not Used* |
| Peer-reviewed empirical relationships (e.g., soil pH vs. bacterial richness) | **Primary** | *Not Used* |
| International treaties & policy targets (e.g., COP16, Kunming-Montreal) | *Supplemental* | **Primary** |
| Real-time regional agricultural alerts & news | *Not Present* | **Primary** |
| Deep cross-web policy investigations | *Not Present* | **Primary** |

If external web search is triggered, the agent explicitly identifies external sources in its citation block alongside internal textbook and research citations.

---

## Conversational Intelligence

The system leverages Google ADK session services to maintain continuous conversational state across complex, multi-turn troubleshooting workflows.

### Handling Incomplete Site Context
When a user provides an underspecified scenario, the agent refuses to provide generic guesses. Instead, it asks targeted diagnostic questions to collect necessary baseline metrics.

```
User:
"Biodiversity is declining on my land. What should I do?"

Agent:
"To provide an evidence-backed diagnostic and customized intervention plan, could you clarify:
 1. What is your approximate soil organic carbon (SOC) or organic matter percentage?
 2. What are your regional rainfall patterns and climate regime (e.g., semi-arid, temperate)?
 3. What is your current crop rotation and tillage practice (e.g., conventional till, no-till)?
 4. Are you experiencing specific symptoms such as compaction, erosion, or pest pressure?"

User:
"SOC is 0.3%, rainfall is low (semi-arid), continuous wheat monoculture with conventional tillage."

Agent:
[Executes search_book_knowledge & search_research_evidence]
"### Diagnostic Assessment
 In semi-arid regions, continuous wheat monoculture combined with conventional tillage accelerates organic matter oxidation, driving SOC down to 0.3% and degrading microbial community richness...

 ### Evidence-Grounded Recommendations
 1. Transition to Conservation Tillage / No-Till: Reduces soil disturbance and preserves mycorrhizal hyphal networks (supported by meta-analyses showing 20–35% recovery in invertebrate density).
 2. Integrate Leguminous Cover Crops: Introduces diverse root exudates and fixes atmospheric nitrogen during fallow periods...
 ..."
```

---

## Example Questions

### 1. Foundational Concept Questions
- *"What is biodiversity and what are its primary levels?"*
- *"Explain Cation-Exchange Capacity (CEC) and its relationship to soil organic matter."*
- *"How does soil texture influence water infiltration and holding capacity?"*

### 2. Peer-Reviewed Research Questions
- *"What does global scientific literature demonstrate regarding soil pH and bacterial taxon richness?"*
- *"How does tropical deforestation alter below-ground microbial versus macro-invertebrate communities?"*
- *"What are the empirical impacts of agricultural wetland restoration on regional avian diversity?"*

### 3. Site-Specific Diagnostic & Recommendation Questions
- *"SOC is 0.4%, soil is sandy loam, rainfall is 350mm/yr under continuous corn with heavy discing. How can I restore biological activity?"*
- *"We are converting conventional pasture to agroforestry in a sub-humid zone. What management practices maximize carbon sequestration without reducing soil moisture?"*

### 4. International Policy & Current Event Questions
- *"What are the key 2030 biodiversity targets established under the Kunming-Montreal Global Biodiversity Framework?"*
- *"Use deep search to investigate recent international policy developments on agricultural soil carbon credits."*

---

## Local Setup

### Prerequisites
- **Python:** 3.12+ installed
- **Node.js & npx:** v18+ (required for Tavily MCP Stdio execution)
- **Google Cloud SDK:** Authenticated via `gcloud auth application-default login` with BigQuery user permissions on the target dataset.

### 1. Clone the Repository
```bash
git clone https://github.com/Shivakumarsullagaddi/Environmental_Rag.git
cd Environmental_Rag
```

### 2. Configure Environment Variables
Create a `.env` file in `implimentation/.env`:
```ini
# Google Cloud & BigQuery Configuration
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=global
GOOGLE_GENAI_USE_VERTEXAI=1
BIGQUERY_LOCATION=US
BIGQUERY_DATASET=your-gcp-project-id.darukaa_kb
BIGQUERY_EMBEDDING_MODEL=your-gcp-project-id.darukaa_kb.embedding_model

# Gemini Model Selection
GEMINI_MODEL=gemini-3.5-flash

# External Research (Tavily MCP)
TAVILY_API_KEY=tvly-your-tavily-api-key
```

### 3. Install Python Dependencies
```bash
pip install --upgrade pip
pip install google-adk==1.39.1 google-genai==2.11.0 mcp==1.26.0 fastapi uvicorn anyio starlette
```

### 4. Launch the Server
```bash
cd implimentation
python3 -m uvicorn server:app --host 0.0.0.0 --port 8000
```

### 5. Access the Web Application
Open your browser and navigate to:
```
http://localhost:8000
```

---

## Security

- **Local Secret Isolation:** All API credentials, GCP project IDs, and sensitive configurations reside solely in the local `.env` file and are excluded from Git via `.gitignore`.
- **Restricted Data Exposure:** High-dimensional vector embeddings are processed natively inside BigQuery and are never transferred to or exposed in client payloads.
- **Controlled MCP Sandboxing:** Tools execute within controlled subprocesses over Stdio with rigid input validation, preventing SQL injection or arbitrary query execution.
- **Trace Redaction:** Internal model reasoning chains and system API keys are never rendered in client-facing event streams.

---

## Current Implementation Status

| Component | Status | Technology / Details |
| :--- | :---: | :--- |
| **Agent Orchestrator** | Operational | Google ADK `1.39.1` with native multi-turn tool resumption |
| **Foundation Model** | Operational | `gemini-3.5-flash` with progressive SSE token streaming |
| **Internal Vector Engine**| Operational | Google BigQuery ML (`text-embedding-004`, 768 dimensions) |
| **Tool Protocol** | Operational | Custom FastMCP server over Stdio (`bq_mcp_server.py`) |
| **External Web Research**| Operational | Tavily MCP over Stdio (`tavily-mcp` via npx) |
| **Knowledge Base** | Operational | 615 textbook chunks + 196 peer-reviewed research evidence units |
| **Multi-Turn Sessions** | Operational | In-memory session tracking with correlation IDs (`request_id`, `session_id`) |
| **Response Delivery** | Operational | Server-Sent Events (SSE) yielding real-time progressive tokens and tool status |
| **User Interface** | Operational | Responsive web interface featuring live tool badges, charts, and citations |
