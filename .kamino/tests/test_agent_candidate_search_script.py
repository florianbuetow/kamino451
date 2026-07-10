"""Tests for deterministic Agent Factory candidate search."""

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


def run_candidate_search(ledger_path: Path, *, limit: str = "10", config_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run candidate search through uv run."""
    fixtures = fixture_dir()
    command = [
        "uv",
        "run",
        ".kamino/evals/scripts/agent_candidate_search.py",
        "--ledger",
        str(ledger_path),
        "--task-eval",
        str(fixtures / "task-eval-coding.json"),
        "--difficulty",
        str(fixtures / "difficulty-coding.json"),
        "--limit",
        limit,
        "--format",
        "json",
    ]
    if config_path is not None:
        command.extend(["--config", str(config_path)])
    return subprocess.run(
        command,
        cwd=repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )


def write_config(tmp_path: Path, *, threshold: float = 0.9, min_attempts: int = 3) -> Path:
    """Write a schema-valid factory config and return its path."""
    config_path = tmp_path / "factory-config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "kamino451.factory-config.v1",
                "routing": {
                    "success_rate_threshold": threshold,
                    "min_attempts_for_rate": min_attempts,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return config_path


def coding_ledger_record(sequence: int, *, blueprint: str, success: bool, pairwise: float) -> dict[str, object]:
    """Build one schema-valid coding ledger record aligned with the coding fixtures."""
    text = f"historical coding task {sequence}"
    return {
        "schema_version": "kamino451.task-outcome-ledger.v1",
        "record_id": f"task-outcome-{sequence}",
        "record_sequence": sequence,
        "timestamp": "2026-07-03T00:00:00Z",
        "task_detail_path": f".kamino/evals/tasks/details/task-{sequence}.json",
        "task_id": f"task-{sequence}",
        "task_text_hash": "sha256:" + "ab" * 32,
        "task_text": text,
        "task_type": "coding",
        "clarity_score": 4,
        "ambiguity_score": 2,
        "consistency_score": 5,
        "completeness_score": 4,
        "semantic_difficulty_score": 3,
        "pairwise_difficulty_score": pairwise,
        "nearest_prior_tasks": [{"task_id": "prior", "distance": 0.1}],
        "route_chosen": "clone",
        "agent_files_used": [".kamino/dispatch-queue/x/01-a.md"],
        "agent_blueprints_used": [blueprint],
        "model": "haiku",
        "effort": "medium",
        "execution_status": "completed" if success else "failed",
        "success": success,
        "failure_mode": "none" if success else "judged_failure",
        "success_judgment_path": ".kamino/evals/tasks/outcomes/x.json",
        "output_paths": ["work/solution.py"],
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


def write_ledger(tmp_path: Path, records: list[dict[str, object]]) -> Path:
    """Write JSONL ledger records and return the ledger path."""
    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8")
    return ledger_path


def parse_success(process: subprocess.CompletedProcess[str]) -> dict[str, object]:
    """Parse a successful strict JSON process result."""
    assert process.returncode == 0, process.stderr
    payload = json.loads(process.stdout)
    if not isinstance(payload, dict):
        raise AssertionError("candidate search output must be an object")
    return payload


def all_prior_record_ids(payload: dict[str, object]) -> list[str]:
    """Return all prior record ids surfaced by the candidate output."""
    candidates = payload["candidates"]
    if not isinstance(candidates, list):
        raise AssertionError("candidates must be a list")
    record_ids: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise AssertionError("candidate must be an object")
        prior_tasks = candidate["similar_prior_tasks"]
        if not isinstance(prior_tasks, list):
            raise AssertionError("similar_prior_tasks must be a list")
        for prior_task in prior_tasks:
            if not isinstance(prior_task, dict):
                raise AssertionError("prior task must be an object")
            record_ids.append(str(prior_task["record_id"]))
    return record_ids


def candidate_index(payload: dict[str, object], blueprint_path: str) -> int:
    """Return the index of a candidate by first blueprint path."""
    candidates = payload["candidates"]
    if not isinstance(candidates, list):
        raise AssertionError("candidates must be a list")
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise AssertionError("candidate must be an object")
        blueprints = candidate["agent_blueprints_used"]
        if not isinstance(blueprints, list):
            raise AssertionError("agent_blueprints_used must be a list")
        if blueprints[0] == blueprint_path:
            return index
    raise AssertionError(f"candidate not found for blueprint: {blueprint_path}")


def walk_keys(value: object) -> list[str]:
    """Recursively collect JSON object keys."""
    keys: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.append(key)
            keys.extend(walk_keys(nested))
    elif isinstance(value, list):
        for item in value:
            keys.extend(walk_keys(item))
    return keys


def test_candidate_search_returns_only_successful_records_by_default() -> None:
    """Failed historical records should not be candidates."""
    payload = parse_success(run_candidate_search(fixture_dir() / "ledger-mixed.jsonl"))

    assert "task-outcome-coding-failed" not in all_prior_record_ids(payload)


def test_candidate_search_ranks_same_task_type_above_unrelated_task_type() -> None:
    """Same task type should be favored over unrelated historical work."""
    payload = parse_success(run_candidate_search(fixture_dir() / "ledger-mixed.jsonl"))
    candidates = payload["candidates"]
    if not isinstance(candidates, list):
        raise AssertionError("candidates must be a list")
    first_candidate = candidates[0]
    if not isinstance(first_candidate, dict):
        raise AssertionError("first candidate must be an object")

    assert "coding" in first_candidate["matched_task_types"]


def test_candidate_search_uses_pairwise_difficulty_distance() -> None:
    """Closer pairwise difficulty should outrank farther coding candidates."""
    payload = parse_success(run_candidate_search(fixture_dir() / "ledger-mixed.jsonl"))

    close_index = candidate_index(payload, ".kamino/agents/library/coding/python-cli-agent.md")
    far_index = candidate_index(payload, ".kamino/agents/library/coding/refactor-agent.md")
    assert close_index < far_index


def test_candidate_search_uses_rubric_profile_distance() -> None:
    """Closer rubric profile should outrank similar-difficulty bad-rubric candidates."""
    payload = parse_success(run_candidate_search(fixture_dir() / "ledger-mixed.jsonl"))

    close_index = candidate_index(payload, ".kamino/agents/library/coding/python-cli-agent.md")
    bad_rubric_index = candidate_index(payload, ".kamino/agents/library/coding/python-service-agent.md")
    assert close_index < bad_rubric_index


def test_candidate_search_aggregates_repeated_successful_combinations() -> None:
    """Repeated route/blueprint/model/effort successes should collapse into one candidate."""
    payload = parse_success(run_candidate_search(fixture_dir() / "ledger-mixed.jsonl"))
    candidates = payload["candidates"]
    if not isinstance(candidates, list):
        raise AssertionError("candidates must be a list")
    first_candidate = candidates[0]
    if not isinstance(first_candidate, dict):
        raise AssertionError("first candidate must be an object")

    assert first_candidate["historical_success_count"] == 2
    assert first_candidate["agent_blueprints_used"] == [".kamino/agents/library/coding/python-cli-agent.md"]


def test_candidate_search_limits_output_to_top_ten() -> None:
    """Limit 10 should cap candidate output even with more successful groups."""
    payload = parse_success(run_candidate_search(fixture_dir() / "ledger-many-successes.jsonl"))

    assert payload["candidate_count"] == 10
    candidates = payload["candidates"]
    if not isinstance(candidates, list):
        raise AssertionError("candidates must be a list")
    assert len(candidates) == 10


def test_candidate_search_supports_zero_candidates() -> None:
    """A ledger with only failures should return valid zero-candidate JSON."""
    payload = parse_success(run_candidate_search(fixture_dir() / "ledger-only-failures.jsonl"))

    assert payload["candidate_count"] == 0
    assert payload["candidates"] == []


def test_candidate_search_cold_start_missing_ledger_returns_zero_candidates() -> None:
    """A ledger file that does not exist yet is a cold start, not an error."""
    payload = parse_success(run_candidate_search(fixture_dir() / "ledger-that-does-not-exist.jsonl"))

    assert payload["candidate_count"] == 0
    assert payload["candidates"] == []


def test_candidate_search_treats_existing_empty_ledger_as_cold_start() -> None:
    """A present-but-empty ledger is the virgin-factory cold start, not an error."""
    payload = parse_success(run_candidate_search(fixture_dir() / "ledger-existing-empty.jsonl"))

    assert payload["candidate_count"] == 0
    assert payload["candidates"] == []


def test_candidate_search_does_not_expose_numeric_score_fields() -> None:
    """Factory-facing candidate output must not contain score or weight fields."""
    payload = parse_success(run_candidate_search(fixture_dir() / "ledger-mixed.jsonl"))

    forbidden = {"score", "similarity_score", "score_components", "weight", "weights"}
    assert forbidden.isdisjoint(set(walk_keys(payload)))


def test_candidate_search_rejects_malformed_and_missing_metadata_ledgers() -> None:
    """Malformed JSONL and schema-invalid ledgers should fail fast."""
    malformed = run_candidate_search(fixture_dir() / "ledger-malformed.jsonl")
    missing_metadata = run_candidate_search(fixture_dir() / "ledger-missing-required-field.jsonl")

    assert malformed.returncode == 1
    assert "malformed JSON" in malformed.stderr
    assert missing_metadata.returncode == 1
    assert "missing required key: task_type" in missing_metadata.stderr


def test_candidate_search_rejects_invalid_limit() -> None:
    """Limit must be a positive integer."""
    zero = run_candidate_search(fixture_dir() / "ledger-mixed.jsonl", limit="0")
    non_integer = run_candidate_search(fixture_dir() / "ledger-mixed.jsonl", limit="not-an-int")

    assert zero.returncode == 1
    assert "--limit must be at least 1" in zero.stderr
    assert non_integer.returncode == 1
    assert "--limit must be an integer" in non_integer.stderr


def test_candidate_search_does_not_mutate_ledger() -> None:
    """Candidate search must be read-only."""
    ledger_path = fixture_dir() / "ledger-mixed.jsonl"
    before = ledger_path.read_bytes()

    parse_success(run_candidate_search(ledger_path))

    assert ledger_path.read_bytes() == before


def test_candidate_search_implementation_avoids_all_pairs_logic() -> None:
    """The search implementation should stay a linear ledger scan, not all-pairs ranking."""
    script_text = (repo_root() / ".kamino" / "evals" / "scripts" / "agent_candidate_search.py").read_text(encoding="utf-8")

    assert "itertools.combinations" not in script_text
    assert "itertools.permutations" not in script_text


def test_candidate_search_reports_success_rate_statistics() -> None:
    """Each candidate must carry attempt counts and success rates including failed attempts."""
    payload = parse_success(run_candidate_search(fixture_dir() / "ledger-mixed.jsonl"))

    index = candidate_index(payload, ".kamino/agents/library/coding/python-cli-agent.md")
    candidate = payload["candidates"][index]
    assert candidate["historical_success_count"] == 2
    assert candidate["historical_attempt_count"] == 3
    assert candidate["historical_success_rate"] == 0.666667
    assert candidate["same_task_type_attempt_count"] == 3
    assert candidate["same_task_type_success_count"] == 2
    assert candidate["same_task_type_success_rate"] == 0.666667
    assert candidate["meets_success_rate_threshold"] is False


def test_candidate_search_reports_routing_config() -> None:
    """The payload must echo the routing config values used for threshold checks."""
    payload = parse_success(run_candidate_search(fixture_dir() / "ledger-mixed.jsonl"))

    routing_config = payload["routing_config"]
    assert routing_config["success_rate_threshold"] == 0.9
    assert routing_config["min_attempts_for_rate"] == 3


def test_candidate_search_threshold_is_configurable(tmp_path: Path) -> None:
    """A lower central threshold must flip qualification and surface a match reason."""
    config_path = write_config(tmp_path, threshold=0.5, min_attempts=3)

    payload = parse_success(run_candidate_search(fixture_dir() / "ledger-mixed.jsonl", config_path=config_path))

    index = candidate_index(payload, ".kamino/agents/library/coding/python-cli-agent.md")
    candidate = payload["candidates"][index]
    assert candidate["meets_success_rate_threshold"] is True
    assert "meets success-rate threshold" in candidate["match_reasons"]
    assert payload["routing_config"]["success_rate_threshold"] == 0.5


def test_candidate_search_ranks_qualified_candidates_first(tmp_path: Path) -> None:
    """A threshold-qualified candidate must outrank a more similar but unproven one."""
    records = [
        coding_ledger_record(1, blueprint=".kamino/agents/library/coding/close-unproven-agent.md", success=True, pairwise=0.4),
        coding_ledger_record(2, blueprint=".kamino/agents/library/coding/far-proven-agent.md", success=True, pairwise=4.0),
        coding_ledger_record(3, blueprint=".kamino/agents/library/coding/far-proven-agent.md", success=True, pairwise=4.0),
        coding_ledger_record(4, blueprint=".kamino/agents/library/coding/far-proven-agent.md", success=True, pairwise=4.0),
    ]
    ledger_path = write_ledger(tmp_path, records)

    payload = parse_success(run_candidate_search(ledger_path))

    proven_index = candidate_index(payload, ".kamino/agents/library/coding/far-proven-agent.md")
    unproven_index = candidate_index(payload, ".kamino/agents/library/coding/close-unproven-agent.md")
    assert proven_index < unproven_index
    assert payload["candidates"][proven_index]["meets_success_rate_threshold"] is True
    assert payload["candidates"][unproven_index]["meets_success_rate_threshold"] is False
