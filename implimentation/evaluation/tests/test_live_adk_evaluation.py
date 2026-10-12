"""Live ADK Agent Evaluation Test Suite (Tier B).

Requirements:
- Gated behind explicit opt-in: RUN_LIVE_ADK_EVAL=1.
- Evaluates real root_agent from environmental_scientist.agent.
- Never reports a skipped test as passed.
"""

import os
import pytest
from pathlib import Path

# Ensure implementation directory is in sys.path
_impl_root = Path(__file__).resolve().parent.parent.parent
import sys
if str(_impl_root) not in sys.path:
    sys.path.insert(0, str(_impl_root))

from google.adk.evaluation.agent_evaluator import AgentEvaluator
from google.adk.evaluation.eval_set import EvalSet


@pytest.fixture
def check_live_opt_in():
    """Skips live evaluation unless explicitly enabled by user."""
    if os.getenv("RUN_LIVE_ADK_EVAL") != "1":
        pytest.skip(
            "Live ADK evaluation requires explicit opt-in (RUN_LIVE_ADK_EVAL=1) to protect credentials and API quotas."
        )


@pytest.mark.asyncio
async def test_live_routing_foundation(check_live_opt_in):
    """Executes live ADK evaluation on routing golden dataset."""
    test_file = _impl_root / "evaluation" / "datasets" / "routing" / "routing.test.json"
    assert test_file.exists(), f"Routing dataset file missing: {test_file}"

    eval_config = AgentEvaluator.find_config_for_test_file(str(test_file))
    eval_set = EvalSet.model_validate_json(test_file.read_text(encoding="utf-8"))

    # Evaluates real root_agent with ADK AgentEvaluator
    await AgentEvaluator.evaluate_eval_set(
        agent_module="environmental_scientist.agent",
        eval_set=eval_set,
        eval_config=eval_config,
        num_runs=1,
        print_detailed_results=True,
    )
