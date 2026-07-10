"""Contract tests for the static factory-vs-prescribed sweep report generator."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


def repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[2]


def task_hash(text: str) -> str:
    """Return a sha256:<hex> identity hash for fixture task text."""
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def write_json(path: Path, payload: object) -> None:
    """Write stable JSON test data, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def ledger_record(
    sequence: int,
    *,
    model: str,
    success: bool,
    agent_files_used: list[str],
    agent_blueprints_used: list[str],
    output_paths: list[str],
    task_detail_path: Path | None,
) -> dict[str, object]:
    """Build one schema-valid ledger record, optionally pointing at a task detail."""
    text = f"sweep task {sequence}"
    record: dict[str, object] = {
        "schema_version": "kamino451.task-outcome-ledger.v1",
        "record_id": f"task-outcome-sweep-{sequence}",
        "record_sequence": sequence,
        "timestamp": "2026-07-09T00:00:00Z",
        "task_id": f"task-sweep-{sequence}",
        "task_text_hash": task_hash(text),
        "task_text": text,
        "task_type": "coding",
        "clarity_score": 4,
        "ambiguity_score": 2,
        "consistency_score": 5,
        "completeness_score": 4,
        "semantic_difficulty_score": 3,
        "pairwise_difficulty_score": 0.4,
        "nearest_prior_tasks": [{"task_id": "prior", "distance": 0.1}],
        "route_chosen": "clone",
        "agent_files_used": agent_files_used,
        "agent_blueprints_used": agent_blueprints_used,
        "model": model,
        "effort": "medium",
        "execution_status": "completed" if success else "failed",
        "success": success,
        "failure_mode": "none" if success else "judged_failure",
        "success_judgment_path": ".kamino/evals/tasks/outcomes/x.json",
        "output_paths": output_paths,
        "verification_evidence": {"tests_passed": success},
        "success_judgment": {
            "success": success,
            "reason": "tests",
            "satisfied_requirements": [],
            "missing_requirements": [] if success else ["tests"],
            "partial_requirements": [],
            "unverifiable_requirements": [],
            "confidence": "high",
        },
    }
    if task_detail_path is not None:
        record["task_detail_path"] = str(task_detail_path)
    return record


def build_stamped_sweep(base: Path, *, label: str, mode: str, sweep_id: str, model: str) -> tuple[Path, Path]:
    """Build one fake run dir (route-decision.json stamped with a sweep) plus its task detail.

    Mirrors the real shape: compile_run.py stamps the raw route-decision.json with
    {"sweep": {...}}, and task_detail_write.py's parsed route_decision never keeps it —
    only route_decision_path lets the report recover the raw file.
    """
    run_dir = base / f"{label}-run"
    run_dir.mkdir(parents=True)
    agent_file = run_dir / "01-agent.md"
    agent_file.write_text("stub agent file", encoding="utf-8")
    blueprint = f".kamino/agents/library/coding/{label}-agent.md"
    write_json(
        run_dir / "route-decision.json",
        {
            "route_chosen": "clone",
            "agent_files_used": [str(agent_file)],
            "agent_blueprints_used": [blueprint],
            "model": model,
            "effort": "medium",
            "binding_reason": "test fixture",
            "corpus_dir": ".kamino/evals/tasks/corpus-demo",
            "corpus_task_id": "9-demo-task",
            "attempt": 1,
            "sweep": {"mode": mode, "sweep_id": sweep_id},
        },
    )
    detail_path = base / f"{label}-task-detail.json"
    write_json(
        detail_path,
        {
            "route_decision_path": str(run_dir / "route-decision.json"),
            "route_decision": {
                "route_chosen": "clone",
                "agent_files_used": [str(agent_file)],
                "agent_blueprints_used": [blueprint],
                "model": model,
                "effort": "medium",
            },
        },
    )
    return detail_path, run_dir


def run_build_report(*, ledger: Path, output: Path) -> subprocess.CompletedProcess[str]:
    """Run build_sweep_report_ui.py through uv run."""
    return subprocess.run(
        [
            "uv",
            "run",
            ".kamino/evals/scripts/build_sweep_report_ui.py",
            "--ledger",
            str(ledger),
            "--output",
            str(output),
            "--format",
            "json",
        ],
        cwd=repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_build_sweep_report_ui_separates_stamped_sweeps_from_legacy(tmp_path: Path) -> None:
    """Two stamped sweeps should be recovered and grouped; an unstamped record stays legacy."""
    auto_detail, auto_run = build_stamped_sweep(tmp_path, label="auto", mode="auto", sweep_id="auto-sweep-1", model="haiku")
    prescribed_detail, prescribed_run = build_stamped_sweep(
        tmp_path, label="prescribed", mode="prescribed", sweep_id="prescribed-sweep-1", model="sonnet"
    )
    legacy_base = tmp_path / "legacy-nonexistent"

    records = [
        ledger_record(
            1,
            model="haiku",
            success=True,
            agent_files_used=[str(auto_run / "01-agent.md")],
            agent_blueprints_used=[".kamino/agents/library/coding/auto-agent.md"],
            output_paths=[str(auto_run / "work" / "solution.py")],
            task_detail_path=auto_detail,
        ),
        ledger_record(
            2,
            model="sonnet",
            success=False,
            agent_files_used=[str(prescribed_run / "01-agent.md")],
            agent_blueprints_used=[".kamino/agents/library/coding/prescribed-agent.md"],
            output_paths=[str(prescribed_run / "work" / "solution.py")],
            task_detail_path=prescribed_detail,
        ),
        ledger_record(
            3,
            model="haiku",
            success=True,
            agent_files_used=[str(legacy_base / "01-agent.md")],
            agent_blueprints_used=[".kamino/agents/library/coding/legacy-agent.md"],
            output_paths=[str(legacy_base / "work" / "solution.py")],
            task_detail_path=None,
        ),
    ]
    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8")
    output_path = tmp_path / "sweeps.html"

    process = run_build_report(ledger=ledger_path, output=output_path)

    assert process.returncode == 0, process.stderr
    payload = json.loads(process.stdout)
    assert payload["records"] == 3
    assert payload["stamped"] == 2
    assert payload["sweeps"] == 2
    assert payload["legacy"] == 1

    assert output_path.is_file()
    output_text = output_path.read_text(encoding="utf-8")
    assert "auto-sweep-1" in output_text
    assert "prescribed-sweep-1" in output_text


def test_build_sweep_report_ui_rejects_missing_ledger(tmp_path: Path) -> None:
    """A nonexistent ledger path must fail instead of emitting an empty report."""
    process = run_build_report(ledger=tmp_path / "missing.jsonl", output=tmp_path / "sweeps.html")

    assert process.returncode != 0
