"""Unit tests for evaluation dataset schemas, helper functions, and redaction logic.

Runs entirely offline in Tier A without external dependencies, credentials, or API tokens.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
_impl_root = Path(__file__).resolve().parent.parent.parent
if str(_impl_root) not in sys.path:
    sys.path.insert(0, str(_impl_root))

from evaluation.scripts.validate_datasets import (
    validate_eval_set_file,
    validate_all_datasets,
    VALID_TOOL_NAMES,
)
from evaluation.scripts.summarize_reports import (
    redact_sensitive_strings,
    generate_evaluation_report,
)
from evaluation.scripts.generate_candidates import generate_draft_candidates


def test_valid_tool_names_set():
    """Confirms all approved ADK tools are registered in whitelist."""
    expected = {
        "search_book_knowledge",
        "search_research_evidence",
        "get_paper_metadata",
        "tavily_search",
        "tavily_research",
        "tavily_extract",
    }
    assert expected == VALID_TOOL_NAMES


def test_golden_datasets_validate_cleanly():
    """Ensures all committed golden evaluation datasets comply strictly with ADK EvalSet schema."""
    datasets_dir = _impl_root / "evaluation" / "datasets"
    report = validate_all_datasets(datasets_dir)
    assert report["valid"] is True, f"Validation errors found: {report['errors']}"
    assert report["total_cases"] >= 6
    assert len(report["errors"]) == 0


def test_draft_guard_blocks_unreviewed_drafts_in_golden(tmp_path):
    """Verifies that files with 'draft' in their name are blocked from golden directories."""
    fake_golden = tmp_path / "routing"
    fake_golden.mkdir(parents=True)
    draft_file = fake_golden / "unreviewed_sample.draft.test.json"
    draft_file.write_text(json.dumps({"eval_set_id": "draft_set", "eval_cases": []}))

    report = validate_all_datasets(tmp_path)
    assert report["valid"] is False
    assert any("Guard Violation" in err for err in report["errors"])


def test_duplicate_eval_id_detection(tmp_path):
    """Verifies duplicate eval_id is flagged across test cases."""
    fake_cat = tmp_path / "routing"
    fake_cat.mkdir(parents=True)
    file1 = fake_cat / "set1.test.json"
    file2 = fake_cat / "set2.test.json"

    case_dup = {
        "eval_set_id": "set1",
        "eval_cases": [
            {
                "eval_id": "duplicate_id",
                "conversation": [
                    {
                        "invocation_id": "inv1",
                        "user_content": {"parts": [{"text": "q"}]},
                        "creation_timestamp": 1700000000.0,
                    }
                ],
                "creation_timestamp": 1700000000.0,
            }
        ],
        "creation_timestamp": 1700000000.0,
    }
    file1.write_text(json.dumps(case_dup))
    file2.write_text(json.dumps(case_dup))

    report = validate_all_datasets(tmp_path)
    assert report["valid"] is False
    assert any("Duplicate eval_id" in err for err in report["errors"])


def test_secret_redaction_masks_api_keys():
    """Confirms sensitive API keys and tokens are securely masked in reports."""
    raw_data = {
        "text": "Using key tvly-abcdef1234567890abcdef for external search",
        "auth": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secretpayload",
        "safe_field": "Biodiversity evaluation result",
    }
    redacted = redact_sensitive_strings(raw_data)
    assert "tvly-[REDACTED]" in redacted["text"]
    assert "Bearer [REDACTED]" in redacted["auth"]
    assert redacted["safe_field"] == "Biodiversity evaluation result"


def test_candidate_generation_writes_draft_only(tmp_path):
    """Ensures candidate generator strictly writes to drafts directory with status 'draft'."""
    draft_file = generate_draft_candidates(tmp_path)
    assert draft_file.exists()
    assert "draft" in draft_file.name

    data = json.loads(draft_file.read_text(encoding="utf-8"))
    assert data["status"] == "draft_unreviewed"
    for cand in data["candidates"]:
        assert cand["review_status"] == "draft"
        assert cand["id"].startswith("cand_")
