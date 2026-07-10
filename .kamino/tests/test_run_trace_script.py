"""Tests for the deterministic run trace writer."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[2]


def valid_record(step: int = 1, *, run_id: str = "260702-120000", status: str = "ok") -> dict[str, object]:
    """Build one valid trace record."""
    return {
        "schema_version": "kamino451.run-trace.v1",
        "run_id": run_id,
        "step": step,
        "attempt": 1,
        "agent_file": f".kamino/dispatch-queue/{run_id}/0{step}-python-coding-agent.md",
        "blueprint": ".kamino/agents/ad-hoc/coding/python-coding-agent.md",
        "model": "haiku",
        "effort": "medium",
        "started_at": "2026-07-02T12:00:00Z",
        "ended_at": "2026-07-02T12:01:30Z",
        "duration_seconds": 90,
        "status": status,
        "output_path": f".kamino/dispatch-queue/{run_id}/outputs/0{step}-python-coding-agent.md",
        "verdict": None,
        "error": None,
        "subagent_summary": "wrote solution.py and ran the tests",
        "verification": {
            "output_non_empty": True,
            "no_template_tokens": True,
            "verification_command": "uv run pytest tests -q",
            "exit_code": 0,
        },
    }


def run_trace_write(trace_path: Path, record_path: Path) -> subprocess.CompletedProcess[str]:
    """Run the trace writer through uv run."""
    return subprocess.run(
        [
            "uv",
            "run",
            ".kamino/evals/scripts/run_trace_write.py",
            "--trace",
            str(trace_path),
            "--record",
            str(record_path),
            "--format",
            "json",
        ],
        cwd=repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )


def write_record(path: Path, record: dict[str, object]) -> Path:
    """Write one record JSON file."""
    path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_trace_writer_appends_records_with_increasing_sequence(tmp_path: Path) -> None:
    """Each append should return the one-based record sequence."""
    trace_path = tmp_path / "trace.jsonl"
    first_record = write_record(tmp_path / "record-1.json", valid_record(1))
    second_record = write_record(tmp_path / "record-2.json", valid_record(2))

    first = run_trace_write(trace_path, first_record)
    second = run_trace_write(trace_path, second_record)
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    lines = trace_path.read_text(encoding="utf-8").splitlines()

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first_payload["record_sequence"] == 1
    assert second_payload["record_sequence"] == 2
    assert len(lines) == 2
    assert json.loads(lines[0])["step"] == 1
    assert json.loads(lines[1])["step"] == 2


def test_trace_writer_rejects_mixed_run_ids_in_one_file(tmp_path: Path) -> None:
    """A trace file belongs to exactly one run."""
    trace_path = tmp_path / "trace.jsonl"
    first_record = write_record(tmp_path / "record-1.json", valid_record(1))
    other_run = write_record(tmp_path / "record-2.json", valid_record(2, run_id="260702-999999"))

    first = run_trace_write(trace_path, first_record)
    second = run_trace_write(trace_path, other_run)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 1
    assert "run_id must match" in second.stderr
    assert len(trace_path.read_text(encoding="utf-8").splitlines()) == 1


def test_trace_writer_rejects_invalid_status(tmp_path: Path) -> None:
    """Status must be ok, skipped, or failed."""
    trace_path = tmp_path / "trace.jsonl"
    record = valid_record(1)
    record["status"] = "partial"
    record_path = write_record(tmp_path / "record.json", record)

    process = run_trace_write(trace_path, record_path)

    assert process.returncode == 1
    assert "status must be one of" in process.stderr
    assert not trace_path.exists()


def test_trace_writer_rejects_invalid_verdict(tmp_path: Path) -> None:
    """Verdict must be PASS, FAIL, or null."""
    trace_path = tmp_path / "trace.jsonl"
    record = valid_record(1)
    record["verdict"] = "MAYBE"
    record_path = write_record(tmp_path / "record.json", record)

    process = run_trace_write(trace_path, record_path)

    assert process.returncode == 1
    assert "verdict must be PASS, FAIL, or null" in process.stderr


def test_trace_writer_rejects_missing_required_field(tmp_path: Path) -> None:
    """A record without a required key must fail fast."""
    trace_path = tmp_path / "trace.jsonl"
    record = valid_record(1)
    del record["verification"]
    record_path = write_record(tmp_path / "record.json", record)

    process = run_trace_write(trace_path, record_path)

    assert process.returncode == 1
    assert "missing required key: verification" in process.stderr


def test_trace_writer_rejects_negative_duration(tmp_path: Path) -> None:
    """Durations must not be negative."""
    trace_path = tmp_path / "trace.jsonl"
    record = valid_record(1)
    record["duration_seconds"] = -1
    record_path = write_record(tmp_path / "record.json", record)

    process = run_trace_write(trace_path, record_path)

    assert process.returncode == 1
    assert "duration_seconds must not be negative" in process.stderr


def test_trace_writer_rejects_malformed_existing_trace(tmp_path: Path) -> None:
    """Appending to a corrupted trace file must fail fast."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text("not json\n", encoding="utf-8")
    record_path = write_record(tmp_path / "record.json", valid_record(1))

    process = run_trace_write(trace_path, record_path)

    assert process.returncode == 1
    assert "malformed JSON" in process.stderr
