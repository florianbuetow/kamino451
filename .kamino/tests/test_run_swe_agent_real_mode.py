"""Tests for the AutoResearch runner adapter's real mode."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[2]


GOOD_SOLUTION = "def add(a, b):\n    return a + b\n"
BAD_SOLUTION = "def add(a, b):\n    return a - b\n"

TEST_FILE = (
    '"""Tests for tiny-add."""\n\n'
    "import sys\n"
    "from pathlib import Path\n\n"
    "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n\n"
    "from solution import add\n\n\n"
    "def test_add_positive() -> None:\n    assert add(2, 3) == 5\n\n\n"
    "def test_add_negative() -> None:\n    assert add(-1, -2) == -3\n"
)


def write_workspace(tmp_path: Path, *, solution_writer: str | None) -> Path:
    """Copy the runner into tmp with a real-mode config and a tiny corpus task."""
    runner_source = repo_root() / ".kamino" / "tests" / "fixtures" / "auto-research" / "run_swe_agent.py"
    (tmp_path / "run_swe_agent.py").write_text(runner_source.read_text(encoding="utf-8"), encoding="utf-8")

    task_dir = tmp_path / "corpus" / "tiny-add"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "task.md").write_text("# Tiny Add\n\nImplement add(a, b).\n", encoding="utf-8")
    (task_dir / "tests" / "test_solution.py").write_text(TEST_FILE, encoding="utf-8")

    if solution_writer is None:
        agent_command = ["python3", "-c", "pass"]
    else:
        agent_command = ["python3", "-c", f"open('solution.py', 'w').write({solution_writer!r})"]

    config = {
        "mode": "real",
        "repo_root": ".",
        "model": "haiku",
        "agent_command": agent_command,
        "test_runner": ["uv", "run", "--project", str(repo_root()), "pytest", "{workdir}/tests", "-q"],
    }
    (tmp_path / "runner-config.json").write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
    (tmp_path / "agent.md").write_text("You are a coding agent. Explore, then test your work.\n", encoding="utf-8")
    return tmp_path


def run_runner(workspace: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the runner exactly the way eval.py does."""
    task = {
        "id": "tiny-add",
        "corpus_dir": "corpus/tiny-add",
        "issue_description": "Implement add.",
        "success_criteria": "Tests pass.",
    }
    return subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(repo_root()),
            str(workspace / "run_swe_agent.py"),
            "--prompt-file",
            str(workspace / "agent.md"),
            "--task",
            json.dumps(task, sort_keys=True),
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )


def test_real_mode_reports_success_when_tests_pass(tmp_path: Path) -> None:
    """A correct solution must yield TASK_SUCCESS from the ground-truth tests."""
    workspace = write_workspace(tmp_path, solution_writer=GOOD_SOLUTION)

    process = run_runner(workspace)

    assert process.returncode == 0, process.stderr
    assert "TASK_SUCCESS" in process.stdout
    assert "FAILURE_MODE:" not in process.stdout


def test_real_mode_reports_failure_when_tests_fail(tmp_path: Path) -> None:
    """A wrong solution must yield TASK_FAILED with a deterministic failure tag."""
    workspace = write_workspace(tmp_path, solution_writer=BAD_SOLUTION)

    process = run_runner(workspace)

    assert process.returncode == 0, process.stderr
    assert "TASK_FAILED" in process.stdout
    assert "FAILURE_MODE:" in process.stdout


def test_real_mode_flags_missing_solution_file(tmp_path: Path) -> None:
    """An agent that writes nothing is classified as editing_wrong_files."""
    workspace = write_workspace(tmp_path, solution_writer=None)

    process = run_runner(workspace)

    assert process.returncode == 0, process.stderr
    assert "TASK_FAILED" in process.stdout
    assert "FAILURE_MODE:editing_wrong_files" in process.stdout


def test_simulate_mode_is_the_default_without_config(tmp_path: Path) -> None:
    """Without runner-config.json the runner keeps the deterministic simulation."""
    runner_source = repo_root() / ".kamino" / "tests" / "fixtures" / "auto-research" / "run_swe_agent.py"
    (tmp_path / "run_swe_agent.py").write_text(runner_source.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "agent.md").write_text("plain prompt\n", encoding="utf-8")
    task = {"id": "sim", "issue_description": "Fix the bug.", "success_criteria": "Done."}

    process = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(repo_root()),
            str(tmp_path / "run_swe_agent.py"),
            "--prompt-file",
            str(tmp_path / "agent.md"),
            "--task",
            json.dumps(task, sort_keys=True),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    assert "TASK_FAILED" in process.stdout
    assert "FAILURE_MODE:weak_codebase_exploration" in process.stdout
