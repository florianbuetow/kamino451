#!/usr/bin/env python3
"""Generate a self-contained static sweep-comparison HTML page from factory data.

Eval sweeps run in two modes, stamped per attempt by compile_run.py into the
run dir's route-decision.json as {"sweep": {"mode": "auto"|"prescribed",
"sweep_id": ...}} (plus corpus_dir, corpus_task_id, attempt). "auto" means the
factory picked the agent (evaluate-factory); "prescribed" means the caller
pinned one agent (evaluate-agent). This page groups ledger records by sweep
and compares auto vs prescribed pass rates per corpus.

The sweep stamp is never embedded in a ledger record or in a task detail's
parsed route_decision (task_detail_write.py's parser keeps only route_chosen,
agent_files_used, agent_blueprints_used, model, and effort). Recovering it
means reading the raw route-decision.json off disk: first via the task
detail's own route_decision_path, then via directories recoverable from the
record's agent/output file paths. Records where no stamp can be recovered
anywhere are legacy (pre-sweep-stamp) and are counted but excluded from the
sweep and comparison sections.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from task_outcome_ledger_common import load_ledger_records

UI_SCHEMA_VERSION = "kamino451.sweep-report-ui.v1"
VALID_MODES = ("auto", "prescribed")


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Build the static sweep-comparison page.")
    parser.add_argument("--ledger", default=".kamino/evals/tasks/task-outcome-ledger.jsonl", help="Path to the task outcome ledger JSONL.")
    parser.add_argument("--output", default=".kamino/evals/tasks/sweeps.html", help="Path of the HTML file to write.")
    parser.add_argument("--format", choices=["json"], required=True, help="Output format.")
    return parser.parse_args(argv)


def lenient_json(path: Path) -> object | None:
    """Load a JSON file leniently: missing, unreadable, or malformed -> None."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def attempt_number(record: dict[str, object]) -> int:
    """Derive the attempt number from the record's task detail filename."""
    detail_path = record.get("task_detail_path")
    if not isinstance(detail_path, str):
        return 1
    stem = Path(detail_path).stem
    task_id = str(record["task_id"])
    suffix = stem[len(task_id) :]
    if suffix.startswith("-a") and suffix[2:].isdigit():
        return int(suffix[2:])
    return 1


def route_decision_file_candidates(record: dict[str, object], detail: dict[str, object] | None) -> list[Path]:
    """Every path worth trying to recover the raw route-decision.json for a record."""
    candidates: list[Path] = []
    if detail is not None:
        route_decision_path = detail.get("route_decision_path")
        if isinstance(route_decision_path, str) and route_decision_path:
            candidates.append(Path(route_decision_path))
    for field in ("agent_files_used", "output_paths"):
        raw_paths = record.get(field)
        if not isinstance(raw_paths, list):
            continue
        for raw_path in raw_paths:
            if not isinstance(raw_path, str) or not raw_path:
                continue
            parent = Path(raw_path).parent
            candidates.append(parent / "route-decision.json")
            candidates.append(parent.parent / "route-decision.json")
    return list(dict.fromkeys(candidates))


def find_sweep_stamp(record: dict[str, object]) -> dict[str, object] | None:
    """Resolve the sweep stamp for one ledger record, or None if it is legacy.

    Prefers the stamp embedded in the record's task detail's route decision;
    falls back to the raw route-decision.json recovered via the task detail's
    route_decision_path, or via directories derived from the record's own
    agent/output paths. Never raises: unreadable or malformed artifacts just
    fail to resolve, and the caller buckets the record as legacy.
    """
    detail_path = record.get("task_detail_path")
    raw_detail = lenient_json(Path(detail_path)) if isinstance(detail_path, str) and detail_path else None
    detail = raw_detail if isinstance(raw_detail, dict) else None

    payloads: list[dict[str, object]] = []
    if detail is not None and isinstance(detail.get("route_decision"), dict):
        payloads.append(detail["route_decision"])
    for path in route_decision_file_candidates(record, detail):
        payload = lenient_json(path)
        if isinstance(payload, dict):
            payloads.append(payload)

    for payload in payloads:
        sweep = payload.get("sweep")
        if not isinstance(sweep, dict):
            continue
        mode = sweep.get("mode")
        sweep_id = sweep.get("sweep_id")
        if mode not in VALID_MODES or not isinstance(sweep_id, str) or sweep_id == "":
            continue
        raw_attempt = payload.get("attempt")
        attempt = raw_attempt if isinstance(raw_attempt, int) and not isinstance(raw_attempt, bool) else attempt_number(record)
        return {
            "mode": mode,
            "sweep_id": sweep_id,
            "corpus_dir": payload.get("corpus_dir") if isinstance(payload.get("corpus_dir"), str) else None,
            "corpus_task_id": payload.get("corpus_task_id") if isinstance(payload.get("corpus_task_id"), str) else None,
            "attempt": attempt,
        }
    return None


