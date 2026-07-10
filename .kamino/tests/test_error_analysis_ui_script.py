"""Tests for the static error-analysis UI generator."""

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


def ledger_record(
    sequence: int,
    *,
    task_id: str,
    text: str,
    model: str,
    success: bool,
    agent_file: str,
    detail_path: str,
) -> dict[str, object]:
    """Build one schema-valid ledger record."""
    return {
        "schema_version": "kamino451.task-outcome-ledger.v1",
        "record_id": f"task-outcome-{task_id}-{sequence}",
        "record_sequence": sequence,
        "timestamp": "2026-07-02T12:00:00Z",
        "task_detail_path": detail_path,
        "task_id": task_id,
        "task_text_hash": task_hash(text),
        "task_text": text,
        "task_type": "code_generation",
        "clarity_score": 4,
        "ambiguity_score": 2,
        "consistency_score": 5,
        "completeness_score": 4,
        "semantic_difficulty_score": 3,
        "pairwise_difficulty_score": 0.4,
        "nearest_prior_tasks": [{"task_id": "other", "distance": 0.1}],
        "route_chosen": "clone",
        "agent_files_used": [agent_file],
        "agent_blueprints_used": [".kamino/agents/ad-hoc/coding/python-coding-agent.md"],
        "model": model,
        "effort": "medium",
        "execution_status": "completed" if success else "failed",
        "success": success,
        "failure_mode": "none" if success else "judged_failure",
        "success_judgment_path": f".kamino/evals/tasks/outcomes/{task_id}-success.json",
        "output_paths": ["work/solution.py"],
        "verification_evidence": {"tests_passed": success},
        "success_judgment": {
            "success": success,
            "reason": "ground truth tests",
            "satisfied_requirements": [],
            "missing_requirements": [] if success else ["ground truth test suite passed"],
            "partial_requirements": [],
            "unverifiable_requirements": [],
            "confidence": "high",
        },
    }


def trace_record(step: int, *, run_id: str, agent_file: str, status: str) -> dict[str, object]:
    """Build one trace record."""
    return {
        "schema_version": "kamino451.run-trace.v1",
        "run_id": run_id,
        "step": step,
        "attempt": 1,
        "agent_file": agent_file,
        "model": "haiku",
        "effort": "medium",
        "started_at": "2026-07-02T12:00:00Z",
        "ended_at": "2026-07-02T12:01:00Z",
        "duration_seconds": 60,
        "status": status,
        "output_path": "work/solution.py",
        "verdict": None,
        "error": None,
        "subagent_summary": "did the thing",
        "verification": {"verification_command": "uv run pytest tests -q", "exit_code": 0 if status == "ok" else 1},
    }


def write_json(path: Path, payload: object) -> Path:
    """Write JSON test data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def run_ui(ledger: Path, output: Path, extra: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run the UI generator through uv run."""
    command = [
        "uv",
        "run",
        ".kamino/evals/scripts/build_error_analysis_ui.py",
        "--ledger",
        str(ledger),
        "--output",
        str(output),
        "--format",
        "html",
    ]
    if extra:
        command.extend(extra)
    return subprocess.run(command, cwd=repo_root(), capture_output=True, text=True, check=False)


def embedded_payload(html: str) -> dict[str, object]:
    """Extract the embedded JSON payload from the generated page."""
    marker = '<script id="data" type="application/json">'
    start = html.index(marker) + len(marker)
    end = html.index("</script>", start)
    return json.loads(html[start:end].replace("<\\/", "</"))


