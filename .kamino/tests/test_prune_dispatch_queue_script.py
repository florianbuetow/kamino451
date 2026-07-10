"""Tests for the dispatch-queue pruner."""

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


def ledger_record(sequence: int, *, agent_file: str) -> dict[str, object]:
    """Build one schema-valid ledger record referencing an agent file."""
    text = f"task {sequence}"
    return {
        "schema_version": "kamino451.task-outcome-ledger.v1",
        "record_id": f"task-outcome-{sequence}",
        "record_sequence": sequence,
        "timestamp": "2026-07-04T00:00:00Z",
        "task_detail_path": f"details/task-{sequence}.json",
        "task_id": f"task-{sequence}",
        "task_text_hash": task_hash(text),
        "task_text": text,
        "task_type": "code_generation",
        "clarity_score": 4,
        "ambiguity_score": 2,
        "consistency_score": 5,
        "completeness_score": 4,
        "semantic_difficulty_score": 3,
        "pairwise_difficulty_score": 0.1,
        "nearest_prior_tasks": [{"task_id": "p", "distance": 0.1}],
        "route_chosen": "clone",
        "agent_files_used": [agent_file],
        "agent_blueprints_used": [".kamino/agents/library/coding/python-coding-agent.md"],
        "model": "haiku",
        "effort": "medium",
        "execution_status": "completed",
        "success": True,
        "failure_mode": "none",
        "success_judgment_path": "outcomes/x.json",
        "output_paths": ["work/solution.py"],
        "verification_evidence": {"tests_passed": True},
        "success_judgment": {
            "success": True,
            "reason": "tests",
            "satisfied_requirements": [],
            "missing_requirements": [],
            "partial_requirements": [],
            "unverifiable_requirements": [],
            "confidence": "high",
        },
    }


def run_prune_process(dispatch_dir: Path, ledger: Path, *, apply: bool = False) -> subprocess.CompletedProcess[str]:
    """Run the pruner and return the completed process without asserting success."""
    command = [
        "uv", "run", ".kamino/evals/scripts/prune_dispatch_queue.py",
        "--dispatch-dir", str(dispatch_dir),
        "--ledger", str(ledger),
        "--format", "json",
    ]
    if apply:
        command.append("--apply")
    return subprocess.run(command, cwd=repo_root(), capture_output=True, text=True, check=False)


def run_prune(dispatch_dir: Path, ledger: Path, *, apply: bool = False) -> dict[str, object]:
    """Run the pruner, assert success, and parse its JSON output."""
    process = run_prune_process(dispatch_dir, ledger, apply=apply)
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def make_run_dir(dispatch_dir: Path, name: str) -> str:
    """Create a run dir with an agent file; return the agent file path."""
    run_dir = dispatch_dir / name
    run_dir.mkdir(parents=True)
    agent_file = run_dir / "01-python-coding-agent.md"
    agent_file.write_text("agent body\n", encoding="utf-8")
    return str(agent_file)


def test_prune_lists_only_unreferenced_dirs_by_default(tmp_path: Path) -> None:
    """Default mode lists prunable dirs without deleting anything."""
    dispatch_dir = tmp_path / "dispatch-queue"
    referenced_agent = make_run_dir(dispatch_dir, "260704-000001")
    make_run_dir(dispatch_dir, "260704-000002-orphan")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(ledger_record(1, agent_file=referenced_agent), sort_keys=True) + "\n", encoding="utf-8")

    payload = run_prune(dispatch_dir, ledger)

    assert payload["referenced_kept"] == 1
    assert payload["unreferenced"] == ["260704-000002-orphan"]
    assert payload["applied"] is False
    assert (dispatch_dir / "260704-000002-orphan").is_dir()


def test_prune_apply_deletes_only_unreferenced_dirs(tmp_path: Path) -> None:
    """Apply mode removes orphans and never touches referenced capsules."""
    dispatch_dir = tmp_path / "dispatch-queue"
    referenced_agent = make_run_dir(dispatch_dir, "260704-000001")
    make_run_dir(dispatch_dir, "260704-000002-orphan")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(ledger_record(1, agent_file=referenced_agent), sort_keys=True) + "\n", encoding="utf-8")

    payload = run_prune(dispatch_dir, ledger, apply=True)

    assert payload["deleted"] == ["260704-000002-orphan"]
    assert not (dispatch_dir / "260704-000002-orphan").exists()
    assert (dispatch_dir / "260704-000001").is_dir()


def test_prune_with_missing_ledger_fails_in_list_mode(tmp_path: Path) -> None:
    """A missing ledger is an error, not a license to call everything unreferenced."""
    dispatch_dir = tmp_path / "dispatch-queue"
    make_run_dir(dispatch_dir, "260704-000003")

    process = run_prune_process(dispatch_dir, tmp_path / "missing-ledger.jsonl")

    assert process.returncode == 1
    assert "ledger" in process.stderr
    assert (dispatch_dir / "260704-000003").is_dir()


def test_prune_with_missing_ledger_fails_and_deletes_nothing_in_apply_mode(tmp_path: Path) -> None:
    """--apply with a missing ledger must abort before deleting any run dir."""
    dispatch_dir = tmp_path / "dispatch-queue"
    make_run_dir(dispatch_dir, "260704-000003")
    make_run_dir(dispatch_dir, "260704-000004")

    process = run_prune_process(dispatch_dir, tmp_path / "missing-ledger.jsonl", apply=True)

    assert process.returncode == 1
    assert "ledger" in process.stderr
    assert (dispatch_dir / "260704-000003").is_dir()
    assert (dispatch_dir / "260704-000004").is_dir()
