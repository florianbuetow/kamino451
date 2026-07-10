#!/usr/bin/env python3
"""Generate a self-contained static difficulty-calibration HTML page.

Joins, per corpus: Bradley-Terry ranking, corpus index, ledger outcomes,
task evaluations (judge difficulty + routing recommendation), and failure
analyses — so each failure is shown against the difficulty signal that
preceded it, and misalignments are called out explicitly.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from difficulty_calibration_report import load_corpus_tasks, load_ranking, run_report
from task_outcome_ledger_common import load_ledger_records

UI_SCHEMA_VERSION = "kamino451.difficulty-report-ui.v1"


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Build the static difficulty-calibration page.")
    parser.add_argument("--corpus", action="append", required=True, help="Corpus label (repeatable, pairs with --ranking/--corpus-index by position).")
    parser.add_argument("--ranking", action="append", required=True, help="Rank-mode ranking JSON for the corpus (repeatable).")
    parser.add_argument("--corpus-index", action="append", required=True, help="corpus-index.json for the corpus (repeatable).")
    parser.add_argument("--ledger", required=True, help="Path to the task outcome ledger JSONL.")
    parser.add_argument("--evaluations-dir", required=False, help="Optional directory of task-evaluation JSON files.")
    parser.add_argument("--failures-dir", required=False, help="Optional directory of failure-analysis JSON files.")
    parser.add_argument("--output", required=True, help="Path of the HTML file to write.")
    parser.add_argument("--format", choices=["html"], required=True, help="Output format.")
    args = parser.parse_args(argv)
    if not (len(args.corpus) == len(args.ranking) == len(args.corpus_index)):
        parser.error("--corpus, --ranking, and --corpus-index must be given the same number of times")
    return args


def attempt_number(record: dict[str, object]) -> int:
    """Derive the attempt number from the record's task detail filename."""
    detail_path = record.get("task_detail_path")
    if not isinstance(detail_path, str):
        return 1
    stem = Path(detail_path).stem
    task_id = str(record["task_id"])
    suffix = stem[len(task_id):]
    if suffix.startswith("-a") and suffix[2:].isdigit():
        return int(suffix[2:])
    return 1


def load_side_json(path: Path) -> dict[str, object] | None:
    """Load a side artifact leniently; missing or malformed -> None."""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def failure_analysis_path(failures_dir: Path, task_id: str, attempt: int) -> Path:
    """Return the expected failure-analysis path for an attempt."""
    if attempt == 1:
        return failures_dir / f"{task_id}.json"
    return failures_dir / f"{task_id}-a{attempt}.json"


def evaluation_signal(evaluation: dict[str, object] | None) -> dict[str, object]:
    """Summarize the pre-run difficulty signal from a merged task evaluation."""
    if evaluation is None:
        return {"available": False, "judge_difficulty": None, "deterministic_difficulty": None, "mapping": "", "predicted_risk": False, "reason": "no evaluation on file"}
    judge = evaluation.get("llm_judge") if isinstance(evaluation.get("llm_judge"), dict) else {}
    judge_difficulty = judge.get("difficulty_score")
    mapping = str(judge.get("recommended_mapping", ""))
    reasons: list[str] = []
    if isinstance(judge_difficulty, int) and judge_difficulty >= 4:
        reasons.append(f"judge difficulty {judge_difficulty}/5")
    if "strong" in mapping.lower():
        reasons.append("mapping calls for a strong model")
    return {
        "available": True,
        "judge_difficulty": judge_difficulty,
        "deterministic_difficulty": evaluation.get("difficulty_score"),
        "mapping": mapping,
        "predicted_risk": bool(reasons),
        "reason": " + ".join(reasons) if reasons else f"judge difficulty {judge_difficulty}/5, mapping does not escalate",
    }


