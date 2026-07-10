"""Tests for deterministic task outcome ledger read/write scripts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType

from pytest import CaptureFixture, raises


def repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[2]


def fixture_dir() -> Path:
    """Return the task outcome ledger fixture directory."""
    return repo_root() / ".kamino" / "tests" / "fixtures" / "task-outcome-ledger"


def candidate_fixture_dir() -> Path:
    """Return the candidate search fixture directory."""
    return repo_root() / ".kamino" / "tests" / "fixtures" / "agent-candidate-search"


def load_script(module_name: str, script_name: str) -> ModuleType:
    """Load one ledger script as a module."""
    script_path = repo_root() / ".kamino" / "evals" / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None:
        raise AssertionError(f"could not load spec for {script_path}")
    if spec.loader is None:
        raise AssertionError(f"could not load loader for {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def checksum(path: Path) -> str:
    """Return the SHA-256 checksum for a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_lines(path: Path) -> list[dict[str, object]]:
    """Read a JSONL file into mappings."""
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise AssertionError("ledger line must parse as a JSON object")
        records.append(payload)
    return records


def write_json(path: Path, payload: object) -> None:
    """Write stable JSON test data."""
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def read_args(ledger_path: Path, task_eval: Path, difficulty: Path) -> list[str]:
    """Build common ledger read args."""
    return [
        "--ledger",
        str(ledger_path),
        "--task-eval",
        str(task_eval),
        "--difficulty",
        str(difficulty),
        "--format",
        "json",
    ]


def write_args(ledger_path: Path, judgment_path: Path) -> list[str]:
    """Build common ledger write args."""
    fixtures = candidate_fixture_dir()
    return [
        "--ledger",
        str(ledger_path),
        "--task-detail",
        str(fixtures / "task-detail-coding.json"),
        "--run-evidence",
        str(fixtures / "run-evidence-coding-success.json"),
        "--success-judgment",
        str(judgment_path),
        "--format",
        "json",
    ]


