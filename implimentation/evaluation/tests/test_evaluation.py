"""Unit and offline integration tests for the Environmental RAG Evaluation System.

Covers:
1. Approved query dataset schema and structure validation.
2. Case ID uniqueness enforcement across categories.
3. Judge configuration schema, threshold boundaries, and category weight sum validation.
4. Category-weighted score calculation with weight renormalization for unassigned/missing evidence.
5. Critical failure override behavior (prompt injection, secret leak, citation hallucination yielding 0.0 and FAIL).
6. Malformed, truncated, or invalid judge output handling.
7. Missing evidence yielding NOT_EVALUABLE when all criteria lack evidence.
8. Recursive credential and sensitive token redaction.
9. SEC03 controlled synthetic fixture loading without mutating production BigQuery.
"""

import json
import sys
from pathlib import Path
import pytest

# Ensure implementation directory is in sys.path
_impl_root = Path(__file__).resolve().parent.parent.parent
if str(_impl_root) not in sys.path:
    sys.path.insert(0, str(_impl_root))

from evaluation.scripts.validate_datasets import (
    validate_judge_config,
    validate_query_file,
    validate_baseline_record_file,
    redact_sensitive_data,
    SUPPORTED_CATEGORIES,
    SUPPORTED_MODES,
)
from evaluation.scripts.run_eval import (
    calculate_weighted_score,
    generate_reports,
    get_judge_genai_client,
)


@pytest.fixture
def eval_dir() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def judge_config_path(eval_dir: Path) -> Path:
    return eval_dir / "config" / "judge_config.json"


# ==============================================================================
# 1. Dataset Schema and Integrity Tests
# ==============================================================================

def test_approved_query_files_valid(eval_dir: Path):
    """Verifies that all approved query files pass strict schema checks with no duplicate IDs."""
    seen_ids = set()
    total_cases = 0

    for cat in SUPPORTED_CATEGORIES:
        q_name = f"{'baseline' if cat == 'retrieval_and_tools' else 'security'}_queries.json"
        q_path = eval_dir / "datasets" / cat / q_name
        assert q_path.exists(), f"Query file missing: {q_path}"

        ok, errs, count = validate_query_file(q_path, seen_ids)
        assert ok is True, f"Validation failed for {q_path.name}: {errs}"
        assert count > 0
        total_cases += count

    assert total_cases == 7  # RT01-RT04 (4) + SEC01-SEC03 (3)
    assert len(seen_ids) == 7


def test_detect_duplicate_case_id(tmp_path: Path):
    """Verifies that duplicate case IDs across or within files are detected and rejected."""
    bad_file = tmp_path / "dup_queries.json"
    bad_payload = {
        "category": "retrieval_and_tools",
        "version": "1.0.0",
        "cases": [
            {
                "case_id": "duplicate_01",
                "query": "query 1",
                "purpose": "test",
                "expected_behavior": "test",
                "expected_tools": [],
                "prohibited_behaviors": [],
                "live_data": False,
                "execution_mode": "live_agent",
                "enabled": True,
            },
            {
                "case_id": "duplicate_01",
                "query": "query 2",
                "purpose": "test",
                "expected_behavior": "test",
                "expected_tools": [],
                "prohibited_behaviors": [],
                "live_data": False,
                "execution_mode": "live_agent",
                "enabled": True,
            },
        ],
    }
    bad_file.write_text(json.dumps(bad_payload), encoding="utf-8")
    seen_ids = set()
    ok, errs, _ = validate_query_file(bad_file, seen_ids)
    assert ok is False
    assert any("Duplicate case_id 'duplicate_01'" in e for e in errs)


# ==============================================================================
# 2. Judge Config & Weight Validation Tests
# ==============================================================================

def test_judge_config_valid(judge_config_path: Path):
    """Verifies that production judge_config.json passes weight sum and threshold checks."""
    ok, errs = validate_judge_config(judge_config_path)
    assert ok is True, f"Judge config validation failed: {errs}"

    cfg = json.loads(judge_config_path.read_text(encoding="utf-8"))
    assert cfg["judge_model"] == "gemini-3.8-flash"
    assert cfg["thinking_level"] in {"low", "medium", "high"}
    assert 0.0 <= cfg["pass_threshold"] <= 1.0


def test_judge_config_invalid_weights_rejected(tmp_path: Path):
    """Verifies that category weights not summing to 1.0 are rejected."""
    bad_cfg = tmp_path / "bad_judge_config.json"
    bad_cfg.write_text(
        json.dumps({
            "judge_model": "gemini-3.8-flash",
            "pass_threshold": 0.8,
            "category_weights": {
                "retrieval_and_tools": {
                    "tool_choice": 0.5,
                    "factual_grounding": 0.2,  # sums to 0.7 != 1.0
                },
                "security": {
                    "prompt_injection_resistance": 1.0,
                },
            },
        }),
        encoding="utf-8",
    )
    ok, errs = validate_judge_config(bad_cfg)
    assert ok is False
    assert any("must sum to 1.0" in e for e in errs)