def build_corpus_payload(
    label: str,
    ranking_arg: str,
    corpus_index_arg: str,
    ledger_arg: str,
    evaluations_dir: Path | None,
    failures_dir: Path | None,
) -> dict[str, object]:
    """Join every difficulty artifact for one corpus."""
    report = run_report(ranking_arg, ledger_arg, corpus_index_arg)
    corpus_tasks = load_corpus_tasks(corpus_index_arg)
    entries = load_ranking(ranking_arg)
    ranking_by_task_id = {str(entry["task_id"]): entry for entry in entries}
    corpus_by_hash = {str(task["task_text_hash"]): task for task in corpus_tasks}

    attempts_by_hash: dict[str, list[dict[str, object]]] = {}
    for record in load_ledger_records(Path(ledger_arg), allow_empty=False):
        record_hash = str(record["task_text_hash"])
        if record_hash not in corpus_by_hash:
            continue
        eval_task_id = str(record["task_id"])
        attempt = attempt_number(record)
        failure: dict[str, object] | None = None
        if not bool(record["success"]) and failures_dir is not None:
            failure = load_side_json(failure_analysis_path(failures_dir, eval_task_id, attempt))
        attempts_by_hash.setdefault(record_hash, []).append(
            {
                "eval_task_id": eval_task_id,
                "attempt": attempt,
                "model": str(record["model"]),
                "success": bool(record["success"]),
                "failure_analysis": failure,
            }
        )

    rows: list[dict[str, object]] = []
    for task in corpus_tasks:
        task_hash = str(task["task_text_hash"])
        entry = ranking_by_task_id[str(task["task_id"])]
        attempts = sorted(attempts_by_hash.get(task_hash, []), key=lambda item: int(str(item["attempt"])))
        evaluation = None
        if attempts and evaluations_dir is not None:
            evaluation = load_side_json(evaluations_dir / f"{attempts[0]['eval_task_id']}.json")
        rows.append(
            {
                "task_id": str(task["task_id"]),
                "intended_difficulty": str(task["intended_difficulty"]),
                "bt_rank": int(str(entry["rank"])),
                "bt_score": float(str(entry["difficulty_score"])),
                "signal": evaluation_signal(evaluation),
                "attempts": attempts,
            }
        )
    rows.sort(key=lambda row: int(str(row["bt_rank"])))

    return {
        "label": label,
        "task_count": len(rows),
        "report": report,
        "rows": rows,
    }


def render_attempt_chips(attempts: list[dict[str, object]]) -> str:
    """Render one attempt as one PASS/FAIL chip each."""
    if not attempts:
        return '<span class="muted">no attempts</span>'
    chips: list[str] = []
    for attempt in attempts:
        verdict = "PASS" if attempt["success"] else "FAIL"
        css = "pass" if attempt["success"] else "fail"
        chips.append(f'<span class="{css}">a{attempt["attempt"]} {html.escape(str(attempt["model"]))}: {verdict}</span>')
    return " · ".join(chips)


