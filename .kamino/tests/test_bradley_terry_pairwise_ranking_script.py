"""Tests for the .kamino Bradley-Terry pairwise ranking tool."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from pytest import CaptureFixture


def load_pairwise_ranking() -> ModuleType:
    """Load the pairwise ranking script as a testable module."""
    script_path = Path(__file__).resolve().parents[2] / ".kamino" / "evals" / "scripts" / "bradley_terry_pairwise_ranking.py"
    spec = importlib.util.spec_from_file_location("kamino_pairwise_ranking", script_path)
    if spec is None:
        raise AssertionError(f"could not load spec for {script_path}")
    if spec.loader is None:
        raise AssertionError(f"could not load loader for {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["kamino_pairwise_ranking"] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: object) -> None:
    """Write stable JSON test fixture data."""
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def task_payload() -> dict[str, object]:
    """Return ordered task fixtures with clear relative difficulty."""
    return {
        "tasks": [
            {"id": "easy", "text": "Format one sentence as Markdown."},
            {"id": "medium", "text": "Write a Python CLI that reads a file and emits JSON."},
            {"id": "hard", "text": "Refactor a package, preserve behavior, add tests, and update docs."},
            {"id": "extreme", "text": "Design and implement a multi-agent research workflow with evaluation and rollback."},
        ]
    }


def comparison_payload() -> dict[str, object]:
    """Return complete judge comparisons for the task fixtures."""
    return {
        "comparisons": [
            {
                "task_a_id": "easy",
                "task_b_id": "medium",
                "harder_task": "B",
                "confidence": 0.95,
                "reasoning": "The CLI requires implementation and validation.",
                "key_factors": ["tool use", "verification"],
            },
            {
                "task_a_id": "easy",
                "task_b_id": "hard",
                "harder_task": "B",
                "confidence": 0.95,
                "reasoning": "The refactor has broader correctness risk.",
                "key_factors": ["regression risk", "tests"],
            },
            {
                "task_a_id": "easy",
                "task_b_id": "extreme",
                "harder_task": "B",
                "confidence": 0.99,
                "reasoning": "The workflow design is much broader.",
                "key_factors": ["multi-agent coordination", "evaluation"],
            },
            {
                "task_a_id": "medium",
                "task_b_id": "hard",
                "harder_task": "B",
                "confidence": 0.9,
                "reasoning": "The refactor needs preservation and tests across a package.",
                "key_factors": ["regression risk"],
            },
            {
                "task_a_id": "medium",
                "task_b_id": "extreme",
                "harder_task": "B",
                "confidence": 0.98,
                "reasoning": "The research workflow has more planning and verification.",
                "key_factors": ["planning", "tool workflow"],
            },
            {
                "task_a_id": "hard",
                "task_b_id": "extreme",
                "harder_task": "B",
                "confidence": 0.9,
                "reasoning": "The multi-agent workflow is broader than a package refactor.",
                "key_factors": ["multi-agent coordination"],
            },
        ]
    }


def test_rank_mode_orders_tasks_by_pairwise_difficulty(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    """Rank mode should fit Bradley-Terry scores and order hardest first."""
    ranking = load_pairwise_ranking()
    tasks_path = tmp_path / "tasks.json"
    comparisons_path = tmp_path / "comparisons.json"
    write_json(tasks_path, task_payload())
    write_json(comparisons_path, comparison_payload())

    exit_code = ranking.main(
        [
            "rank",
            "--tasks",
            str(tasks_path),
            "--comparisons",
            str(comparisons_path),
            "--format",
            "json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["schema_version"] == "kamino451.bradley-terry-pairwise-ranking.v1"
    assert payload["mode"] == "rank"
    assert payload["coverage"]["comparison_coverage"] == 1.0
    assert [entry["task_id"] for entry in payload["ranking"]] == ["extreme", "hard", "medium", "easy"]
    assert payload["ranking"][0]["difficulty_score"] > payload["ranking"][-1]["difficulty_score"]


def test_similar_mode_requests_next_binary_search_comparison(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    """Similar mode should return the next missing comparison instead of guessing."""
    ranking = load_pairwise_ranking()
    ranking_path = tmp_path / "ranking.json"
    target_path = tmp_path / "target.json"
    comparisons_path = tmp_path / "target-comparisons.json"
    write_json(
        ranking_path,
        {
            "schema_version": "kamino451.bradley-terry-pairwise-ranking.v1",
            "mode": "rank",
            "ranking": [
                {
                    "rank": 1,
                    "task_id": "extreme",
                    "task_text": "Extreme task.",
                    "difficulty_score": 2.0,
                    "difficulty_probability": 0.55,
                    "comparison_count": 3,
                },
                {
                    "rank": 2,
                    "task_id": "hard",
                    "task_text": "Hard task.",
                    "difficulty_score": 1.0,
                    "difficulty_probability": 0.25,
                    "comparison_count": 3,
                },
                {
                    "rank": 3,
                    "task_id": "medium",
                    "task_text": "Medium task.",
                    "difficulty_score": 0.0,
                    "difficulty_probability": 0.14,
                    "comparison_count": 3,
                },
                {
                    "rank": 4,
                    "task_id": "easy",
                    "task_text": "Easy task.",
                    "difficulty_score": -1.0,
                    "difficulty_probability": 0.06,
                    "comparison_count": 3,
                },
            ],
        },
    )
    write_json(target_path, {"task": {"id": "new", "text": "Add tests for a small CLI command."}})
    write_json(comparisons_path, {"comparisons": []})

    exit_code = ranking.main(
        [
            "similar",
            "--ranking",
            str(ranking_path),
            "--target-task",
            str(target_path),
            "--comparisons",
            str(comparisons_path),
            "--neighbors",
            "3",
            "--format",
            "json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["mode"] == "similar"
    assert payload["status"] == "needs_comparison"
    assert payload["next_pair"]["task_a"]["task_id"] == "new"
    assert payload["next_pair"]["task_b"]["task_id"] == "medium"


def test_similar_mode_returns_neighbors_after_binary_search_path(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    """Similar mode should place a target task and return nearby ranked tasks."""
    ranking = load_pairwise_ranking()
    ranking_path = tmp_path / "ranking.json"
    target_path = tmp_path / "target.json"
    comparisons_path = tmp_path / "target-comparisons.json"
    write_json(
        ranking_path,
        {
            "schema_version": "kamino451.bradley-terry-pairwise-ranking.v1",
            "mode": "rank",
            "ranking": [
                {
                    "rank": 1,
                    "task_id": "extreme",
                    "task_text": "Extreme task.",
                    "difficulty_score": 2.0,
                    "difficulty_probability": 0.55,
                    "comparison_count": 3,
                },
                {
                    "rank": 2,
                    "task_id": "hard",
                    "task_text": "Hard task.",
                    "difficulty_score": 1.0,
                    "difficulty_probability": 0.25,
                    "comparison_count": 3,
                },
                {
                    "rank": 3,
                    "task_id": "medium",
                    "task_text": "Medium task.",
                    "difficulty_score": 0.0,
                    "difficulty_probability": 0.14,
                    "comparison_count": 3,
                },
                {
                    "rank": 4,
                    "task_id": "easy",
                    "task_text": "Easy task.",
                    "difficulty_score": -1.0,
                    "difficulty_probability": 0.06,
                    "comparison_count": 3,
                },
            ],
        },
    )
    write_json(target_path, {"task": {"id": "new", "text": "Add tests for a small CLI command."}})
    write_json(
        comparisons_path,
        {
            "comparisons": [
                {
                    "task_a_id": "new",
                    "task_b_id": "medium",
                    "harder_task": "A",
                    "confidence": 0.85,
                    "reasoning": "The target needs code validation beyond the medium anchor.",
                    "key_factors": ["verification"],
                },
                {
                    "task_a_id": "new",
                    "task_b_id": "hard",
                    "harder_task": "B",
                    "confidence": 0.85,
                    "reasoning": "The hard anchor touches more files and carries broader regression risk.",
                    "key_factors": ["regression risk"],
                },
            ]
        },
    )

    exit_code = ranking.main(
        [
            "similar",
            "--ranking",
            str(ranking_path),
            "--target-task",
            str(target_path),
            "--comparisons",
            str(comparisons_path),
            "--neighbors",
            "3",
            "--format",
            "json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["status"] == "complete"
    assert payload["estimated_insertion_rank"] == 3
    assert [entry["task_id"] for entry in payload["similar_tasks"][:2]] == ["hard", "medium"]
    assert [step["anchor_task_id"] for step in payload["binary_search_path"]] == ["medium", "hard"]


def test_sample_task_repository_ranks_and_places_target(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    """The checked-in .kamino/evals/tasks examples should run end to end."""
    ranking = load_pairwise_ranking()
    repo_root = Path(__file__).resolve().parents[2]
    tasks_dir = repo_root / ".kamino" / "evals" / "tasks"
    ranking_path = tmp_path / "sample-ranking.json"

    rank_exit_code = ranking.main(
        [
            "rank",
            "--tasks",
            str(tasks_dir / "sample-difficulty-tasks.json"),
            "--comparisons",
            str(tasks_dir / "sample-difficulty-comparisons.json"),
            "--format",
            "json",
        ]
    )
    rank_output = capsys.readouterr().out
    ranking_path.write_text(rank_output, encoding="utf-8")
    rank_payload = json.loads(rank_output)

    assert rank_exit_code == 0
    assert [entry["task_id"] for entry in rank_payload["ranking"]] == [
        "multi_agent_research_workflow",
        "package_refactor_tests",
        "python_cli_json",
        "format_markdown_sentence",
    ]

    similar_exit_code = ranking.main(
        [
            "similar",
            "--ranking",
            str(ranking_path),
            "--target-task",
            str(tasks_dir / "sample-target-task.json"),
            "--comparisons",
            str(tasks_dir / "sample-target-comparisons.json"),
            "--neighbors",
            "3",
            "--format",
            "json",
        ]
    )

    similar_payload = json.loads(capsys.readouterr().out)
    assert similar_exit_code == 0
    assert similar_payload["status"] == "complete"
    assert similar_payload["estimated_insertion_rank"] == 3
    assert [entry["task_id"] for entry in similar_payload["similar_tasks"][:2]] == ["package_refactor_tests", "python_cli_json"]


def test_task_outcome_fixture_ranks_and_places_similar_task(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    """The task outcome ledger fixtures should cover ranking and similar placement."""
    ranking = load_pairwise_ranking()
    repo_root = Path(__file__).resolve().parents[2]
    fixture_dir = repo_root / ".kamino" / "tests" / "fixtures" / "task-outcome-ledger"
    ranking_path = tmp_path / "fixture-ranking.json"
    target_comparisons_path = tmp_path / "target-comparisons.json"

    rank_exit_code = ranking.main(
        [
            "rank",
            "--tasks",
            str(fixture_dir / "ranking-tasks.json"),
            "--comparisons",
            str(fixture_dir / "ranking-comparisons.json"),
            "--format",
            "json",
        ]
    )
    rank_output = capsys.readouterr().out
    ranking_path.write_text(rank_output, encoding="utf-8")
    rank_payload = json.loads(rank_output)

    assert rank_exit_code == 0
    assert [entry["task_id"] for entry in rank_payload["ranking"]] == [
        "article_pipeline",
        "review_article",
        "format_sentence",
    ]

    write_json(
        target_comparisons_path,
        {
            "comparisons": [
                {
                    "task_a_id": "target_article_review",
                    "task_b_id": "review_article",
                    "harder_task": "Tie",
                    "confidence": 0.9,
                    "reasoning": "Both tasks require article review and concrete recommendations.",
                    "key_factors": ["quality judgment"],
                }
            ]
        },
    )
    similar_exit_code = ranking.main(
        [
            "similar",
            "--ranking",
            str(ranking_path),
            "--target-task",
            str(fixture_dir / "target-task.json"),
            "--comparisons",
            str(target_comparisons_path),
            "--neighbors",
            "2",
            "--format",
            "json",
        ]
    )

    similar_payload = json.loads(capsys.readouterr().out)
    assert similar_exit_code == 0
    assert similar_payload["status"] == "complete"
    assert similar_payload["estimated_insertion_rank"] == 2
    assert similar_payload["similar_tasks"][0]["task_id"] == "review_article"


def test_pairwise_ranking_agents_wire_script_and_judge() -> None:
    """The Claude project agent definitions should wire the script to the LLM judge."""
    repo_root = Path(__file__).resolve().parents[2]
    meta_agent = repo_root / ".claude" / "agents" / "bradley-terry-pairwise-ranking.md"
    judge_agent = repo_root / ".claude" / "agents" / "pairwise-difficulty-judge.md"

    meta_agent_text = meta_agent.read_text(encoding="utf-8")
    judge_agent_text = judge_agent.read_text(encoding="utf-8")

    assert "name: bradley-terry-pairwise-ranking" in meta_agent_text
    assert "uv run .kamino/evals/scripts/bradley_terry_pairwise_ranking.py rank" in meta_agent_text
    assert "uv run .kamino/evals/scripts/bradley_terry_pairwise_ranking.py similar" in meta_agent_text
    assert "pairwise-difficulty-judge" in meta_agent_text

    assert "name: pairwise-difficulty-judge" in judge_agent_text
    assert "harder_task" in judge_agent_text
    assert "confidence" in judge_agent_text
    assert "strict JSON only" in judge_agent_text
