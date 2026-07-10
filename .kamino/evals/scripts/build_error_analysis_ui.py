#!/usr/bin/env python3
"""Generate a self-contained static error-analysis HTML page from factory data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from difficulty_calibration_report import load_corpus_tasks, load_ranking
from task_outcome_ledger_common import load_ledger_records

UI_SCHEMA_VERSION = "kamino451.error-analysis-ui.v1"


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Build the static error-analysis page.")
    parser.add_argument("--ledger", required=True, help="Path to the task outcome ledger JSONL.")
    parser.add_argument("--output", required=True, help="Path of the HTML file to write.")
    parser.add_argument("--ranking", required=False, help="Optional rank-mode ranking JSON (enables the difficulty chart).")
    parser.add_argument("--corpus-index", required=False, help="Optional corpus-index.json (enables the difficulty chart).")
    parser.add_argument("--failures-dir", required=False, help="Optional directory of failure-analysis JSON files.")
    parser.add_argument("--trace-reviews-dir", required=False, help="Optional directory of LLM-judge trace-review JSON files.")
    parser.add_argument("--catalog", required=False, help="Optional failure-mode-catalog.md; its slugs feed the labeling dropdown.")
    parser.add_argument("--format", choices=["html"], required=True, help="Output format.")
    return parser.parse_args(argv)


def parse_catalog_slugs(catalog_arg: str | None) -> list[str]:
    """Extract failure-mode slugs from the catalog markdown table rows."""
    if catalog_arg is None:
        return []
    catalog_path = Path(catalog_arg)
    if not catalog_path.is_file():
        raise FileNotFoundError(f"failure-mode catalog does not exist: {catalog_path}")
    import re

    slugs: list[str] = []
    for line in catalog_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\|\s*`([a-z0-9_]+)`\s*\|", line)
        if match:
            slugs.append(match.group(1))
    return slugs


def side_load_json(path: Path) -> tuple[object | None, str | None]:
    """Load a side artifact leniently: (payload, error). Missing file -> (None, None)."""
    if not path.is_file():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, f"malformed JSON: {exc}"


def side_load_trace(dispatch_dir: Path) -> tuple[list[object], str | None]:
    """Load a dispatch dir's trace.jsonl leniently: (records, error)."""
    trace_path = dispatch_dir / "trace.jsonl"
    if not trace_path.is_file():
        return [], None
    try:
        text = trace_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [], f"unreadable trace file: {exc}"
    records: list[object] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if raw_line.strip() == "":
            continue
        try:
            records.append(json.loads(raw_line))
        except json.JSONDecodeError as exc:
            return [], f"malformed trace line {line_number}: {exc}"
    return records, None


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


def failure_analysis_path(failures_dir: Path, task_id: str, attempt: int) -> Path:
    """Return the expected failure-analysis path for an attempt."""
    if attempt == 1:
        return failures_dir / f"{task_id}.json"
    return failures_dir / f"{task_id}-a{attempt}.json"


def trace_review_path(reviews_dir: Path, task_id: str, attempt: int) -> Path:
    """Return the expected trace-review path for an attempt (always attempt-suffixed)."""
    return reviews_dir / f"{task_id}-a{attempt}.json"