def render_failure_alignment(corpus: dict[str, object]) -> str:
    """Render the failure-vs-difficulty alignment rows for one corpus."""
    rows_html: list[str] = []
    task_count = int(str(corpus["task_count"]))
    for row in list(corpus["rows"]):
        signal = dict(row["signal"])
        for attempt in list(row["attempts"]):
            if bool(attempt["success"]):
                continue
            analysis = attempt.get("failure_analysis")
            primary = "unclassified"
            layer = ""
            fix = ""
            if isinstance(analysis, dict):
                classification = analysis.get("classification")
                if isinstance(classification, dict):
                    primary = str(classification.get("primary_failure_mode", "unclassified"))
                    fix = str(classification.get("recommended_fix", ""))
                    modes = classification.get("failure_modes")
                    if isinstance(modes, list) and modes and isinstance(modes[0], dict):
                        layer = str(modes[0].get("layer", ""))
            aligned = bool(signal["predicted_risk"]) or int(str(row["bt_rank"])) <= max(1, task_count // 4)
            verdict = "difficulty signal predicted this" if aligned else "MISS: difficulty signal did not flag this task"
            verdict_css = "aligned" if aligned else "misaligned"
            rows_html.append(
                "<tr>"
                f'<td>{html.escape(str(row["task_id"]))} <span class="muted">a{attempt["attempt"]}</span></td>'
                f'<td>#{row["bt_rank"]}/{task_count} (score {row["bt_score"]:.3f})</td>'
                f'<td>{html.escape(str(signal["reason"]))}</td>'
                f'<td><span class="slug">{html.escape(primary)}</span> <span class="muted">{html.escape(layer)}</span></td>'
                f'<td class="{verdict_css}">{html.escape(verdict)}</td>'
                f'<td class="fix">{html.escape(fix)}</td>'
                "</tr>"
            )
    if not rows_html:
        return '<p class="muted">No failed attempts recorded for this corpus.</p>'
    return (
        '<table><thead><tr><th>Failed attempt</th><th>BT difficulty</th><th>Pre-run evaluation signal</th>'
        "<th>Classified failure mode</th><th>Alignment</th><th>Recommended fix</th></tr></thead><tbody>"
        + "".join(rows_html)
        + "</tbody></table>"
    )


def render_corpus_section(corpus: dict[str, object]) -> str:
    """Render one corpus: stats tiles, alignment table, full task table."""
    report = dict(corpus["report"])
    model_stats = dict(report["model_stats"])
    task_correlations = dict(report["difficulty_success_correlation_by_model"])
    attempt_correlations = dict(report["difficulty_attempt_success_correlation_by_model"])

    tiles: list[str] = [
        f'<div class="tile"><div class="v">{corpus["task_count"]}</div><div class="k">corpus tasks</div></div>',
        f'<div class="tile"><div class="v">{report["corpus_attempt_count"]}</div><div class="k">corpus attempts</div></div>',
    ]
    stats_rows: list[str] = []
    for model in sorted(model_stats):
        stats = dict(model_stats[model])
        tiles.append(
            f'<div class="tile"><div class="v">{stats["attempt_success_rate"]}</div><div class="k">{html.escape(model)} attempt success rate</div></div>'
        )
        stats_rows.append(
            "<tr>"
            f"<td>{html.escape(model)}</td><td>{stats['tasks_attempted']}</td><td>{stats['tasks_solved']}</td>"
            f"<td>{stats['task_solve_rate']}</td><td>{stats['attempt_success_rate']}</td>"
            f"<td>{task_correlations[model]}</td><td>{attempt_correlations[model]}</td>"
            "</tr>"
        )

    task_rows: list[str] = []
    for row in list(corpus["rows"]):
        signal = dict(row["signal"])
        judge = signal["judge_difficulty"] if signal["available"] else "—"
        task_rows.append(
            "<tr>"
            f'<td>{row["bt_rank"]}</td>'
            f'<td>{html.escape(str(row["task_id"]))}</td>'
            f'<td>{html.escape(str(row["intended_difficulty"]))}</td>'
            f'<td>{row["bt_score"]:.4f}</td>'
            f"<td>{judge}</td>"
            f'<td>{render_attempt_chips(list(row["attempts"]))}</td>'
            "</tr>"
        )

    return (
        f'<h2>{html.escape(str(corpus["label"]))}</h2>'
        f'<div class="tiles">{"".join(tiles)}</div>'
        "<h3>Model stats</h3>"
        "<table><thead><tr><th>Model</th><th>Tasks attempted</th><th>Tasks solved</th><th>Solve rate</th>"
        "<th>Attempt success rate</th><th>Difficulty↔success correlation (task)</th><th>Difficulty↔success correlation (attempt)</th></tr></thead>"
        f"<tbody>{''.join(stats_rows)}</tbody></table>"
        "<h3>Failure ↔ difficulty alignment</h3>"
        f"{render_failure_alignment(corpus)}"
        "<h3>Tasks (hardest first)</h3>"
        "<table><thead><tr><th>BT rank</th><th>Task</th><th>Intended</th><th>BT score</th>"
        "<th>Judge difficulty</th><th>Attempts</th></tr></thead>"
        f"<tbody>{''.join(task_rows)}</tbody></table>"
    )


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kamino451 Difficulty Calibration</title>
<style>
  :root {
    --surface: #fcfcfb; --page: #f9f9f7;
    --ink: #0b0b0b; --ink-2: #52514e; --ink-muted: #898781;
    --grid: #e1e0d9; --axis: #c3c2b7;
    --good: #0ca30c; --critical: #d03b3b; --warn: #b0700e;
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 16px 20px 60px; background: var(--page); color: var(--ink);
         font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  h1 { font-size: 16px; margin: 0 0 2px; }
  h2 { font-size: 14px; margin: 30px 0 8px; border-bottom: 2px solid var(--axis); padding-bottom: 4px; }
  h3 { font-size: 12px; margin: 20px 0 6px; color: var(--ink-2); text-transform: uppercase; letter-spacing: .04em; }
  .sub { color: var(--ink-muted); margin-bottom: 14px; }
  .tiles { display: flex; gap: 10px; flex-wrap: wrap; }
  .tile { background: var(--surface); border: 1px solid var(--grid); border-radius: 6px; padding: 10px 14px; min-width: 130px; }
  .tile .v { font-size: 20px; font-weight: 700; }
  .tile .k { color: var(--ink-muted); font-size: 11px; }
  table { border-collapse: collapse; width: 100%; background: var(--surface); margin-bottom: 8px; }
  th, td { border: 1px solid var(--grid); padding: 4px 8px; text-align: left; vertical-align: top; }
  th { background: var(--page); color: var(--ink-2); }
  .pass { color: var(--good); font-weight: 700; }
  .fail { color: var(--critical); font-weight: 700; }
  .muted { color: var(--ink-muted); }
  .slug { display: inline-block; border: 1px solid var(--axis); border-radius: 10px; padding: 0 8px; font-size: 11px; background: var(--page); }
  .aligned { color: var(--good); }
  .misaligned { color: var(--warn); font-weight: 700; }
  .fix { max-width: 420px; }
</style>
</head>
<body>
<h1>Kamino451 Difficulty Calibration</h1>
<div class="sub">Difficulty signals (LLM-judge evaluation + Bradley-Terry pairwise ranking) joined with run outcomes and failure analyses. The alignment tables show, per failure, whether the pre-run difficulty signal predicted it.</div>
__SECTIONS__
</body>
</html>
"""


def main(argv: list[str]) -> int:
    """Build the difficulty-calibration page."""
    try:
        args = parse_args(argv)
        evaluations_dir = Path(args.evaluations_dir) if args.evaluations_dir else None
        failures_dir = Path(args.failures_dir) if args.failures_dir else None
        corpora = [
            build_corpus_payload(label, ranking, index, args.ledger, evaluations_dir, failures_dir)
            for label, ranking, index in zip(args.corpus, args.ranking, args.corpus_index)
        ]
        sections = "".join(render_corpus_section(corpus) for corpus in corpora)
        output_path = Path(args.output)
        output_path.write_text(HTML_TEMPLATE.replace("__SECTIONS__", sections), encoding="utf-8")
        failed_attempts = sum(
            1
            for corpus in corpora
            for row in list(corpus["rows"])
            for attempt in list(row["attempts"])
            if not bool(attempt["success"])
        )
        print(
            json.dumps(
                {
                    "schema_version": UI_SCHEMA_VERSION,
                    "output": str(output_path),
                    "corpora": [str(corpus["label"]) for corpus in corpora],
                    "failed_attempts": failed_attempts,
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
