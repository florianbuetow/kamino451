#!/usr/bin/env python3
"""Derive a binary success judgment deterministically from ground-truth test results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from task_outcome_ledger_common import (
    load_json_file,
    parse_run_evidence,
    parse_success_judgment,
    require_bool,
    require_key,
)

JUDGMENT_SOURCE = "deterministic_ground_truth_tests"
WRITE_SCHEMA_VERSION = "kamino451.success-judgment-from-tests.v1"

GROUND_TRUTH_REQUIREMENT = "ground truth test suite passed"
EXECUTION_REQUIREMENT = "pipeline execution completed"


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Derive a strict binary success judgment from run evidence with ground-truth test results.",
    )
    parser.add_argument("--run-evidence", required=True, help="Path to run evidence JSON containing verification_evidence.tests_passed.")
    parser.add_argument("--output", required=False, help="Optional path to write the judgment JSON to. Refuses overwrite.")
    parser.add_argument("--format", choices=["json"], required=True, help="Output format.")
    return parser.parse_args(argv)


def derive_judgment(run_evidence: dict[str, object]) -> dict[str, object]:
    """Build the strict success judgment from execution status and test results."""
    verification_evidence = run_evidence["verification_evidence"]
    if not isinstance(verification_evidence, dict):
        raise TypeError("run evidence.verification_evidence must be a JSON object")
    tests_passed = require_bool(
        require_key(verification_evidence, "tests_passed", "run evidence.verification_evidence"),
        "run evidence.verification_evidence.tests_passed",
    )
    execution_completed = run_evidence["execution_status"] == "completed"

    satisfied: list[str] = []
    missing: list[str] = []
    if execution_completed:
        satisfied.append(EXECUTION_REQUIREMENT)
    else:
        missing.append(EXECUTION_REQUIREMENT)
    if tests_passed:
        satisfied.append(GROUND_TRUTH_REQUIREMENT)
    else:
        missing.append(GROUND_TRUTH_REQUIREMENT)

    success = execution_completed and tests_passed
    if success:
        reason = "The pipeline completed and the task's ground-truth test suite passed."
    elif not execution_completed and not tests_passed:
        reason = "The pipeline did not complete and the task's ground-truth test suite did not pass."
    elif not execution_completed:
        reason = "The task's ground-truth test suite passed but the pipeline did not complete."
    else:
        reason = "The pipeline completed but the task's ground-truth test suite did not pass."

    judgment = {
        "success": success,
        "reason": reason,
        "satisfied_requirements": satisfied,
        "missing_requirements": missing,
        "partial_requirements": [],
        "unverifiable_requirements": [],
        "confidence": "high",
        "judgment_source": JUDGMENT_SOURCE,
    }
    parse_success_judgment(judgment)
    return judgment


def write_judgment(output_path: Path, judgment: dict[str, object]) -> None:
    """Write the judgment artifact and refuse overwrites."""
    if output_path.exists():
        raise FileExistsError(f"success judgment file already exists: {output_path}")
    parent = output_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(judgment, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def format_json(payload: dict[str, object]) -> str:
    """Render stable JSON."""
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: list[str]) -> int:
    """Run the deterministic judgment CLI."""
    try:
        args = parse_args(argv)
        run_evidence = parse_run_evidence(load_json_file(args.run_evidence, "run evidence"))
        judgment = derive_judgment(run_evidence)
        if args.output is not None:
            write_judgment(Path(args.output), judgment)
        print(format_json(judgment))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
