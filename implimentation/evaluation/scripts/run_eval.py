"""Evaluation runner for captured baseline records using independent Gemini 3.8 Flash Judge.

Workflow:
1. Loads and validates judge_config.json and approved query definitions.
2. Locates baseline records for the specified capture run ID.
3. Sends each captured record to Gemini 3.8 Flash with judge_instructions.md.
4. Calculates category-weighted scores (with weight renormalization for missing evidence).
5. Applies critical-failure overrides (secret leaks, prompt injections, citation fabrications).
6. Generates machine-readable JSON and human-readable Markdown evaluation reports under results/<eval_run_id>/.
7. CLI options: --category, --run-id, --dry-run (0 quota).
"""

import argparse
import asyncio
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure implementation root is in sys.path
_impl_root = Path(__file__).resolve().parent.parent.parent
if str(_impl_root) not in sys.path:
    sys.path.insert(0, str(_impl_root))

from evaluation.scripts.validate_datasets import (
    validate_judge_config,
    redact_sensitive_data,
    SUPPORTED_CATEGORIES,
)


def calculate_weighted_score(
    criteria_scores: Dict[str, Any],
    category: str,
    category_weights: Dict[str, float],
    critical_failures: List[str],
    pass_threshold: float,
) -> Tuple[float, bool, List[str]]:
    """Calculates category-weighted score with weight renormalization and critical failure overrides."""
    if critical_failures:
        return 0.0, False, []

    applicable_weights: Dict[str, float] = {}
    normalized_scores: Dict[str, float] = {}
    not_evaluable: List[str] = []

    for crit_name, base_weight in category_weights.items():
        entry = criteria_scores.get(crit_name)
        if entry is None:
            not_evaluable.append(crit_name)
            continue
        score_val = entry.get("score") if isinstance(entry, dict) else entry
        if score_val is None or score_val == "null" or score_val == "NOT_EVALUABLE":
            not_evaluable.append(crit_name)
            continue
        try:
            numeric_score = float(score_val)
            normalized_scores[crit_name] = max(0.0, min(1.0, numeric_score))
            applicable_weights[crit_name] = base_weight
        except (ValueError, TypeError):
            not_evaluable.append(crit_name)

    total_weight = sum(applicable_weights.values())
    if total_weight == 0:
        return 0.0, False, not_evaluable

    # Weighted sum normalized to 1.0 scale
    weighted_total = sum(normalized_scores[c] * (applicable_weights[c] / total_weight) for c in applicable_weights)
    passed = (weighted_total >= pass_threshold) and (len(critical_failures) == 0)

    return round(weighted_total, 4), passed, not_evaluable


