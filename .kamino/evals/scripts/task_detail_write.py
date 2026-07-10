#!/usr/bin/env python3
"""Write immutable pre-run task detail artifacts for Agent Factory runs."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from task_outcome_ledger_common import (
    TASK_DETAIL_SCHEMA_VERSION,
    load_json_file,
    parse_candidate_search,
    parse_difficulty_placement,
    parse_route_decision,
    parse_task_detail,
    parse_task_evaluation,
)

WRITE_SCHEMA_VERSION = "kamino451.task-detail-write.v1"


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Write one immutable task detail JSON artifact.")
    parser.add_argument("--output-dir", required=True, help="Directory where <task_id>.json will be written.")
    parser.add_argument("--task-eval", required=True, help="Path to task evaluation JSON.")
    parser.add_argument("--difficulty", required=True, help="Path to difficulty placement JSON.")
    parser.add_argument("--candidate-search", required=True, help="Path to candidate search JSON.")
    parser.add_argument("--route", required=True, help="Path to route decision JSON.")
    parser.add_argument("--attempt", required=False, default="1", help="Attempt number for this task. Defaults to 1.")
    parser.add_argument("--format", required=True, help="Output format. Must be json.")
    return parser.parse_args(argv)


def parse_attempt(raw_attempt: str) -> int:
    """Parse a required positive integer attempt number."""
    if raw_attempt.strip() == "":
        raise ValueError("--attempt must not be empty")
    try:
        attempt = int(raw_attempt)
    except ValueError as exc:
        raise ValueError("--attempt must be an integer") from exc
    if str(attempt) != raw_attempt:
        raise ValueError("--attempt must be an integer")
    if attempt < 1:
        raise ValueError("--attempt must be at least 1")
    return attempt


def utc_timestamp() -> str:
    """Return the current UTC timestamp in task-detail format."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_same_task_identity(task_evaluation: dict[str, object], candidate_search: dict[str, object]) -> None:
    """Require task evaluation and candidate search to describe the same task."""
    if task_evaluation["task_id"] != candidate_search["task_id"]:
        raise ValueError("candidate search task_id must match task evaluation task_id")
    if task_evaluation["task_text_hash"] != candidate_search["task_text_hash"]:
        raise ValueError("candidate search task_text_hash must match task evaluation task_text_hash")


def ensure_difficulty_target_matches(raw_difficulty: object, task_evaluation: dict[str, object]) -> None:
    """Validate difficulty target identity when the payload exposes it."""
    if not isinstance(raw_difficulty, dict):
        return
    raw_target = raw_difficulty.get("target")
    if raw_target is None:
        return
    if not isinstance(raw_target, dict):
        raise TypeError("difficulty.target must be a JSON object")
    raw_task_id = raw_target.get("task_id")
    if raw_task_id is not None and raw_task_id != task_evaluation["task_id"]:
        raise ValueError("difficulty target task_id must match task evaluation task_id")


def build_task_detail(
    task_evaluation_path: Path,
    difficulty_path: Path,
    candidate_search_path: Path,
    route_path: Path,
    task_evaluation: dict[str, object],
    difficulty: dict[str, object],
    candidate_search: dict[str, object],
    route: dict[str, object],
    attempt: int,
) -> dict[str, object]:
    """Build and validate one task detail payload."""
    payload = {
        "schema_version": TASK_DETAIL_SCHEMA_VERSION,
        "task_id": task_evaluation["task_id"],
        "task_text_hash": task_evaluation["task_text_hash"],
        "task_text": task_evaluation["task_text"],
        "task_evaluation_path": str(task_evaluation_path),
        "difficulty_placement_path": str(difficulty_path),
        "candidate_search_path": str(candidate_search_path),
        "route_decision_path": str(route_path),
        "created_at": utc_timestamp(),
        "task_evaluation": task_evaluation,
        "difficulty_placement": difficulty,
        "candidate_search": candidate_search,
        "route_decision": route,
        "attempt": attempt,
    }
    parse_task_detail(payload)
    return payload


def task_detail_filename(task_id: str, attempt: int) -> str:
    """Return the detail filename for an attempt; attempt 1 keeps the bare task id."""
    if attempt == 1:
        return f"{task_id}.json"
    return f"{task_id}-a{attempt}.json"


def write_task_detail(output_dir: Path, payload: dict[str, object], attempt: int) -> Path:
    """Write the immutable task detail file and refuse overwrites."""
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError(f"output directory path exists but is not a directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    task_id = str(payload["task_id"])
    output_path = output_dir / task_detail_filename(task_id, attempt)
    if output_path.exists():
        raise FileExistsError(f"task detail file already exists: {output_path}")
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def result_payload(output_path: Path, payload: dict[str, object]) -> dict[str, object]:
    """Build strict JSON CLI result."""
    return {
        "schema_version": WRITE_SCHEMA_VERSION,
        "task_detail_schema_version": TASK_DETAIL_SCHEMA_VERSION,
        "task_id": payload["task_id"],
        "task_text_hash": payload["task_text_hash"],
        "task_detail_path": str(output_path),
        "attempt": payload["attempt"],
    }


def format_json(payload: dict[str, object]) -> str:
    """Render stable JSON."""
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: list[str]) -> int:
    """Run the task detail writer CLI."""
    try:
        args = parse_args(argv)
        if args.format != "json":
            raise ValueError("--format must be json")
        attempt = parse_attempt(args.attempt)

        task_evaluation_path = Path(args.task_eval)
        difficulty_path = Path(args.difficulty)
        candidate_search_path = Path(args.candidate_search)
        route_path = Path(args.route)

        raw_task_evaluation = load_json_file(args.task_eval, "task evaluation")
        raw_difficulty = load_json_file(args.difficulty, "difficulty placement")
        raw_candidate_search = load_json_file(args.candidate_search, "candidate search")
        raw_route = load_json_file(args.route, "route decision")

        task_evaluation = parse_task_evaluation(raw_task_evaluation)
        difficulty = parse_difficulty_placement(raw_difficulty)
        candidate_search = parse_candidate_search(raw_candidate_search)
        route = parse_route_decision(raw_route)

        ensure_same_task_identity(task_evaluation, candidate_search)
        ensure_difficulty_target_matches(raw_difficulty, task_evaluation)

        task_detail = build_task_detail(
            task_evaluation_path,
            difficulty_path,
            candidate_search_path,
            route_path,
            task_evaluation,
            difficulty,
            candidate_search,
            route,
            attempt,
        )
        output_path = write_task_detail(Path(args.output_dir), task_detail, attempt)
        print(format_json(result_payload(output_path, task_detail)))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