def add_to_sweep(sweeps: dict[str, dict[str, object]], record: dict[str, object], stamp: dict[str, object]) -> None:
    """Fold one stamped record into its sweep's running entry."""
    entry = sweeps.setdefault(
        str(stamp["sweep_id"]),
        {
            "sweep_id": stamp["sweep_id"],
            "mode": stamp["mode"],
            "corpus_dir": stamp["corpus_dir"],
            "blueprints": set(),
            "models": set(),
            "tasks": [],
        },
    )
    blueprints = record.get("agent_blueprints_used")
    if isinstance(blueprints, list):
        entry["blueprints"].update(str(item) for item in blueprints if isinstance(item, str))
    model = record.get("model") if isinstance(record.get("model"), str) else None
    if model is not None:
        entry["models"].add(model)
    entry["tasks"].append(
        {
            "corpus_task_id": stamp["corpus_task_id"],
            "attempt": stamp["attempt"],
            "model": model,
            "success": bool(record.get("success")),
            "record_id": record.get("record_id"),
        }
    )


def finalize_sweep(entry: dict[str, object]) -> dict[str, object]:
    """Compute derived stats for one sweep entry and make it JSON-serializable."""
    tasks = sorted(list(entry["tasks"]), key=lambda task: (str(task["corpus_task_id"]), int(task["attempt"])))
    passed = sum(1 for task in tasks if task["success"])
    total = len(tasks)
    distinct_tasks = {task["corpus_task_id"] for task in tasks if task["corpus_task_id"] is not None}
    corpus_dir = entry["corpus_dir"]
    return {
        "sweep_id": entry["sweep_id"],
        "mode": entry["mode"],
        "corpus": Path(corpus_dir).name if isinstance(corpus_dir, str) and corpus_dir else "(unknown corpus)",
        "corpus_dir": corpus_dir,
        "blueprints": sorted(entry["blueprints"]),
        "models": sorted(entry["models"]),
        "attempt_count": total,
        "distinct_task_count": len(distinct_tasks),
        "passed": passed,
        "pass_rate": round(passed / total, 6) if total else None,
        "tasks": tasks,
    }


def mode_summary_for_corpus(sweep_list: list[dict[str, object]], corpus: str, mode: str) -> dict[str, object] | None:
    """Aggregate pass rate (overall + per-model) across every sweep of one mode for one corpus."""
    tasks: list[dict[str, object]] = []
    for sweep in sweep_list:
        if sweep["corpus"] == corpus and sweep["mode"] == mode:
            tasks.extend(list(sweep["tasks"]))
    if not tasks:
        return None
    passed = sum(1 for task in tasks if task["success"])
    total = len(tasks)
    by_model: dict[str, dict[str, int]] = {}
    for task in tasks:
        model = task["model"] if isinstance(task["model"], str) else "(unknown model)"
        stats = by_model.setdefault(model, {"attempts": 0, "passed": 0})
        stats["attempts"] += 1
        if task["success"]:
            stats["passed"] += 1
    return {
        "attempts": total,
        "passed": passed,
        "pass_rate": round(passed / total, 6),
        "by_model": {
            model: {
                "attempts": stats["attempts"],
                "passed": stats["passed"],
                "pass_rate": round(stats["passed"] / stats["attempts"], 6),
            }
            for model, stats in sorted(by_model.items())
        },
    }


def build_corpus_comparison(sweep_list: list[dict[str, object]]) -> list[dict[str, object]]:
    """Compare auto vs prescribed sweeps for every corpus that has at least one stamped sweep."""
    corpora = sorted({str(sweep["corpus"]) for sweep in sweep_list})
    return [
        {
            "corpus": corpus,
            "auto": mode_summary_for_corpus(sweep_list, corpus, "auto"),
            "prescribed": mode_summary_for_corpus(sweep_list, corpus, "prescribed"),
        }
        for corpus in corpora
    ]


