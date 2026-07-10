#!/usr/bin/env python3
"""Corpus difficulty tooling: deterministic placements and calibration reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from task_outcome_ledger_common import (
    load_json_file,
    load_ledger_records,
    require_key,
    require_list,
    require_mapping,
    require_number,
    require_positive_int,
    require_string,
)

PAIRWISE_SCHEMA_VERSION = "kamino451.bradley-terry-pairwise-ranking.v1"
CORPUS_INDEX_SCHEMA_VERSION = "kamino451.corpus-index.v1"
REPORT_SCHEMA_VERSION = "kamino451.difficulty-calibration-report.v1"

VALID_INTENDED_DIFFICULTIES = ("easy", "medium", "hard")


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Corpus difficulty placement and calibration reporting.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    placement = subparsers.add_parser("placement", help="Derive a difficulty placement for a corpus task from the ranking.")
    placement.add_argument("--ranking", required=True, help="Path to a rank-mode Bradley-Terry ranking JSON.")
    placement.add_argument("--task-id", required=True, help="Corpus task id present in the ranking.")
    placement.add_argument("--neighbors", required=False, default="3", help="How many nearest prior tasks to include.")
    placement.add_argument("--format", choices=["json"], required=True, help="Output format.")

    report = subparsers.add_parser("report", help="Join ranking and ledger outcomes into a calibration report.")
    report.add_argument("--ranking", required=True, help="Path to a rank-mode Bradley-Terry ranking JSON.")
    report.add_argument("--ledger", required=True, help="Path to the task outcome ledger JSONL.")
    report.add_argument("--corpus-index", required=True, help="Path to corpus-index.json.")
    report.add_argument("--format", choices=["json", "markdown"], required=True, help="Output format.")

    return parser.parse_args(argv)


def task_text_hash(task_text: str) -> str:
    """Hash task text exactly like evaluate_task.py."""
    digest = hashlib.sha256(task_text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def load_ranking(path_arg: str) -> list[dict[str, object]]:
    """Load and validate a rank-mode ranking payload's entries."""
    payload = require_mapping(load_json_file(path_arg, "ranking payload"), "ranking payload")
    schema_version = require_string(require_key(payload, "schema_version", "ranking payload"), "ranking payload.schema_version")
    if schema_version != PAIRWISE_SCHEMA_VERSION:
        raise ValueError(f"ranking payload schema_version must be {PAIRWISE_SCHEMA_VERSION}")
    mode = require_string(require_key(payload, "mode", "ranking payload"), "ranking payload.mode")
    if mode != "rank":
        raise ValueError("ranking payload mode must be rank")
    raw_entries = require_list(require_key(payload, "ranking", "ranking payload"), "ranking payload.ranking")
    if len(raw_entries) < 2:
        raise ValueError("ranking payload must contain at least two tasks")

    entries: list[dict[str, object]] = []
    for index, raw_entry in enumerate(raw_entries):
        mapping = require_mapping(raw_entry, f"ranking[{index}]")
        entries.append(
            {
                "rank": require_positive_int(require_key(mapping, "rank", f"ranking[{index}]"), f"ranking[{index}].rank"),
                "task_id": require_string(require_key(mapping, "task_id", f"ranking[{index}]"), f"ranking[{index}].task_id"),
                "task_text": require_string(require_key(mapping, "task_text", f"ranking[{index}]"), f"ranking[{index}].task_text"),
                "difficulty_score": require_number(
                    require_key(mapping, "difficulty_score", f"ranking[{index}]"),
                    f"ranking[{index}].difficulty_score",
                ),
            }
        )
    return entries