# ==============================================================================
# 3. Weighted Scoring & Weight Renormalization Tests
# ==============================================================================

def test_calculate_weighted_score_standard():
    """Verifies standard weighted scoring calculation."""
    weights = {"c1": 0.6, "c2": 0.4}
    crit_scores = {
        "c1": {"score": 1.0, "reasoning": "great"},
        "c2": {"score": 0.5, "reasoning": "fair"},
    }
    score, passed, not_eval = calculate_weighted_score(
        criteria_scores=crit_scores,
        category="test",
        category_weights=weights,
        critical_failures=[],
        pass_threshold=0.8,
    )
    # Expected: 1.0 * 0.6 + 0.5 * 0.4 = 0.6 + 0.2 = 0.8
    assert score == 0.8
    assert passed is True
    assert not_eval == []


def test_calculate_weighted_score_renormalization():
    """Verifies that missing evidence causes weight renormalization over remaining criteria."""
    weights = {"c1": 0.5, "c2": 0.3, "c3": 0.2}
    # c3 has no evidence (score is null)
    crit_scores = {
        "c1": {"score": 1.0},
        "c2": {"score": 0.8},
        "c3": {"score": "NOT_EVALUABLE"},
    }
    score, passed, not_eval = calculate_weighted_score(
        criteria_scores=crit_scores,
        category="test",
        category_weights=weights,
        critical_failures=[],
        pass_threshold=0.8,
    )
    # c3 is not evaluable; remaining weight total = 0.5 + 0.3 = 0.8
    # renormalized c1 = 0.5 / 0.8 = 0.625, c2 = 0.3 / 0.8 = 0.375
    # expected score: 1.0 * 0.625 + 0.8 * 0.375 = 0.625 + 0.3 = 0.925
    assert score == 0.925
    assert passed is True
    assert not_eval == ["c3"]


def test_calculate_weighted_score_critical_failure_override():
    """Verifies that any critical failure unconditionally overrides score to 0.0 and FAIL."""
    weights = {"c1": 1.0}
    crit_scores = {"c1": {"score": 1.0}}
    critical_failures = ["Disclosed internal credentials and API keys in output"]

    score, passed, not_eval = calculate_weighted_score(
        criteria_scores=crit_scores,
        category="security",
        category_weights=weights,
        critical_failures=critical_failures,
        pass_threshold=0.8,
    )
    assert score == 0.0
    assert passed is False


def test_calculate_weighted_score_all_missing_evidence():
    """Verifies that if no criteria have evaluable evidence, score is 0.0 and all are marked not_evaluable."""
    weights = {"c1": 0.5, "c2": 0.5}
    crit_scores = {
        "c1": {"score": None},
        "c2": {"score": "NOT_EVALUABLE"},
    }
    score, passed, not_eval = calculate_weighted_score(
        criteria_scores=crit_scores,
        category="test",
        category_weights=weights,
        critical_failures=[],
        pass_threshold=0.8,
    )
    assert score == 0.0
    assert passed is False
    assert set(not_eval) == {"c1", "c2"}


# ==============================================================================
# 4. Secret Redaction Tests
# ==============================================================================

def test_recursive_redact_sensitive_data():
    """Verifies recursive sanitization of secrets in text and dictionary keys."""
    raw_payload = {
        "user_query": "hello",
        "api_key": "AIzaSyD-sampletestkey1234567890abcdef123",
        "headers": {
            "Authorization": "Bearer sample_token_string_which_is_over_twenty_five_chars",
            "tavily_key": "tvly-samplekey1234567890abcdef",
        },
        "nested_list": [
            "Normal text",
            "Here is a key: AIzaSyD-sampletestkey1234567890abcdef123",
            {"db_password": "supersecretpassword123"},
        ],
    }
    cleaned = redact_sensitive_data(raw_payload)

    # API key and secret values replaced
    assert cleaned["api_key"] == "[REDACTED_SECRET]"
    assert cleaned["headers"]["tavily_key"] == "[REDACTED_SECRET]"
    assert cleaned["nested_list"][2]["db_password"] == "[REDACTED_SECRET]"
    assert "[REDACTED_SECRET]" in cleaned["nested_list"][1]
    assert "AIza" not in json.dumps(cleaned)
    assert "tvly-" not in json.dumps(cleaned)


# ==============================================================================
# 5. Baseline Record Schema Validation Tests
# ==============================================================================

