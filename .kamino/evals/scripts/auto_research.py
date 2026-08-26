#!/usr/bin/env python3
"""AutoResearch workspace wrapper for keep-or-revert prompt optimization."""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REQUIRED_BASELINE_FILES = [
    ".gitignore",
    "agent.md",
    "program.md",
    "eval.py",
    "tasks.json",
    "run_swe_agent.py",
    "best_score.txt",
]


@dataclass(frozen=True)
class EvaluationRun:
    """Captured evaluation score and process output."""

    score: float
    stdout: str
    stderr: str


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Manage a Kamino451 AutoResearch workspace.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize the nested AutoResearch git repository.")
    init_parser.add_argument("--workspace", required=True, help="Path to the AutoResearch workspace.")

    evaluate_parser = subparsers.add_parser("evaluate-change", help="Evaluate the current agent.md and keep or revert it.")
    evaluate_parser.add_argument("--workspace", required=True, help="Path to the AutoResearch workspace.")

    loop_parser = subparsers.add_parser("run-loop", help="Run an external improver command for several iterations.")
    loop_parser.add_argument("--workspace", required=True, help="Path to the AutoResearch workspace.")
    loop_parser.add_argument("--iterations", required=True, type=int, help="Number of optimization iterations.")
    loop_parser.add_argument("--improver-command", required=True, help="Shell-like command line for the meta-agent improver.")

    return parser.parse_args(argv)


def resolve_workspace(raw_workspace: str) -> Path:
    """Resolve and validate a workspace path."""
    workspace = Path(raw_workspace).resolve()
    if not workspace.is_dir():
        raise FileNotFoundError(f"AutoResearch workspace does not exist: {workspace}")
    return workspace


def run_process(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and capture output."""
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True)


def run_checked(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and raise on failure."""
    result = run_process(command, cwd)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}\n{result.stderr}")
    return result


