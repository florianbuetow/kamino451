#!/usr/bin/env python3
"""Immutable AutoResearch evaluation harness for the target agent.md file."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TASKS_FILE = BASE_DIR / "tasks.json"
PROMPT_FILE = BASE_DIR / "agent.md"
RUNNER_FILE = BASE_DIR / "run_swe_agent.py"
RESULTS_FILE = BASE_DIR / "last_eval_results.json"
FAILURE_SUMMARY_FILE = BASE_DIR / "failure_mode_summary.md"
TIMEOUT_PER_TASK_SECONDS = 300

FAILURE_MODE_LABELS = {
    "weak_codebase_exploration": "Weak codebase exploration",
    "editing_wrong_files": "Editing wrong files",
    "no_test_verification": "No test verification",
    "hallucinating_code": "Hallucinating code",
    "premature_giving_up": "Premature giving up",
    "poor_context_management": "Poor context management",
    "bad_final_output_format": "Bad final output format",
    "introducing_new_bugs": "Introducing new bugs",
    "ineffective_tool_use": "Ineffective tool use",
    "ignoring_edge_cases": "Ignoring edge cases",
    "unclear_task_or_eval": "Unclear task or eval",
    "unknown_failure": "Unknown failure",
}


@dataclass(frozen=True)
class TaskOutcome:
    """Result of running one validation task."""

    task_id: str
    success: bool
    steps: int
    tokens: int
    latency_seconds: float
    raw_output: str
    returncode: int
    failure_modes: list[str]


def read_required_text(path: Path, label: str) -> str:
    """Read a required UTF-8 text file."""
    if not path.is_file():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    content = path.read_text(encoding="utf-8")
    if content.strip() == "":
        raise ValueError(f"{label} is empty: {path}")
    return content


def load_agent_prompt() -> str:
    """Load the latest candidate prompt."""
    return read_required_text(PROMPT_FILE, "agent prompt")


def require_task_string(task: dict[str, object], key: str) -> str:
    """Read a required string from a task object."""
    value = task[key]
    if not isinstance(value, str):
        raise TypeError(f"task field must be a string: {key}")
    if value.strip() == "":
        raise ValueError(f"task field must not be empty: {key}")
    return value


def validate_task(task: object) -> dict[str, object]:
    """Validate one task object."""
    if not isinstance(task, dict):
        raise TypeError("each task must be a JSON object")
    require_task_string(task, "id")
    require_task_string(task, "issue_description")
    require_task_string(task, "success_criteria")
    return task


def load_tasks() -> list[dict[str, object]]:
    """Load validation tasks."""
    raw_text = read_required_text(TASKS_FILE, "tasks file")
    parsed = json.loads(raw_text)
    if not isinstance(parsed, list):
        raise TypeError("tasks file must contain a JSON array")
    if len(parsed) == 0:
        raise ValueError("tasks file must contain at least one task")
    return [validate_task(task) for task in parsed]


def parse_int_signal(output: str, signal_name: str) -> int:
    """Parse an integer signal from runner output."""
    matches = re.findall(rf"(?m)^{re.escape(signal_name)}:(\d+)\s*$", output)
    if len(matches) == 0:
        return 0
    return int(matches[-1])


def parse_failure_modes(output: str, success: bool) -> list[str]:
    """Parse deterministic failure mode tags from runner output."""
    if success:
        return []

    modes = re.findall(r"(?m)^FAILURE_MODE:([a-z0-9_]+)\s*$", output)
    known_modes = [mode for mode in modes if mode in FAILURE_MODE_LABELS]
    if len(known_modes) > 0:
        return sorted(set(known_modes))

    lowered = output.lower()
    inferred: list[str] = []
    if "not found" in lowered or "no such file" in lowered:
        inferred.append("hallucinating_code")
    if "timeout" in lowered or "stuck" in lowered:
        inferred.append("premature_giving_up")
    if "test" not in lowered:
        inferred.append("no_test_verification")
    if len(inferred) == 0:
        inferred.append("unknown_failure")
    return sorted(set(inferred))


def run_agent_on_task(task: dict[str, object], prompt: str) -> TaskOutcome:
    """Run the current agent prompt on one task."""
    task_id = require_task_string(task, "id")
    command = [
        "uv",
        "run",
        str(RUNNER_FILE),
        "--prompt-file",
        str(PROMPT_FILE),
        "--task",
        json.dumps(task, sort_keys=True),
    ]
    started_at = time.monotonic()
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_PER_TASK_SECONDS,
            cwd=BASE_DIR,
        )
        combined_output = f"{result.stdout}\n{result.stderr}"
        success = "TASK_SUCCESS" in combined_output and result.returncode == 0
        latency_seconds = round(time.monotonic() - started_at, 2)
        steps = parse_int_signal(combined_output, "STEPS")
        tokens = parse_int_signal(combined_output, "TOKENS")
        return TaskOutcome(
            task_id=task_id,
            success=success,
            steps=steps,
            tokens=tokens,
            latency_seconds=latency_seconds,
            raw_output=combined_output[-2000:],
            returncode=result.returncode,
            failure_modes=parse_failure_modes(combined_output, success),
        )
    except subprocess.TimeoutExpired as exc:
        timeout_output = f"Task timed out after {TIMEOUT_PER_TASK_SECONDS} seconds"
        if exc.stdout is not None:
            timeout_output = f"{timeout_output}\n{exc.stdout}"
        if exc.stderr is not None:
            timeout_output = f"{timeout_output}\n{exc.stderr}"
        return TaskOutcome(
            task_id=task_id,
            success=False,
            steps=0,
            tokens=0,
            latency_seconds=float(TIMEOUT_PER_TASK_SECONDS),
            raw_output=timeout_output[-2000:],
            returncode=-1,
            failure_modes=["premature_giving_up"],
        )


def outcome_to_dict(outcome: TaskOutcome) -> dict[str, object]:
    """Convert a task outcome to JSON-safe data."""
    return {
        "task_id": outcome.task_id,
        "success": outcome.success,
        "steps": outcome.steps,
        "tokens": outcome.tokens,
        "latency_seconds": outcome.latency_seconds,
        "raw_output": outcome.raw_output,
        "returncode": outcome.returncode,
        "failure_modes": outcome.failure_modes,
    }


def aggregate_failure_modes(outcomes: list[TaskOutcome]) -> dict[str, int]:
    """Aggregate failure mode counts."""
    counts: dict[str, int] = {}
    for outcome in outcomes:
        for mode in outcome.failure_modes:
            if mode not in counts:
                counts[mode] = 0
            counts[mode] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def render_failure_summary(outcomes: list[TaskOutcome], counts: dict[str, int]) -> str:
    """Render a Markdown failure summary for the prompt-improver agent."""
    failed_outcomes = [outcome for outcome in outcomes if not outcome.success]
    lines = [
        "# Failure Mode Summary",
        "",
        "This file is generated by `eval.py` for the next AutoResearch prompt edit.",
        "",
        "## Aggregate Failure Modes",
        "",
    ]
    if len(counts) == 0:
        lines.append("- No failed tasks.")
    else:
        for mode, count in counts.items():
            label = FAILURE_MODE_LABELS[mode]
            lines.append(f"- `{mode}` ({label}): {count}")

    lines.extend(
        [
            "",
            "## Failed Tasks",
            "",
        ]
    )
    if len(failed_outcomes) == 0:
        lines.append("- None.")
    else:
        for outcome in failed_outcomes:
            mode_text = ", ".join(f"`{mode}`" for mode in outcome.failure_modes)
            lines.append(f"- `{outcome.task_id}`: {mode_text}")

    lines.extend(
        [
            "",
            "## LLM Judge Boundary",
            "",
            "If these deterministic tags are insufficient, instantiate `.claude/agents/autoresearch-llm-evaluator.md` as a subagent.",
            "Do not edit `eval.py` to add an ad hoc LLM judge.",
        ]
    )
    return "\n".join(lines)


def save_results(score_data: dict[str, object], failure_summary: str) -> None:
    """Persist detailed eval artifacts."""
    RESULTS_FILE.write_text(json.dumps(score_data, indent=2, sort_keys=True), encoding="utf-8")
    FAILURE_SUMMARY_FILE.write_text(failure_summary, encoding="utf-8")


def evaluate() -> float:
    """Run the full evaluation and return the primary scalar score."""
    print("=" * 60)
    print("Starting SWE Agent Evaluation")
    print("=" * 60)

    prompt = load_agent_prompt()
    tasks = load_tasks()
    print(f"Loaded {len(tasks)} tasks")
    print(f"Using prompt from: {PROMPT_FILE}")
    print("")

    outcomes: list[TaskOutcome] = []
    for index, task in enumerate(tasks):
        task_id = require_task_string(task, "id")
        print(f"[{index + 1}/{len(tasks)}] Running task: {task_id} ... ", end="", flush=True)
        outcome = run_agent_on_task(task, prompt)
        outcomes.append(outcome)
        if outcome.success:
            print("SUCCESS")
        else:
            print("FAILED")

    total = len(outcomes)
    successes = sum(1 for outcome in outcomes if outcome.success)
    success_rate = successes / total
    avg_steps = sum(outcome.steps for outcome in outcomes) / total
    avg_tokens = sum(outcome.tokens for outcome in outcomes) / total
    avg_latency = sum(outcome.latency_seconds for outcome in outcomes) / total
    failure_counts = aggregate_failure_modes(outcomes)

    score_data: dict[str, object] = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "primary_metric": "success_rate",
        "higher_is_better": True,
        "success_rate": round(success_rate, 4),
        "num_tasks": total,
        "num_successes": successes,
        "avg_steps": round(avg_steps, 1),
        "avg_tokens": round(avg_tokens),
        "avg_latency_seconds": round(avg_latency, 2),
        "failure_mode_counts": failure_counts,
        "llm_evaluator_agent": ".claude/agents/autoresearch-llm-evaluator.md",
        "detailed_results": [outcome_to_dict(outcome) for outcome in outcomes],
    }
    failure_summary = render_failure_summary(outcomes, failure_counts)
    save_results(score_data, failure_summary)

    print("")
    print("=" * 60)
    print("EVALUATION COMPLETE")
    print("=" * 60)
    print(f"Success Rate:     {success_rate * 100:.2f}% ({successes}/{total})")
    print(f"Average Steps:    {avg_steps:.1f}")
    print(f"Average Tokens:   {avg_tokens:,.0f}")
    print(f"Average Latency:  {avg_latency:.1f}s")
    print(f"Detailed results saved to: {RESULTS_FILE}")
    print(f"Failure summary saved to: {FAILURE_SUMMARY_FILE}")
    print("=" * 60)
    return success_rate


if __name__ == "__main__":
    final_score = evaluate()
    print(f"FINAL_SCORE:{final_score}")
    sys.exit(0)