def test_ui_generator_embeds_attempts_traces_and_failures(tmp_path: Path) -> None:
    """The page should carry attempts with trace records and failure slugs."""
    run_dir = tmp_path / "dispatch" / "260702-120000"
    run_dir.mkdir(parents=True)
    agent_file = str(run_dir / "01-python-coding-agent.md")
    (run_dir / "trace.jsonl").write_text(
        json.dumps(trace_record(1, run_id="260702-120000", agent_file=agent_file, status="failed"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    detail_path = str(tmp_path / "details" / "task-abc.json")
    write_json(Path(detail_path), {"attempt": 1})
    failures_dir = tmp_path / "failures"
    write_json(
        failures_dir / "task-abc.json",
        {
            "schema_version": "kamino451.failure-analysis.v1",
            "task_id": "task-abc",
            "attempt": 1,
            "classification": {
                "primary_failure_mode": "wrong_model",
                "failure_modes": [{"slug": "wrong_model", "layer": "factory-decision", "component_to_improve": "Model binding", "evidence": ["e"]}],
                "recommended_fix": "escalate",
                "confidence": "high",
            },
        },
    )
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        json.dumps(
            ledger_record(1, task_id="task-abc", text="Solve it.", model="haiku", success=False, agent_file=agent_file, detail_path=detail_path),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "errors.html"

    process = run_ui(ledger, output, ["--failures-dir", str(failures_dir)])
    html = output.read_text(encoding="utf-8")
    payload = embedded_payload(html)

    assert process.returncode == 0, process.stderr
    attempts = payload["attempts"]
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["task_id"] == "task-abc"
    assert attempt["failure_slugs"] == ["wrong_model"]
    assert len(attempt["trace"]) == 1
    assert attempt["trace"][0]["status"] == "failed"
    assert "Kamino451 Error Analysis" in html


def test_ui_generator_marks_malformed_trace_instead_of_crashing(tmp_path: Path) -> None:
    """A corrupted trace must surface as an error marker, not kill the page."""
    run_dir = tmp_path / "dispatch" / "260702-130000"
    run_dir.mkdir(parents=True)
    agent_file = str(run_dir / "01-python-coding-agent.md")
    (run_dir / "trace.jsonl").write_text("not json\n", encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        json.dumps(
            ledger_record(1, task_id="task-bad", text="Solve.", model="haiku", success=True, agent_file=agent_file, detail_path=str(tmp_path / "missing.json")),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "errors.html"

    process = run_ui(ledger, output)
    payload = embedded_payload(output.read_text(encoding="utf-8"))

    assert process.returncode == 0, process.stderr
    assert "malformed trace line 1" in str(payload["attempts"][0]["trace_error"])


def test_ui_generator_includes_chart_only_with_ranking_and_corpus(tmp_path: Path) -> None:
    """Chart data appears only when both ranking and corpus index are provided."""
    text = "# T\n\nDo.\n"
    corpus_dir = tmp_path / "corpus" / "task-one"
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "task.md").write_text(text, encoding="utf-8")
    index_path = write_json(
        tmp_path / "corpus" / "corpus-index.json",
        {
            "schema_version": "kamino451.corpus-index.v1",
            "tasks": [
                {"task_id": "task-one", "title": "T", "intended_difficulty": "easy", "path": "task-one", "solution_file": "solution.py", "test_command": "uv run pytest tests -q"}
            ],
        },
    )
    ranking_path = write_json(
        tmp_path / "ranking.json",
        {
            "schema_version": "kamino451.bradley-terry-pairwise-ranking.v1",
            "mode": "rank",
            "ranking": [
                {"rank": 1, "task_id": "task-one", "task_text": text, "difficulty_score": 0.5},
                {"rank": 2, "task_id": "task-two", "task_text": "other", "difficulty_score": -0.5},
            ],
        },
    )
    agent_file = str(tmp_path / "dispatch" / "260702-140000" / "01-a.md")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        json.dumps(
            ledger_record(1, task_id="task-one", text=text, model="haiku", success=True, agent_file=agent_file, detail_path=str(tmp_path / "d.json")),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with_chart = run_ui(ledger, tmp_path / "with.html", ["--ranking", str(ranking_path), "--corpus-index", str(index_path)])
    without_chart = run_ui(ledger, tmp_path / "without.html")
    with_payload = embedded_payload((tmp_path / "with.html").read_text(encoding="utf-8"))
    without_payload = embedded_payload((tmp_path / "without.html").read_text(encoding="utf-8"))

    assert with_chart.returncode == 0, with_chart.stderr
    assert without_chart.returncode == 0, without_chart.stderr
    assert with_payload["chart"][0]["task_id"] == "task-one"
    assert with_payload["chart"][0]["attempts"][0]["model"] == "haiku"
    assert without_payload["chart"] is None


def test_ui_generator_requires_both_chart_inputs_or_neither(tmp_path: Path) -> None:
    """Passing only --ranking without --corpus-index must fail fast."""
    agent_file = str(tmp_path / "dispatch" / "x" / "01-a.md")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        json.dumps(
            ledger_record(1, task_id="t", text="x", model="haiku", success=True, agent_file=agent_file, detail_path=str(tmp_path / "d.json")),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    ranking_path = write_json(tmp_path / "ranking.json", {"schema_version": "kamino451.bradley-terry-pairwise-ranking.v1", "mode": "rank", "ranking": []})

    process = run_ui(ledger, tmp_path / "errors.html", ["--ranking", str(ranking_path)])

    assert process.returncode == 1
    assert "provide both --ranking and --corpus-index" in process.stderr


def test_ui_generator_fails_on_missing_ledger(tmp_path: Path) -> None:
    """No ledger, no page."""
    process = run_ui(tmp_path / "missing.jsonl", tmp_path / "errors.html")

    assert process.returncode == 1
    assert "ledger file does not exist" in process.stderr


def test_ui_generator_embeds_trace_reviews(tmp_path: Path) -> None:
    """With a trace-reviews dir, attempts carry verdicts and the summary section fills."""
    agent_file = str(tmp_path / "dispatch" / "260704-010000" / "01-a.md")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        json.dumps(
            ledger_record(1, task_id="task-rev", text="Solve.", model="haiku", success=True, agent_file=agent_file, detail_path=str(tmp_path / "d.json")),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    reviews_dir = tmp_path / "trace-reviews"
    write_json(
        reviews_dir / "task-rev-a1.json",
        {
            "schema_version": "kamino451.trace-review.v1",
            "task_id": "task-rev",
            "record_id": "task-outcome-task-rev-1",
            "attempt": 1,
            "agent_variant": "oracle",
            "verdict": "pass_with_latent_risks",
            "latent_risks": [{"slug": "ignoring_edge_cases", "evidence": ["e"], "would_fail_on": "large inputs"}],
            "memorization_signals": [],
            "solution_quality_notes": ["n"],
            "confidence": "high",
        },
    )
    output = tmp_path / "errors.html"

    process = run_ui(ledger, output, ["--trace-reviews-dir", str(reviews_dir)])
    html = output.read_text(encoding="utf-8")
    payload = embedded_payload(html)

    assert process.returncode == 0, process.stderr
    attempt = payload["attempts"][0]
    assert attempt["trace_review_verdict"] == "pass_with_latent_risks"
    assert attempt["trace_review"]["latent_risks"][0]["slug"] == "ignoring_edge_cases"
    assert 'id="tracereviews"' in html


def test_ui_generator_embeds_labeling_catalog_and_export(tmp_path: Path) -> None:
    """With a catalog, the page carries the label dropdown slugs and the export control."""
    agent_file = str(tmp_path / "dispatch" / "260703-150000" / "01-a.md")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        json.dumps(
            ledger_record(1, task_id="task-lbl", text="Solve.", model="haiku", success=False, agent_file=agent_file, detail_path=str(tmp_path / "d.json")),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    catalog = repo_root() / ".kamino" / "evals" / "tasks" / "failure-mode-catalog.md"
    output = tmp_path / "errors.html"

    process = run_ui(ledger, output, ["--catalog", str(catalog)])
    html = output.read_text(encoding="utf-8")
    payload = embedded_payload(html)

    assert process.returncode == 0, process.stderr
    assert "wrong_model" in payload["labels_catalog"]
    assert "no_test_verification" in payload["labels_catalog"]
    assert 'id="export-labels"' in html
    assert "kamino451-error-labels" in html
