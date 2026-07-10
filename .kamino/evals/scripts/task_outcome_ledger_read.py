#!/usr/bin/env python3
"""Read historical Kamino task outcome records for factory routing evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from task_outcome_ledger_common import (
    LEDGER_SCHEMA_VERSION,
    load_json_file,
    load_ledger_records,
    parse_difficulty_placement,
    parse_task_evaluation,
    require_mapping,
)

READ_SCHEMA_VERSION = "kamino451.task-outcome-ledger-read.v1"


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Read task outcome ledger records for routing evidence.")
    parser.add_argument("--ledger", required=True, help="Path to a JSONL task outcome ledger.")
    parser.add_argument("--task-eval", required=True, help="Path to a task evaluation JSON artifact.")
    parser.add_argument("--difficulty", required=True, help="Path to a difficulty placement JSON artifact.")
    parser.add_argument("--task-type", required=False, help="Optional task_type filter.")
    parser.add_argument("--difficulty-band", required=False, help="Optional non-negative score distance filter.")
    parser.add_argument("--agent", required=False, help="Optional agent file or blueprint path filter.")
    parser.add_argument("--model", required=False, help="Optional model filter.")
    parser.add_argument("--effort", required=False, help="Optional effort filter.")
    parser.add_argument("--format", choices=["json"], required=True, help="Output format.")
    return parser.parse_args(argv)


def parse_optional_non_empty(value: object, label: str) -> str | None:
    """Parse an optional non-empty string."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    if value.strip() == "":
        raise ValueError(f"{label} must not be empty")
    return value


