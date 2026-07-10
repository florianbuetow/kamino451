#!/usr/bin/env python3
"""Append validated per-step run trace records to a dispatch-queue trace JSONL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from task_outcome_ledger_common import (
    load_json_file,
    require_key,
    require_mapping,
    require_number,
    require_positive_int,
    require_string,
)

TRACE_SCHEMA_VERSION = "kamino451.run-trace.v1"
WRITE_SCHEMA_VERSION = "kamino451.run-trace-write.v1"

VALID_STATUSES = {"ok", "skipped", "failed"}
VALID_VERDICTS = {"PASS", "FAIL"}


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Append one validated run trace record.")
    parser.add_argument("--trace", required=True, help="Path to the trace JSONL file to append.")
    parser.add_argument("--record", required=True, help="Path to a JSON file containing one trace record.")
    parser.add_argument("--format", choices=["json"], required=True, help="Output format.")
    return parser.parse_args(argv)


def require_utc_timestamp(value: object, label: str) -> str:
    """Require an ISO-8601 UTC timestamp ending in Z."""
    text = require_string(value, label)
    if not text.endswith("Z"):
        raise ValueError(f"{label} must be an ISO-8601 UTC timestamp ending in Z")
    return text


def require_nullable_string(value: object, label: str) -> str | None:
    """Require a non-empty string or JSON null."""
    if value is None:
        return None
    text = require_string(value, label)
    return text


def validate_trace_record(payload: object, label: str) -> dict[str, object]:
    """Validate one run trace record."""
    mapping = require_mapping(payload, label)

    schema_version = require_string(require_key(mapping, "schema_version", label), f"{label}.schema_version")
    if schema_version != TRACE_SCHEMA_VERSION:
        raise ValueError(f"{label}.schema_version must be {TRACE_SCHEMA_VERSION}")

    status = require_string(require_key(mapping, "status", label), f"{label}.status")
    if status not in VALID_STATUSES:
        raise ValueError(f"{label}.status must be one of: {', '.join(sorted(VALID_STATUSES))}")

    raw_verdict = require_key(mapping, "verdict", label)
    if raw_verdict is not None:
        verdict = require_string(raw_verdict, f"{label}.verdict")
        if verdict not in VALID_VERDICTS:
            raise ValueError(f"{label}.verdict must be PASS, FAIL, or null")

    duration_seconds = require_number(require_key(mapping, "duration_seconds", label), f"{label}.duration_seconds")
    if duration_seconds < 0:
        raise ValueError(f"{label}.duration_seconds must not be negative")

    validated: dict[str, object] = {
        "schema_version": schema_version,
        "run_id": require_string(require_key(mapping, "run_id", label), f"{label}.run_id"),
        "step": require_positive_int(require_key(mapping, "step", label), f"{label}.step"),
        "attempt": require_positive_int(require_key(mapping, "attempt", label), f"{label}.attempt"),
        "agent_file": require_string(require_key(mapping, "agent_file", label), f"{label}.agent_file"),
        "model": require_string(require_key(mapping, "model", label), f"{label}.model"),
        "effort": require_string(require_key(mapping, "effort", label), f"{label}.effort"),
        "started_at": require_utc_timestamp(require_key(mapping, "started_at", label), f"{label}.started_at"),
        "ended_at": require_utc_timestamp(require_key(mapping, "ended_at", label), f"{label}.ended_at"),
        "duration_seconds": duration_seconds,
        "status": status,
        "output_path": require_string(require_key(mapping, "output_path", label), f"{label}.output_path"),
        "verdict": raw_verdict,
        "error": require_nullable_string(require_key(mapping, "error", label), f"{label}.error"),
        "subagent_summary": require_nullable_string(
            require_key(mapping, "subagent_summary", label),
            f"{label}.subagent_summary",
        ),
        "verification": require_mapping(require_key(mapping, "verification", label), f"{label}.verification"),
    }
    if "blueprint" in mapping:
        validated["blueprint"] = require_string(require_key(mapping, "blueprint", label), f"{label}.blueprint")
    return validated


def load_existing_trace(path: Path) -> list[dict[str, object]]:
    """Load and validate any existing trace records."""
    if not path.exists():
        return []
    if not path.is_file():
        raise ValueError(f"trace path exists but is not a file: {path}")
    text = path.read_text(encoding="utf-8")
    if text.strip() == "":
        raise ValueError(f"trace file is empty: {path}")
    records: list[dict[str, object]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if raw_line.strip() == "":
            raise ValueError(f"trace line {line_number} is empty")
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"trace line {line_number} is malformed JSON") from exc
        records.append(validate_trace_record(payload, f"trace line {line_number}"))
    return records


def ensure_single_run(existing: list[dict[str, object]], record: dict[str, object]) -> None:
    """Require every record in one trace file to belong to the same run."""
    for prior in existing:
        if prior["run_id"] != record["run_id"]:
            raise ValueError("trace record run_id must match the run_id already in the trace file")


def append_trace_record(path: Path, record: dict[str, object]) -> int:
    """Append one record and return its one-based sequence."""
    parent = path.parent
    if not parent.is_dir():
        raise ValueError(f"trace parent directory does not exist: {parent}")
    existing = load_existing_trace(path)
    ensure_single_run(existing, record)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return len(existing) + 1


def result_payload(path: Path, record: dict[str, object], sequence: int) -> dict[str, object]:
    """Build strict JSON CLI result."""
    return {
        "schema_version": WRITE_SCHEMA_VERSION,
        "trace_schema_version": TRACE_SCHEMA_VERSION,
        "trace_path": str(path),
        "run_id": record["run_id"],
        "step": record["step"],
        "attempt": record["attempt"],
        "record_sequence": sequence,
    }


def format_json(payload: dict[str, object]) -> str:
    """Render stable JSON."""
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: list[str]) -> int:
    """Run the trace writer CLI."""
    try:
        args = parse_args(argv)
        record = validate_trace_record(load_json_file(args.record, "trace record"), "trace record")
        trace_path = Path(args.trace)
        sequence = append_trace_record(trace_path, record)
        print(format_json(result_payload(trace_path, record, sequence)))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
