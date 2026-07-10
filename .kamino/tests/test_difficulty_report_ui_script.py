"""Tests for the static difficulty-calibration page builder."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


def repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[2]


def task_hash(text: str) -> str:
    """Hash task text like evaluate_task.py."""
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def ranking_payload() -> dict[str, object]:
    """Build a rank-mode ranking with three tasks, hardest first."""
    return {
        "schema_version": "kamino451.bradley-terry-pairwise-ranking.v1",
        "mode": "rank",
        "task_count": 3,
        "comparison_count": 3,
        "fit": {"algorithm": "Bradley-Terry", "iterations": 10, "prior_strength": 0.1},
        "coverage": {"compared_pair_count": 3, "possible_pair_count": 3, "comparison_coverage": 1.0},
        "ranking": [
            {"rank": 1, "task_id": "hard-task", "task_text": "hard text", "difficulty_score": 1.2, "difficulty_probability": 0.7, "comparison_count": 2},
            {"rank": 2, "task_id": "medium-task", "task_text": "medium text", "difficulty_score": 0.1, "difficulty_probability": 0.5, "comparison_count": 2},
            {"rank": 3, "task_id": "easy-task", "task_text": "easy text", "difficulty_score": -1.3, "difficulty_probability": 0.3, "comparison_count": 2},
        ],
    }


def write_json(path: Path, payload: object) -> Path:
    """Write JSON test data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def build_corpus(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Create a three-task corpus with task.md files and an index; return index path and texts."""
    corpus_dir = tmp_path / "corpus"
    texts = {
        "easy-task": "# Easy\n\nDo the easy thing.\n",
        "medium-task": "# Medium\n\nDo the medium thing.\n",
        "hard-task": "# Hard\n\nDo the hard thing.\n",
    }
    tasks = []
    for task_id, text in texts.items():
        task_dir = corpus_dir / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "task.md").write_text(text, encoding="utf-8")
        band = task_id.split("-")[0]
        tasks.append(
            {
                "task_id": task_id,
                "title": task_id.replace("-", " "),
                "intended_difficulty": band,
                "path": task_id,
                "solution_file": "solution.py",
                "test_command": "uv run pytest tests -q",
            }
        )
    index_path = write_json(
        corpus_dir / "corpus-index.json",
        {"schema_version": "kamino451.corpus-index.v1", "tasks": tasks},
    )
    return index_path, texts


def ledger_record(sequence: int, *, task_id: str, text: str, model: str, success: bool) -> dict[str, object]:
    """Build one schema-valid ledger record for a corpus attempt."""
    return {
        "schema_version": "kamino451.task-outcome-ledger.v1",
        "record_id": f"task-outcome-{task_id}-{sequence}",
        "record_sequence": sequence,
        "timestamp": "2026-07-02T12:00:00Z",
        "task_detail_path": f".kamino/evals/tasks/details/{task_id}.json",
        "task_id": task_id,
        "task_text_hash": task_hash(text),
        "task_text": text,
        "task_type": "code_generation",
        "clarity_score": 4,
        "ambiguity_score": 2,
        "consistency_score": 5,
        "completeness_score": 4,
        "semantic_difficulty_score": 3,
        "pairwise_difficulty_score": 0.1,
        "nearest_prior_tasks": [{"task_id": "medium-task", "distance": 0.0}],
        "route_chosen": "clone",
        "agent_files_used": [".kamino/dispatch-queue/fixture/01-python-coding-agent.md"],
        "agent_blueprints_used": [".kamino/agents/library/coding/python-coding-agent.md"],
        "model": model,
        "effort": "medium",
        "execution_status": "completed" if success else "failed",
        "success": success,
        "failure_mode": "none" if success else "judged_failure",
        "success_judgment_path": f".kamino/evals/tasks/outcomes/{task_id}-success.json",
        "output_paths": [".kamino/dispatch-queue/fixture/work/solution.py"],
        "verification_evidence": {"tests_passed": success},
        "success_judgment": {
            "success": success,
            "reason": "ground truth tests",
            "satisfied_requirements": ["ground truth test suite passed"] if success else [],
            "missing_requirements": [] if success else ["ground truth test suite passed"],
            "partial_requirements": [],
            "unverifiable_requirements": [],
            "confidence": "high",
        },
    }


def write_ledger(path: Path, records: list[dict[str, object]]) -> Path:
    """Write ledger records as JSONL."""
    path.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8")
    return path


def run_builder(tmp_path: Path, *, corpora: list[tuple[str, Path, Path]], ledger: Path, evaluations_dir: Path | None = None, failures_dir: Path | None = None) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the page builder through uv run."""
    output = tmp_path / "difficulty.html"
    command = ["uv", "run", ".kamino/evals/scripts/build_difficulty_report_ui.py"]
    for label, ranking, index in corpora:
        command.extend(["--corpus", label, "--ranking", str(ranking), "--corpus-index", str(index)])
    command.extend(["--ledger", str(ledger), "--output", str(output), "--format", "html"])
    if evaluations_dir is not None:
        command.extend(["--evaluations-dir", str(evaluations_dir)])
    if failures_dir is not None:
        command.extend(["--failures-dir", str(failures_dir)])
    process = subprocess.run(command, cwd=repo_root(), capture_output=True, text=True, check=False)
    return process, output


def test_builds_page_with_tasks_stats_and_chips(tmp_path: Path) -> None:
    """The page carries the task table, correlation columns, and PASS/FAIL chips."""
    index_path, texts = build_corpus(tmp_path)
    ranking_path = write_json(tmp_path / "ranking.json", ranking_payload())
    ledger = write_ledger(
        tmp_path / "ledger.jsonl",
        [
            ledger_record(1, task_id="easy-task", text=texts["easy-task"], model="haiku", success=True),
            ledger_record(2, task_id="hard-task", text=texts["hard-task"], model="haiku", success=False),
        ],
    )

    process, output = run_builder(tmp_path, corpora=[("demo-corpus", ranking_path, index_path)], ledger=ledger)
    payload = json.loads(process.stdout)

    assert process.returncode == 0, process.stderr
    assert payload["failed_attempts"] == 1
    page = output.read_text(encoding="utf-8")
    assert "demo-corpus" in page
    assert "hard-task" in page
    assert "Difficulty↔success correlation (attempt)" in page
    assert "a1 haiku: PASS" in page
    assert "a1 haiku: FAIL" in page


def test_alignment_uses_evaluation_signal_and_failure_analysis(tmp_path: Path) -> None:
    """A flagged hard failure reads as predicted; an unflagged easy failure reads as a miss."""
    index_path, texts = build_corpus(tmp_path)
    ranking_path = write_json(tmp_path / "ranking.json", ranking_payload())
    ledger = write_ledger(
        tmp_path / "ledger.jsonl",
        [
            ledger_record(1, task_id="hard-task", text=texts["hard-task"], model="haiku", success=False),
            ledger_record(2, task_id="easy-task", text=texts["easy-task"], model="haiku", success=False),
        ],
    )
    evaluations_dir = tmp_path / "evaluations"
    write_json(
        evaluations_dir / "hard-task.json",
        {"task_id": "hard-task", "difficulty_score": 5, "llm_judge": {"difficulty_score": 5, "recommended_mapping": "Route to a strong reasoning model."}},
    )
    write_json(
        evaluations_dir / "easy-task.json",
        {"task_id": "easy-task", "difficulty_score": 2, "llm_judge": {"difficulty_score": 2, "recommended_mapping": "Any cheap model handles this."}},
    )
    failures_dir = tmp_path / "failures"
    write_json(
        failures_dir / "hard-task.json",
        {
            "schema_version": "kamino451.failure-analysis.v1",
            "task_id": "hard-task",
            "attempt": 1,
            "classification": {
                "primary_failure_mode": "wrong_model",
                "failure_modes": [{"slug": "wrong_model", "layer": "factory-decision", "component_to_improve": "Model binding", "evidence": ["e"]}],
                "recommended_fix": "escalate the model",
                "confidence": "high",
            },
        },
    )

    process, output = run_builder(
        tmp_path,
        corpora=[("demo-corpus", ranking_path, index_path)],
        ledger=ledger,
        evaluations_dir=evaluations_dir,
        failures_dir=failures_dir,
    )

    assert process.returncode == 0, process.stderr
    page = output.read_text(encoding="utf-8")
    assert "judge difficulty 5/5 + mapping calls for a strong model" in page
    assert "difficulty signal predicted this" in page
    assert "MISS: difficulty signal did not flag this task" in page
    assert "wrong_model" in page
    assert "escalate the model" in page
    assert "unclassified" in page


def test_multi_corpus_sections_render(tmp_path: Path) -> None:
    """Two corpora produce two titled sections in one page."""
    index_path, texts = build_corpus(tmp_path)
    ranking_path = write_json(tmp_path / "ranking.json", ranking_payload())
    ledger = write_ledger(
        tmp_path / "ledger.jsonl",
        [ledger_record(1, task_id="medium-task", text=texts["medium-task"], model="haiku", success=True)],
    )

    process, output = run_builder(
        tmp_path,
        corpora=[("corpus-one", ranking_path, index_path), ("corpus-two", ranking_path, index_path)],
        ledger=ledger,
    )

    assert process.returncode == 0, process.stderr
    page = output.read_text(encoding="utf-8")
    assert "corpus-one" in page
    assert "corpus-two" in page


def test_missing_ranking_fails_cleanly(tmp_path: Path) -> None:
    """A missing ranking file yields a non-zero exit and an error message."""
    index_path, texts = build_corpus(tmp_path)
    ledger = write_ledger(
        tmp_path / "ledger.jsonl",
        [ledger_record(1, task_id="easy-task", text=texts["easy-task"], model="haiku", success=True)],
    )

    process, _ = run_builder(tmp_path, corpora=[("demo", tmp_path / "missing-ranking.json", index_path)], ledger=ledger)

    assert process.returncode == 1
    assert "ERROR" in process.stderr