def get_judge_genai_client():
    """Initializes and returns the independent Google Gen AI client for the evaluation judge.

    Reuses the authenticated provider pattern established by the working Environmental RAG application:
    - Vertex AI via Application Default Credentials (ADC) / Google Cloud Project & Location.
    - Alternatively Gemini API Key if explicitly provided via GEMINI_API_KEY.

    Raises RuntimeError with clear, non-secret diagnostic instructions if authentication is missing.
    """
    # Import unified configuration from implementation root to inherit environment settings
    try:
        import config  # noqa: F401 - ensures GOOGLE_GENAI_USE_VERTEXAI and GOOGLE_CLOUD_* are loaded
    except ImportError:
        pass

    import google.auth
    from google.auth.exceptions import DefaultCredentialsError
    from google import genai

    use_vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in {"1", "true", "yes"}
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    gemini_key = os.environ.get("GEMINI_API_KEY")

    # If neither Vertex AI nor Gemini API Key is configured, diagnose immediately
    if not use_vertex and not gemini_key:
        err_msg = (
            "\n" + "=" * 80 + "\n"
            "[JUDGE AUTHENTICATION FAILURE]\n"
            "The evaluation judge failed to authenticate its Google Gen AI client.\n\n"
            "Provider Configured: Vertex AI (Application Default Credentials / Google Cloud)\n"
            f"  - GOOGLE_GENAI_USE_VERTEXAI: '{os.environ.get('GOOGLE_GENAI_USE_VERTEXAI', 'not set')}'\n"
            f"  - GOOGLE_CLOUD_PROJECT: '{project or 'not set'}'\n"
            f"  - GOOGLE_CLOUD_LOCATION: '{location or 'not set'}'\n"
            f"  - GEMINI_API_KEY: {'[SET]' if gemini_key else '[NOT SET]'}\n\n"
            "Resolution Options:\n"
            "1. Vertex AI (Recommended - used by Environmental RAG app):\n"
            "   Ensure Google Cloud Application Default Credentials (ADC) are active:\n"
            "     $ gcloud auth application-default login\n"
            "     $ export GOOGLE_CLOUD_PROJECT=\"developer-491706\"\n"
            "     $ export GOOGLE_CLOUD_LOCATION=\"global\"\n"
            "     $ export GOOGLE_GENAI_USE_VERTEXAI=\"1\"\n\n"
            "2. Gemini Developer API:\n"
            "   Export your Gemini API key in your environment:\n"
            "     $ export GEMINI_API_KEY=\"<your-api-key>\"\n"
            "=" * 80 + "\n"
        )
        raise RuntimeError(err_msg)

    # Validate ADC eagerly if using Vertex AI
    if use_vertex:
        try:
            google.auth.default()
        except DefaultCredentialsError as adc_err:
            err_msg = (
                "\n" + "=" * 80 + "\n"
                "[JUDGE AUTHENTICATION FAILURE - VERTEX AI ADC MISSING]\n"
                "The evaluation judge is configured for Vertex AI, but Application Default Credentials (ADC) were not found.\n\n"
                f"Configured Project: '{project or 'not set'}'\n"
                f"Configured Location: '{location or 'not set'}'\n\n"
                "How to authenticate:\n"
                "  $ gcloud auth application-default login\n"
                f"Underlying Error: {adc_err}\n"
                "=" * 80 + "\n"
            )
            raise RuntimeError(err_msg) from adc_err

        try:
            return genai.Client(vertexai=True, project=project, location=location)
        except Exception as e:
            err_msg = (
                "\n" + "=" * 80 + "\n"
                "[JUDGE CLIENT INITIALIZATION ERROR]\n"
                f"Failed to create Vertex AI client for project '{project}' and location '{location}'.\n"
                f"Underlying SDK Error: {type(e).__name__}: {e}\n"
                "=" * 80 + "\n"
            )
            raise RuntimeError(err_msg) from e

    # Fallback to direct Gemini API key
    if gemini_key:
        try:
            return genai.Client(api_key=gemini_key)
        except Exception as e:
            err_msg = (
                "\n" + "=" * 80 + "\n"
                "[JUDGE CLIENT INITIALIZATION ERROR]\n"
                "Failed to create Gemini Developer API client with GEMINI_API_KEY.\n"
                f"Underlying SDK Error: {type(e).__name__}: {e}\n"
                "=" * 80 + "\n"
            )
            raise RuntimeError(err_msg) from e

    return genai.Client()


async def evaluate_record_with_judge(
    judge_model: str,
    thinking_level: str,
    system_instruction: str,
    record: Dict[str, Any],
    query_meta: Dict[str, Any],
    timeout_sec: float = 60.0,
    max_retries: int = 2,
    client: Any = None,
) -> Dict[str, Any]:
    """Invokes independent Gemini 3.8 Flash Judge on a captured execution record."""
    from google.genai import types

    # Initialize client once or reuse passed client
    if client is None:
        try:
            client = get_judge_genai_client()
        except RuntimeError as auth_err:
            return {
                "case_id": record.get("case_id"),
                "category": record.get("category"),
                "decision": "ERROR",
                "error_message": str(auth_err),
                "criteria_scores": {},
                "critical_failures": ["Authentication failure: Judge client could not be initialized."],
                "overall_weighted_score": 0.0,
            }

    prompt_data = {
        "case_id": record.get("case_id"),
        "category": record.get("category"),
        "query_definition": query_meta,
        "user_query": record.get("user_query"),
        "agent_response": record.get("agent_response"),
        "captured_tool_calls": record.get("captured_tool_calls", []),
        "retrieved_evidence": record.get("retrieved_evidence", []),
        "capture_metadata": {
            "execution_status": record.get("execution_status"),
            "latency_sec": record.get("latency_sec"),
            "timestamp_utc": record.get("timestamp_utc"),
            "execution_mode": record.get("execution_mode"),
        },
        "security_indicators": record.get("security_indicators", []),
    }

    eval_prompt = (
        "Evaluate the following captured agent execution record against the rubric and captured evidence.\n\n"
        f"RECORD ARTIFACTS:\n```json\n{json.dumps(prompt_data, indent=2)}\n```\n\n"
        "Return ONLY a valid JSON object matching the rubric output schema."
    )

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
    )

    # Thinking configuration
    if hasattr(config, "thinking_config"):
        budget_map = {"low": 1024, "medium": 4096, "high": 8192}
        budget = budget_map.get(thinking_level.lower(), 1024)
        config.thinking_config = types.ThinkingConfig(thinking_budget=budget)

    for attempt in range(max_retries + 1):
        try:
            resp = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=judge_model,
                    contents=eval_prompt,
                    config=config,
                ),
                timeout=timeout_sec,
            )
            raw_text = resp.text or "{}"
            parsed = json.loads(raw_text)
            return parsed
        except Exception as e:
            # Non-retryable authentication or configuration errors
            err_str = str(e).lower()
            if "api key" in err_str or "unauthenticated" in err_str or "permission" in err_str or "credentials" in err_str:
                return {
                    "case_id": record.get("case_id"),
                    "category": record.get("category"),
                    "decision": "ERROR",
                    "error_message": f"Non-retryable authorization error: {e}",
                    "criteria_scores": {},
                    "critical_failures": [f"Judge invocation failed with auth/permission error: {e}"],
                    "overall_weighted_score": 0.0,
                }
            if attempt < max_retries:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            return {
                "case_id": record.get("case_id"),
                "category": record.get("category"),
                "decision": "ERROR",
                "error_message": str(e),
                "criteria_scores": {},
                "critical_failures": [f"Judge invocation failed after {max_retries+1} attempts: {e}"],
                "overall_weighted_score": 0.0,
            }


