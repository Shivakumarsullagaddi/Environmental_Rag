"""Dataset validation and integrity module for ADK evaluation datasets.

Ensures:
1. JSON syntax and strict schema validation against google.adk.evaluation.eval_set.EvalSet.
2. Unique eval_id across all test cases.
3. Valid tool names from approved tool whitelist.
4. Review-state enforcement: files in runnable dataset folders must NOT be unreviewed drafts.
5. Strict absence of secrets, credentials, or private keys.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

# Whitelist of valid ADK tools in Environmental Scientist system
VALID_TOOL_NAMES: Set[str] = {
    "search_book_knowledge",
    "search_research_evidence",
    "get_paper_metadata",
    "tavily_search",
    "tavily_research",
    "tavily_extract",
}

# Regex patterns for detecting potential credential/secret leakage
SECRET_PATTERNS = [
    re.compile(r"tvly-[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"AIza[0-9A-Za-z-_]{35}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"bearer\s+[a-zA-Z0-9_\-\.]{25,}", re.IGNORECASE),
]


def validate_eval_set_file(file_path: Path) -> Tuple[bool, List[str], int]:
    """Validates a single .test.json or .evalset.json file against ADK EvalSet schema."""
    errors: List[str] = []
    case_count = 0

    if not file_path.exists():
        return False, [f"File not found: {file_path}"], 0

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return False, [f"Failed to read {file_path}: {e}"], 0

    # 1. Check for credential/secret leakage
    for pattern in SECRET_PATTERNS:
        if pattern.search(content):
            errors.append(f"CRITICAL: Potential secret/credential pattern detected in {file_path.name}")

    # 2. Parse JSON
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        return False, [f"Invalid JSON in {file_path}: {e}"], 0

    # 3. Validate against Google ADK Pydantic EvalSet
    try:
        from google.adk.evaluation.eval_set import EvalSet
        eval_set = EvalSet.model_validate(data)
        case_count = len(eval_set.eval_cases)
    except Exception as e:
        errors.append(f"ADK EvalSet schema validation error in {file_path.name}: {e}")
        return False, errors, 0

    # 4. Deep check tool names in intermediate_data
    for case in eval_set.eval_cases:
        if not case.eval_id or not case.eval_id.strip():
            errors.append(f"Empty eval_id found in {file_path.name}")
        if not case.conversation:
            errors.append(f"Eval case '{case.eval_id}' has empty conversation.")
            continue
        for inv in case.conversation:
            if inv.intermediate_data and hasattr(inv.intermediate_data, "tool_uses"):
                for tu in inv.intermediate_data.tool_uses:
                    if tu.name not in VALID_TOOL_NAMES:
                        errors.append(
                            f"Case '{case.eval_id}': Tool '{tu.name}' is not in approved whitelist {VALID_TOOL_NAMES}"
                        )

    return len(errors) == 0, errors, case_count


def validate_all_datasets(datasets_dir: Path) -> Dict[str, Any]:
    """Recursively validates all evaluation dataset folders and ensures global ID uniqueness."""
    results: Dict[str, Any] = {
        "valid": True,
        "files_checked": 0,
        "total_cases": 0,
        "errors": [],
        "warnings": [],
        "seen_ids": {},
    }

    golden_dirs = [
        "routing",
        "scientific_quality",
        "site_diagnosis",
        "external_research",
        "evidence_gaps",
        "security",
    ]

    all_ids: Dict[str, str] = {}

    for cat in golden_dirs:
        cat_dir = datasets_dir / cat
        if not cat_dir.exists():
            continue

        for test_file in cat_dir.rglob("*.test.json"):
            results["files_checked"] += 1
            # Check unreviewed draft guard
            if "draft" in test_file.name.lower():
                results["errors"].append(
                    f"Guard Violation: Unreviewed draft file '{test_file.name}' found in golden directory '{cat}'!"
                )
                results["valid"] = False

            ok, errs, count = validate_eval_set_file(test_file)
            results["total_cases"] += count
            if not ok:
                results["valid"] = False
                results["errors"].extend(errs)

            # Check ID uniqueness across all files
            try:
                data = json.loads(test_file.read_text(encoding="utf-8"))
                for case in data.get("eval_cases", []):
                    c_id = case.get("eval_id")
                    if c_id in all_ids:
                        results["errors"].append(
                            f"Duplicate eval_id '{c_id}' found in '{test_file.name}' (already defined in '{all_ids[c_id]}')"
                        )
                        results["valid"] = False
                    else:
                        all_ids[c_id] = test_file.name
            except Exception:
                pass

    results["seen_ids"] = all_ids
    return results


if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent.parent / "datasets"
    print(f"[DATASET-VALIDATOR] Scanning datasets under {base_dir}...")
    report = validate_all_datasets(base_dir)
    print(f"Files checked: {report['files_checked']}, Total cases: {report['total_cases']}")
    if report["errors"]:
        print("ERRORS FOUND:")
        for err in report["errors"]:
            print(f" - {err}")
        sys.exit(1)
    else:
        print("[DATASET-VALIDATOR] All evaluation datasets are valid and strictly schema-compliant!")
        sys.exit(0)
