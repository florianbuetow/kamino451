#!/usr/bin/env python3
"""List or delete dispatch-queue run dirs not referenced by any ledger record."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from task_outcome_ledger_common import load_ledger_records

PRUNE_SCHEMA_VERSION = "kamino451.dispatch-queue-prune.v1"


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Prune dispatch-queue run dirs with no ledger reference.")
    parser.add_argument("--dispatch-dir", required=True, help="Path to the dispatch-queue directory.")
    parser.add_argument("--ledger", required=True, help="Path to the task outcome ledger JSONL.")
    parser.add_argument("--apply", action="store_true", help="Actually delete. Without this flag, only list.")
    parser.add_argument("--format", choices=["json"], required=True, help="Output format.")
    return parser.parse_args(argv)


def referenced_run_dirs(ledger_path: Path) -> set[str]:
    """Collect the run-dir names referenced by any ledger record's agent files."""
    if not ledger_path.is_file():
        raise FileNotFoundError(f"ledger does not exist: {ledger_path}")
    referenced: set[str] = set()
    for record in load_ledger_records(ledger_path, allow_empty=False):
        agent_files = record["agent_files_used"]
        if isinstance(agent_files, list):
            for agent_file in agent_files:
                referenced.add(Path(str(agent_file)).parent.name)
    return referenced


def main(argv: list[str]) -> int:
    """Run the pruner CLI."""
    try:
        args = parse_args(argv)
        dispatch_dir = Path(args.dispatch_dir)
        if not dispatch_dir.is_dir():
            raise NotADirectoryError(f"dispatch directory does not exist: {dispatch_dir}")
        referenced = referenced_run_dirs(Path(args.ledger))

        kept: list[str] = []
        prunable: list[str] = []
        for run_dir in sorted(path for path in dispatch_dir.iterdir() if path.is_dir()):
            if run_dir.name in referenced:
                kept.append(run_dir.name)
            else:
                prunable.append(run_dir.name)

        deleted: list[str] = []
        if args.apply:
            for name in prunable:
                shutil.rmtree(dispatch_dir / name)
                deleted.append(name)

        print(
            json.dumps(
                {
                    "schema_version": PRUNE_SCHEMA_VERSION,
                    "referenced_kept": len(kept),
                    "unreferenced": prunable,
                    "applied": args.apply,
                    "deleted": deleted,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
