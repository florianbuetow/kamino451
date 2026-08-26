"""Tests for the Kamino AutoResearch scaffold."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType


def load_module(module_name: str, path: Path) -> ModuleType:
    """Load a Python module from a path."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None:
        raise AssertionError(f"could not load spec for {path}")
    if spec.loader is None:
        raise AssertionError(f"could not load loader for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_autoresearch_agents_define_required_roles() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    agents_dir = repo_root / ".claude" / "agents"

    improver = (agents_dir / "autoresearch-agent-improver.md").read_text(encoding="utf-8")
    program_author = (agents_dir / "autoresearch-program-author.md").read_text(encoding="utf-8")
    eval_author = (agents_dir / "autoresearch-eval-author.md").read_text(encoding="utf-8")
    llm_evaluator = (agents_dir / "autoresearch-llm-evaluator.md").read_text(encoding="utf-8")

    assert "name: autoresearch-agent-improver" in improver
    assert "autoresearch-program-author" in improver
    assert "autoresearch-eval-author" in improver
    assert "autoresearch-llm-evaluator" in improver
    assert "Only `agent.md` is editable" in improver

    assert "name: autoresearch-program-author" in program_author
    assert "program.md" in program_author
    assert "failure-mode catalog" in program_author

    assert "name: autoresearch-eval-author" in eval_author
    assert "FINAL_SCORE" in eval_author
    assert "failure_mode_summary.md" in eval_author

    assert "name: autoresearch-llm-evaluator" in llm_evaluator
    assert "weak_codebase_exploration" in llm_evaluator
    assert "strict JSON" in llm_evaluator


def test_eval_harness_generates_failure_mode_summary(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    source_dir = repo_root / ".kamino" / "tests" / "fixtures" / "auto-research"
    for filename in ["agent.md", "eval.py", "run_swe_agent.py"]:
        (tmp_path / filename).write_text((source_dir / filename).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "tasks.json").write_text(
        json.dumps(
            [
                {
                    "id": "task-one",
                    "repo": "example/repo",
                    "issue_description": "Improve validation error handling.",
                    "relevant_files": ["src/example.py"],
                    "test_command": "uv run pytest tests/test_example.py",
                    "success_criteria": "The validation test passes.",
                }
            ]
        ),
        encoding="utf-8",
    )

    eval_module = load_module("tmp_autoresearch_eval", tmp_path / "eval.py")
    score = eval_module.evaluate()

    assert score == 0.0
    results = json.loads((tmp_path / "last_eval_results.json").read_text(encoding="utf-8"))
    summary = (tmp_path / "failure_mode_summary.md").read_text(encoding="utf-8")
    assert results["success_rate"] == 0.0
    assert results["failure_mode_counts"]["weak_codebase_exploration"] == 1
    assert results["llm_evaluator_agent"] == ".claude/agents/autoresearch-llm-evaluator.md"
    assert "`weak_codebase_exploration`" in summary
    assert "autoresearch-llm-evaluator.md" in summary


def test_auto_research_parse_final_score() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    runner = load_module("kamino_auto_research", repo_root / ".kamino" / "evals" / "scripts" / "auto_research.py")

    assert runner.parse_final_score("noise\nFINAL_SCORE:0.625\n") == 0.625


def test_auto_research_init_creates_missing_best_score_without_readme(tmp_path: Path) -> None:
    """The documented workspace recipe should initialize without extra hidden files."""
    repo_root = Path(__file__).resolve().parents[2]
    runner = load_module("kamino_auto_research_init", repo_root / ".kamino" / "evals" / "scripts" / "auto_research.py")
    (tmp_path / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    for filename in ["agent.md", "program.md", "eval.py", "tasks.json", "run_swe_agent.py"]:
        (tmp_path / filename).write_text(f"{filename}\n", encoding="utf-8")

    runner.initialize_workspace_git(tmp_path)

    assert (tmp_path / "best_score.txt").read_text(encoding="utf-8") == "-inf\n"
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert "best_score.txt" in tracked
    assert "README.md" not in tracked


def test_keep_or_revert_reverts_non_improving_agent(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    runner = load_module("kamino_auto_research_revert", repo_root / ".kamino" / "evals" / "scripts" / "auto_research.py")

    (tmp_path / "agent.md").write_text("baseline\n", encoding="utf-8")
    (tmp_path / "best_score.txt").write_text("0.5\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "agent.md", "best_score.txt"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=kamino451",
            "-c",
            "user.email=kamino451@example.invalid",
            "commit",
            "-m",
            "baseline",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    (tmp_path / "agent.md").write_text("candidate\n", encoding="utf-8")

    evaluation = runner.EvaluationRun(score=0.4, stdout="FINAL_SCORE:0.4\n", stderr="")
    result = runner.apply_keep_or_revert(tmp_path, evaluation)

    assert result == "reverted non-improvement: 0.4000 <= 0.5000"
    assert (tmp_path / "agent.md").read_text(encoding="utf-8") == "baseline\n"
    assert (tmp_path / "best_score.txt").read_text(encoding="utf-8") == "0.5\n"


def test_keep_or_revert_commits_improving_agent(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    runner = load_module("kamino_auto_research_keep", repo_root / ".kamino" / "evals" / "scripts" / "auto_research.py")

    (tmp_path / "agent.md").write_text("baseline\n", encoding="utf-8")
    (tmp_path / "best_score.txt").write_text("0.5\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "agent.md", "best_score.txt"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=kamino451",
            "-c",
            "user.email=kamino451@example.invalid",
            "commit",
            "-m",
            "baseline",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    (tmp_path / "agent.md").write_text("candidate\n", encoding="utf-8")

    evaluation = runner.EvaluationRun(score=0.6, stdout="FINAL_SCORE:0.6\n", stderr="")
    result = runner.apply_keep_or_revert(tmp_path, evaluation)
    log_result = subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, check=True, capture_output=True, text=True)

    assert result == "kept improvement: 0.5000 -> 0.6000"
    assert (tmp_path / "agent.md").read_text(encoding="utf-8") == "candidate\n"
    assert (tmp_path / "best_score.txt").read_text(encoding="utf-8") == "0.6\n"
    assert "Improve prompt score to 0.6000" in log_result.stdout