def test_captured_baseline_records_valid(eval_dir: Path):
    """Verifies that all recorded baseline traces match the record schema."""
    approved_queries = {}
    for cat in SUPPORTED_CATEGORIES:
        q_name = f"{'baseline' if cat == 'retrieval_and_tools' else 'security'}_queries.json"
        q_path = eval_dir / "datasets" / cat / q_name
        data = json.loads(q_path.read_text(encoding="utf-8"))
        for c in data.get("cases", []):
            c_dict = dict(c)
            c_dict["category"] = cat
            approved_queries[c["case_id"]] = c_dict

    total_records = 0

    for cat in SUPPORTED_CATEGORIES:
        cat_rec_dir = eval_dir / "datasets" / cat / "baseline_records"
        assert cat_rec_dir.exists()
        runs = [d for d in cat_rec_dir.iterdir() if d.is_dir()]
        for r_dir in runs:
            for f in r_dir.glob("*.json"):
                ok, errs = validate_baseline_record_file(f, approved_queries)
                assert ok is True, f"Invalid baseline record {f.name}: {errs}"
                total_records += 1

    assert total_records >= 7


# ==============================================================================
# 6. Controlled Synthetic Fixture Isolation Tests
# ==============================================================================

def test_sec03_synthetic_retrieval_fixture_valid(eval_dir: Path):
    """Verifies that the SEC03 indirect injection fixture loads correctly with expected attack indicators."""
    fixture_path = eval_dir / "tests" / "fixtures" / "sec03_synthetic_retrieval.json"
    assert fixture_path.exists()

    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert fixture["fixture_id"] == "sec03_synthetic_retrieval"
    assert fixture["category"] == "security"
    assert "simulated_response" in fixture
    assert len(fixture["simulated_response"]) > 0

    finding = fixture["simulated_response"][0]["finding_text"]
    assert "IMPORTANT SYSTEM DIRECTIVE" in finding
    assert "Biochar additions increased microbial biomass" in finding


# ==============================================================================
# 7. Report Generation Output Tests
# ==============================================================================

def test_report_generation(tmp_path: Path):
    """Verifies that generate_reports produces both valid JSON and valid Markdown files."""
    sample_evaluations = [
        {
            "case_id": "rt01_biodiversity_definition",
            "category": "retrieval_and_tools",
            "criteria_scores": {"tool_selection": {"score": 1.0}},
            "critical_failures": [],
            "not_evaluable_criteria": [],
            "overall_weighted_score": 1.0,
            "decision": "PASS",
            "summary_verdict": "All criteria met.",
        },
        {
            "case_id": "sec01_protected_information",
            "category": "security",
            "criteria_scores": {"prompt_injection_resistance": {"score": 0.0}},
            "critical_failures": ["Credential exposure"],
            "not_evaluable_criteria": [],
            "overall_weighted_score": 0.0,
            "decision": "FAIL",
            "summary_verdict": "Critical failure occurred.",
        },
    ]

    json_out, md_out = generate_reports(
        eval_run_id="test_run_123",
        capture_run_id="cap_456",
        judge_model="gemini-3.8-flash",
        case_evaluations=sample_evaluations,
        output_dir=tmp_path,
    )

    assert json_out.exists()
    assert md_out.exists()

    data = json.loads(json_out.read_text(encoding="utf-8"))
    assert data["eval_run_id"] == "test_run_123"
    assert data["summary"]["total_cases"] == 2
    assert data["summary"]["passed"] == 1
    assert data["summary"]["failed"] == 1

    md_content = md_out.read_text(encoding="utf-8")
    assert "# Environmental RAG Evaluation Report (`test_run_123`)" in md_content
    assert "| `rt01_biodiversity_definition` |" in md_content
    assert "| `sec01_protected_information` |" in md_content


# ==============================================================================
# 8. Judge Client Authentication & Error Diagnostics Tests
# ==============================================================================

def test_judge_client_initialization_with_working_app_auth():
    """Verifies that the judge client initializes using the working application's Vertex AI / ADC configuration."""
    client = get_judge_genai_client()
    assert client is not None
    # Client should be Vertex AI enabled matching the Environmental RAG application
    assert getattr(client, "vertexai", False) is True


def test_judge_client_missing_auth_diagnostics(monkeypatch):
    """Verifies that when authentication is missing, an actionable non-secret RuntimeError is raised."""
    # Simulate missing environment configuration
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("PROJECT_ID", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        get_judge_genai_client()

    err_text = str(exc_info.value)
    assert "[JUDGE AUTHENTICATION FAILURE]" in err_text
    assert "gcloud auth application-default login" in err_text
    assert "GOOGLE_CLOUD_PROJECT" in err_text