def build_attempt_payload(record: dict[str, object], failures_dir: Path | None, trace_reviews_dir: Path | None) -> dict[str, object]:
    """Assemble the UI payload for one ledger record."""
    task_id = str(record["task_id"])
    attempt = attempt_number(record)
    agent_files = record["agent_files_used"]
    dispatch_dir: Path | None = None
    if isinstance(agent_files, list) and len(agent_files) > 0:
        dispatch_dir = Path(str(agent_files[0])).parent

    trace_records: list[object] = []
    trace_error: str | None = None
    if dispatch_dir is not None:
        trace_records, trace_error = side_load_trace(dispatch_dir)

    detail_payload: object | None = None
    detail_error: str | None = None
    detail_path = record.get("task_detail_path")
    if isinstance(detail_path, str):
        detail_payload, detail_error = side_load_json(Path(detail_path))

    failure_payload: object | None = None
    failure_error: str | None = None
    if failures_dir is not None:
        failure_payload, failure_error = side_load_json(failure_analysis_path(failures_dir, task_id, attempt))

    trace_review_payload: object | None = None
    trace_review_error: str | None = None
    if trace_reviews_dir is not None:
        trace_review_payload, trace_review_error = side_load_json(trace_review_path(trace_reviews_dir, task_id, attempt))

    failure_slugs: list[str] = []
    if isinstance(failure_payload, dict):
        classification = failure_payload.get("classification")
        if isinstance(classification, dict):
            modes = classification.get("failure_modes")
            if isinstance(modes, list):
                for mode in modes:
                    if isinstance(mode, dict) and isinstance(mode.get("slug"), str):
                        failure_slugs.append(str(mode["slug"]))

    return {
        "record_id": record["record_id"],
        "record_sequence": record["record_sequence"],
        "timestamp": record["timestamp"],
        "task_id": task_id,
        "attempt": attempt,
        "task_text_hash": record["task_text_hash"],
        "task_text": record["task_text"],
        "task_type": record["task_type"],
        "semantic_difficulty_score": record["semantic_difficulty_score"],
        "pairwise_difficulty_score": record["pairwise_difficulty_score"],
        "route_chosen": record["route_chosen"],
        "model": record["model"],
        "effort": record["effort"],
        "execution_status": record["execution_status"],
        "success": record["success"],
        "failure_mode": record["failure_mode"],
        "output_paths": record["output_paths"],
        "success_judgment": record["success_judgment"],
        "task_detail_path": record.get("task_detail_path"),
        "task_detail": detail_payload,
        "task_detail_error": detail_error,
        "dispatch_dir": str(dispatch_dir) if dispatch_dir is not None else None,
        "trace": trace_records,
        "trace_error": trace_error,
        "failure_analysis": failure_payload,
        "failure_analysis_error": failure_error,
        "failure_slugs": failure_slugs,
        "trace_review": trace_review_payload,
        "trace_review_error": trace_review_error,
        "trace_review_verdict": trace_review_payload.get("verdict") if isinstance(trace_review_payload, dict) else None,
    }


