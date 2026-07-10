"""Tests for the .kamino task evaluator script."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from pytest import CaptureFixture


def load_task_evaluator() -> ModuleType:
    """Load the task evaluator script as a testable module."""
    script_path = Path(__file__).resolve().parents[2] / ".kamino" / "evals" / "scripts" / "evaluate_task.py"
    spec = importlib.util.spec_from_file_location("kamino_task_evaluator", script_path)
    if spec is None:
        raise AssertionError(f"could not load spec for {script_path}")
    if spec.loader is None:
        raise AssertionError(f"could not load loader for {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["kamino_task_evaluator"] = module
    spec.loader.exec_module(module)
    return module


def test_evaluate_task_returns_core_metrics_and_routing() -> None:
    """Evaluate a clear code task and assert the core note-derived metrics exist."""
    evaluator = load_task_evaluator()
    task = """
    Implement a Python CLI script.
    - It must accept an input file.
    - It must emit JSON output.
    - Success means pytest passes and the output includes scores.
    """

    evaluation = evaluator.evaluate_task(task, evaluator.TaskSource(kind="inline", path=None))
    payload = evaluator.evaluation_to_dict(evaluation)

    assert payload["schema_version"] == "kamino451.task-evaluation.v1"
    assert payload["task_id"].startswith("task-")
    assert payload["task_text_hash"].startswith("sha256:")
    assert payload["task_text"] == task
    assert payload["task_type"] == payload["judgement"]["task_type"]
    assert payload["clarity_score"] == payload["judgement"]["clarity_score"]
    assert isinstance(payload["open_issues"], list)
    assert payload["score_scale"]["ambiguity_score"] == "1 = low ambiguity, 5 = high ambiguity"
    metrics = payload["metrics"]
    judgement = payload["judgement"]
    assert metrics["word_count"] > 20
    assert metrics["estimated_token_count"] > 0
    assert metrics["flesch_reading_ease"] != 0
    assert metrics["bullet_count"] == 3
    assert metrics["explicit_requirement_count"] >= 3
    assert metrics["input_output_indicator_count"] >= 2
    assert judgement["task_type"] == "code_generation"
    assert judgement["clarity_score"] >= 4
    expected_mappings = {
        "standard_model_task_agent",
        "strong_model_planning_tool_agent",
        "small_fast_model_simple_agent",
    }
    assert judgement["recommended_mapping"] in expected_mappings


def test_evaluate_task_flags_ambiguous_conflicting_tasks() -> None:
    """Contradiction and vague wording should route to clarification or review."""
    evaluator = load_task_evaluator()
    task = "Make it better somehow. The task has conflicting requirements: do the migration and do not do the migration."

    evaluation = evaluator.evaluate_task(task, evaluator.TaskSource(kind="inline", path=None))
    payload = evaluator.evaluation_to_dict(evaluation)

    metrics = payload["metrics"]
    judgement = payload["judgement"]
    assert metrics["vague_term_count"] >= 2
    assert metrics["contradiction_indicator_count"] >= 1
    assert judgement["human_review_required"] is True
    assert judgement["recommended_mapping"] == "clarification_agent_or_human_review"


def test_evaluate_task_counts_imperative_requirements_and_readability_penalty() -> None:
    """Imperative task phrasing should be treated as requirements, not missed."""
    evaluator = load_task_evaluator()
    task = (
        "Implement a Python CLI script that accepts an input file, evaluates clarity ambiguity consistency "
        "completeness difficulty, emits JSON, and passes tests."
    )

    evaluation = evaluator.evaluate_task(task, evaluator.TaskSource(kind="inline", path=None))
    payload = evaluator.evaluation_to_dict(evaluation)

    metrics = payload["metrics"]
    judgement = payload["judgement"]
    assert metrics["explicit_requirement_count"] >= 4
    assert metrics["vague_term_count"] >= 1
    assert judgement["clarity_score"] <= 3


def test_cli_main_writes_json_for_file_input(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    """The script should be runnable as a CLI-style module with file input."""
    evaluator = load_task_evaluator()
    task_file = tmp_path / "task.txt"
    task_file.write_text("Research the available APIs, cite sources, and return a JSON comparison table.", encoding="utf-8")

    exit_code = evaluator.main(["--file", str(task_file), "--format", "json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["source"]["kind"] == "file"
    assert payload["source"]["path"] == str(task_file)
    assert payload["judgement"]["task_type"] == "research"


def test_task_evaluator_agents_wire_script_and_llm_judge() -> None:
    """The Kamino agent definitions should wire the script to the LLM judge agent."""
    repo_root = Path(__file__).resolve().parents[2]
    task_evaluator = repo_root / ".claude" / "agents" / "task-evaluator.md"
    llm_judge = repo_root / ".claude" / "agents" / "task-llm-judge.md"

    task_evaluator_text = task_evaluator.read_text(encoding="utf-8")
    llm_judge_text = llm_judge.read_text(encoding="utf-8")

    assert "name: task-evaluator" in task_evaluator_text
    assert "uv run .kamino/evals/scripts/evaluate_task.py" in task_evaluator_text
    assert "task-llm-judge" in task_evaluator_text
    assert "Task" in task_evaluator_text

    assert "name: task-llm-judge" in llm_judge_text
    assert "clarity_score" in llm_judge_text
    assert "ambiguity_score" in llm_judge_text
    assert "strict JSON only" in llm_judge_text