def build_payload(ledger_path: Path, records: list[dict[str, object]]) -> dict[str, object]:
    """Join every ledger record with its resolved sweep stamp, or bucket it as legacy."""
    sweeps: dict[str, dict[str, object]] = {}
    legacy_count = 0
    for record in records:
        stamp = find_sweep_stamp(record)
        if stamp is None:
            legacy_count += 1
            continue
        add_to_sweep(sweeps, record, stamp)

    sweep_list = [finalize_sweep(entry) for entry in sweeps.values()]
    sweep_list.sort(key=lambda entry: (str(entry["mode"]), str(entry["corpus"]), str(entry["sweep_id"])))

    return {
        "schema_version": UI_SCHEMA_VERSION,
        "ledger_path": str(ledger_path),
        "generated_note": "from the task outcome ledger",
        "totals": {
            "records": len(records),
            "stamped": len(records) - legacy_count,
            "legacy": legacy_count,
            "sweep_count": len(sweep_list),
        },
        "sweeps": sweep_list,
        "corpus_comparison": build_corpus_comparison(sweep_list),
    }


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kamino451 Sweep Report</title>
<style>
  :root {
    --surface: #fcfcfb; --page: #f9f9f7;
    --ink: #0b0b0b; --ink-2: #52514e; --ink-muted: #898781;
    --grid: #e1e0d9; --axis: #c3c2b7;
    --auto: #2a78d6; --prescribed: #1baf7a;
    --good: #0ca30c; --critical: #d03b3b;
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 16px 20px 60px; background: var(--page); color: var(--ink);
         font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  h1 { font-size: 16px; margin: 0 0 2px; }
  h2 { font-size: 13px; margin: 26px 0 8px; color: var(--ink-2); text-transform: uppercase; letter-spacing: .04em; }
  .sub { color: var(--ink-muted); margin-bottom: 14px; }
  .tiles { display: flex; gap: 10px; flex-wrap: wrap; }
  .tile { background: var(--surface); border: 1px solid var(--grid); border-radius: 6px; padding: 10px 14px; min-width: 130px; }
  .tile .v { font-size: 20px; font-weight: 700; }
  .tile .k { color: var(--ink-muted); font-size: 11px; }
  table { border-collapse: collapse; width: 100%; background: var(--surface); }
  th, td { border: 1px solid var(--grid); padding: 4px 8px; text-align: left; vertical-align: top; }
  th { background: var(--page); color: var(--ink-2); }
  .pass { color: var(--good); font-weight: 700; }
  .fail { color: var(--critical); font-weight: 700; }
  .muted { color: var(--ink-muted); }
  .cards { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 6px; }
  .card { background: var(--surface); border: 1px solid var(--grid); border-left: 3px solid var(--axis); border-radius: 6px;
          padding: 10px 14px; width: 340px; cursor: pointer; }
  .card.auto { border-left-color: var(--auto); }
  .card.prescribed { border-left-color: var(--prescribed); }
  .card h3 { margin: 0 0 6px; font-size: 13px; }
  .card .row { display: flex; justify-content: space-between; gap: 8px; margin: 2px 0; }
  .card .row .k { color: var(--ink-muted); }
  .card table { display: none; margin-top: 8px; cursor: default; }
  .card.open table { display: table; }
  .badge { display: inline-block; border-radius: 10px; padding: 0 8px; font-size: 11px; border: 1px solid var(--axis); background: var(--page); margin: 1px 2px 1px 0; }
  #legacy { color: var(--ink-muted); margin-top: 30px; border-top: 1px solid var(--grid); padding-top: 10px; }
</style>
</head>
<body>
<h1>Kamino451 Sweep Report</h1>
<div class="sub" id="generated"></div>

<div class="tiles" id="tiles"></div>

<h2>Factory evaluations (auto)</h2>
<div class="cards" id="auto-cards"></div>

<h2>Agent evaluations (prescribed)</h2>
<div class="cards" id="prescribed-cards"></div>

<h2>Factory vs prescribed agents by corpus</h2>
<div id="comparison"></div>

<div id="legacy"></div>

<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById("data").textContent);
const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const pct = rate => (rate === null || rate === undefined) ? "—" : (rate * 100).toFixed(1) + "%";

document.getElementById("generated").textContent =
  "generated " + DATA.generated_note + " — ledger: " + DATA.ledger_path;

// ---- summary tiles -------------------------------------------------------
(function tiles() {
  const t = DATA.totals;
  const tiles = [["records", t.records], ["sweeps", t.sweep_count], ["stamped", t.stamped], ["unstamped", t.legacy]];
  document.getElementById("tiles").innerHTML = tiles.map(([k, v]) =>
    '<div class="tile"><div class="v">' + esc(v) + '</div><div class="k">' + esc(k) + "</div></div>").join("");
})();