def run_placement(ranking_arg: str, task_id: str, neighbors_arg: str) -> dict[str, object]:
    """Build a difficulty placement for a task that is itself a ranking anchor."""
    if neighbors_arg.strip() == "":
        raise ValueError("--neighbors must not be empty")
    try:
        neighbor_count = int(neighbors_arg)
    except ValueError as exc:
        raise ValueError("--neighbors must be an integer") from exc
    if str(neighbor_count) != neighbors_arg:
        raise ValueError("--neighbors must be an integer")
    if neighbor_count < 1:
        raise ValueError("--neighbors must be at least 1")

    entries = load_ranking(ranking_arg)
    target = None
    for entry in entries:
        if entry["task_id"] == task_id:
            target = entry
            break
    if target is None:
        raise ValueError(f"task id is not in the ranking: {task_id}")

    target_score = float(str(target["difficulty_score"]))
    others = [entry for entry in entries if entry["task_id"] != task_id]
    others.sort(key=lambda entry: (abs(float(str(entry["difficulty_score"])) - target_score), str(entry["task_id"])))
    nearest = [
        {
            "task_id": entry["task_id"],
            "distance": round(abs(float(str(entry["difficulty_score"])) - target_score), 6),
        }
        for entry in others[:neighbor_count]
    ]

    return {
        "schema_version": PAIRWISE_SCHEMA_VERSION,
        "mode": "placement",
        "source": "corpus-ranking",
        "task_id": task_id,
        "estimated_insertion_rank": target["rank"],
        "estimated_difficulty_score": round(target_score, 6),
        "nearest_prior_tasks": nearest,
    }


def load_corpus_tasks(index_arg: str) -> list[dict[str, object]]:
    """Load corpus index entries and hash each task's task.md content."""
    index_path = Path(index_arg)
    payload = require_mapping(load_json_file(index_arg, "corpus index"), "corpus index")
    schema_version = require_string(require_key(payload, "schema_version", "corpus index"), "corpus index.schema_version")
    if schema_version != CORPUS_INDEX_SCHEMA_VERSION:
        raise ValueError(f"corpus index schema_version must be {CORPUS_INDEX_SCHEMA_VERSION}")
    raw_tasks = require_list(require_key(payload, "tasks", "corpus index"), "corpus index.tasks")
    if len(raw_tasks) == 0:
        raise ValueError("corpus index.tasks must not be empty")

    tasks: list[dict[str, object]] = []
    for index, raw_task in enumerate(raw_tasks):
        mapping = require_mapping(raw_task, f"corpus index.tasks[{index}]")
        task_id = require_string(require_key(mapping, "task_id", f"corpus index.tasks[{index}]"), f"corpus index.tasks[{index}].task_id")
        intended = require_string(
            require_key(mapping, "intended_difficulty", f"corpus index.tasks[{index}]"),
            f"corpus index.tasks[{index}].intended_difficulty",
        )
        if intended not in VALID_INTENDED_DIFFICULTIES:
            raise ValueError(f"corpus index.tasks[{index}].intended_difficulty must be one of: easy, medium, hard")
        relative_path = require_string(require_key(mapping, "path", f"corpus index.tasks[{index}]"), f"corpus index.tasks[{index}].path")
        task_md_path = index_path.parent / relative_path / "task.md"
        if not task_md_path.is_file():
            raise FileNotFoundError(f"corpus task.md does not exist: {task_md_path}")
        task_text = task_md_path.read_text(encoding="utf-8")
        if task_text.strip() == "":
            raise ValueError(f"corpus task.md is empty: {task_md_path}")
        tasks.append(
            {
                "task_id": task_id,
                "title": require_string(require_key(mapping, "title", f"corpus index.tasks[{index}]"), f"corpus index.tasks[{index}].title"),
                "intended_difficulty": intended,
                "path": relative_path,
                "task_text_hash": task_text_hash(task_text),
            }
        )
    return tasks


def pearson_correlation(pairs: list[tuple[float, float]]) -> float | None:
    """Pearson correlation, or None when it is undefined for the sample."""
    count = len(pairs)
    if count < 2:
        return None
    mean_x = sum(pair[0] for pair in pairs) / count
    mean_y = sum(pair[1] for pair in pairs) / count
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    variance_x = sum((x - mean_x) ** 2 for x, y in pairs)
    variance_y = sum((y - mean_y) ** 2 for x, y in pairs)
    if variance_x == 0 or variance_y == 0:
        return None
    return round(covariance / ((variance_x**0.5) * (variance_y**0.5)), 6)