def git_command(workspace: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run git inside the AutoResearch workspace."""
    return run_checked(["git", *args], workspace)


def git_commit(workspace: Path, message: str) -> None:
    """Create a git commit with local identity configuration."""
    git_command(
        workspace,
        [
            "-c",
            "user.name=kamino451",
            "-c",
            "user.email=kamino451@example.invalid",
            "commit",
            "-m",
            message,
        ],
    )


def ensure_required_files(workspace: Path) -> None:
    """Ensure the workspace contains all baseline files."""
    missing: list[str] = []
    for relative_file in REQUIRED_BASELINE_FILES:
        path = workspace / relative_file
        if not path.is_file():
            missing.append(relative_file)
    if len(missing) > 0:
        raise FileNotFoundError(f"AutoResearch workspace is missing required files: {', '.join(missing)}")


def initialize_workspace_git(workspace: Path) -> None:
    """Initialize the nested git repository and baseline commit."""
    best_score_path = workspace / "best_score.txt"
    if not best_score_path.exists():
        best_score_path.write_text("-inf\n", encoding="utf-8")
    ensure_required_files(workspace)
    git_dir = workspace / ".git"
    if not git_dir.exists():
        git_command(workspace, ["init"])
    git_command(workspace, ["add", *REQUIRED_BASELINE_FILES])
    status = git_command(workspace, ["status", "--short"]).stdout.strip()
    if status == "":
        print("AutoResearch git repository already has a clean baseline.")
        return
    commit_message = "\n".join(
        [
            "Establish prompt optimization baseline",
            "",
            "Constraint: AutoResearch requires one editable artifact and an immutable eval harness.",
            "Rejected: Tracking generated eval outputs | they change every run and do not define the baseline.",
            "Confidence: high",
            "Scope-risk: narrow",
            "Directive: During optimization, edit only agent.md and let the wrapper commit improvements.",
            "Tested: Baseline files were staged explicitly.",
            "Not-tested: Real SWE agent execution is still adapter-specific.",
        ]
    )
    git_commit(workspace, commit_message)
    print("Initialized AutoResearch git baseline.")


def git_status_lines(workspace: Path) -> list[str]:
    """Return porcelain status lines for tracked files."""
    output = git_command(workspace, ["status", "--short", "--untracked-files=no"]).stdout
    return [line for line in output.splitlines() if line.strip() != ""]


def status_path(status_line: str) -> str:
    """Extract the path from one git porcelain status line."""
    if len(status_line) < 4:
        raise ValueError(f"unexpected git status line: {status_line}")
    return status_line[3:]


def ensure_only_agent_changed(workspace: Path) -> None:
    """Reject candidate states that modified files other than agent.md."""
    changed_paths = [status_path(line) for line in git_status_lines(workspace)]
    forbidden_paths = [path for path in changed_paths if path != "agent.md"]
    if len(forbidden_paths) > 0:
        raise RuntimeError(f"only agent.md may change during optimization; found: {', '.join(forbidden_paths)}")


def read_best_score(workspace: Path) -> float:
    """Read the current best score."""
    score_path = workspace / "best_score.txt"
    raw_score = score_path.read_text(encoding="utf-8").strip()
    if raw_score == "":
        raise ValueError(f"best score file is empty: {score_path}")
    return float(raw_score)


def write_best_score(workspace: Path, score: float) -> None:
    """Write the current best score."""
    (workspace / "best_score.txt").write_text(f"{score}\n", encoding="utf-8")


def parse_final_score(output: str) -> float:
    """Parse FINAL_SCORE from eval output."""
    matches = re.findall(r"(?m)^FINAL_SCORE:([0-9]+(?:\.[0-9]+)?)\s*$", output)
    if len(matches) == 0:
        raise ValueError("evaluation output did not contain FINAL_SCORE:<float>")
    return float(matches[-1])


def run_evaluation(workspace: Path) -> EvaluationRun:
    """Run eval.py through uv and parse the primary score."""
    result = run_process(["uv", "run", "eval.py"], workspace)
    combined_output = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0:
        raise RuntimeError(f"evaluation failed ({result.returncode})\n{combined_output}")
    return EvaluationRun(score=parse_final_score(combined_output), stdout=result.stdout, stderr=result.stderr)


def revert_agent(workspace: Path) -> None:
    """Revert a non-improving agent.md candidate."""
    git_command(workspace, ["checkout", "--", "agent.md"])


def commit_improvement(workspace: Path, score: float, evaluation: EvaluationRun) -> None:
    """Commit an improving agent.md candidate."""
    write_best_score(workspace, score)
    git_command(workspace, ["add", "agent.md", "best_score.txt"])
    commit_message = "\n".join(
        [
            f"Improve prompt score to {score:.4f}",
            "",
            "Constraint: AutoResearch keeps only candidates that strictly improve the scalar metric.",
            "Rejected: Non-improving prompt candidates | wrapper reverts them automatically.",
            "Confidence: medium",
            "Scope-risk: narrow",
            "Directive: Keep eval.py, tasks.json, and run_swe_agent.py immutable during this series.",
            "Tested: uv run eval.py",
            f"Not-tested: External holdout set beyond this workspace; stderr bytes={len(evaluation.stderr)}.",
        ]
    )
    git_commit(workspace, commit_message)


def apply_keep_or_revert(workspace: Path, evaluation: EvaluationRun) -> str:
    """Apply the keep-or-revert rule for one candidate."""
    best_score = read_best_score(workspace)
    if evaluation.score > best_score:
        commit_improvement(workspace, evaluation.score, evaluation)
        return f"kept improvement: {best_score:.4f} -> {evaluation.score:.4f}"
    revert_agent(workspace)
    return f"reverted non-improvement: {evaluation.score:.4f} <= {best_score:.4f}"


def evaluate_change(workspace: Path) -> str:
    """Evaluate the current candidate and keep or revert it."""
    ensure_required_files(workspace)
    ensure_only_agent_changed(workspace)
    evaluation = run_evaluation(workspace)
    result = apply_keep_or_revert(workspace, evaluation)
    print(evaluation.stdout)
    if evaluation.stderr.strip() != "":
        print(evaluation.stderr, file=sys.stderr)
    print(result)
    return result


def run_improver_command(workspace: Path, command_line: str) -> None:
    """Run one external meta-agent improver command."""
    command = shlex.split(command_line)
    if len(command) == 0:
        raise ValueError("improver command must not be empty")
    result = run_process(command, workspace)
    if result.returncode != 0:
        raise RuntimeError(f"improver command failed ({result.returncode})\n{result.stdout}\n{result.stderr}")
    if result.stdout.strip() != "":
        print(result.stdout)
    if result.stderr.strip() != "":
        print(result.stderr, file=sys.stderr)


def run_loop(workspace: Path, iterations: int, improver_command: str) -> None:
    """Run an external improver command and keep-or-revert loop."""
    if iterations < 1:
        raise ValueError("--iterations must be at least 1")
    for iteration in range(1, iterations + 1):
        print(f"=== AutoResearch iteration {iteration}/{iterations} ===")
        run_improver_command(workspace, improver_command)
        evaluate_change(workspace)


def main(argv: list[str]) -> int:
    """Run the AutoResearch wrapper CLI."""
    try:
        args = parse_args(argv)
        command = args.command
        if not isinstance(command, str):
            raise TypeError("command must be a string")
        workspace_arg = args.workspace
        if not isinstance(workspace_arg, str):
            raise TypeError("--workspace must be a string")
        workspace = resolve_workspace(workspace_arg)

        if command == "init":
            initialize_workspace_git(workspace)
            return 0
        if command == "evaluate-change":
            evaluate_change(workspace)
            return 0
        if command == "run-loop":
            iterations = args.iterations
            improver_command = args.improver_command
            if not isinstance(iterations, int):
                raise TypeError("--iterations must be an int")
            if not isinstance(improver_command, str):
                raise TypeError("--improver-command must be a string")
            run_loop(workspace, iterations, improver_command)
            return 0
        raise ValueError(f"unsupported command: {command}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