// ---- per-sweep cards ------------------------------------------------------
function taskTableHtml(tasks) {
  const rows = tasks.map(t =>
    "<tr><td>" + esc(t.corpus_task_id || "—") + "</td><td>" + esc(t.attempt) + "</td><td>" + esc(t.model || "—") + "</td>" +
    (t.success ? '<td class="pass">PASS</td>' : '<td class="fail">FAIL</td>') +
    "<td>" + esc(t.record_id) + "</td></tr>").join("");
  return "<table><thead><tr><th>Task</th><th>Attempt</th><th>Model</th><th>Result</th><th>Record</th></tr></thead><tbody>" + rows + "</tbody></table>";
}

function renderCards(containerId, mode) {
  const container = document.getElementById(containerId);
  const sweeps = DATA.sweeps.filter(s => s.mode === mode);
  if (sweeps.length === 0) {
    container.innerHTML = '<div class="muted">No ' + mode + ' sweeps recorded yet.</div>';
    return;
  }
  container.innerHTML = sweeps.map(s =>
    '<div class="card ' + mode + '">' +
      "<h3>" + esc(s.corpus) + "</h3>" +
      '<div class="row"><span class="k">sweep</span><span>' + esc(s.sweep_id) + "</span></div>" +
      '<div class="row"><span class="k">blueprint(s)</span><span>' + esc(s.blueprints.join(", ") || "—") + "</span></div>" +
      '<div class="row"><span class="k">models</span><span>' + s.models.map(m => '<span class="badge">' + esc(m) + "</span>").join("") + "</span></div>" +
      '<div class="row"><span class="k">attempts</span><span>' + s.attempt_count + "</span></div>" +
      '<div class="row"><span class="k">distinct tasks</span><span>' + s.distinct_task_count + "</span></div>" +
      '<div class="row"><span class="k">pass rate</span><span>' + s.passed + "/" + s.attempt_count + " (" + pct(s.pass_rate) + ")</span></div>" +
      taskTableHtml(s.tasks) +
    "</div>").join("");
  container.querySelectorAll(".card").forEach(card => {
    card.addEventListener("click", event => {
      if (event.target.closest("table")) return;
      card.classList.toggle("open");
    });
  });
}
renderCards("auto-cards", "auto");
renderCards("prescribed-cards", "prescribed");

// ---- factory vs prescribed by corpus --------------------------------------
(function comparison() {
  const wrap = document.getElementById("comparison");
  if (DATA.corpus_comparison.length === 0) {
    wrap.innerHTML = '<div class="muted">No stamped sweeps yet — nothing to compare.</div>';
    return;
  }
  function sideCells(side) {
    if (!side) return '<td class="muted">no data</td><td class="muted">no data</td><td class="muted">no data</td>';
    const models = Object.keys(side.by_model).sort().map(m =>
      '<span class="badge">' + esc(m) + " " + pct(side.by_model[m].pass_rate) + "</span>").join("");
    return "<td>" + side.attempts + "</td><td>" + side.passed + "/" + side.attempts + " (" + pct(side.pass_rate) + ")</td><td>" + models + "</td>";
  }
  let rows = "";
  for (const entry of DATA.corpus_comparison) {
    rows += '<tr><td rowspan="2">' + esc(entry.corpus) + '</td><td>auto</td>' + sideCells(entry.auto) + "</tr>";
    rows += "<tr><td>prescribed</td>" + sideCells(entry.prescribed) + "</tr>";
  }
  wrap.innerHTML = "<table><thead><tr><th>Corpus</th><th>Mode</th><th>Attempts</th><th>Pass rate</th><th>Per-model</th></tr></thead><tbody>" + rows + "</tbody></table>";
})();

document.getElementById("legacy").textContent =
  DATA.totals.legacy + " of " + DATA.totals.records + " records are unstamped (pre-sweep-stamp records) — excluded from the sweeps and comparison above.";
</script>
</body>
</html>
"""


def build_page(payload: dict[str, object]) -> str:
    """Embed the data payload into the HTML template."""
    data_json = json.dumps(payload, sort_keys=True).replace("</", "<\\/")
    return HTML_TEMPLATE.replace("__DATA__", data_json)


def main(argv: list[str]) -> int:
    """Run the sweep report UI generator CLI."""
    try:
        args = parse_args(argv)
        ledger_path = Path(args.ledger)
        records = load_ledger_records(ledger_path, allow_empty=True)
        payload = build_payload(ledger_path, records)
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(build_page(payload), encoding="utf-8")
        totals = payload["totals"]
        print(
            json.dumps(
                {
                    "schema_version": UI_SCHEMA_VERSION,
                    "output": str(output_path),
                    "records": totals["records"],
                    "stamped": totals["stamped"],
                    "legacy": totals["legacy"],
                    "sweeps": totals["sweep_count"],
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