def run_report(ranking_arg: str, ledger_arg: str, corpus_index_arg: str) -> dict[str, object]:
    """Join ranking, corpus, and ledger outcomes into the calibration report."""
    entries = load_ranking(ranking_arg)
    corpus_tasks = load_corpus_tasks(corpus_index_arg)
    ledger_records = load_ledger_records(Path(ledger_arg), allow_empty=False)

    ranking_by_task_id = {str(entry["task_id"]): entry for entry in entries}
    corpus_by_hash = {str(task["task_text_hash"]): task for task in corpus_tasks}
    for task in corpus_tasks:
        if str(task["task_id"]) not in ranking_by_task_id:
            raise ValueError(f"corpus task is missing from the ranking: {task['task_id']}")

    attempts_by_hash: dict[str, list[dict[str, object]]] = {}
    non_corpus_record_count = 0
    for record in ledger_records:
        record_hash = str(record["task_text_hash"])
        if record_hash not in corpus_by_hash:
            non_corpus_record_count += 1
            continue
        attempts_by_hash.setdefault(record_hash, []).append(
            {
                "record_id": record["record_id"],
                "model": record["model"],
                "effort": record["effort"],
                "success": record["success"],
                "failure_mode": record["failure_mode"],
            }
        )

    task_rows: list[dict[str, object]] = []
    models: set[str] = set()
    for task in corpus_tasks:
        entry = ranking_by_task_id[str(task["task_id"])]
        attempts = attempts_by_hash.get(str(task["task_text_hash"]), [])
        solved_by: dict[str, bool] = {}
        for attempt in attempts:
            model = str(attempt["model"])
            models.add(model)
            solved_by[model] = solved_by.get(model, False) or bool(attempt["success"])
        task_rows.append(
            {
                "task_id": task["task_id"],
                "title": task["title"],
                "intended_difficulty": task["intended_difficulty"],
                "bt_rank": entry["rank"],
                "bt_difficulty_score": entry["difficulty_score"],
                "attempts": attempts,
                "solved_by_model": solved_by,
            }
        )
    task_rows.sort(key=lambda row: int(str(row["bt_rank"])))

    model_stats: dict[str, dict[str, object]] = {}
    correlations: dict[str, object] = {}
    attempt_correlations: dict[str, object] = {}
    for model in sorted(models):
        attempted_rows = [row for row in task_rows if model in dict(row["solved_by_model"])]
        attempt_records = [
            attempt
            for row in task_rows
            for attempt in list(row["attempts"])
            if str(attempt["model"]) == model
        ]
        solved_count = sum(1 for row in attempted_rows if dict(row["solved_by_model"])[model])
        attempt_success_count = sum(1 for attempt in attempt_records if bool(attempt["success"]))
        model_stats[model] = {
            "tasks_attempted": len(attempted_rows),
            "tasks_solved": solved_count,
            "task_solve_rate": round(solved_count / len(attempted_rows), 6) if attempted_rows else None,
            "attempts": len(attempt_records),
            "attempt_successes": attempt_success_count,
            "attempt_success_rate": round(attempt_success_count / len(attempt_records), 6) if attempt_records else None,
        }
        pairs = [
            (float(str(row["bt_difficulty_score"])), 1.0 if dict(row["solved_by_model"])[model] else 0.0)
            for row in attempted_rows
        ]
        correlations[model] = pearson_correlation(pairs)
        attempt_pairs = [
            (float(str(row["bt_difficulty_score"])), 1.0 if bool(attempt["success"]) else 0.0)
            for row in task_rows
            for attempt in list(row["attempts"])
            if str(attempt["model"]) == model
        ]
        attempt_correlations[model] = pearson_correlation(attempt_pairs)

    band_stats: dict[str, dict[str, object]] = {}
    for band in VALID_INTENDED_DIFFICULTIES:
        band_rows = [row for row in task_rows if row["intended_difficulty"] == band]
        per_model: dict[str, object] = {}
        for model in sorted(models):
            attempted = [row for row in band_rows if model in dict(row["solved_by_model"])]
            solved = sum(1 for row in attempted if dict(row["solved_by_model"])[model])
            per_model[model] = {
                "tasks_attempted": len(attempted),
                "tasks_solved": solved,
                "task_solve_rate": round(solved / len(attempted), 6) if attempted else None,
            }
        band_stats[band] = {"task_count": len(band_rows), "models": per_model}

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "corpus_task_count": len(corpus_tasks),
        "ledger_record_count": len(ledger_records),
        "corpus_attempt_count": sum(len(list(row["attempts"])) for row in task_rows),
        "non_corpus_record_count": non_corpus_record_count,
        "tasks": task_rows,
        "model_stats": model_stats,
        "difficulty_success_correlation_by_model": correlations,
        "difficulty_attempt_success_correlation_by_model": attempt_correlations,
        "intended_difficulty_bands": band_stats,
    }


