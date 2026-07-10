"""Tests for the deterministic ground-truth success judgment script."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[2]


def run_evidence(*, execution_status: str = "completed", tests_passed: object = True) -> dict[str, object]:
    """Build run evidence with ground-truth test results."""
    verification: dict[str, object] = {"outputs_non_empty": True}
    if tests_passed is not None:
        verification["tests_passed"] = tests_passed
    return {
        "execution_status": execution_status,
        "output_paths": [".kamino/dispatch-queue/fixture/outputs/01-python-coding-agent.md"],
        "verification_evidence": verification,
    }


def run_judgment(evidence_path: Path, *, output: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run the judgment script through uv run."""
    command = [
        "uv",
        "run",
        ".kamino/evals/scripts/success_judgment_from_tests.py",
        "--run-evidence",
        str(evidence_path),
        "--format",
        "json",
    ]
    if output is not None:
        command.extend(["--output", str(output)])
    return subprocess.run(
        command,
        cwd=repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )


def write_json(path: Path, payload: object) -> Path:
    """Write JSON test data."""
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_judgment_is_success_when_execution_completed_and_tests_passed(tmp_path: Path) -> None:
    """Completed execution plus passing tests should judge success true."""
    evidence_path = write_json(tmp_path / "evidence.json", run_evidence())

    process = run_judgment(evidence_path)
    judgment = json.loads(process.stdout)

    assert process.returncode == 0, process.stderr
    assert judgment["success"] is True
    assert judgment["confidence"] == "high"
    assert judgment["judgment_source"] == "deterministic_ground_truth_tests"
    assert "ground truth test suite passed" in judgment["satisfied_requirements"]
    assert judgment["missing_requirements"] == []


def test_judgment_is_failure_when_tests_failed(tmp_path: Path) -> None:
    """Failing ground-truth tests should judge success false."""
    evidence_path = write_json(tmp_path / "evidence.json", run_evidence(tests_passed=False))

    process = run_judgment(evidence_path)
    judgment = json.loads(process.stdout)

    assert process.returncode == 0, process.stderr
    assert judgment["success"] is False
    assert "ground truth test suite passed" in judgment["missing_requirements"]


def test_judgment_is_failure_when_execution_failed(tmp_path: Path) -> None:
    """A failed pipeline is a failed task even if tests somehow passed."""
    evidence_path = write_json(tmp_path / "evidence.json", run_evidence(execution_status="failed"))

    process = run_judgment(evidence_path)
    judgment = json.loads(process.stdout)

    assert process.returncode == 0, process.stderr
    assert judgment["success"] is False
    assert "pipeline execution completed" in judgment["missing_requirements"]


def test_judgment_requires_boolean_tests_passed(tmp_path: Path) -> None:
    """Evidence without a boolean tests_passed flag must fail fast."""
    missing = write_json(tmp_path / "missing.json", run_evidence(tests_passed=None))
    non_boolean = write_json(tmp_path / "non-boolean.json", run_evidence(tests_passed="yes"))

    missing_process = run_judgment(missing)
    non_boolean_process = run_judgment(non_boolean)

    assert missing_process.returncode == 1
    assert "missing required key: tests_passed" in missing_process.stderr
    assert non_boolean_process.returncode == 1
    assert "tests_passed must be a boolean" in non_boolean_process.stderr


def test_judgment_writes_artifact_and_refuses_overwrite(tmp_path: Path) -> None:
    """The optional output artifact must be written once and never overwritten."""
    evidence_path = write_json(tmp_path / "evidence.json", run_evidence())
    output_path = tmp_path / "outcomes" / "task-success.json"

    first = run_judgment(evidence_path, output=output_path)
    second = run_judgment(evidence_path, output=output_path)

    assert first.returncode == 0, first.stderr
    assert output_path.is_file()
    assert json.loads(output_path.read_text(encoding="utf-8"))["success"] is True
    assert second.returncode == 1
    assert "success judgment file already exists" in second.stderr


def test_judgment_feeds_the_ledger_writer(tmp_path: Path) -> None:
    """A deterministic judgment must be accepted by the outcome ledger writer."""
    fixtures = repo_root() / ".kamino" / "tests" / "fixtures" / "agent-candidate-search"
    evidence_path = write_json(tmp_path / "evidence.json", run_evidence())
    judgment_path = tmp_path / "judgment.json"
    ledger_path = tmp_path / "ledger.jsonl"

    judgment_process = run_judgment(evidence_path, output=judgment_path)
    ledger_process = subprocess.run(
        [
            "uv",
            "run",
            ".kamino/evals/scripts/task_outcome_ledger_write.py",
            "--ledger",
            str(ledger_path),
            "--task-detail",
            str(fixtures / "task-detail-coding.json"),
            "--run-evidence",
            str(evidence_path),
            "--success-judgment",
            str(judgment_path),
            "--format",
            "json",
        ],
        cwd=repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )
    record = json.loads(ledger_path.read_text(encoding="utf-8").splitlines()[0])

    assert judgment_process.returncode == 0, judgment_process.stderr
    assert ledger_process.returncode == 0, ledger_process.stderr
    assert record["success"] is True
    assert record["failure_mode"] == "none"
