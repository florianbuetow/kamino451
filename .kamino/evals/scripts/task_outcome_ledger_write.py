#!/usr/bin/env python3
"""Append validated Kamino task outcome records to a JSONL ledger."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from task_outcome_ledger_common import (
    LEDGER_SCHEMA_VERSION,
    failure_mode_for_judgment,
    load_json_file,
    load_ledger_records,
    normalized_success,
    parse_run_evidence,
    parse_success_judgment,
    parse_task_detail,
    stable_sha256,
    validate_ledger_record,
)

WRITE_SCHEMA_VERSION = "kamino451.task-outcome-ledger-write.v1"


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Append one validated task outcome ledger record.")
    parser.add_argument("--ledger", required=True, help="Path to the JSONL task outcome ledger to append.")
    parser.add_argument("--task-detail", required=True, help="Path to a durable task detail JSON artifact.")
    parser.add_argument("--run-evidence", required=True, help="Path to a run evidence JSON artifact.")
    parser.add_argument("--success-judgment", required=True, help="Path to a binary success judgment JSON artifact.")
    parser.add_argument("--format", choices=["json"], required=True, help="Output format.")
    return parser.parse_args(argv)


def utc_timestamp() -> str:
    """Return the current UTC timestamp in ledger format."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_ledger_writable(path: Path) -> None:
    """Validate that the ledger path can be written."""
    if path.exists() and not path.is_file():
        raise ValueError(f"ledger path exists but is not a file: {path}")
    parent = path.parent
    if not parent.is_dir():
        raise FileNotFoundError(f"ledger parent directory does not exist: {parent}")


def existing_records_for_append(path: Path) -> list[dict[str, object]]:
    """Load existing ledger records when the file exists."""
    if not path.exists():
        return []
    return load_ledger_records(path, allow_empty=True)


def build_record(
    ledger_path: Path,
    existing_record_count: int,
    task_detail: dict[str, object],
    task_detail_path: Path,
    run_evidence: dict[str, object],
    success_judgment: dict[str, object],
    success_judgment_path: Path,
) -> dict[str, object]:
    """Build one validated ledger record."""
    record_sequence = existing_record_count + 1
    success = normalized_success(success_judgment)
    task_evaluation = task_detail["task_evaluation"]
    if not isinstance(task_evaluation, dict):
        raise TypeError("task detail.task_evaluation must be a JSON object")
    difficulty = task_detail["difficulty_placement"]
    if not isinstance(difficulty, dict):
        raise TypeError("task detail.difficulty_placement must be a JSON object")
    route = task_detail["route_decision"]
    if not isinstance(route, dict):
        raise TypeError("task detail.route_decision must be a JSON object")
    record_payload = {
        "ledger_path": str(ledger_path),
        "record_sequence": record_sequence,
        "task_text_hash": task_evaluation["task_text_hash"],
        "task_detail_path": str(task_detail_path),
        "route_chosen": route["route_chosen"],
        "success": success,
        "output_paths": run_evidence["output_paths"],
    }
    record_id = f"task-outcome-{stable_sha256(record_payload)[:16]}"
    record = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "record_id": record_id,
        "record_sequence": record_sequence,
        "timestamp": utc_timestamp(),
        "task_detail_path": str(task_detail_path),
        "task_id": task_evaluation["task_id"],
        "task_text_hash": task_evaluation["task_text_hash"],
        "task_text": task_evaluation["task_text"],
        "task_type": task_evaluation["task_type"],
        "clarity_score": task_evaluation["clarity_score"],
        "ambiguity_score": task_evaluation["ambiguity_score"],
        "consistency_score": task_evaluation["consistency_score"],
        "completeness_score": task_evaluation["completeness_score"],
        "semantic_difficulty_score": task_evaluation["difficulty_score"],
        "pairwise_difficulty_score": difficulty["estimated_difficulty_score"],
        "nearest_prior_tasks": difficulty["nearest_prior_tasks"],
        "route_chosen": route["route_chosen"],
        "agent_files_used": route["agent_files_used"],
        "agent_blueprints_used": route["agent_blueprints_used"],
        "model": route["model"],
        "effort": route["effort"],
        "execution_status": run_evidence["execution_status"],
        "success": success,
        "failure_mode": failure_mode_for_judgment(success_judgment),
        "success_judgment_path": str(success_judgment_path),
        "output_paths": run_evidence["output_paths"],
        "verification_evidence": run_evidence["verification_evidence"],
        "success_judgment": success_judgment,
    }
    validate_ledger_record(record, "new ledger record")
    return record


def append_record(ledger_file: TextIO, record: dict[str, object]) -> None:
    """Append one stable JSONL record to an open, exclusively locked file."""
    line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    ledger_file.seek(0, os.SEEK_END)
    ledger_file.write(f"{line}\n")
    ledger_file.flush()
    os.fsync(ledger_file.fileno())


def build_and_append_locked(
    ledger_path: Path,
    task_detail: dict[str, object],
    task_detail_path: Path,
    run_evidence: dict[str, object],
    success_judgment: dict[str, object],
    success_judgment_path: Path,
) -> dict[str, object]:
    """Allocate the sequence and append atomically across writer processes."""
    with ledger_path.open("a+", encoding="utf-8") as ledger_file:
        fcntl.flock(ledger_file.fileno(), fcntl.LOCK_EX)
        try:
            existing_records = existing_records_for_append(ledger_path)
            record = build_record(
                ledger_path,
                len(existing_records),
                task_detail,
                task_detail_path,
                run_evidence,
                success_judgment,
                success_judgment_path,
            )
            append_record(ledger_file, record)
            return record
        finally:
            fcntl.flock(ledger_file.fileno(), fcntl.LOCK_UN)


def result_payload(ledger_path: Path, record: dict[str, object]) -> dict[str, object]:
    """Build strict JSON CLI result."""
    return {
        "schema_version": WRITE_SCHEMA_VERSION,
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "record_id": record["record_id"],
        "ledger_path": str(ledger_path),
        "success": record["success"],
        "task_text_hash": record["task_text_hash"],
        "task_detail_path": record["task_detail_path"],
        "record_sequence": record["record_sequence"],
    }


def format_json(payload: dict[str, object]) -> str:
    """Render stable JSON."""
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: list[str]) -> int:
    """Run the task outcome ledger write CLI."""
    try:
        args = parse_args(argv)
        output_format = args.format
        if output_format != "json":
            raise ValueError("--format must be json")

        ledger_path = Path(args.ledger)
        ensure_ledger_writable(ledger_path)
        task_detail_path = Path(args.task_detail)
        task_detail = parse_task_detail(load_json_file(args.task_detail, "task detail"))
        run_evidence = parse_run_evidence(load_json_file(args.run_evidence, "run evidence"))
        success_judgment_path = Path(args.success_judgment)
        success_judgment = parse_success_judgment(load_json_file(args.success_judgment, "success judgment"))
        record = build_and_append_locked(
            ledger_path,
            task_detail,
            task_detail_path,
            run_evidence,
            success_judgment,
            success_judgment_path,
        )
        print(format_json(result_payload(ledger_path, record)))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