def format_report_markdown(report: dict[str, object]) -> str:
    """Render the calibration report as Markdown."""
    lines: list[str] = ["# Difficulty Calibration Report", ""]
    lines.append(f"- Corpus tasks: {report['corpus_task_count']}")
    lines.append(f"- Corpus attempts in ledger: {report['corpus_attempt_count']}")
    lines.append(f"- Non-corpus ledger records ignored: {report['non_corpus_record_count']}")
    lines.append("")
    lines.append("## Tasks (hardest first)")
    lines.append("")
    lines.append("| BT rank | Task | Intended | BT score | Attempts (model: result) |")
    lines.append("|---:|---|---|---:|---|")
    for row in list(report["tasks"]):
        attempts = list(row["attempts"])
        rendered_attempts = "; ".join(
            f"{attempt['model']}: {'PASS' if attempt['success'] else 'FAIL'}" for attempt in attempts
        )
        if rendered_attempts == "":
            rendered_attempts = "—"
        lines.append(
            f"| {row['bt_rank']} | `{row['task_id']}` | {row['intended_difficulty']} | {row['bt_difficulty_score']} | {rendered_attempts} |"
        )
    lines.append("")
    lines.append("## Model stats")
    lines.append("")
    lines.append(
        "| Model | Tasks attempted | Tasks solved | Solve rate | Attempt success rate | Difficulty↔success correlation (task) | Difficulty↔success correlation (attempt) |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    model_stats = dict(report["model_stats"])
    correlations = dict(report["difficulty_success_correlation_by_model"])
    attempt_correlations = dict(report["difficulty_attempt_success_correlation_by_model"])
    for model in sorted(model_stats):
        stats = dict(model_stats[model])
        lines.append(
            f"| {model} | {stats['tasks_attempted']} | {stats['tasks_solved']} | {stats['task_solve_rate']} | {stats['attempt_success_rate']} | {correlations[model]} | {attempt_correlations[model]} |"
        )
    lines.append("")
    lines.append("## Intended difficulty bands")
    lines.append("")
    lines.append("| Band | Tasks | Model | Attempted | Solved | Solve rate |")
    lines.append("|---|---:|---|---:|---:|---:|")
    bands = dict(report["intended_difficulty_bands"])
    for band in VALID_INTENDED_DIFFICULTIES:
        band_payload = dict(bands[band])
        band_models = dict(band_payload["models"])
        if len(band_models) == 0:
            lines.append(f"| {band} | {band_payload['task_count']} | — | 0 | 0 | None |")
        for model in sorted(band_models):
            stats = dict(band_models[model])
            lines.append(
                f"| {band} | {band_payload['task_count']} | {model} | {stats['tasks_attempted']} | {stats['tasks_solved']} | {stats['task_solve_rate']} |"
            )
    lines.append("")
    return "\n".join(lines)


def format_json(payload: dict[str, object]) -> str:
    """Render stable JSON."""
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: list[str]) -> int:
    """Run the calibration CLI."""
    try:
        args = parse_args(argv)
        if args.command == "placement":
            print(format_json(run_placement(args.ranking, args.task_id, args.neighbors)))
            return 0
        if args.command == "report":
            report = run_report(args.ranking, args.ledger, args.corpus_index)
            if args.format == "markdown":
                print(format_report_markdown(report))
            else:
                print(format_json(report))
            return 0
        raise ValueError(f"unknown command: {args.command}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
