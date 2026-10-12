# ADK Evaluation Framework for Environmental AI Scientist

This directory contains the Google Agent Development Kit (ADK) evaluation framework for the `Environmental_Rag` system.

---

## 1. Architecture & Testing Tiers

The evaluation framework is strictly separated into two operational tiers:

```mermaid
flowchart TD
    subgraph Tier A: Fast Local Validation [Tier A: Fast Local Validation (Offline, Zero Quota)]
        A1[validate_datasets.py] -->|ADK EvalSet Pydantic Schema| A2[Syntax & Field Validation]
        A2 --> A3[ID Uniqueness & Tool Whitelist]
        A3 --> A4[Unreviewed Draft Guard]
        A4 --> A5[Secret Redaction & Report Generator]
    end

    subgraph Tier B: Live ADK Evaluation [Tier B: Live ADK Evaluation (Explicit Opt-In)]
        B1[RUN_LIVE_ADK_EVAL=1] --> B2[google.adk.evaluation.AgentEvaluator]
        B2 --> B3[Load root_agent from environmental_scientist.agent]
        B3 --> B4[Execute Real Tools BigQuery + Tavily]
        B4 --> B5[ADK Metric Evaluation: Tool Trajectory & Response Evaluation]
    end
```

### **Tier A: Fast Local Validation (Default)**
- **Quota & Cost:** 0 LLM tokens, 0 BigQuery scans, 0 Tavily API calls.
- **Scope:**
  - Strict validation against `google.adk.evaluation.eval_set.EvalSet` and `EvalConfig`.
  - Tool name whitelist verification (`search_book_knowledge`, `search_research_evidence`, etc.).
  - Duplicate `eval_id` collision detection across all dataset files.
  - Guard against unreviewed draft promotion (`*.draft.json`).
  - Secret and credential masking tests.

### **Tier B: Live ADK Agent Evaluation (Explicit Opt-In)**
- **Gating:** Requires `RUN_LIVE_ADK_EVAL=1`.
- **Scope:** Evaluates the real production `root_agent` using ADK's built-in `AgentEvaluator`.
- **Safety:** Automatically skips when credentials or flags are missing, explicitly reporting skipped status rather than false passes.

---

## 2. Directory Layout

```text
implimentation/evaluation/
├── README.md                          <- This guide
├── datasets/                          <- Golden ADK evaluation test sets
│   ├── routing/                       <- Category A: Foundational definitions & tool trajectories
│   │   ├── routing.test.json
│   │   └── test_config.json
│   ├── scientific_quality/            <- Category B: Empirical evidence synthesis
│   │   ├── scientific_quality.test.json
│   │   └── test_config.json
│   ├── site_diagnosis/                <- Category D: Clarification on incomplete site scenarios
│   │   ├── site_diagnosis.test.json
│   │   └── test_config.json
│   ├── external_research/             <- Category E: Tavily search & single-search turn budget
│   │   ├── external_research.test.json
│   │   └── test_config.json
│   ├── evidence_gaps/                 <- Category F: Zero-hallucination & unsupported telemetry
│   │   ├── evidence_gaps.test.json
│   │   └── test_config.json
│   ├── security/                      <- Category H: Prompt injection & persona defense
│   │   ├── security.test.json
│   │   └── test_config.json
│   └── drafts/                        <- Staging area for unreviewed candidate cases
├── scripts/
│   ├── validate_datasets.py           <- Schema, integrity, and draft guard validator
│   ├── generate_candidates.py         <- Controlled candidate generation (writes to drafts/)
│   ├── run_eval.py                    <- Unified evaluation CLI for Tier A and Tier B
│   └── summarize_reports.py           <- Secret-redacting report generator
├── tests/
│   ├── test_evaluation_schemas.py     <- Tier A pytest suite (fast, offline)
│   └── test_live_adk_evaluation.py    <- Tier B pytest suite (opt-in)
└── results/                           <- Output folder for evaluation JSON & Markdown reports
```

---

## 3. Workflow & Dataset Promotion Lifecycle

1. **Candidate Generation**:
   Candidates generated via scripts are saved exclusively to `datasets/drafts/` with review status `draft`.
   ```bash
   python3 implimentation/evaluation/scripts/generate_candidates.py
   ```
2. **Review & Validation**:
   Domain experts verify scientific citations, rubrics, and source passages.
3. **Promotion**:
   Reviewed and approved cases are formatted as `.test.json` files in the corresponding golden category folder. The validator script blocks any file with `draft` in its name from entering golden folders.

---

## 4. Execution Commands

### **Fast Offline Testing (Tier A - Zero Quota)**

**Bash (Linux / macOS):**
```bash
# Run schema and integrity unit tests
pytest implimentation/evaluation/tests/test_evaluation_schemas.py

# Run dataset validator
python3 implimentation/evaluation/scripts/validate_datasets.py

# Run Tier A evaluation runner and generate report
python3 implimentation/evaluation/scripts/run_eval.py --tier tier_a
```

**PowerShell (Windows):**
```powershell
pytest implimentation/evaluation/tests/test_evaluation_schemas.py
python implimentation/evaluation/scripts/validate_datasets.py
python implimentation/evaluation/scripts/run_eval.py --tier tier_a
```

---

### **Live ADK Evaluation (Tier B - Real Agent)**

> [!WARNING]
> Live evaluation invokes real Gemini 3.5 Flash queries, BigQuery vector searches, and Tavily MCP calls. It consumes API quota and requires Google Cloud authentication.

**Bash:**
```bash
export RUN_LIVE_ADK_EVAL=1
pytest implimentation/evaluation/tests/test_live_adk_evaluation.py
```

**PowerShell:**
```powershell
$env:RUN_LIVE_ADK_EVAL="1"
pytest implimentation/evaluation/tests/test_live_adk_evaluation.py
```

**ADK CLI Native Execution:**
```bash
adk eval implimentation/environmental_scientist/agent.py implimentation/evaluation/datasets/routing/routing.test.json --print_detailed_results
```
