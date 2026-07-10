#!/usr/bin/env python3
"""Find score-free Agent Factory candidates from successful historical outcomes."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from task_outcome_ledger_common import (
    CANDIDATE_SEARCH_SCHEMA_VERSION,
    load_json_file,
    load_ledger_records,
    load_routing_config,
    parse_difficulty_placement,
    parse_task_evaluation,
)

CLONE_MAPPINGS = {"small_fast_model_simple_agent", "standard_model_task_agent"}
TASKGRAPH_MAPPINGS = {"strong_model_planning_tool_agent"}
MAX_EXAMPLE_AGENT_FILES = 3
MAX_PRIOR_TASKS = 3


@dataclass(frozen=True)
class ScoredRecord:
    """One ledger record plus internal similarity evidence."""

    record: dict[str, object]
    base_value: float
    task_type_similarity: float
    pairwise_similarity: float
    semantic_difficulty_similarity: float
    rubric_similarity: float
    route_similarity: float


@dataclass
class CandidateGroup:
    """Grouped successful historical records for one reusable agent/model/effort option."""

    route_chosen: str
    agent_blueprints_used: tuple[str, ...]
    model: str
    effort: str
    records: list[ScoredRecord]
    agent_files_used: list[str]
    matched_task_types: list[str]


@dataclass
class AttemptStats:
    """Attempt counts for one group key over all ledger records, failures included."""

    attempts: int = 0
    successes: int = 0
    same_type_attempts: int = 0
    same_type_successes: int = 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Search successful Agent Factory candidates from the outcome ledger.")
    parser.add_argument("--ledger", required=True, help="Path to the task outcome ledger JSONL file.")
    parser.add_argument("--task-eval", required=True, help="Path to the current task evaluation JSON.")
    parser.add_argument("--difficulty", required=True, help="Path to the current difficulty placement JSON.")
    parser.add_argument("--limit", required=True, help="Maximum number of candidates to return.")
    parser.add_argument("--config", required=False, help="Path to the central factory config JSON (default: .kamino/factory-config.json).")
    parser.add_argument("--format", required=True, help="Output format. Must be json.")
    return parser.parse_args(argv)


def parse_limit(raw_limit: str) -> int:
    """Parse a required positive integer limit."""
    if raw_limit.strip() == "":
        raise ValueError("--limit must not be empty")
    try:
        limit = int(raw_limit)
    except ValueError as exc:
        raise ValueError("--limit must be an integer") from exc
    if str(limit) != raw_limit:
        raise ValueError("--limit must be an integer")
    if limit < 1:
        raise ValueError("--limit must be at least 1")
    return limit


def clamp_similarity(value: float) -> float:
    """Clamp a similarity value into the 0..1 range."""
    return max(0.0, min(1.0, value))


def preferred_route(recommended_mapping: str) -> str | None:
    """Map task evaluator routing recommendations to a preferred factory route."""
    if recommended_mapping in CLONE_MAPPINGS:
        return "clone"
    if recommended_mapping in TASKGRAPH_MAPPINGS:
        return "taskgraph"
    return None


def route_similarity(recommended_mapping: str, route_chosen: str) -> float:
    """Return route similarity for a historical record."""
    route = preferred_route(recommended_mapping)
    if route is None:
        return 0.5
    if route_chosen == route:
        return 1.0
    return 0.0


def average(values: list[float]) -> float:
    """Return the average for a non-empty list."""
    if len(values) == 0:
        raise ValueError("cannot average an empty list")
    return sum(values) / len(values)


def score_record(
    task_evaluation: dict[str, object],
    difficulty: dict[str, object],
    record: dict[str, object],
) -> ScoredRecord:
    """Compute internal similarity evidence for one successful historical record."""
    current_task_type = str(task_evaluation["task_type"])
    record_task_type = str(record["task_type"])
    task_type_similarity = 1.0 if current_task_type == record_task_type else 0.0

    current_pairwise = float(difficulty["estimated_difficulty_score"])
    record_pairwise = float(record["pairwise_difficulty_score"])
    pairwise_similarity = 1.0 / (1.0 + abs(current_pairwise - record_pairwise))

    semantic_distance = abs(float(task_evaluation["difficulty_score"]) - float(record["semantic_difficulty_score"]))
    semantic_difficulty_similarity = clamp_similarity(1.0 - (semantic_distance / 4.0))

    rubric_distances = [
        abs(float(task_evaluation["clarity_score"]) - float(record["clarity_score"])),
        abs(float(task_evaluation["ambiguity_score"]) - float(record["ambiguity_score"])),
        abs(float(task_evaluation["consistency_score"]) - float(record["consistency_score"])),
        abs(float(task_evaluation["completeness_score"]) - float(record["completeness_score"])),
    ]
    rubric_similarity = clamp_similarity(1.0 - (average(rubric_distances) / 4.0))
    route_value = route_similarity(str(task_evaluation["recommended_mapping"]), str(record["route_chosen"]))

    base_value = (
        (0.30 * task_type_similarity)
        + (0.25 * pairwise_similarity)
        + (0.15 * semantic_difficulty_similarity)
        + (0.20 * rubric_similarity)
        + (0.05 * route_value)
    )

    return ScoredRecord(
        record=record,
        base_value=base_value,
        task_type_similarity=task_type_similarity,
        pairwise_similarity=pairwise_similarity,
        semantic_difficulty_similarity=semantic_difficulty_similarity,
        rubric_similarity=rubric_similarity,
        route_similarity=route_value,
    )


def record_is_candidate(record: dict[str, object]) -> bool:
    """Return whether a ledger record can be considered for candidate reuse."""
    if record["success"] is not True:
        return False
    if record["failure_mode"] != "none":
        return False
    blueprints = record["agent_blueprints_used"]
    if not isinstance(blueprints, list) or len(blueprints) == 0:
        return False
    if str(record["model"]).strip() == "":
        return False
    if str(record["effort"]).strip() == "":
        return False
    return True


def group_key(record: dict[str, object]) -> tuple[str, tuple[str, ...], str, str]:
    """Build the exact grouping key required by the implementation plan."""
    return (
        str(record["route_chosen"]),
        tuple(str(item) for item in record["agent_blueprints_used"]),
        str(record["model"]),
        str(record["effort"]),
    )


def record_has_group_key(record: dict[str, object]) -> bool:
    """Return whether a ledger record carries the fields needed for group statistics."""
    blueprints = record["agent_blueprints_used"]
    if not isinstance(blueprints, list) or len(blueprints) == 0:
        return False
    if str(record["model"]).strip() == "":
        return False
    if str(record["effort"]).strip() == "":
        return False
    return True


def build_attempt_stats(
    ledger_records: list[dict[str, object]],
    current_task_type: str,
) -> dict[tuple[str, tuple[str, ...], str, str], AttemptStats]:
    """Count attempts and successes per group key over all records, failures included."""
    stats: dict[tuple[str, tuple[str, ...], str, str], AttemptStats] = {}
    for record in ledger_records:
        if not record_has_group_key(record):
            continue
        key = group_key(record)
        if key not in stats:
            stats[key] = AttemptStats()
        entry = stats[key]
        entry.attempts += 1
        succeeded = record["success"] is True
        if succeeded:
            entry.successes += 1
        if str(record["task_type"]) == current_task_type:
            entry.same_type_attempts += 1
            if succeeded:
                entry.same_type_successes += 1
    return stats


def meets_threshold(stats: AttemptStats, routing_config: dict[str, object]) -> bool:
    """Return whether same-task-type attempts clear the configured success-rate bar."""
    min_attempts = int(str(routing_config["min_attempts_for_rate"]))
    threshold = float(str(routing_config["success_rate_threshold"]))
    if stats.same_type_attempts < min_attempts:
        return False
    return stats.same_type_successes / stats.same_type_attempts > threshold


def append_unique_limited(items: list[str], raw_items: object, limit: int) -> None:
    """Append distinct string values while preserving first-seen order."""
    if not isinstance(raw_items, list):
        raise TypeError("expected a list of strings")
    for raw_item in raw_items:
        item = str(raw_item)
        if item in items:
            continue
        if len(items) >= limit:
            return
        items.append(item)


def append_unique(items: list[str], value: str) -> None:
    """Append one distinct string while preserving first-seen order."""
    if value not in items:
        items.append(value)


def build_groups(scored_records: list[ScoredRecord]) -> list[CandidateGroup]:
    """Group successful historical records by route, blueprint list, model, and effort."""
    groups: dict[tuple[str, tuple[str, ...], str, str], CandidateGroup] = {}
    for scored_record in scored_records:
        record = scored_record.record
        key = group_key(record)
        if key not in groups:
            groups[key] = CandidateGroup(
                route_chosen=key[0],
                agent_blueprints_used=key[1],
                model=key[2],
                effort=key[3],
                records=[],
                agent_files_used=[],
                matched_task_types=[],
            )
        group = groups[key]
        group.records.append(scored_record)
        append_unique_limited(group.agent_files_used, record["agent_files_used"], MAX_EXAMPLE_AGENT_FILES)
        append_unique(group.matched_task_types, str(record["task_type"]))
    return list(groups.values())


def candidate_internal_value(group: CandidateGroup) -> float:
    """Calculate internal grouped candidate ranking value."""
    best_record_value = max(scored_record.base_value for scored_record in group.records)
    repeat_success_similarity = min(len(group.records), 5) / 5.0
    return best_record_value + (0.05 * repeat_success_similarity)


def normalize_excerpt(text: str) -> str:
    """Return a whitespace-normalized task excerpt."""
    normalized = " ".join(text.split())
    if len(normalized) <= 240:
        return normalized
    return f"{normalized[:237]}..."


def prior_task_payload(scored_record: ScoredRecord) -> dict[str, object]:
    """Build one prior task payload for factory-facing candidate evidence."""
    record = scored_record.record
    return {
        "record_id": record["record_id"],
        "task_id": record["task_id"],
        "task_text_excerpt": normalize_excerpt(str(record["task_text"])),
        "task_type": record["task_type"],
        "route_chosen": record["route_chosen"],
        "model": record["model"],
        "effort": record["effort"],
    }


def match_reasons(group: CandidateGroup, qualifies: bool) -> list[str]:
    """Build score-free, positive-evidence match reasons."""
    best = max(group.records, key=lambda item: item.base_value)
    reasons: list[str] = []
    if qualifies:
        reasons.append("meets success-rate threshold")
    if best.task_type_similarity == 1.0:
        reasons.append("same task_type")
    if best.pairwise_similarity >= 0.75:
        reasons.append("similar pairwise difficulty")
    if best.semantic_difficulty_similarity >= 0.75:
        reasons.append("similar semantic difficulty")
    if best.rubric_similarity >= 0.75:
        reasons.append("similar rubric profile")
    if best.route_similarity == 1.0:
        reasons.append("route matches task mapping")
    if len(group.records) > 1:
        reasons.append("repeated successful history")
    if len(reasons) == 0:
        return ["successful historical agent"]
    return reasons


def sorted_group_records(group: CandidateGroup) -> list[ScoredRecord]:
    """Sort records inside one candidate group for prior-task evidence."""
    return sorted(
        group.records,
        key=lambda item: (-item.base_value, int(item.record["record_sequence"]), str(item.record["record_id"])),
    )


def candidate_payload(
    group: CandidateGroup,
    index: int,
    stats: AttemptStats,
    routing_config: dict[str, object],
) -> dict[str, object]:
    """Build one score-free candidate payload including success-rate statistics."""
    prior_tasks = [prior_task_payload(scored_record) for scored_record in sorted_group_records(group)[:MAX_PRIOR_TASKS]]
    qualifies = meets_threshold(stats, routing_config)
    payload: dict[str, object] = {
        "candidate_id": f"candidate-{index}",
        "route_chosen": group.route_chosen,
        "agent_blueprints_used": list(group.agent_blueprints_used),
        "agent_files_used": group.agent_files_used,
        "model": group.model,
        "effort": group.effort,
        "historical_success_count": len(group.records),
        "historical_attempt_count": stats.attempts,
        "historical_success_rate": round(stats.successes / stats.attempts, 6),
        "same_task_type_attempt_count": stats.same_type_attempts,
        "same_task_type_success_count": stats.same_type_successes,
        "meets_success_rate_threshold": qualifies,
        "matched_task_types": group.matched_task_types,
        "similar_prior_tasks": prior_tasks,
        "match_reasons": match_reasons(group, qualifies),
    }
    if stats.same_type_attempts > 0:
        payload["same_task_type_success_rate"] = round(stats.same_type_successes / stats.same_type_attempts, 6)
    return payload


def group_stats(
    group: CandidateGroup,
    attempt_stats: dict[tuple[str, tuple[str, ...], str, str], AttemptStats],
) -> AttemptStats:
    """Return the attempt statistics matching one candidate group."""
    key = (group.route_chosen, group.agent_blueprints_used, group.model, group.effort)
    return attempt_stats[key]


def sorted_groups(
    groups: list[CandidateGroup],
    attempt_stats: dict[tuple[str, tuple[str, ...], str, str], AttemptStats],
    routing_config: dict[str, object],
) -> list[CandidateGroup]:
    """Sort groups: threshold qualifiers first, then deterministic internal ranking and tie-breaking."""
    return sorted(
        groups,
        key=lambda group: (
            not meets_threshold(group_stats(group, attempt_stats), routing_config),
            -candidate_internal_value(group),
            -len(group.records),
            group.route_chosen,
            group.agent_blueprints_used[0],
            group.model,
            group.effort,
        ),
    )


def search_candidates(
    ledger_records: list[dict[str, object]],
    task_evaluation: dict[str, object],
    difficulty: dict[str, object],
    limit: int,
    routing_config: dict[str, object],
) -> dict[str, object]:
    """Build a score-free shortlist of historical candidates with success-rate evidence."""
    scored_records = [
        score_record(task_evaluation, difficulty, record)
        for record in ledger_records
        if record_is_candidate(record)
    ]
    attempt_stats = build_attempt_stats(ledger_records, str(task_evaluation["task_type"]))
    groups = sorted_groups(build_groups(scored_records), attempt_stats, routing_config)
    candidates = [
        candidate_payload(group, index, group_stats(group, attempt_stats), routing_config)
        for index, group in enumerate(groups[:limit], start=1)
    ]
    return {
        "schema_version": CANDIDATE_SEARCH_SCHEMA_VERSION,
        "task_id": task_evaluation["task_id"],
        "task_text_hash": task_evaluation["task_text_hash"],
        "limit": limit,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "routing_config": {
            "success_rate_threshold": routing_config["success_rate_threshold"],
            "min_attempts_for_rate": routing_config["min_attempts_for_rate"],
            "config_source": routing_config["config_source"],
            "config_path": routing_config["config_path"],
        },
    }


def format_json(payload: dict[str, object]) -> str:
    """Render stable JSON."""
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: list[str]) -> int:
    """Run the candidate search CLI."""
    try:
        args = parse_args(argv)
        if args.format != "json":
            raise ValueError("--format must be json")
        limit = parse_limit(args.limit)
        routing_config = load_routing_config(args.config)
        ledger_path = Path(args.ledger)
        if ledger_path.exists():
            # allow_empty: a present-but-empty ledger is the virgin-factory cold
            # start (the reports pipeline touches the file before any sweep runs).
            ledger_records = load_ledger_records(ledger_path, allow_empty=True)
        else:
            # Cold start: no ledger yet means no history, not an error.
            ledger_records = []
        task_evaluation = parse_task_evaluation(load_json_file(args.task_eval, "task evaluation"))
        difficulty = parse_difficulty_placement(load_json_file(args.difficulty, "difficulty placement"))
        payload = search_candidates(ledger_records, task_evaluation, difficulty, limit, routing_config)
        print(format_json(payload))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
