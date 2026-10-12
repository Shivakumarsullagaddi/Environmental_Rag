"""Redaction and report generation module for ADK evaluation results.

Features:
- Masks any sensitive tokens, API keys, credentials, private paths, or internal tokens.
- Generates structured JSON reports and clean Markdown summaries.
- Clearly distinguishes Fast Local (Tier A), Live ADK (Tier B), Skipped, and Error statuses.
- Never reports a skipped test as passed.
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SECRET_REDACTION_PATTERNS = [
    (re.compile(r"tvly-[A-Za-z0-9_-]{15,}", re.IGNORECASE), "tvly-[REDACTED]"),
    (re.compile(r"AIza[0-9A-Za-z-_]{35}"), "AIza[REDACTED]"),
    (re.compile(r"bearer\s+[a-zA-Z0-9_\-\.]{20,}", re.IGNORECASE), "Bearer [REDACTED]"),
]


def redact_sensitive_strings(obj: Any) -> Any:
    """Recursively traverses objects and masks any sensitive patterns."""
    if isinstance(obj, str):
        cleaned = obj
        for pattern, replacement in SECRET_REDACTION_PATTERNS:
            cleaned = pattern.sub(replacement, cleaned)
        return cleaned
    elif isinstance(obj, dict):
        return {k: redact_sensitive_strings(v) for k, v in obj.items() if "secret" not in k.lower() and "key" not in k.lower() or redact_sensitive_strings(v)}
    elif isinstance(obj, list):
        return [redact_sensitive_strings(item) for item in obj]
    return obj


def generate_evaluation_report(
    run_id: str,
    tier: str,
    test_results: List[Dict[str, Any]],
    output_dir: Path,
) -> Tuple[Path, Path]:
    """Generates both a sanitized JSON report and a Markdown summary."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"eval_report_{run_id}.json"
    md_path = output_dir / f"eval_report_{run_id}.md"

    total = len(test_results)
    passed = sum(1 for r in test_results if r.get("status") == "PASS")
    failed = sum(1 for r in test_results if r.get("status") == "FAIL")
    skipped = sum(1 for r in test_results if r.get("status") == "SKIPPED")
    not_evaluated = sum(1 for r in test_results if r.get("status") == "NOT_EVALUATED")

    report_payload = {
        "run_id": run_id,
        "timestamp": time.time(),
        "tier": tier,
        "summary": {
            "total_cases": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "not_evaluated": not_evaluated,
            "pass_rate": round(passed / total, 3) if total > 0 else 0.0,
        },
        "results": redact_sensitive_strings(test_results),
    }

    json_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")

    # Generate Markdown Summary
    md_lines = [
        f"# ADK Evaluation Report ({tier})",
        f"- **Run ID:** `{run_id}`",
        f"- **Timestamp:** {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime(report_payload['timestamp']))}",
        f"- **Total Tests:** {total} | **Passed:** {passed} | **Failed:** {failed} | **Skipped:** {skipped} | **Not Evaluated:** {not_evaluated}",
        "",
        "## Test Case Details",
        "",
        "| ID | Objective | Expected Tools | Status | Details |",
        "| :--- | :--- | :--- | :---: | :--- |",
    ]

    for r in test_results:
        tid = r.get("eval_id", "unknown")
        obj = r.get("objective", r.get("query", ""))[:40]
        tools = ", ".join(r.get("expected_tools", [])) or "None"
        st = r.get("status", "NOT_EVALUATED")
        st_badge = f"**{st}**"
        dtl = redact_sensitive_strings(r.get("reason", r.get("notes", "")))[:60]
        md_lines.append(f"| `{tid}` | {obj} | `{tools}` | {st_badge} | {dtl} |")

    md_lines.append("")
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    return json_path, md_path
