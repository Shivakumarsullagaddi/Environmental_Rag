"""Dataset, schema, and baseline record integrity validator for the Environmental RAG Evaluation System.

Validates:
1. JSON syntax, required top-level and case-level fields in approved query files.
2. Unique case IDs across all datasets.
3. Known categories (retrieval_and_tools, security) and execution modes.
4. Judge configuration schema, score thresholds, and category weight sums (must equal 1.0).
5. Baseline record correspondence with approved queries.
6. Required response, tool trace, and status fields in captured records.
7. Safe absence of unredacted credentials or private keys in stored files.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Supported categories and execution modes
SUPPORTED_CATEGORIES = {"retrieval_and_tools", "security"}
SUPPORTED_MODES = {"live_agent", "controlled_fixture"}
REQUIRED_QUERY_FIELDS = {
    "case_id",
    "query",
    "purpose",
    "expected_behavior",
    "expected_tools",
    "prohibited_behaviors",
    "live_data",
    "execution_mode",
    "enabled",
}
REQUIRED_RECORD_FIELDS = {
    "run_id",
    "case_id",
    "category",
    "user_query",
    "agent_response",
    "captured_tool_calls",
    "retrieved_evidence",
    "execution_status",
    "timestamp_utc",
    "execution_mode",
    "security_indicators",
}

# Regex for detecting sensitive credential leakage
SECRET_PATTERNS = [
    re.compile(r"tvly-[A-Za-z0-9_-]{15,}", re.IGNORECASE),
    re.compile(r"AIza[0-9A-Za-z-_]{35}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"bearer\s+[a-zA-Z0-9_\-\.]{25,}", re.IGNORECASE),
]


def redact_sensitive_data(obj: Any) -> Any:
    """Recursively redacts API keys, tokens, and sensitive key names from any data structure."""
    if isinstance(obj, str):
        cleaned = obj
        for pattern in SECRET_PATTERNS:
            cleaned = pattern.sub("[REDACTED_SECRET]", cleaned)
        return cleaned
    elif isinstance(obj, dict):
        new_dict = {}
        for k, v in obj.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in ["secret", "password", "token", "private_key", "api_key", "apikey"]):
                new_dict[k] = "[REDACTED_SECRET]"
            else:
                new_dict[k] = redact_sensitive_data(v)
        return new_dict
    elif isinstance(obj, list):
        return [redact_sensitive_data(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(redact_sensitive_data(item) for item in obj)
    return obj


def validate_judge_config(config_path: Path) -> Tuple[bool, List[str]]:
    """Validates judge_config.json weights, thresholds, and required parameters."""
    errors: List[str] = []
    if not config_path.exists():
        return False, [f"Judge config not found at: {config_path}"]

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, [f"Failed to parse judge_config.json: {e}"]

    # Required fields
    for f in ["judge_model", "pass_threshold", "category_weights"]:
        if f not in data:
            errors.append(f"Missing required field '{f}' in judge_config.json")

    threshold = data.get("pass_threshold", 0.0)
    if not (0.0 <= threshold <= 1.0):
        errors.append(f"pass_threshold must be between 0.0 and 1.0, got {threshold}")

    # Validate category weights
    cat_weights = data.get("category_weights", {})
    for cat in SUPPORTED_CATEGORIES:
        if cat not in cat_weights:
            errors.append(f"Missing weights for category '{cat}' in judge_config.json")
            continue
        weights = cat_weights[cat]
        weight_sum = round(sum(weights.values()), 4)
        if weight_sum != 1.0:
            errors.append(f"Weights for category '{cat}' must sum to 1.0, got {weight_sum}")

    return len(errors) == 0, errors


def validate_query_file(file_path: Path, all_seen_ids: Set[str]) -> Tuple[bool, List[str], int]:
    """Validates an approved query definitions file."""
    errors: List[str] = []
    case_count = 0

    if not file_path.exists():
        return False, [f"Query file not found: {file_path}"], 0

    try:
        content = file_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as e:
        return False, [f"Failed to read/parse {file_path.name}: {e}"], 0

    # Credential leakage check
    for pat in SECRET_PATTERNS:
        if pat.search(content):
            errors.append(f"CRITICAL: Potential secret/credential pattern detected in {file_path.name}")

    category = data.get("category")
    if category not in SUPPORTED_CATEGORIES:
        errors.append(f"Invalid category '{category}' in {file_path.name}. Supported: {SUPPORTED_CATEGORIES}")

    cases = data.get("cases", [])
    if not isinstance(cases, list) or len(cases) == 0:
        errors.append(f"No cases array found or cases is empty in {file_path.name}")

    for idx, c in enumerate(cases):
        case_count += 1
        cid = c.get("case_id")
        if not cid or not isinstance(cid, str):
            errors.append(f"Case #{idx} in {file_path.name} missing valid 'case_id'")
            continue

        if cid in all_seen_ids:
            errors.append(f"Duplicate case_id '{cid}' detected across evaluation sets!")
        all_seen_ids.add(cid)

        # Check required fields
        for rf in REQUIRED_QUERY_FIELDS:
            if rf not in c:
                errors.append(f"Case '{cid}' is missing required field '{rf}'")

        mode = c.get("execution_mode")
        if mode not in SUPPORTED_MODES:
            errors.append(f"Case '{cid}' has unsupported execution_mode '{mode}'")

    return len(errors) == 0, errors, case_count


def validate_baseline_record_file(record_path: Path, expected_queries: Dict[str, Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """Validates a single captured baseline record JSON file."""
    errors: List[str] = []
    if not record_path.exists():
        return False, [f"Record file not found: {record_path}"]

    try:
        content = record_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as e:
        return False, [f"Failed to read/parse record {record_path.name}: {e}"]

    # Secret check
    for pat in SECRET_PATTERNS:
        if pat.search(content):
            errors.append(f"CRITICAL: Potential unredacted secret found in {record_path.name}")

    for rf in REQUIRED_RECORD_FIELDS:
        if rf not in data:
            errors.append(f"Record {record_path.name} is missing required field '{rf}'")

    cid = data.get("case_id")
    if cid not in expected_queries:
        errors.append(f"Record {record_path.name} has case_id '{cid}' which is not in approved queries!")
    else:
        q_meta = expected_queries[cid]
        if data.get("category") != q_meta.get("category"):
            errors.append(f"Record category '{data.get('category')}' does not match query category '{q_meta.get('category')}'")

    status = data.get("execution_status")
    if status not in {"SUCCESS", "FAILED", "ERROR", "DRY_RUN"}:
        errors.append(f"Record {record_path.name} has invalid execution_status '{status}'")

    return len(errors) == 0, errors


def validate_all(evaluation_dir: Path, check_run_id: Optional[str] = None) -> Dict[str, Any]:
    """Runs complete offline validation of configs, datasets, and baseline records."""
    all_errors: List[str] = []
    all_seen_ids: Set[str] = set()
    total_cases = 0

    # 1. Validate judge_config.json
    cfg_path = evaluation_dir / "config" / "judge_config.json"
    ok_cfg, errs_cfg = validate_judge_config(cfg_path)
    all_errors.extend(errs_cfg)

    # 2. Validate query files
    query_files = [
        evaluation_dir / "datasets" / "retrieval_and_tools" / "baseline_queries.json",
        evaluation_dir / "datasets" / "security" / "security_queries.json",
    ]

    approved_queries: Dict[str, Dict[str, Any]] = {}
    for qf in query_files:
        ok_q, errs_q, count = validate_query_file(qf, all_seen_ids)
        total_cases += count
        all_errors.extend(errs_q)
        if ok_q and qf.exists():
            try:
                data = json.loads(qf.read_text(encoding="utf-8"))
                for c in data.get("cases", []):
                    c_dict = dict(c)
                    c_dict["category"] = data.get("category")
                    approved_queries[c["case_id"]] = c_dict
            except Exception:
                pass

    # 3. Optional validate specific capture run
    records_checked = 0
    if check_run_id:
        for cat in SUPPORTED_CATEGORIES:
            run_dir = evaluation_dir / "datasets" / cat / "baseline_records" / check_run_id
            if run_dir.exists():
                for rf in run_dir.glob("*.json"):
                    records_checked += 1
                    ok_r, errs_r = validate_baseline_record_file(rf, approved_queries)
                    all_errors.extend(errs_r)

    return {
        "valid": len(all_errors) == 0,
        "total_queries_defined": total_cases,
        "records_checked": records_checked,
        "errors": all_errors,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Environmental RAG Evaluation Dataset Validator")
    parser.add_argument("--run-id", type=str, default=None, help="Optional capture run ID to validate records for")
    args = parser.parse_args()

    eval_dir = Path(__file__).resolve().parent.parent
    print(f"[DATASET-VALIDATOR] Scanning evaluation directory: {eval_dir}")
    report = validate_all(eval_dir, check_run_id=args.run_id)

    print(f"Total Approved Queries: {report['total_queries_defined']}")
    if args.run_id:
        print(f"Records Checked for run '{args.run_id}': {report['records_checked']}")

    if report["valid"]:
        print("[DATASET-VALIDATOR] SUCCESS: All configurations, query definitions, and records are valid!")
        sys.exit(0)
    else:
        print("[DATASET-VALIDATOR] FAILED: Integrity errors encountered:")
        for err in report["errors"]:
            print(f" - {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