def build_chart_payload(ranking_arg: str | None, corpus_index_arg: str | None, attempts: list[dict[str, object]]) -> object | None:
    """Join ranking + corpus with attempts for the difficulty chart, when inputs are given."""
    if ranking_arg is None and corpus_index_arg is None:
        return None
    if ranking_arg is None or corpus_index_arg is None:
        raise ValueError("provide both --ranking and --corpus-index, or neither")
    entries = load_ranking(ranking_arg)
    corpus_tasks = load_corpus_tasks(corpus_index_arg)
    ranking_by_task_id = {str(entry["task_id"]): entry for entry in entries}
    rows: list[dict[str, object]] = []
    for task in corpus_tasks:
        entry = ranking_by_task_id.get(str(task["task_id"]))
        if entry is None:
            raise ValueError(f"corpus task is missing from the ranking: {task['task_id']}")
        task_attempts = [
            {"model": attempt["model"], "success": attempt["success"], "attempt": attempt["attempt"]}
            for attempt in attempts
            if attempt["task_text_hash"] == task["task_text_hash"]
        ]
        rows.append(
            {
                "task_id": task["task_id"],
                "intended_difficulty": task["intended_difficulty"],
                "bt_rank": entry["rank"],
                "bt_difficulty_score": entry["difficulty_score"],
                "attempts": task_attempts,
            }
        )
    rows.sort(key=lambda row: int(str(row["bt_rank"])))
    return rows


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kamino451 Error Analysis</title>
<style>
  :root {
    --surface: #fcfcfb; --page: #f9f9f7;
    --ink: #0b0b0b; --ink-2: #52514e; --ink-muted: #898781;
    --grid: #e1e0d9; --axis: #c3c2b7;
    --haiku: #2a78d6; --sonnet: #1baf7a; --other-model: #4a3aa7;
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
  .filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 8px 0 10px; }
  .filters select, .filters input { font: inherit; padding: 4px 6px; border: 1px solid var(--axis); border-radius: 4px; background: var(--surface); color: var(--ink); }
  table { border-collapse: collapse; width: 100%; background: var(--surface); }
  th, td { border: 1px solid var(--grid); padding: 4px 8px; text-align: left; vertical-align: top; }
  th { background: var(--page); color: var(--ink-2); position: sticky; top: 0; cursor: default; }
  tr.attempt { cursor: pointer; }
  tr.attempt:hover { background: #f2f6fc; }
  tr.open { background: #eef3fa; }
  .pass { color: var(--good); font-weight: 700; }
  .fail { color: var(--critical); font-weight: 700; }
  .muted { color: var(--ink-muted); }
  .slug { display: inline-block; border: 1px solid var(--axis); border-radius: 10px; padding: 0 8px; margin: 1px 2px 1px 0; font-size: 11px; background: var(--page); }
  .detail { display: none; }
  .detail.open { display: table-row; }
  .detail td { background: #fbfbf9; padding: 10px 14px; }
  .detail pre { margin: 6px 0; padding: 8px; background: var(--surface); border: 1px solid var(--grid); border-radius: 4px;
                overflow-x: auto; max-height: 320px; }
  .cols { display: flex; gap: 18px; flex-wrap: wrap; }
  .col { flex: 1 1 380px; min-width: 320px; }
  .legend { display: flex; gap: 16px; align-items: center; margin: 4px 0 6px; color: var(--ink-2); }
  .swatch { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; vertical-align: -1px; }
  #chartwrap { background: var(--surface); border: 1px solid var(--grid); border-radius: 6px; padding: 12px; overflow-x: auto; }
  #tooltip { position: fixed; display: none; background: var(--ink); color: #fff; padding: 5px 8px; border-radius: 4px;
             font-size: 12px; pointer-events: none; z-index: 10; max-width: 340px; }
  .err { color: var(--critical); }
</style>
</head>
<body>
<h1>Kamino451 Error Analysis</h1>
<div class="sub" id="generated"></div>

<div class="tiles" id="tiles"></div>

<div id="chartsection" style="display:none">
  <h2>Difficulty vs outcome (Bradley-Terry anchors)</h2>
  <div class="legend" id="chartlegend"></div>
  <div id="chartwrap"></div>
</div>

<h2>Failure modes</h2>
<div id="failuremodes" class="muted">No failure analyses found.</div>

<h2>Trace reviews (LLM judge)</h2>
<div id="tracereviews" class="muted">No trace reviews found.</div>

<h2>Attempts</h2>
<div class="filters">
  <select id="f-model"><option value="">model: all</option></select>
  <select id="f-success"><option value="">result: all</option><option value="true">PASS</option><option value="false">FAIL</option></select>
  <select id="f-tasktype"><option value="">task type: all</option></select>
  <select id="f-slug"><option value="">failure mode: all</option></select>
  <input id="f-text" placeholder="search task text / id" size="28">
  <button id="export-labels" title="Download your manual labels/notes as JSON">Export labels</button>
  <span class="muted" id="f-count"></span>
</div>
<table id="attempts">
  <thead><tr>
    <th>#</th><th>Task</th><th>Attempt</th><th>Model</th><th>Effort</th><th>BT diff.</th>
    <th>Execution</th><th>Result</th><th>Failure modes</th><th>When</th>
  </tr></thead>
  <tbody></tbody>
</table>

<div id="tooltip"></div>

<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById("data").textContent);
const MODEL_COLORS = { haiku: "var(--haiku)", sonnet: "var(--sonnet)" };
const modelColor = m => MODEL_COLORS[m] || "var(--other-model)";
const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

document.getElementById("generated").textContent =
  "generated " + DATA.generated_note + " — ledger: " + DATA.ledger_path + " (" + DATA.attempts.length + " attempts)";

// ---- summary tiles -------------------------------------------------------
(function tiles() {
  const byModel = {};
  for (const a of DATA.attempts) {
    byModel[a.model] = byModel[a.model] || { n: 0, ok: 0 };
    byModel[a.model].n += 1;
    if (a.success) byModel[a.model].ok += 1;
  }
  const wrap = document.getElementById("tiles");
  const total = DATA.attempts.length;
  const okTotal = DATA.attempts.filter(a => a.success).length;
  const tiles = [["attempts", total], ["passed", okTotal], ["failed", total - okTotal]];
  for (const m of Object.keys(byModel).sort()) {
    tiles.push([m + " pass rate", byModel[m].ok + "/" + byModel[m].n]);
  }
  wrap.innerHTML = tiles.map(([k, v]) =>
    '<div class="tile"><div class="v">' + esc(v) + '</div><div class="k">' + esc(k) + "</div></div>").join("");
})();

// ---- failure mode counts -------------------------------------------------
(function failureModes() {
  const counts = {};
  for (const a of DATA.attempts) for (const s of a.failure_slugs) counts[s] = (counts[s] || 0) + 1;
  const slugs = Object.keys(counts).sort((x, y) => counts[y] - counts[x]);
  if (slugs.length === 0) return;
  document.getElementById("failuremodes").className = "";
  document.getElementById("failuremodes").innerHTML =
    slugs.map(s => '<span class="slug">' + esc(s) + " × " + counts[s] + "</span>").join(" ");
})();

// ---- trace review verdict counts ------------------------------------------
(function traceReviews() {
  const counts = {};
  for (const a of DATA.attempts) if (a.trace_review_verdict) counts[a.trace_review_verdict] = (counts[a.trace_review_verdict] || 0) + 1;
  const verdicts = Object.keys(counts).sort((x, y) => counts[y] - counts[x]);
  if (verdicts.length === 0) return;
  document.getElementById("tracereviews").className = "";
  document.getElementById("tracereviews").innerHTML =
    verdicts.map(v => '<span class="slug">' + esc(v) + " × " + counts[v] + "</span>").join(" ") +
    ' <span class="muted">— open a reviewed attempt for the full judgment</span>';
})();

// ---- difficulty chart ----------------------------------------------------
(function chart() {
  const rows = DATA.chart;
  if (!rows || rows.length === 0) return;
  document.getElementById("chartsection").style.display = "";
  const models = [...new Set(rows.flatMap(r => r.attempts.map(a => a.model)))].sort();
  document.getElementById("chartlegend").innerHTML =
    models.map(m => '<span><span class="swatch" style="background:' + modelColor(m) + '"></span>' + esc(m) + "</span>").join("") +
    '<span class="muted">filled ● = PASS, open ○ = FAIL, · = no attempt</span>';

  const W = 860, ROW = 26, LEFT = 250, RIGHT = 40, TOP = 24;
  const H = TOP + rows.length * ROW + 34;
  const scores = rows.map(r => r.bt_difficulty_score);
  const min = Math.min(...scores), max = Math.max(...scores);
  const span = (max - min) || 1;
  const x = v => LEFT + ((v - min) / span) * (W - LEFT - RIGHT);
  let svg = '<svg width="' + W + '" height="' + H + '" role="img" aria-label="Task difficulty vs pass/fail per model">';
  // gridlines + axis labels (4 ticks)
  for (let index = 0; index <= 3; index++) {
    const v = min + (span * index) / 3;
    svg += '<line x1="' + x(v) + '" y1="' + TOP + '" x2="' + x(v) + '" y2="' + (H - 30) + '" stroke="var(--grid)" stroke-width="1"/>';
    svg += '<text x="' + x(v) + '" y="' + (H - 14) + '" fill="var(--ink-muted)" font-size="11" text-anchor="middle">' + v.toFixed(2) + "</text>";
  }
  rows.forEach((r, i) => {
    const cy = TOP + i * ROW + ROW / 2;
    svg += '<text x="' + (LEFT - 10) + '" y="' + (cy + 4) + '" fill="var(--ink-2)" font-size="12" text-anchor="end">' + esc(r.task_id) + "</text>";
    svg += '<line x1="' + LEFT + '" y1="' + cy + '" x2="' + (W - RIGHT) + '" y2="' + cy + '" stroke="var(--grid)" stroke-width="1"/>';
    if (r.attempts.length === 0) {
      svg += '<circle cx="' + x(r.bt_difficulty_score) + '" cy="' + cy + '" r="2.5" fill="var(--axis)"/>';
    }
    r.attempts.forEach((a, j) => {
      const cx = x(r.bt_difficulty_score) + j * 14;   // offset repeated attempts sideways
      const color = modelColor(a.model);
      const common = ' data-tip="' + esc(r.task_id + " — " + a.model + " attempt " + a.attempt + ": " + (a.success ? "PASS" : "FAIL")) + '"';
      if (a.success) {
        svg += '<circle class="dot" cx="' + cx + '" cy="' + cy + '" r="6" fill="' + color + '" stroke="var(--surface)" stroke-width="2"' + common + "/>";
      } else {
        svg += '<circle class="dot" cx="' + cx + '" cy="' + cy + '" r="6" fill="var(--surface)" stroke="' + color + '" stroke-width="2.5"' + common + "/>";
      }
    });
  });
  svg += "</svg>";
  document.getElementById("chartwrap").innerHTML = svg;

  const tooltip = document.getElementById("tooltip");
  document.querySelectorAll(".dot").forEach(dot => {
    dot.addEventListener("mousemove", event => {
      tooltip.style.display = "block";
      tooltip.textContent = dot.getAttribute("data-tip");
      tooltip.style.left = (event.clientX + 12) + "px";
      tooltip.style.top = (event.clientY + 12) + "px";
    });
    dot.addEventListener("mouseleave", () => { tooltip.style.display = "none"; });
  });
})();

// ---- attempts table ------------------------------------------------------
const tbody = document.querySelector("#attempts tbody");

const LABEL_STORE_KEY = "kamino451-error-labels";
const loadLabels = () => { try { return JSON.parse(localStorage.getItem(LABEL_STORE_KEY) || "{}"); } catch { return {}; } };
const saveLabels = labels => localStorage.setItem(LABEL_STORE_KEY, JSON.stringify(labels));

function labelingHtml(a) {
  const labels = loadLabels();
  const current = labels[a.record_id] || { label: "", notes: "" };
  const options = ['<option value="">— label failure mode —</option>']
    .concat(DATA.labels_catalog.map(s =>
      '<option value="' + esc(s) + '"' + (current.label === s ? " selected" : "") + ">" + esc(s) + "</option>"))
    .join("");
  return '<div class="labeling"><b>Manual label</b> ' +
    '<select class="label-select" data-record="' + esc(a.record_id) + '">' + options + "</select> " +
    '<input class="label-notes" data-record="' + esc(a.record_id) + '" placeholder="notes" size="48" value="' + esc(current.notes) + '">' +
    "</div>";
}

function detailHtml(a) {
  let html = '<div class="cols">';
  html += '<div class="col"><b>Task text</b><pre>' + esc(a.task_text) + "</pre>";
  html += "<b>Success judgment</b><pre>" + esc(JSON.stringify(a.success_judgment, null, 2)) + "</pre>";
  html += "<b>Outputs</b><pre>" + esc(a.output_paths.join("\\n")) + "</pre></div>";
  html += '<div class="col">';
  if (a.trace_error) html += '<div class="err">trace: ' + esc(a.trace_error) + "</div>";
  if (a.trace && a.trace.length) {
    html += "<b>Trace (" + a.trace.length + " steps)</b><pre>" + esc(a.trace.map(t =>
      "step " + t.step + " attempt " + t.attempt + " [" + t.status + "] " + t.model + "/" + t.effort +
      " " + t.duration_seconds + "s" +
      (t.verification && t.verification.verification_command ? " · verify exit " + t.verification.exit_code : "") +
      (t.error ? " · error: " + t.error : "")).join("\\n")) + "</pre>";
  } else if (!a.trace_error) {
    html += '<div class="muted">no trace captured</div>';
  }
  if (a.failure_analysis_error) html += '<div class="err">failure analysis: ' + esc(a.failure_analysis_error) + "</div>";
  if (a.failure_analysis) {
    html += "<b>Failure analysis</b><pre>" + esc(JSON.stringify(a.failure_analysis, null, 2)) + "</pre>";
  }
  if (a.trace_review_error) html += '<div class="err">trace review: ' + esc(a.trace_review_error) + "</div>";
  if (a.trace_review) {
    html += "<b>Trace review (LLM judge): " + esc(a.trace_review_verdict || "?") + "</b><pre>" + esc(JSON.stringify(a.trace_review, null, 2)) + "</pre>";
  }
  if (a.task_detail_error) html += '<div class="err">task detail: ' + esc(a.task_detail_error) + "</div>";
  if (a.task_detail_path) html += '<div class="muted">detail: ' + esc(a.task_detail_path) + "</div>";
  if (a.dispatch_dir) html += '<div class="muted">dispatch: ' + esc(a.dispatch_dir) + "</div>";
  html += labelingHtml(a);
  html += "</div></div>";
  return html;
}

function wireLabeling(root) {
  root.querySelectorAll(".label-select, .label-notes").forEach(element => {
    element.addEventListener("click", event => event.stopPropagation());
    element.addEventListener("change", () => {
      const record = element.getAttribute("data-record");
      const labels = loadLabels();
      const entry = labels[record] || { label: "", notes: "" };
      if (element.classList.contains("label-select")) entry.label = element.value;
      else entry.notes = element.value;
      labels[record] = entry;
      saveLabels(labels);
    });
  });
}

document.getElementById("export-labels").addEventListener("click", () => {
  const payload = { schema_version: "kamino451.error-labels.v1", exported_from: DATA.ledger_path, labels: loadLabels() };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const anchor = document.createElement("a");
  anchor.href = URL.createObjectURL(blob);
  anchor.download = "kamino451-error-labels.json";
  anchor.click();
  URL.revokeObjectURL(anchor.href);
});

function render() {
  const model = document.getElementById("f-model").value;
  const success = document.getElementById("f-success").value;
  const taskType = document.getElementById("f-tasktype").value;
  const slug = document.getElementById("f-slug").value;
  const text = document.getElementById("f-text").value.toLowerCase();
  tbody.innerHTML = "";
  let shown = 0;
  DATA.attempts.forEach((a, i) => {
    if (model && a.model !== model) return;
    if (success && String(a.success) !== success) return;
    if (taskType && a.task_type !== taskType) return;
    if (slug && !a.failure_slugs.includes(slug)) return;
    if (text && !(a.task_text.toLowerCase().includes(text) || a.task_id.toLowerCase().includes(text))) return;
    shown += 1;
    const tr = document.createElement("tr");
    tr.className = "attempt";
    tr.innerHTML =
      "<td>" + a.record_sequence + "</td>" +
      "<td>" + esc(a.task_id) + '<br><span class="muted">' + esc(a.task_text.slice(0, 70)) + (a.task_text.length > 70 ? "…" : "") + "</span></td>" +
      "<td>" + a.attempt + "</td>" +
      '<td><span class="swatch" style="background:' + modelColor(a.model) + '"></span>' + esc(a.model) + "</td>" +
      "<td>" + esc(a.effort) + "</td>" +
      "<td>" + Number(a.pairwise_difficulty_score).toFixed(3) + "</td>" +
      "<td>" + esc(a.execution_status) + "</td>" +
      (a.success ? '<td class="pass">✓ PASS</td>' : '<td class="fail">✗ FAIL</td>') +
      "<td>" + (a.failure_slugs.length ? a.failure_slugs.map(s => '<span class="slug">' + esc(s) + "</span>").join("") : '<span class="muted">—</span>') +
        (a.trace_review_verdict ? '<br><span class="slug" title="LLM-judge trace review">' + esc(a.trace_review_verdict) + "</span>" : "") + "</td>" +
      '<td class="muted">' + esc(a.timestamp) + "</td>";
    const detail = document.createElement("tr");
    detail.className = "detail";
    detail.innerHTML = '<td colspan="10">' + detailHtml(a) + "</td>";
    tr.addEventListener("click", () => {
      detail.classList.toggle("open");
      tr.classList.toggle("open");
    });
    tbody.appendChild(tr);
    tbody.appendChild(detail);
    wireLabeling(detail);
  });
  document.getElementById("f-count").textContent = shown + " of " + DATA.attempts.length + " attempts";
}

(function initFilters() {
  const models = [...new Set(DATA.attempts.map(a => a.model))].sort();
  const types = [...new Set(DATA.attempts.map(a => a.task_type))].sort();
  const slugs = [...new Set(DATA.attempts.flatMap(a => a.failure_slugs))].sort();
  const add = (id, values) => {
    const select = document.getElementById(id);
    for (const value of values) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    }
    select.addEventListener("change", render);
  };
  add("f-model", models);
  add("f-tasktype", types);
  add("f-slug", slugs);
  document.getElementById("f-success").addEventListener("change", render);
  document.getElementById("f-text").addEventListener("input", render);
  render();
})();
</script>
</body>
</html>
"""


def build_page(payload: dict[str, object]) -> str:
    """Embed the data payload into the HTML template."""
    data_json = json.dumps(payload, sort_keys=True).replace("</", "<\\/")
    return HTML_TEMPLATE.replace("__DATA__", data_json)


def main(argv: list[str]) -> int:
    """Run the UI generator CLI."""
    try:
        args = parse_args(argv)
        ledger_path = Path(args.ledger)
        records = load_ledger_records(ledger_path, allow_empty=False)
        failures_dir = Path(args.failures_dir) if args.failures_dir is not None else None
        trace_reviews_dir = Path(args.trace_reviews_dir) if args.trace_reviews_dir is not None else None
        attempts = [build_attempt_payload(record, failures_dir, trace_reviews_dir) for record in records]
        chart = build_chart_payload(args.ranking, args.corpus_index, attempts)
        payload: dict[str, object] = {
            "schema_version": UI_SCHEMA_VERSION,
            "ledger_path": str(ledger_path),
            "generated_note": "from the task outcome ledger",
            "attempts": attempts,
            "chart": chart,
            "labels_catalog": parse_catalog_slugs(args.catalog),
        }
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(build_page(payload), encoding="utf-8")
        print(json.dumps({"schema_version": UI_SCHEMA_VERSION, "output": str(output_path), "attempt_count": len(attempts)}, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