def generate_reports(
    eval_run_id: str,
    capture_run_id: str,
    judge_model: str,
    case_evaluations: List[Dict[str, Any]],
    output_dir: Path,
) -> Tuple[Path, Path]:
    """Generates machine-readable JSON and human-readable Markdown evaluation reports."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"eval_report_{eval_run_id}.json"
    md_path = output_dir / f"eval_report_{eval_run_id}.md"

    total = len(case_evaluations)
    passed = sum(1 for c in case_evaluations if c.get("decision") == "PASS")
    failed = sum(1 for c in case_evaluations if c.get("decision") == "FAIL")
    not_eval = sum(1 for c in case_evaluations if c.get("decision") == "NOT_EVALUABLE")
    errors = sum(1 for c in case_evaluations if c.get("decision") in {"ERROR", "DRY_RUN"})

    # Category summaries
    cat_summary: Dict[str, Dict[str, Any]] = {}
    for cat in SUPPORTED_CATEGORIES:
        cat_cases = [c for c in case_evaluations if c.get("category") == cat]
        if cat_cases:
            valid_scores = [c["overall_weighted_score"] for c in cat_cases if c.get("decision") in {"PASS", "FAIL"}]
            avg_score = round(sum(valid_scores) / len(valid_scores), 4) if valid_scores else 0.0
            cat_summary[cat] = {
                "total": len(cat_cases),
                "passed": sum(1 for c in cat_cases if c.get("decision") == "PASS"),
                "failed": sum(1 for c in cat_cases if c.get("decision") == "FAIL"),
                "not_evaluable": sum(1 for c in cat_cases if c.get("decision") == "NOT_EVALUABLE"),
                "average_score": avg_score,
            }

    report_payload = {
        "eval_run_id": eval_run_id,
        "capture_run_id": capture_run_id,
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "judge_model": judge_model,
        "summary": {
            "total_cases": total,
            "passed": passed,
            "failed": failed,
            "not_evaluable": not_eval,
            "errors": errors,
            "pass_rate": round(passed / total, 3) if total > 0 else 0.0,
        },
        "category_summaries": cat_summary,
        "cases": redact_sensitive_data(case_evaluations),
    }

    json_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")

    # Generate Markdown Summary
    md_lines = [
        f"# Environmental RAG Evaluation Report (`{eval_run_id}`)",
        f"- **Capture Run ID:** `{capture_run_id}`",
        f"- **Timestamp:** {report_payload['timestamp_utc']}",
        f"- **Judge Model:** `{judge_model}`",
        f"- **Summary:** Total: {total} | **Passed:** {passed} | **Failed:** {failed} | **Not Evaluable:** {not_eval} | **Errors/DryRun:** {errors}",
        "",
        "## Category Performance",
        "",
        "| Category | Total | Passed | Failed | Not Evaluable | Average Score |",
        "| :--- | :---: | :---: | :---: | :---: | :---: |",
    ]

    for cat, cdata in cat_summary.items():
        md_lines.append(
            f"| `{cat}` | {cdata['total']} | {cdata['passed']} | {cdata['failed']} | {cdata['not_evaluable']} | {cdata['average_score'] * 100:.1f}% |"
        )

    md_lines.extend([
        "",
        "## Evaluated Case Details",
        "",
        "| Case ID | Category | Decision | Score | Critical Failures | Notes |",
        "| :--- | :--- | :---: | :---: | :--- | :--- |",
    ])

    for c in case_evaluations:
        cid = c.get("case_id", "unknown")
        cat = c.get("category", "")
        dec = c.get("decision", "ERROR")
        score = f"{c.get('overall_weighted_score', 0.0) * 100:.1f}%"
        crits = ", ".join(c.get("critical_failures", [])) or "None"
        notes = c.get("summary_verdict", c.get("error_message", ""))[:70]
        md_lines.append(f"| `{cid}` | `{cat}` | **{dec}** | {score} | {crits} | {notes} |")

    md_lines.append("")
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    return json_path, md_path


async def run_evaluation(
    eval_dir: Path,
    category: str,
    run_id: Optional[str],
    dry_run: bool,
) -> int:
    """Executes evaluation across requested categories."""
    config_path = eval_dir / "config" / "judge_config.json"
    ok_cfg, errs_cfg = validate_judge_config(config_path)
    if not ok_cfg:
        print(f"[RUN_EVAL] Judge configuration error: {errs_cfg}")
        return 2

    judge_cfg = json.loads(config_path.read_text(encoding="utf-8"))
    instructions_file = eval_dir / "config" / "judge_instructions.md"
    judge_instructions = instructions_file.read_text(encoding="utf-8") if instructions_file.exists() else ""

    judge_model = judge_cfg.get("judge_model", "gemini-3.8-flash")
    pass_threshold = judge_cfg.get("pass_threshold", 0.8)
    cat_weights = judge_cfg.get("category_weights", {})

    cats = ["retrieval_and_tools", "security"] if category == "all" else [category]

    # Find capture run ID
    if not run_id:
        # Pick latest capture run
        for cat in cats:
            b_dir = eval_dir / "datasets" / cat / "baseline_records"
            if b_dir.exists():
                runs = sorted([d.name for d in b_dir.iterdir() if d.is_dir()])
                if runs:
                    run_id = runs[-1]
                    break
        if not run_id:
            print("[RUN_EVAL] No baseline capture runs found. Please run capture_baselines.py first.")
            return 2

    # Live Evaluation Guardrail Check
    # If not dry_run, require RUN_LIVE_ADK_EVAL=1 in environment / .env
    if not dry_run:
        try:
            from config import is_live_eval_enabled
            live_allowed = is_live_eval_enabled()
        except ImportError:
            live_allowed = os.environ.get("RUN_LIVE_ADK_EVAL", "0").strip().lower() in {"1", "true", "yes"}

        if not live_allowed:
            print("\n" + "=" * 80)
            print("[LIVE EVALUATION BLOCKED - GUARDRAIL ACTIVE]")
            print("Live evaluation using Gemini LLM models is currently disabled by configuration.")
            print(f"Current setting: RUN_LIVE_ADK_EVAL={os.environ.get('RUN_LIVE_ADK_EVAL', '0')}")
            print("\nTo enable live evaluation:")
            print("  1. Edit your .env file in 'implimentation/.env':")
            print("       RUN_LIVE_ADK_EVAL=1")
            print("  2. Or export it in your shell environment:")
            print("       export RUN_LIVE_ADK_EVAL=1")
            print("\nIf you want to run offline verification without model quota:")
            print("  Run with the --dry-run flag:")
            print(f"    python scripts/run_eval.py --category {category} {f'--run-id {run_id}' if run_id else ''} --dry-run")
            print("=" * 80 + "\n")
            return 2

    print("=" * 80)
    print(f"RUNNING INDEPENDENT EVALUATION (Target Run ID: {run_id})")
    print(f"Judge Model: {judge_model} | Pass Threshold: {pass_threshold * 100:.0f}%")
    print(f"Mode: {'DRY RUN (0 Quota, Local Schema Evaluation)' if dry_run else 'LIVE GEMINI 3.8 FLASH JUDGE'}")
    print("=" * 80)

    # Load approved queries
    approved_queries: Dict[str, Dict[str, Any]] = {}
    for cat in cats:
        q_file = eval_dir / "datasets" / cat / f"{'baseline' if cat == 'retrieval_and_tools' else 'security'}_queries.json"
        if q_file.exists():
            data = json.loads(q_file.read_text(encoding="utf-8"))
            for c in data.get("cases", []):
                approved_queries[c["case_id"]] = c

    evaluated_cases: List[Dict[str, Any]] = []

    for cat in cats:
        rec_dir = eval_dir / "datasets" / cat / "baseline_records" / run_id
        if not rec_dir.exists():
            print(f"[RUN_EVAL] No records found for category '{cat}' in run '{run_id}'")
            continue

        for r_file in sorted(rec_dir.glob("*.json")):
            record = json.loads(r_file.read_text(encoding="utf-8"))
            cid = record.get("case_id")
            q_meta = approved_queries.get(cid, {})

            print(f"\n[EVAL] Assessing Case: [{cat}] {cid}...")

            if dry_run or record.get("execution_status") == "DRY_RUN":
                print("  -> Dry Run Mode: Evaluating schema and recorded flags without calling Gemini.")
                crits = record.get("security_indicators", [])
                score = 0.0 if crits else 1.0
                decision = "FAIL" if crits else "PASS"
                eval_res = {
                    "case_id": cid,
                    "category": cat,
                    "criteria_scores": {
                        "dry_run_check": {"score": score, "reasoning": "Dry run verification"}
                    },
                    "critical_failures": crits,
                    "not_evaluable_criteria": [],
                    "overall_weighted_score": score,
                    "decision": decision,
                    "summary_verdict": f"Dry run validation completed. Status: {decision}",
                }
            else:
                # Live evaluation with Gemini 3.8 Flash Judge
                print(f"  -> Invoking Gemini 3.8 Flash Judge...")
                judge_raw = await evaluate_record_with_judge(
                    judge_model=judge_model,
                    thinking_level=judge_cfg.get("thinking_level", "low"),
                    system_instruction=judge_instructions,
                    record=record,
                    query_meta=q_meta,
                    timeout_sec=judge_cfg.get("request_timeout_seconds", 60),
                    max_retries=judge_cfg.get("max_retries", 2),
                )

                if judge_raw.get("decision") == "ERROR":
                    eval_res = {
                        "case_id": cid,
                        "category": cat,
                        "criteria_scores": {},
                        "critical_failures": judge_raw.get("critical_failures", []),
                        "not_evaluable_criteria": list(cat_weights.get(cat, {}).keys()),
                        "overall_weighted_score": 0.0,
                        "decision": "ERROR",
                        "summary_verdict": f"Evaluation error: {judge_raw.get('error_message', 'Judge execution failed')}",
                        "error_message": judge_raw.get("error_message"),
                    }
                else:
                    crit_scores = judge_raw.get("criteria_scores", {})
                    critical_fails = judge_raw.get("critical_failures", [])
                    c_weights = cat_weights.get(cat, {})

                    w_score, passed, not_eval = calculate_weighted_score(
                        criteria_scores=crit_scores,
                        category=cat,
                        category_weights=c_weights,
                        critical_failures=critical_fails,
                        pass_threshold=pass_threshold,
                    )

                    decision = "FAIL" if (critical_fails or not passed) else ("PASS" if not_eval != list(c_weights.keys()) else "NOT_EVALUABLE")
                    eval_res = {
                        "case_id": cid,
                        "category": cat,
                        "criteria_scores": crit_scores,
                        "critical_failures": critical_fails,
                        "not_evaluable_criteria": not_eval,
                        "overall_weighted_score": w_score,
                        "decision": decision,
                        "summary_verdict": judge_raw.get("summary_verdict", f"Evaluated with score {w_score * 100:.1f}%"),
                    }

            print(f"  -> Result: {eval_res['decision']} (Score: {eval_res['overall_weighted_score'] * 100:.1f}%)")
            evaluated_cases.append(eval_res)

    eval_run_id = f"eval_{int(time.time())}"
    results_dir = eval_dir / "results" / eval_run_id
    json_rep, md_rep = generate_reports(
        eval_run_id=eval_run_id,
        capture_run_id=run_id,
        judge_model=judge_model if not dry_run else "DryRunValidator",
        case_evaluations=evaluated_cases,
        output_dir=results_dir,
    )

    print("\n" + "=" * 80)
    print(f"EVALUATION COMPLETE (Run ID: {eval_run_id})")
    print(f"JSON Report: {json_rep}")
    print(f"Markdown Report: {md_rep}")
    print("=" * 80)

    any_fails = any(c["decision"] in {"FAIL", "ERROR"} for c in evaluated_cases)
    return 1 if any_fails else 0


def main():
    parser = argparse.ArgumentParser(description="Environmental RAG Evaluation Runner")
    parser.add_argument("--category", choices=["retrieval_and_tools", "security", "all"], default="all")
    parser.add_argument("--run-id", type=str, default=None, help="Capture run ID to evaluate")
    parser.add_argument("--dry-run", action="store_true", help="Perform offline dry-run evaluation without model calls")
    args = parser.parse_args()

    eval_dir = Path(__file__).resolve().parent.parent
    exit_code = asyncio.run(run_evaluation(
        eval_dir=eval_dir,
        category=args.category,
        run_id=args.run_id,
        dry_run=args.dry_run,
    ))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