def parse_difficulty_band(value: object) -> float | None:
    """Parse an optional non-negative difficulty-band filter."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("--difficulty-band must be a string")
    if value.strip() == "":
        raise ValueError("--difficulty-band must not be empty")
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError("--difficulty-band must be a number") from exc
    if parsed < 0.0:
        raise ValueError("--difficulty-band must be non-negative")
    return parsed


def parse_filters(args: argparse.Namespace) -> dict[str, object]:
    """Parse optional filters explicitly."""
    return {
        "task_type": parse_optional_non_empty(args.task_type, "--task-type"),
        "difficulty_band": parse_difficulty_band(args.difficulty_band),
        "agent": parse_optional_non_empty(args.agent, "--agent"),
        "model": parse_optional_non_empty(args.model, "--model"),
        "effort": parse_optional_non_empty(args.effort, "--effort"),
    }


def record_matches_filters(record: dict[str, object], filters: dict[str, object], target_score: float) -> bool:
    """Return whether a validated ledger record passes explicit filters."""
    task_type = filters["task_type"]
    if task_type is not None and record["task_type"] != task_type:
        return False

    difficulty_band = filters["difficulty_band"]
    if difficulty_band is not None:
        band = float(difficulty_band)
        record_score = float(record["pairwise_difficulty_score"])
        if abs(record_score - target_score) > band:
            return False

    agent = filters["agent"]
    if agent is not None:
        agent_string = str(agent)
        agent_files = record["agent_files_used"]
        agent_blueprints = record["agent_blueprints_used"]
        if agent_string not in agent_files and agent_string not in agent_blueprints:
            return False

    model = filters["model"]
    if model is not None and record["model"] != model:
        return False

    effort = filters["effort"]
    if effort is not None and record["effort"] != effort:
        return False

    return True


def nearest_task_ids(difficulty: dict[str, object]) -> dict[str, float]:
    """Return nearest prior task ids and distances from difficulty placement."""
    raw_tasks = difficulty["nearest_prior_tasks"]
    if not isinstance(raw_tasks, list):
        raise TypeError("difficulty.nearest_prior_tasks must be a list")
    mapping: dict[str, float] = {}
    for raw_task in raw_tasks:
        task = require_mapping(raw_task, "difficulty.nearest_prior_tasks[]")
        task_id = task["task_id"]
        distance = task["distance"]
        if not isinstance(task_id, str):
            raise TypeError("difficulty.nearest_prior_tasks[].task_id must be a string")
        if isinstance(distance, bool) or not isinstance(distance, int | float):
            raise TypeError("difficulty.nearest_prior_tasks[].distance must be a number")
        mapping[task_id] = float(distance)
    return mapping


def similarity_sort_key(record: dict[str, object], nearest_distances: dict[str, float], target_score: float) -> tuple[float, str]:
    """Sort records by explicit nearest-task distance, then score distance."""
    task_id = str(record["task_id"])
    if task_id in nearest_distances:
        return (nearest_distances[task_id], task_id)
    score_distance = abs(float(record["pairwise_difficulty_score"]) - target_score)
    return (score_distance + 1000.0, task_id)


def build_combo(record: dict[str, object]) -> dict[str, object]:
    """Build an agent/model/effort summary key from one record."""
    return {
        "route_chosen": record["route_chosen"],
        "agent_files_used": record["agent_files_used"],
        "agent_blueprints_used": record["agent_blueprints_used"],
        "model": record["model"],
        "effort": record["effort"],
    }


def combo_key(combo: dict[str, object]) -> str:
    """Build a stable string key for combo grouping."""
    return json.dumps(combo, sort_keys=True, separators=(",", ":"))


def summarize_combos(records: list[dict[str, object]], success_value: bool) -> list[dict[str, object]]:
    """Summarize successful or failed agent/model/effort combinations."""
    grouped: dict[str, dict[str, object]] = {}
    for record in records:
        if record["success"] != success_value:
            continue
        combo = build_combo(record)
        key = combo_key(combo)
        if key not in grouped:
            grouped[key] = {
                "route_chosen": combo["route_chosen"],
                "agent_files_used": combo["agent_files_used"],
                "agent_blueprints_used": combo["agent_blueprints_used"],
                "model": combo["model"],
                "effort": combo["effort"],
                "count": 0,
                "task_ids": [],
            }
        grouped_record = grouped[key]
        grouped_record["count"] = int(grouped_record["count"]) + 1
        task_ids = grouped_record["task_ids"]
        if not isinstance(task_ids, list):
            raise TypeError("internal grouped task_ids must be a list")
        task_ids.append(record["task_id"])

    summaries = list(grouped.values())
    summaries.sort(key=lambda item: (-int(item["count"]), str(item["route_chosen"]), str(item["model"]), str(item["effort"])))
    return summaries


def build_risk_notes(records: list[dict[str, object]]) -> list[str]:
    """Build risk notes derived only from failed ledger records."""
    failure_counts: dict[str, int] = {}
    route_failures: dict[str, int] = {}
    for record in records:
        if record["success"] is True:
            continue
        failure_mode = str(record["failure_mode"])
        route = str(record["route_chosen"])
        if failure_mode not in failure_counts:
            failure_counts[failure_mode] = 0
        if route not in route_failures:
            route_failures[route] = 0
        failure_counts[failure_mode] += 1
        route_failures[route] += 1

    notes: list[str] = []
    for failure_mode, count in sorted(failure_counts.items()):
        notes.append(f"{count} similar record(s) failed with failure_mode={failure_mode}")
    for route, count in sorted(route_failures.items()):
        notes.append(f"{count} similar failure record(s) used route={route}")
    return notes


def build_similar_tasks(
    records: list[dict[str, object]],
    nearest_distances: dict[str, float],
    target_score: float,
) -> list[dict[str, object]]:
    """Build sorted similar historical task summaries."""
    sorted_records = sorted(records, key=lambda record: similarity_sort_key(record, nearest_distances, target_score))
    tasks: list[dict[str, object]] = []
    for record in sorted_records:
        task_id = str(record["task_id"])
        if task_id in nearest_distances:
            distance = nearest_distances[task_id]
        else:
            distance = round(abs(float(record["pairwise_difficulty_score"]) - target_score), 6)
        tasks.append(
            {
                "task_id": task_id,
                "task_text_hash": record["task_text_hash"],
                "task_type": record["task_type"],
                "distance": distance,
                "route_chosen": record["route_chosen"],
                "model": record["model"],
                "effort": record["effort"],
                "success": record["success"],
                "failure_mode": record["failure_mode"],
            }
        )
    return tasks


def build_lookup(
    ledger_records: list[dict[str, object]],
    task_evaluation: dict[str, object],
    difficulty: dict[str, object],
    filters: dict[str, object],
) -> dict[str, object]:
    """Build deterministic routing evidence from ledger records."""
    target_score = float(difficulty["estimated_difficulty_score"])
    nearest_distances = nearest_task_ids(difficulty)
    filtered_records = [
        record for record in ledger_records if record_matches_filters(record, filters, target_score)
    ]

    nearest_records = [record for record in filtered_records if str(record["task_id"]) in nearest_distances]
    if len(nearest_records) > 0:
        matched_records = nearest_records
    else:
        task_type = str(task_evaluation["task_type"])
        matched_records = [record for record in filtered_records if record["task_type"] == task_type]

    return {
        "schema_version": READ_SCHEMA_VERSION,
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "task_id": task_evaluation["task_id"],
        "task_text_hash": task_evaluation["task_text_hash"],
        "task_type": task_evaluation["task_type"],
        "target_pairwise_difficulty_score": target_score,
        "filters_applied": filters,
        "ledger_record_count": len(ledger_records),
        "filtered_record_count": len(filtered_records),
        "match_count": len(matched_records),
        "similar_historical_tasks": build_similar_tasks(matched_records, nearest_distances, target_score),
        "successful_agent_model_effort": summarize_combos(matched_records, True),
        "failed_agent_model_effort": summarize_combos(matched_records, False),
        "risk_notes": build_risk_notes(matched_records),
    }


def format_json(payload: dict[str, object]) -> str:
    """Render stable JSON."""
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: list[str]) -> int:
    """Run the task outcome ledger read CLI."""
    try:
        args = parse_args(argv)
        output_format = args.format
        if output_format != "json":
            raise ValueError("--format must be json")

        filters = parse_filters(args)
        ledger_path = Path(args.ledger)
        # allow_empty: a present-but-empty ledger is the virgin-factory cold
        # start (the reports pipeline touches the file before any sweep runs).
        records = load_ledger_records(ledger_path, allow_empty=True) if ledger_path.exists() else []
        task_evaluation = parse_task_evaluation(load_json_file(args.task_eval, "task evaluation"))
        difficulty = parse_difficulty_placement(load_json_file(args.difficulty, "difficulty placement"))
        payload = build_lookup(records, task_evaluation, difficulty, filters)
        print(format_json(payload))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
