"""Tests for durable pre-run task detail artifacts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[2]


def fixture_dir() -> Path:
    """Return the candidate search fixture directory."""
    return repo_root() / ".kamino" / "tests" / "fixtures" / "agent-candidate-search"


def run_task_detail(output_dir: Path, *, attempt: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run task detail writer through uv run."""
    fixtures = fixture_dir()
    command = [
        "uv",
        "run",
        ".kamino/evals/scripts/task_detail_write.py",
        "--output-dir",
        str(output_dir),
        "--task-eval",
        str(fixtures / "task-eval-coding.json"),
        "--difficulty",
        str(fixtures / "difficulty-coding.json"),
        "--candidate-search",
        str(fixtures / "candidate-search-coding.json"),
        "--route",
        str(fixtures / "route-clone-coding.json"),
        "--format",
        "json",
    ]
    if attempt is not None:
        command.extend(["--attempt", attempt])
    return subprocess.run(
        command,
        cwd=repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )


def run_ledger_write(ledger_path: Path, task_detail_path: Path, success_judgment_path: Path) -> subprocess.CompletedProcess[str]:
    """Run outcome ledger writer through uv run."""
    fixtures = fixture_dir()
    return subprocess.run(
        [
            "uv",
            "run",
            ".kamino/evals/scripts/task_outcome_ledger_write.py",
            "--ledger",
            str(ledger_path),
            "--task-detail",
            str(task_detail_path),
            "--run-evidence",
            str(fixtures / "run-evidence-coding-success.json"),
            "--success-judgment",
            str(success_judgment_path),
            "--format",
            "json",
        ],
        cwd=repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )


def read_json(path: Path) -> dict[str, object]:
    """Read one JSON object."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError("payload must be a JSON object")
    return payload


def write_json(path: Path, payload: object) -> None:
    """Write stable JSON test data."""
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def test_task_detail_writer_writes_file_named_by_task_id(tmp_path: Path) -> None:
    """The task detail file should be named <task_id>.json."""
    output_dir = tmp_path / "details"

    process = run_task_detail(output_dir)
    payload = json.loads(process.stdout)
    output_path = output_dir / "task-current-coding.json"

    assert process.returncode == 0, process.stderr
    assert payload["task_detail_path"] == str(output_path)
    assert output_path.is_file()
    written = read_json(output_path)
    assert written["schema_version"] == "kamino451.task-detail.v1"
    assert written["task_id"] == "task-current-coding"


def test_task_detail_writer_refuses_to_overwrite_existing_file(tmp_path: Path) -> None:
    """Repeated writes for the same task id should fail instead of overwriting."""
    output_dir = tmp_path / "details"

    first = run_task_detail(output_dir)
    second = run_task_detail(output_dir)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 1
    assert "task detail file already exists" in second.stderr


def test_task_detail_writer_defaults_to_attempt_one(tmp_path: Path) -> None:
    """Without --attempt the writer records attempt 1 under the bare task id name."""
    output_dir = tmp_path / "details"

    process = run_task_detail(output_dir)
    payload = json.loads(process.stdout)
    written = read_json(output_dir / "task-current-coding.json")

    assert process.returncode == 0, process.stderr
    assert payload["attempt"] == 1
    assert written["attempt"] == 1


def test_task_detail_writer_writes_attempt_suffixed_file_for_retries(tmp_path: Path) -> None:
    """Attempt N > 1 must write <task_id>-a<N>.json alongside the first attempt."""
    output_dir = tmp_path / "details"

    first = run_task_detail(output_dir)
    second = run_task_detail(output_dir, attempt="2")
    second_payload = json.loads(second.stdout)
    second_path = output_dir / "task-current-coding-a2.json"

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert second_payload["attempt"] == 2
    assert second_payload["task_detail_path"] == str(second_path)
    assert second_path.is_file()
    assert read_json(second_path)["attempt"] == 2


def test_task_detail_writer_refuses_to_overwrite_same_attempt(tmp_path: Path) -> None:
    """Repeating the same attempt number must fail instead of overwriting."""
    output_dir = tmp_path / "details"

    first = run_task_detail(output_dir, attempt="2")
    second = run_task_detail(output_dir, attempt="2")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 1
    assert "task detail file already exists" in second.stderr


def test_task_detail_writer_rejects_invalid_attempt(tmp_path: Path) -> None:
    """Attempt must be a positive integer."""
    output_dir = tmp_path / "details"

    zero = run_task_detail(output_dir, attempt="0")
    non_integer = run_task_detail(output_dir, attempt="two")

    assert zero.returncode == 1
    assert "--attempt must be at least 1" in zero.stderr
    assert non_integer.returncode == 1
    assert "--attempt must be an integer" in non_integer.stderr


def test_task_detail_writer_fails_on_inconsistent_task_identity(tmp_path: Path) -> None:
    """Task evaluation and candidate search identity must match."""
    fixtures = fixture_dir()
    bad_candidate_path = tmp_path / "bad-candidate-search.json"
    output_dir = tmp_path / "details"
    candidate = read_json(fixtures / "candidate-search-coding.json")
    candidate["task_id"] = "different-task"
    write_json(bad_candidate_path, candidate)

    process = subprocess.run(
        [
            "uv",
            "run",
            ".kamino/evals/scripts/task_detail_write.py",
            "--output-dir",
            str(output_dir),
            "--task-eval",
            str(fixtures / "task-eval-coding.json"),
            "--difficulty",
            str(fixtures / "difficulty-coding.json"),
            "--candidate-search",
            str(bad_candidate_path),
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

    assert process.returncode == 1
    assert "candidate search task_id must match" in process.stderr


def test_task_detail_writer_does_not_write_outcome_ledger(tmp_path: Path) -> None:
    """Pre-run task detail persistence must not create an outcome ledger."""
    output_dir = tmp_path / "details"
    ledger_path = tmp_path / "task-outcome-ledger.jsonl"

    process = run_task_detail(output_dir)

    assert process.returncode == 0, process.stderr
    assert not ledger_path.exists()


def test_ledger_writer_reads_task_detail_and_appends_final_outcome(tmp_path: Path) -> None:
    """Outcome ledger writer should append exactly one final row using task detail context."""
    ledger_path = tmp_path / "ledger.jsonl"
    task_detail_path = fixture_dir() / "task-detail-coding.json"

    process = run_ledger_write(ledger_path, task_detail_path, fixture_dir() / "success-judgment-coding-true.json")
    payload = json.loads(process.stdout)
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])

    assert process.returncode == 0, process.stderr
    assert payload["task_detail_path"] == str(task_detail_path)
    assert len(lines) == 1
    assert record["task_detail_path"] == str(task_detail_path)
    assert record["success"] is True


def test_ledger_writer_refuses_missing_binary_judgment(tmp_path: Path) -> None:
    """Outcome ledger writer must not append without a valid binary success judgment."""
    ledger_path = tmp_path / "ledger.jsonl"
    bad_judgment_path = tmp_path / "bad-judgment.json"
    write_json(
        bad_judgment_path,
        {
            "reason": "missing success field",
            "satisfied_requirements": [],
            "missing_requirements": [],
            "partial_requirements": [],
            "unverifiable_requirements": [],
            "confidence": "high",
        },
    )

    process = run_ledger_write(ledger_path, fixture_dir() / "task-detail-coding.json", bad_judgment_path)

    assert process.returncode == 1
    assert "missing required key: success" in process.stderr
    assert not ledger_path.exists()