def test_ledger_read_returns_expected_matches_and_groups(capsys: CaptureFixture[str]) -> None:
    """Valid lookup should return nearest records and success/failure groups."""
    reader = load_script("kamino_task_outcome_ledger_read", "task_outcome_ledger_read.py")
    fixtures = fixture_dir()
    ledger_path = fixtures / "ledger-valid.jsonl"
    before = checksum(ledger_path)

    exit_code = reader.main(
        read_args(
            ledger_path,
            fixtures / "task-eval-complex.json",
            fixtures / "difficulty-complex.json",
        )
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert checksum(ledger_path) == before
    assert payload["schema_version"] == "kamino451.task-outcome-ledger-read.v1"
    assert payload["match_count"] == 2
    assert [item["task_id"] for item in payload["similar_historical_tasks"]] == [
        "prior-article-pipeline",
        "prior-blueprint-gap",
    ]
    assert payload["successful_agent_model_effort"][0]["route_chosen"] == "taskgraph"
    assert payload["failed_agent_model_effort"][0]["route_chosen"] == "createblueprint"
    assert "missing_required_output" in payload["risk_notes"][0]


def test_ledger_schema_validator_accepts_example_and_rejects_required_field_omissions() -> None:
    """The ledger schema validator should enforce critical required fields."""
    common = load_script("kamino_task_outcome_ledger_common_schema", "task_outcome_ledger_common.py")
    valid_record = json_lines(fixture_dir() / "ledger-valid.jsonl")[0]

    parsed = common.validate_ledger_record(valid_record, "valid example")

    assert parsed["record_id"] == "task-outcome-prior-review"
    for required_field in ["success", "task_text_hash", "route_chosen", "success_judgment_path"]:
        invalid_record = dict(valid_record)
        del invalid_record[required_field]
        with raises(ValueError, match=f"missing required key: {required_field}"):
            common.validate_ledger_record(invalid_record, f"missing {required_field}")


def test_compile_only_evaluation_ranking_lookup_does_not_mutate_ledger(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    """The compile-only evidence sequence should read but never mutate the ledger."""
    evaluator = load_script("kamino_compile_only_task_evaluator", "evaluate_task.py")
    ranking = load_script("kamino_compile_only_pairwise_ranking", "bradley_terry_pairwise_ranking.py")
    reader = load_script("kamino_compile_only_ledger_read", "task_outcome_ledger_read.py")
    fixtures = fixture_dir()
    ledger_path = tmp_path / "ledger-valid.jsonl"
    task_eval_path = tmp_path / "task-evaluation.json"
    ranking_path = tmp_path / "ranking.json"
    target_comparisons_path = tmp_path / "target-comparisons.json"
    difficulty_path = tmp_path / "difficulty-placement.json"
    shutil.copyfile(fixtures / "ledger-valid.jsonl", ledger_path)

    eval_exit = evaluator.main(
        [
            "--task",
            "Write a concise article review using the existing article-review agent. Return Markdown feedback and include concrete revision recommendations.",
            "--format",
            "json",
        ]
    )
    eval_output = capsys.readouterr().out
    assert eval_exit == 0
    task_eval_path.write_text(eval_output, encoding="utf-8")

    rank_exit = ranking.main(
        [
            "rank",
            "--tasks",
            str(fixtures / "ranking-tasks.json"),
            "--comparisons",
            str(fixtures / "ranking-comparisons.json"),
            "--format",
            "json",
        ]
    )
    rank_output = capsys.readouterr().out
    assert rank_exit == 0
    ranking_path.write_text(rank_output, encoding="utf-8")
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

    similar_exit = ranking.main(
        [
            "similar",
            "--ranking",
            str(ranking_path),
            "--target-task",
            str(fixtures / "target-task.json"),
            "--comparisons",
            str(target_comparisons_path),
            "--neighbors",
            "2",
            "--format",
            "json",
        ]
    )
    similar_output = capsys.readouterr().out
    assert similar_exit == 0
    difficulty_path.write_text(similar_output, encoding="utf-8")

    before = checksum(ledger_path)
    lookup_exit = reader.main(read_args(ledger_path, task_eval_path, difficulty_path))
    lookup_output = capsys.readouterr().out
    after = checksum(ledger_path)
    lookup_payload = json.loads(lookup_output)

    assert lookup_exit == 0
    assert before == after
    assert lookup_payload["schema_version"] == "kamino451.task-outcome-ledger-read.v1"
    assert lookup_payload["match_count"] >= 1


def test_ledger_read_rejects_malformed_and_missing_required_ledgers(capsys: CaptureFixture[str]) -> None:
    """Malformed JSONL and schema-invalid records should fail."""
    reader = load_script("kamino_task_outcome_ledger_read_invalid", "task_outcome_ledger_read.py")
    fixtures = fixture_dir()

    malformed_exit = reader.main(
        read_args(
            fixtures / "ledger-malformed.jsonl",
            fixtures / "task-eval-simple.json",
            fixtures / "difficulty-simple.json",
        )
    )
    malformed_capture = capsys.readouterr()
    assert malformed_exit == 1
    assert "malformed JSON" in malformed_capture.err

    missing_exit = reader.main(
        read_args(
            fixtures / "ledger-missing-required-field.jsonl",
            fixtures / "task-eval-simple.json",
            fixtures / "difficulty-simple.json",
        )
    )
    missing_capture = capsys.readouterr()
    assert missing_exit == 1
    assert "missing required key: success" in missing_capture.err


def test_ledger_read_rejects_invalid_filter(capsys: CaptureFixture[str]) -> None:
    """Invalid filters should fail before emitting lookup evidence."""
    reader = load_script("kamino_task_outcome_ledger_read_filter", "task_outcome_ledger_read.py")
    fixtures = fixture_dir()

    exit_code = reader.main(
        read_args(
            fixtures / "ledger-valid.jsonl",
            fixtures / "task-eval-simple.json",
            fixtures / "difficulty-simple.json",
        )
        + ["--difficulty-band", "not-a-number"]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "--difficulty-band must be a number" in captured.err


def test_ledger_write_appends_success_failure_and_partial_records(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    """Valid judgments should append exactly one record and normalize partial completion."""
    writer = load_script("kamino_task_outcome_ledger_write", "task_outcome_ledger_write.py")
    fixtures = fixture_dir()
    ledger_path = tmp_path / "ledger.jsonl"

    success_exit = writer.main(write_args(ledger_path, fixtures / "success-judgment-true.json"))
    success_capture = capsys.readouterr()
    success_payload = json.loads(success_capture.out)
    records_after_success = json_lines(ledger_path)
    assert success_exit == 0
    assert success_payload["success"] is True
    assert success_payload["task_detail_path"] == str(candidate_fixture_dir() / "task-detail-coding.json")
    assert len(records_after_success) == 1
    assert records_after_success[0]["task_detail_path"] == str(candidate_fixture_dir() / "task-detail-coding.json")

    failure_exit = writer.main(write_args(ledger_path, fixtures / "success-judgment-false.json"))
    failure_capture = capsys.readouterr()
    failure_payload = json.loads(failure_capture.out)
    records_after_failure = json_lines(ledger_path)
    assert failure_exit == 0
    assert failure_payload["success"] is False
    assert len(records_after_failure) == 2
    assert records_after_failure[1]["failure_mode"] == "missing_required_output"

    partial_exit = writer.main(write_args(ledger_path, fixtures / "success-judgment-partial.json"))
    partial_capture = capsys.readouterr()
    partial_payload = json.loads(partial_capture.out)
    records_after_partial = json_lines(ledger_path)
    assert partial_exit == 0
    assert partial_payload["success"] is False
    assert len(records_after_partial) == 3
    assert records_after_partial[2]["success"] is False
    assert records_after_partial[2]["failure_mode"] == "partial_completion"


def test_ledger_write_rejects_missing_and_malformed_judgments_without_mutation(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    """Missing or malformed binary judgments must not modify the ledger."""
    writer = load_script("kamino_task_outcome_ledger_write_invalid", "task_outcome_ledger_write.py")
    fixtures = fixture_dir()
    ledger_path = tmp_path / "ledger.jsonl"
    shutil.copyfile(fixtures / "ledger-valid.jsonl", ledger_path)
    before = checksum(ledger_path)

    missing_exit = writer.main(write_args(ledger_path, fixtures / "success-judgment-missing-success.json"))
    missing_capture = capsys.readouterr()
    assert missing_exit == 1
    assert "missing required key: success" in missing_capture.err
    assert checksum(ledger_path) == before

    malformed_exit = writer.main(write_args(ledger_path, fixtures / "success-judgment-malformed.json"))
    malformed_capture = capsys.readouterr()
    assert malformed_exit == 1
    assert "success judgment.success must be a boolean" in malformed_capture.err
    assert checksum(ledger_path) == before


def test_ledger_write_rejects_unsupported_route_without_mutation(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    """Unsupported route decisions should fail before writing."""
    writer = load_script("kamino_task_outcome_ledger_write_route", "task_outcome_ledger_write.py")
    fixtures = candidate_fixture_dir()
    ledger_path = tmp_path / "ledger.jsonl"
    task_detail_path = tmp_path / "bad-task-detail.json"
    task_detail = json.loads((fixtures / "task-detail-coding.json").read_text(encoding="utf-8"))
    route_decision = task_detail["route_decision"]
    if not isinstance(route_decision, dict):
        raise AssertionError("route_decision must be an object")
    route_decision["route_chosen"] = "run"
    write_json(task_detail_path, task_detail)

    args = write_args(ledger_path, fixtures / "success-judgment-coding-true.json")
    detail_index = args.index("--task-detail") + 1
    args[detail_index] = str(task_detail_path)
    exit_code = writer.main(args)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "route_decision.route_chosen must be one of" in captured.err or "route decision.route_chosen must be one of" in captured.err
    assert not ledger_path.exists()


def test_ledger_write_duplicate_invocation_appends_auditable_second_record(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    """Repeated valid invocations should append a second record with a new sequence."""
    writer = load_script("kamino_task_outcome_ledger_write_duplicate", "task_outcome_ledger_write.py")
    fixtures = fixture_dir()
    ledger_path = tmp_path / "ledger.jsonl"

    first_exit = writer.main(write_args(ledger_path, fixtures / "success-judgment-true.json"))
    capsys.readouterr()
    second_exit = writer.main(write_args(ledger_path, fixtures / "success-judgment-true.json"))
    capsys.readouterr()

    records = json_lines(ledger_path)
    assert first_exit == 0
    assert second_exit == 0
    assert len(records) == 2
    assert records[0]["record_sequence"] == 1
    assert records[1]["record_sequence"] == 2
    assert records[0]["record_id"] != records[1]["record_id"]
