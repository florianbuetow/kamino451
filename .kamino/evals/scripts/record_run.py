#!/usr/bin/env python3
"""Post-flight and record one corpus attempt: tests, trace, evidence, judgment, ledger.

Corpus-agnostic: works for any dispatch-queue run directory using the isolated
layout (work/solution.py produced by the agent; test tiers staged under
verify/). The solver's solution is copied next to the staged tests so the
suites can import it, then every present tier runs in one bounded pytest
invocation. The references complete these suites in under a second; a solution
that cannot finish within the budget has failed the tests on resources.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TASKS = REPO / ".kamino" / "evals" / "tasks"
VERIFY_TIMEOUT_SECONDS = 300


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True, help="Eval task id (keys details/, outcomes/, ledger records).")
    parser.add_argument("--run-dir", required=True, help="Dispatch-queue run directory for this attempt.")
    parser.add_argument("--model", required=True, help="Model that ran the attempt.")
    parser.add_argument("--effort", required=True, help="Effort that ran the attempt.")
    parser.add_argument("--started-at", required=True, help="Attempt start time (ISO-8601, Z suffix).")
    parser.add_argument("--ended-at", default="now", help="Attempt end time (ISO-8601, Z suffix) or 'now'.")
    parser.add_argument("--attempt", type=int, default=1, help="Attempt number. Defaults to 1.")
    parser.add_argument("--ledger", default=str(TASKS / "task-outcome-ledger.jsonl"), help="Ledger JSONL to append to.")
    parser.add_argument("--tasks-root", default=str(TASKS), help="Eval tasks root holding details/ and outcomes/. Defaults to the repo's.")
    parser.add_argument("--format", choices=["json"], required=True, help="Output format.")
    return parser


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, cwd=REPO, check=False)
    if check and result.returncode != 0:
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stderr}")
    return result


def parse_passed_count(output: str) -> int:
    matches = re.findall(r"(\d+) passed", output)
    return int(matches[-1]) if matches else 0


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    run_dir = Path(args.run_dir).resolve()
    run_id = run_dir.name
    ended_at = args.ended_at
    if ended_at == "now":
        ended_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    solution = run_dir / "work" / "solution.py"
    agent_files = sorted(run_dir.glob("01-*.md"))
    if len(agent_files) != 1:
        raise SystemExit(f"expected exactly one 01-*.md agent file in {run_dir}, found {len(agent_files)}")
    agent_file = agent_files[0]

    route_file = run_dir / "route-decision.json"
    if route_file.is_file():
        route = json.loads(route_file.read_text(encoding="utf-8"))
        blueprints = route.get("agent_blueprints_used") or []
        blueprint = blueprints[0] if blueprints else agent_file.name[3:]
    else:
        blueprint = agent_file.name[3:]

    solution_exists = solution.is_file() and solution.stat().st_size > 0
    no_tokens = False
    if solution_exists:
        no_tokens = run([str(REPO / ".kamino" / "scripts" / "template-replace-completed.sh"), str(solution)], check=False).returncode == 0

    verify_root = run_dir / "verify"
    if verify_root.is_dir():
        # Isolated layout: the agent never saw the tests; stage its solution
        # next to them so the suites can import it, then run every tier.
        if solution_exists:
            shutil.copy2(solution, verify_root / "solution.py")
        test_dirs = [verify_root / "tests"]
        if (verify_root / "tests_hidden").is_dir():
            test_dirs.append(verify_root / "tests_hidden")
    else:
        test_dirs = [run_dir / "work" / "tests"]

    verify_command = ["uv", "run", "--project", str(REPO), "pytest", *[str(path) for path in test_dirs], "-q"]
    try:
        verify = subprocess.run(verify_command, capture_output=True, text=True, cwd=REPO, check=False, timeout=VERIFY_TIMEOUT_SECONDS)
        tests_passed = verify.returncode == 0
        verify_exit_code: int | None = verify.returncode
        verify_output = f"{verify.stdout}\n{verify.stderr}"
    except subprocess.TimeoutExpired as exc:
        tests_passed = False
        verify_exit_code = None
        captured_out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        captured_err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        verify_output = f"{captured_out}\n{captured_err}\nVERIFICATION TIMED OUT after {VERIFY_TIMEOUT_SECONDS}s"
    passed_count = parse_passed_count(verify_output)

    status = "ok" if (solution_exists and no_tokens and tests_passed) else "failed"
    duration = (datetime.fromisoformat(ended_at.replace("Z", "+00:00")) - datetime.fromisoformat(args.started_at.replace("Z", "+00:00"))).total_seconds()

    trace_record = {
        "schema_version": "kamino451.run-trace.v1",
        "run_id": run_id,
        "step": 1,
        "attempt": args.attempt,
        "agent_file": str(agent_file),
        "blueprint": blueprint,
        "model": args.model,
        "effort": args.effort,
        "started_at": args.started_at,
        "ended_at": ended_at,
        "duration_seconds": duration,
        "status": status,
        "output_path": str(solution),
        "verdict": None,
        "error": None if solution_exists else "agent did not write the solution file",
        "subagent_summary": None,
        "verification": {
            "output_non_empty": solution_exists,
            "no_template_tokens": no_tokens,
            "verification_command": " ".join(verify_command),
            "exit_code": verify_exit_code,
            "tests_passed": tests_passed,
            "tests_passed_count": passed_count,
            "note": "step judged from disk state and bounded test run",
        },
    }
    record_path = run_dir / "trace-record.json"
    record_path.write_text(json.dumps(trace_record, indent=2, sort_keys=True), encoding="utf-8")
    run(["uv", "run", str(REPO / ".kamino" / "evals" / "scripts" / "run_trace_write.py"), "--trace", str(run_dir / "trace.jsonl"), "--record", str(record_path), "--format", "json"])

    evidence = {
        "execution_status": "completed" if status == "ok" else "failed",
        "output_paths": [str(solution)],
        "verification_evidence": {
            "outputs_non_empty": solution_exists,
            "no_template_tokens": no_tokens,
            "verification_command": " ".join(verify_command),
            "verification_exit_code": verify_exit_code,
            "tests_passed": tests_passed,
            "tests_passed_count": passed_count,
            "trace_path": str(run_dir / "trace.jsonl"),
        },
    }
    evidence_path = run_dir / "run-evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")

    tasks_root = Path(args.tasks_root).resolve()
    suffix = "" if args.attempt == 1 else f"-a{args.attempt}"
    judgment_path = tasks_root / "outcomes" / f"{args.task_id}{suffix}-success.json"
    run(["uv", "run", str(REPO / ".kamino" / "evals" / "scripts" / "success_judgment_from_tests.py"), "--run-evidence", str(evidence_path), "--output", str(judgment_path), "--format", "json"])

    ledger_out = json.loads(
        run(
            [
                "uv", "run", str(REPO / ".kamino" / "evals" / "scripts" / "task_outcome_ledger_write.py"),
                "--ledger", args.ledger,
                "--task-detail", str(tasks_root / "details" / f"{args.task_id}{suffix}.json"),
                "--run-evidence", str(evidence_path),
                "--success-judgment", str(judgment_path),
                "--format", "json",
            ]
        ).stdout
    )

    print(
        json.dumps(
            {
                "task_id": args.task_id,
                "run_id": run_id,
                "model": args.model,
                "tests_passed": tests_passed,
                "tests_passed_count": passed_count,
                "status": status,
                "record_id": ledger_out["record_id"],
                "record_sequence": ledger_out["record_sequence"],
                "success": ledger_out["success"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
