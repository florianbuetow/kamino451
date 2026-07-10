#!/usr/bin/env python3
"""SWE agent runner adapter used by the AutoResearch eval harness.

Two modes, selected by `runner-config.json` next to this file:

- No config file, or `"mode": "simulate"` -> the deterministic simulation
  (offline, used by tests and as the safe default).
- `"mode": "real"` -> run the current agent prompt on a real corpus task via a
  configurable agent command, then run the task's tests as ground truth.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent
CONFIG_FILE = RUNNER_DIR / "runner-config.json"

AGENT_TIMEOUT_SECONDS = 240
TEST_TIMEOUT_SECONDS = 120


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Run one SWE task with the current agent prompt.")
    parser.add_argument("--prompt-file", required=True, help="Path to agent.md.")
    parser.add_argument("--task", required=True, help="JSON-encoded task record.")
    return parser.parse_args(argv)


def load_prompt(prompt_file: str) -> str:
    """Load the system prompt from disk."""
    prompt_path = Path(prompt_file)
    if not prompt_path.is_file():
        raise FileNotFoundError(f"prompt file does not exist: {prompt_path}")
    prompt = prompt_path.read_text(encoding="utf-8")
    if prompt.strip() == "":
        raise ValueError(f"prompt file is empty: {prompt_path}")
    return prompt


def load_task(task_json: str) -> dict[str, object]:
    """Load one task record from JSON."""
    parsed = json.loads(task_json)
    if not isinstance(parsed, dict):
        raise TypeError("--task JSON must decode to an object")
    return parsed


def require_task_text(task: dict[str, object], key: str) -> str:
    """Read a required task string field."""
    value = task[key]
    if not isinstance(value, str):
        raise TypeError(f"task field must be a string: {key}")
    if value.strip() == "":
        raise ValueError(f"task field must not be empty: {key}")
    return value


def load_runner_config() -> dict[str, object] | None:
    """Load runner-config.json when present; None selects simulate mode."""
    if not CONFIG_FILE.is_file():
        return None
    parsed = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise TypeError("runner-config.json must contain a JSON object")
    mode = parsed.get("mode")
    if mode not in {"simulate", "real"}:
        raise ValueError("runner-config.json mode must be simulate or real")
    return parsed


def require_config_command(config: dict[str, object], key: str) -> list[str]:
    """Read a required command template (list of strings) from the config."""
    value = config[key]
    if not isinstance(value, list) or len(value) == 0 or not all(isinstance(item, str) for item in value):
        raise TypeError(f"runner-config.json {key} must be a non-empty list of strings")
    return list(value)


def require_config_text(config: dict[str, object], key: str) -> str:
    """Read a required string field from the config."""
    if key not in config:
        raise ValueError(f"runner-config.json is missing required key: {key}")
    value = config[key]
    if not isinstance(value, str):
        raise TypeError(f"runner-config.json {key} must be a string")
    if value.strip() == "":
        raise ValueError(f"runner-config.json {key} must not be empty")
    return value


def substitute_tokens(template: list[str], replacements: dict[str, str]) -> list[str]:
    """Substitute {token} placeholders in a command template."""
    command: list[str] = []
    for element in template:
        for token, replacement in replacements.items():
            element = element.replace("{" + token + "}", replacement)
        command.append(element)
    return command


def simulate(task: dict[str, object], system_prompt: str) -> int:
    """Deterministic offline simulation (the original starter behavior)."""
    task_id = require_task_text(task, "id")
    issue = require_task_text(task, "issue_description")

    print(f"[Agent] Starting task: {task_id}")
    print(f"[Agent] Issue: {issue[:120]}")

    prompt_lower = system_prompt.lower()
    issue_lower = issue.lower()
    has_exploration_instruction = "search" in prompt_lower or "explore" in prompt_lower
    has_verification_instruction = "test" in prompt_lower or "verification" in prompt_lower
    issue_looks_like_direct_bug = "bug" in issue_lower or "fix" in issue_lower

    success = issue_looks_like_direct_bug and has_exploration_instruction and has_verification_instruction

    if success:
        print("TASK_SUCCESS")
        print("All relevant tests passed.")
        print("STEPS:14")
        print("TOKENS:8200")
        return 0

    print("TASK_FAILED")
    print("Agent stopped before enough repository exploration and test verification.")
    print("FAILURE_MODE:weak_codebase_exploration")
    print("FAILURE_MODE:no_test_verification")
    print("STEPS:8")
    print("TOKENS:4500")
    return 0


def parse_pytest_counts(output: str) -> tuple[int, int]:
    """Extract (passed, failed) counts from a pytest summary line."""
    import re

    passed_matches = re.findall(r"(\d+) passed", output)
    failed_matches = re.findall(r"(\d+) failed", output)
    passed = int(passed_matches[-1]) if passed_matches else 0
    failed = int(failed_matches[-1]) if failed_matches else 0
    return passed, failed


def run_real(task: dict[str, object], system_prompt: str, config: dict[str, object]) -> int:
    """Run the agent for real against a corpus task; tests are the ground truth."""
    task_id = require_task_text(task, "id")
    corpus_dir_value = require_task_text(task, "corpus_dir")
    repo_root_value = require_config_text(config, "repo_root")
    model = require_config_text(config, "model")
    agent_command_template = require_config_command(config, "agent_command")
    test_runner_template = require_config_command(config, "test_runner")

    repo_root = (RUNNER_DIR / repo_root_value).resolve()
    corpus_dir = repo_root / corpus_dir_value
    task_md = corpus_dir / "task.md"
    tests_dir = corpus_dir / "tests"
    if not task_md.is_file():
        raise FileNotFoundError(f"corpus task.md does not exist: {task_md}")
    if not tests_dir.is_dir():
        raise FileNotFoundError(f"corpus tests directory does not exist: {tests_dir}")

    workdir = Path(tempfile.mkdtemp(prefix=f"kamino-ar-{task_id}-"))
    shutil.copy2(task_md, workdir / "task.md")
    shutil.copytree(tests_dir, workdir / "tests")
    solution_path = workdir / "solution.py"

    print(f"[Agent] Starting task: {task_id}")
    print(f"[Agent] Workdir: {workdir}")

    full_prompt = (
        f"{system_prompt}\n\n"
        f"## Task\n\n{task_md.read_text(encoding='utf-8')}\n\n"
        f"## Where to work\n\n"
        f"Write your complete solution to exactly this file: {solution_path}\n"
        f"The tests that will verify it are in: {workdir / 'tests'}\n"
        f"You may run them to self-check. Do not modify the tests.\n"
    )
    prompt_file = workdir / "prompt.md"
    prompt_file.write_text(full_prompt, encoding="utf-8")

    replacements = {
        "prompt": full_prompt,
        "prompt_file": str(prompt_file),
        "workdir": str(workdir),
        "model": model,
        "repo_root": str(repo_root),
    }
    agent_command = substitute_tokens(agent_command_template, replacements)
    try:
        agent_result = subprocess.run(
            agent_command,
            capture_output=True,
            text=True,
            timeout=AGENT_TIMEOUT_SECONDS,
            cwd=workdir,
            check=False,
        )
        print(f"[Agent] Agent command exit code: {agent_result.returncode}")
    except subprocess.TimeoutExpired:
        print("TASK_FAILED")
        print(f"Agent command timed out after {AGENT_TIMEOUT_SECONDS} seconds")
        print("FAILURE_MODE:premature_giving_up")
        print("STEPS:0")
        print("TOKENS:0")
        return 0

    if not solution_path.is_file():
        print("TASK_FAILED")
        print(f"Agent did not write the required solution file: {solution_path}")
        print("FAILURE_MODE:editing_wrong_files")
        print("STEPS:0")
        print("TOKENS:0")
        return 0

    test_command = substitute_tokens(test_runner_template, replacements)
    try:
        test_result = subprocess.run(
            test_command,
            capture_output=True,
            text=True,
            timeout=TEST_TIMEOUT_SECONDS,
            cwd=workdir,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("TASK_FAILED")
        print(f"Ground-truth tests timed out after {TEST_TIMEOUT_SECONDS} seconds")
        print("FAILURE_MODE:premature_giving_up")
        print("STEPS:0")
        print("TOKENS:0")
        return 0

    test_output = f"{test_result.stdout}\n{test_result.stderr}"
    passed, failed = parse_pytest_counts(test_output)
    print(f"[Tests] exit code {test_result.returncode}: {passed} passed, {failed} failed")

    if test_result.returncode == 0:
        print("TASK_SUCCESS")
        print("All ground-truth tests passed.")
        print("STEPS:0")
        print("TOKENS:0")
        return 0

    print("TASK_FAILED")
    print(test_output[-1500:])
    if passed > 0 and failed > 0:
        print("FAILURE_MODE:ignoring_edge_cases")
    elif passed == 0 and failed == 0:
        print("FAILURE_MODE:bad_final_output_format")
    else:
        print("FAILURE_MODE:unknown_failure")
    print("STEPS:0")
    print("TOKENS:0")
    return 0


def main(argv: list[str]) -> int:
    """Run the adapter."""
    args = parse_args(argv)
    prompt_file = args.prompt_file
    task_json = args.task
    if not isinstance(prompt_file, str):
        raise TypeError("--prompt-file must be a string")
    if not isinstance(task_json, str):
        raise TypeError("--task must be a string")

    system_prompt = load_prompt(prompt_file)
    task = load_task(task_json)
    config = load_runner_config()

    if config is None or config.get("mode") == "simulate":
        return simulate(task, system_prompt)
    return run_real(task, system_prompt, config)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
