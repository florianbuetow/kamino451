"""Tests for the weighted-majority route recommendation script."""

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


def task_evaluation(task_type: str = "code_generation") -> dict[str, object]:
    """Build a minimal valid task evaluation."""
    text = "Solve the current task."
    return {
        "schema_version": "kamino451.task-evaluation.v1",
        "task_id": "task-current",
        "task_text_hash": task_hash(text),
        "task_text": text,
        "task_type": task_type,
        "clarity_score": 4,
        "ambiguity_score": 2,
        "consistency_score": 5,
        "completeness_score": 4,
        "difficulty_score": 3,
        "recommended_mapping": "standard_model_task_agent",
        "open_issues": [],
    }


def difficulty_placement(score: float = 0.5) -> dict[str, object]:
    """Build a minimal valid difficulty placement."""
    return {
        "schema_version": "kamino451.bradley-terry-pairwise-ranking.v1",
        "estimated_insertion_rank": 2,
        "estimated_difficulty_score": score,
        "nearest_prior_tasks": [{"task_id": "prior", "distance": 0.1}],
    }


def ledger_record(
    sequence: int,
    *,
    model: str,
    effort: str,
    success: bool,
    task_type: str,
    pairwise: float,
    blueprint: str = ".kamino/agents/ad-hoc/coding/python-coding-agent.md",
) -> dict[str, object]:
    """Build one schema-valid ledger record."""
    text = f"historical task {sequence}"
    return {
        "schema_version": "kamino451.task-outcome-ledger.v1",
        "record_id": f"task-outcome-{sequence}",
        "record_sequence": sequence,
        "timestamp": "2026-07-03T00:00:00Z",
        "task_detail_path": f".kamino/evals/tasks/details/task-{sequence}.json",
        "task_id": f"task-{sequence}",
        "task_text_hash": task_hash(text),
        "task_text": text,
        "task_type": task_type,
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
        "model": model,
        "effort": effort,
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


def factory_config(threshold: float = 0.9, min_attempts: int = 3) -> dict[str, object]:
    """Build a schema-valid factory config payload."""
    return {
        "schema_version": "kamino451.factory-config.v1",
        "routing": {
            "success_rate_threshold": threshold,
            "min_attempts_for_rate": min_attempts,
        },
    }


def run_recommendation_process(
    tmp_path: Path,
    records: list[dict[str, object]],
    *,
    ledger_exists: bool = True,
    score: float = 0.5,
    config: dict[str, object] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the script and return the raw process result."""
    ledger_path = tmp_path / "ledger.jsonl"
    if ledger_exists:
        ledger_path.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8")
    eval_path = tmp_path / "eval.json"
    eval_path.write_text(json.dumps(task_evaluation(), sort_keys=True), encoding="utf-8")
    difficulty_path = tmp_path / "difficulty.json"
    difficulty_path.write_text(json.dumps(difficulty_placement(score), sort_keys=True), encoding="utf-8")

    command = [
        "uv", "run", ".kamino/evals/scripts/route_recommendation.py",
        "--ledger", str(ledger_path),
        "--task-eval", str(eval_path),
        "--difficulty", str(difficulty_path),
        "--format", "json",
    ]
    if config is not None:
        config_path = tmp_path / "factory-config.json"
        config_path.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
        command.extend(["--config", str(config_path)])

    return subprocess.run(
        command,
        cwd=repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )


def run_recommendation(
    tmp_path: Path,
    records: list[dict[str, object]],
    *,
    ledger_exists: bool = True,
    score: float = 0.5,
    config: dict[str, object] | None = None,
) -> dict[str, object]:
    """Run the script and parse its JSON output."""
    process = run_recommendation_process(tmp_path, records, ledger_exists=ledger_exists, score=score, config=config)
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def test_cold_start_recommends_cheap_first(tmp_path: Path) -> None:
    """No ledger yet -> haiku by escalation policy."""
    payload = run_recommendation(tmp_path, [], ledger_exists=False)

    assert payload["recommended_model"] == "haiku"
    assert payload["source"] == "cold_start_policy"
    assert payload["successful_records_considered"] == 0


def test_majority_of_similar_successes_wins(tmp_path: Path) -> None:
    """Same-type nearby successes should dominate the recommendation."""
    records = [
        ledger_record(1, model="haiku", effort="medium", success=True, task_type="code_generation", pairwise=0.4),
        ledger_record(2, model="haiku", effort="medium", success=True, task_type="code_generation", pairwise=0.6),
        ledger_record(3, model="sonnet", effort="high", success=True, task_type="writing", pairwise=5.0),
    ]

    payload = run_recommendation(tmp_path, records)

    assert payload["recommended_model"] == "haiku"
    assert payload["recommended_effort"] == "medium"
    assert payload["source"] == "weighted_majority"
    assert payload["successful_records_considered"] == 3


def test_failures_carry_no_weight(tmp_path: Path) -> None:
    """Only successes vote; a pile of failures must not win."""
    records = [
        ledger_record(1, model="haiku", effort="medium", success=False, task_type="code_generation", pairwise=0.5),
        ledger_record(2, model="haiku", effort="medium", success=False, task_type="code_generation", pairwise=0.5),
        ledger_record(3, model="sonnet", effort="medium", success=True, task_type="code_generation", pairwise=0.5),
    ]

    payload = run_recommendation(tmp_path, records)

    assert payload["recommended_model"] == "sonnet"
    assert payload["successful_records_considered"] == 1


def test_tie_breaks_cheap_first(tmp_path: Path) -> None:
    """Equal support goes to the cheaper model."""
    records = [
        ledger_record(1, model="sonnet", effort="medium", success=True, task_type="code_generation", pairwise=0.5),
        ledger_record(2, model="haiku", effort="medium", success=True, task_type="code_generation", pairwise=0.5),
    ]

    payload = run_recommendation(tmp_path, records)

    assert payload["recommended_model"] == "haiku"


def test_difficulty_proximity_outweighs_distant_matches(tmp_path: Path) -> None:
    """A same-type success near the current difficulty beats several far ones."""
    records = [
        ledger_record(1, model="sonnet", effort="medium", success=True, task_type="code_generation", pairwise=0.5),
        ledger_record(2, model="haiku", effort="medium", success=True, task_type="code_generation", pairwise=9.0),
        ledger_record(3, model="haiku", effort="medium", success=True, task_type="code_generation", pairwise=9.0),
    ]

    payload = run_recommendation(tmp_path, records, score=0.5)

    assert payload["recommended_model"] == "sonnet"


HAIKU_BLUEPRINT = ".kamino/agents/ad-hoc/coding/haiku-agent.md"
SONNET_BLUEPRINT = ".kamino/agents/ad-hoc/coding/sonnet-agent.md"


def rate_records(
    *,
    haiku_successes: int,
    haiku_failures: int,
    sonnet_successes: int,
    sonnet_failures: int,
    haiku_task_type: str = "code_generation",
) -> list[dict[str, object]]:
    """Build one haiku and one sonnet agent combination with the given outcome counts."""
    records: list[dict[str, object]] = []
    sequence = 1
    for index in range(haiku_successes + haiku_failures):
        records.append(
            ledger_record(
                sequence,
                model="haiku",
                effort="medium",
                success=index < haiku_successes,
                task_type=haiku_task_type,
                pairwise=0.5,
                blueprint=HAIKU_BLUEPRINT,
            )
        )
        sequence += 1
    for index in range(sonnet_successes + sonnet_failures):
        records.append(
            ledger_record(
                sequence,
                model="sonnet",
                effort="medium",
                success=index < sonnet_successes,
                task_type="code_generation",
                pairwise=0.5,
                blueprint=SONNET_BLUEPRINT,
            )
        )
        sequence += 1
    return records


def test_success_rate_gate_overrides_cheap_first(tmp_path: Path) -> None:
    """A cheap combination below the rate threshold must lose to a qualified pricier one."""
    records = rate_records(haiku_successes=4, haiku_failures=2, sonnet_successes=5, sonnet_failures=0)

    payload = run_recommendation(tmp_path, records, config=factory_config())

    assert payload["source"] == "success_rate_policy"
    assert payload["recommended_model"] == "sonnet"
    assert payload["recommended_agent_blueprints"] == [SONNET_BLUEPRINT]
    assert payload["selected_combination"]["same_task_type_attempts"] == 5
    assert payload["selected_combination"]["same_task_type_success_rate"] == 1.0


def test_qualified_combinations_pick_cheapest_not_highest_rate(tmp_path: Path) -> None:
    """Among qualified combinations the cheaper model wins, not the higher success rate."""
    records = rate_records(haiku_successes=10, haiku_failures=1, sonnet_successes=8, sonnet_failures=0)

    payload = run_recommendation(tmp_path, records, config=factory_config())

    assert payload["source"] == "success_rate_policy"
    assert payload["recommended_model"] == "haiku"
    assert payload["recommended_agent_blueprints"] == [HAIKU_BLUEPRINT]
    assert len(payload["qualified_combinations"]) == 2


def test_success_rate_threshold_is_configurable(tmp_path: Path) -> None:
    """Lowering the central threshold qualifies a combination the default would reject."""
    records = rate_records(haiku_successes=4, haiku_failures=2, sonnet_successes=5, sonnet_failures=0)

    payload = run_recommendation(tmp_path, records, config=factory_config(threshold=0.5))

    assert payload["source"] == "success_rate_policy"
    assert payload["recommended_model"] == "haiku"
    assert payload["routing_config"]["success_rate_threshold"] == 0.5


def test_min_attempts_guards_small_samples(tmp_path: Path) -> None:
    """A perfect rate over too few attempts must not qualify."""
    records = rate_records(haiku_successes=2, haiku_failures=0, sonnet_successes=2, sonnet_failures=0)

    payload = run_recommendation(tmp_path, records, config=factory_config(min_attempts=3))

    assert payload["source"] == "weighted_majority"
    assert payload["qualified_combinations"] == []


def test_success_rate_is_scoped_to_task_type(tmp_path: Path) -> None:
    """A qualifying rate on a different task type must not qualify for the current one."""
    records = rate_records(
        haiku_successes=5,
        haiku_failures=0,
        sonnet_successes=3,
        sonnet_failures=0,
        haiku_task_type="writing",
    )

    payload = run_recommendation(tmp_path, records, config=factory_config())

    assert payload["source"] == "success_rate_policy"
    assert payload["recommended_model"] == "sonnet"
    assert payload["recommended_agent_blueprints"] == [SONNET_BLUEPRINT]


def test_recommendation_reports_routing_config(tmp_path: Path) -> None:
    """Every recommendation must echo the routing config values it used."""
    payload = run_recommendation(tmp_path, [], ledger_exists=False, config=factory_config())

    assert payload["routing_config"]["success_rate_threshold"] == 0.9
    assert payload["routing_config"]["min_attempts_for_rate"] == 3
    assert payload["routing_config"]["config_source"] == "config_file"


def test_invalid_config_fails_clearly(tmp_path: Path) -> None:
    """An out-of-range threshold must fail instead of silently degrading."""
    process = run_recommendation_process(tmp_path, [], ledger_exists=False, config=factory_config(threshold=1.5))

    assert process.returncode == 1
    assert "success_rate_threshold" in process.stderr
