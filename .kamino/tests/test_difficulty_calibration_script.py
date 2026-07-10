"""Tests for corpus difficulty placement and calibration reporting."""

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
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def run_placement(ranking_path: Path, task_id: str, *, neighbors: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run the placement subcommand through uv run."""
    command = [
        "uv",
        "run",
        ".kamino/evals/scripts/difficulty_calibration_report.py",
        "placement",
        "--ranking",
        str(ranking_path),
        "--task-id",
        task_id,
        "--format",
        "json",
    ]
    if neighbors is not None:
        command.extend(["--neighbors", neighbors])
    return subprocess.run(command, cwd=repo_root(), capture_output=True, text=True, check=False)


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
        "agent_blueprints_used": [".kamino/agents/ad-hoc/coding/python-coding-agent.md"],
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
    """Write JSONL ledger test data."""
    path.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8")
    return path


def run_report(ranking_path: Path, ledger_path: Path, index_path: Path, *, output_format: str = "json") -> subprocess.CompletedProcess[str]:
    """Run the report subcommand through uv run."""
    return subprocess.run(
        [
            "uv",
            "run",
            ".kamino/evals/scripts/difficulty_calibration_report.py",
            "report",
            "--ranking",
            str(ranking_path),
            "--ledger",
            str(ledger_path),
            "--corpus-index",
            str(index_path),
            "--format",
            output_format,
        ],
        cwd=repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_placement_uses_the_tasks_own_ranking_entry(tmp_path: Path) -> None:
    """A corpus anchor's placement comes straight from the ranking."""
    ranking_path = write_json(tmp_path / "ranking.json", ranking_payload())

    process = run_placement(ranking_path, "medium-task")
    placement = json.loads(process.stdout)

    assert process.returncode == 0, process.stderr
    assert placement["schema_version"] == "kamino451.bradley-terry-pairwise-ranking.v1"
    assert placement["estimated_insertion_rank"] == 2
    assert placement["estimated_difficulty_score"] == 0.1
    nearest_ids = [entry["task_id"] for entry in placement["nearest_prior_tasks"]]
    assert nearest_ids[0] == "hard-task"
    assert set(nearest_ids) == {"hard-task", "easy-task"}


def test_placement_is_accepted_by_the_task_detail_validator(tmp_path: Path) -> None:
    """Placement output must satisfy parse_difficulty_placement downstream."""
    ranking_path = write_json(tmp_path / "ranking.json", ranking_payload())
    fixtures = repo_root() / ".kamino" / "tests" / "fixtures" / "agent-candidate-search"

    placement_process = run_placement(ranking_path, "easy-task", neighbors="1")
    placement_path = write_json(tmp_path / "placement.json", json.loads(placement_process.stdout))
    detail_process = subprocess.run(
        [
            "uv",
            "run",
            ".kamino/evals/scripts/task_detail_write.py",
            "--output-dir",
            str(tmp_path / "details"),
            "--task-eval",
            str(fixtures / "task-eval-coding.json"),
            "--difficulty",
            str(placement_path),
            "--candidate-search",
            str(fixtures / "candidate-search-coding.json"),
            "--route",
            str(fixtures / "route-clone-coding.json"),
            "--format",
            "json",
        ],
        cwd=repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert placement_process.returncode == 0, placement_process.stderr
    assert detail_process.returncode == 0, detail_process.stderr


def test_placement_rejects_unknown_task_id(tmp_path: Path) -> None:
    """Placement must fail when the task is not an anchor."""
    ranking_path = write_json(tmp_path / "ranking.json", ranking_payload())

    process = run_placement(ranking_path, "not-a-task")

    assert process.returncode == 1
    assert "task id is not in the ranking" in process.stderr


def test_report_joins_ledger_outcomes_to_corpus_tasks(tmp_path: Path) -> None:
    """The report should join by task text hash and compute per-model stats."""
    index_path, texts = build_corpus(tmp_path)
    ranking = ranking_payload()
    for entry in ranking["ranking"]:
        entry["task_text"] = texts[str(entry["task_id"])]
    ranking_path = write_json(tmp_path / "ranking.json", ranking)
    records = [
        ledger_record(1, task_id="easy-task", text=texts["easy-task"], model="haiku", success=True),
        ledger_record(2, task_id="medium-task", text=texts["medium-task"], model="haiku", success=False),
        ledger_record(3, task_id="medium-task", text=texts["medium-task"], model="sonnet", success=True),
        ledger_record(4, task_id="hard-task", text=texts["hard-task"], model="haiku", success=False),
        ledger_record(5, task_id="other-task", text="not a corpus task", model="haiku", success=True),
    ]
    ledger_path = write_ledger(tmp_path / "ledger.jsonl", records)

    process = run_report(ranking_path, ledger_path, index_path)
    report = json.loads(process.stdout)

    assert process.returncode == 0, process.stderr
    assert report["corpus_task_count"] == 3
    assert report["corpus_attempt_count"] == 4
    assert report["non_corpus_record_count"] == 1
    haiku = report["model_stats"]["haiku"]
    assert haiku["tasks_attempted"] == 3
    assert haiku["tasks_solved"] == 1
    sonnet = report["model_stats"]["sonnet"]
    assert sonnet["tasks_attempted"] == 1
    assert sonnet["tasks_solved"] == 1
    correlation = report["difficulty_success_correlation_by_model"]["haiku"]
    assert correlation is not None
    assert correlation < 0
    assert report["difficulty_success_correlation_by_model"]["sonnet"] is None
    attempt_correlation = report["difficulty_attempt_success_correlation_by_model"]["haiku"]
    assert attempt_correlation is not None
    assert attempt_correlation < 0
    assert report["difficulty_attempt_success_correlation_by_model"]["sonnet"] is None
    first_row = report["tasks"][0]
    assert first_row["task_id"] == "hard-task"
    assert first_row["bt_rank"] == 1


def test_report_renders_markdown(tmp_path: Path) -> None:
    """Markdown output should carry the tables."""
    index_path, texts = build_corpus(tmp_path)
    ranking = ranking_payload()
    for entry in ranking["ranking"]:
        entry["task_text"] = texts[str(entry["task_id"])]
    ranking_path = write_json(tmp_path / "ranking.json", ranking)
    ledger_path = write_ledger(
        tmp_path / "ledger.jsonl",
        [ledger_record(1, task_id="easy-task", text=texts["easy-task"], model="haiku", success=True)],
    )

    process = run_report(ranking_path, ledger_path, index_path, output_format="markdown")

    assert process.returncode == 0, process.stderr
    assert "# Difficulty Calibration Report" in process.stdout
    assert "## Model stats" in process.stdout
    assert "haiku: PASS" in process.stdout


def test_report_fails_without_ledger(tmp_path: Path) -> None:
    """The calibration report needs real outcomes; a missing ledger fails fast."""
    index_path, texts = build_corpus(tmp_path)
    ranking = ranking_payload()
    for entry in ranking["ranking"]:
        entry["task_text"] = texts[str(entry["task_id"])]
    ranking_path = write_json(tmp_path / "ranking.json", ranking)

    process = run_report(ranking_path, tmp_path / "missing-ledger.jsonl", index_path)

    assert process.returncode == 1
    assert "ledger file does not exist" in process.stderr


def test_report_fails_when_corpus_task_missing_from_ranking(tmp_path: Path) -> None:
    """Every corpus task must be a ranking anchor."""
    index_path, texts = build_corpus(tmp_path)
    ranking = ranking_payload()
    ranking["ranking"] = [entry for entry in ranking["ranking"] if entry["task_id"] != "hard-task"]
    ranking["task_count"] = 2
    ranking_path = write_json(tmp_path / "ranking.json", ranking)
    ledger_path = write_ledger(
        tmp_path / "ledger.jsonl",
        [ledger_record(1, task_id="easy-task", text=texts["easy-task"], model="haiku", success=True)],
    )

    process = run_report(ranking_path, ledger_path, index_path)

    assert process.returncode == 1
    assert "corpus task is missing from the ranking" in process.stderr
