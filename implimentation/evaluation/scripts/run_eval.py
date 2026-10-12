"""Unified evaluation runner for Tier A (Fast Offline) and Tier B (Live ADK Evaluation).

Tier A:
- Validates all dataset files against schema.
- Runs offline checks without cloud credentials or external API calls.
- Fast execution, zero quota usage.

Tier B:
- Gated behind RUN_LIVE_ADK_EVAL=1.
- Evaluates real root_agent using ADK AgentEvaluator.
- Reports live tool trajectories and outputs.
- Never reports a skipped suite as passed.
"""

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Ensure implementation directory is in sys.path
_impl_root = Path(__file__).resolve().parent.parent.parent
if str(_impl_root) not in sys.path:
    sys.path.insert(0, str(_impl_root))

from evaluation.scripts.validate_datasets import validate_all_datasets
from evaluation.scripts.summarize_reports import generate_evaluation_report


def run_tier_a_offline(datasets_dir: Path, results_dir: Path) -> int:
    """Executes Tier A Fast Local Validation (Zero quota, offline)."""
    print("=" * 80)
    print("RUNNING TIER A: FAST LOCAL VALIDATION (OFFLINE, ZERO QUOTA)")
    print("=" * 80)

    t0 = time.time()
    validation_report = validate_all_datasets(datasets_dir)
    elapsed = time.time() - t0

    test_results: List[Dict[str, Any]] = []

    # Record dataset validation outcome
    status = "PASS" if validation_report["valid"] else "FAIL"
    test_results.append({
        "eval_id": "tier_a_schema_and_integrity",
        "objective": "Dataset JSON syntax, ADK schema, ID uniqueness, and draft guard",
        "status": status,
        "reason": f"Checked {validation_report['files_checked']} files, {validation_report['total_cases']} cases. Errors: {len(validation_report['errors'])}",
    })

    for err in validation_report["errors"]:
        test_results.append({
            "eval_id": "schema_error",
            "objective": "Integrity Check",
            "status": "FAIL",
            "reason": err,
        })

    run_id = f"tier_a_{int(time.time())}"
    json_path, md_path = generate_evaluation_report(run_id, "Tier A (Offline)", test_results, results_dir)

    print(f"\n[TIER A COMPLETED in {elapsed:.2f}s]")
    print(f"Status: {status}")
    print(f"Total Cases Checked: {validation_report['total_cases']}")
    print(f"Report JSON: {json_path}")
    print(f"Report Summary: {md_path}")
    print("=" * 80)

    return 0 if validation_report["valid"] else 1


async def run_tier_b_live(datasets_dir: Path, results_dir: Path, category: str = "all") -> int:
    """Executes Tier B Live ADK Evaluation (Requires RUN_LIVE_ADK_EVAL=1)."""
    print("=" * 80)
    print("RUNNING TIER B: LIVE ADK AGENT EVALUATION")
    print("=" * 80)

    if os.getenv("RUN_LIVE_ADK_EVAL") != "1":
        print("\n[SKIP] RUN_LIVE_ADK_EVAL is not set to '1'.")
        print("To run live ADK agent evaluation against the real agent, set:")
        print("  export RUN_LIVE_ADK_EVAL=1")
        print("Live evaluation skipped safely to protect credentials and API quotas.\n")
        return 0

    from google.adk.evaluation.agent_evaluator import AgentEvaluator

    target_dir = datasets_dir if category == "all" else datasets_dir / category
    if not target_dir.exists():
        print(f"Category path not found: {target_dir}")
        return 1

    print(f"Targeting dataset directory: {target_dir}")
    print("Evaluating real root_agent from module: environmental_scientist.agent...")

    t0 = time.time()
    try:
        await AgentEvaluator.evaluate(
            agent_module="environmental_scientist.agent",
            eval_dataset_file_path_or_dir=str(target_dir),
            num_runs=1,
            print_detailed_results=True,
        )
        print(f"\n[TIER B LIVE EVALUATION PASSED in {time.time() - t0:.2f}s]")
        return 0
    except AssertionError as ae:
        print(f"\n[TIER B LIVE EVALUATION DETECTED FAILURES]:\n{ae}")
        return 1
    except Exception as e:
        print(f"\n[TIER B INFRASTRUCTURE OR SERVICE ERROR]: {e}")
        return 2


def main():
    parser = argparse.ArgumentParser(description="Environmental Scientist ADK Evaluation Runner")
    parser.add_argument("--tier", choices=["tier_a", "tier_b", "both"], default="tier_a", help="Testing tier to execute")
    parser.add_argument("--category", default="all", help="Dataset category (routing, scientific_quality, etc.)")
    args = parser.parse_args()

    eval_dir = Path(__file__).resolve().parent.parent
    datasets_dir = eval_dir / "datasets"
    results_dir = eval_dir / "results"

    if args.tier in ["tier_a", "both"]:
        exit_code = run_tier_a_offline(datasets_dir, results_dir)
        if exit_code != 0 and args.tier == "both":
            sys.exit(exit_code)

    if args.tier in ["tier_b", "both"]:
        exit_code = asyncio.run(run_tier_b_live(datasets_dir, results_dir, category=args.category))
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
