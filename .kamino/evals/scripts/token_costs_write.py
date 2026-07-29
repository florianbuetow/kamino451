#!/usr/bin/env python3
"""Compute per-agent token usage and deterministic cost for one dispatch-queue run.

Post-run accounting: reads the capsule's trace.jsonl, locates each dispatched
agent's Claude Code transcript (root sessions and Task-tool subagent files
under the project's transcript directory), extracts the API's exact usage
counters per call, adds a chars/4 estimate over the same conversation traffic,
prices both against the pricing table in the factory config, copies matched
transcripts into the capsule, and writes token_costs.json. Transcripts are
matched by the dispatched agent file path plus the trace time window; zero or
ambiguous matches fail loudly — nothing is guessed.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCHEMA_VERSION = "kamino451.token-costs.v1"
CHARS_PER_TOKEN = 4
MATCH_WINDOW_SECONDS = 120
PROBE_BYTES = 262144
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
TRACE_KEYS = ("run_id", "step", "attempt", "agent_file", "model", "status", "started_at", "ended_at")


def default_transcripts_root() -> Path:
    """Claude Code's transcript directory for this repo: path with '/' and '.' as '-'."""
    slug = re.sub(r"[/.]", "-", str(REPO))
    return Path.home() / ".claude" / "projects" / slug


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Dispatch-queue run directory containing trace.jsonl.")
    parser.add_argument("--transcripts-root", default=str(default_transcripts_root()),
                        help="Claude Code project transcript directory. Defaults to this repo's.")
    parser.add_argument("--config", default=str(REPO / ".kamino" / "factory-config.json"),
                        help="Factory config holding the pricing table.")
    parser.add_argument("--output", default=None, help="Output path. Defaults to <run-dir>/token_costs.json.")
    parser.add_argument("--format", choices=["json"], required=True, help="Output format.")
    return parser


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_jsonl(path: Path) -> list[dict]:
    """Parse a JSONL file, skipping blank and torn trailing lines."""
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def deduped_assistant_calls(entries: list[dict]) -> list[dict]:
    """One record per API call. Streaming appends progressive snapshots sharing one
    message id with growing output counts; the last snapshot is complete, so later
    entries replace earlier ones. Synthetic entries carry no real usage."""
    calls: dict[str, dict] = {}
    order: list[str] = []
    for entry in entries:
        if entry.get("type") != "assistant":
            continue
        message = entry.get("message") or {}
        message_id = message.get("id")
        model = message.get("model")
        usage = message.get("usage")
        if not message_id or model in (None, "<synthetic>") or not isinstance(usage, dict):
            continue
        if message_id not in calls:
            order.append(message_id)
        calls[message_id] = {"model_id": str(model), "usage": usage, "content": message.get("content")}
    return [calls[message_id] for message_id in order]


def usage_totals(calls: list[dict]) -> dict[str, int]:
    totals = {key: 0 for key in USAGE_KEYS}
    for call in calls:
        for key in USAGE_KEYS:
            totals[key] += int(call["usage"].get(key) or 0)
    return totals


def session_block(measured: dict[str, int]) -> dict[str, int]:
    """Counted-once totals over the agent session: output tokens are generated
    fresh per API call, and new prompt content (including the harness system
    prompt and file reads fed in as tool results) lands in input/cache_creation
    exactly once — history resends hit cache_read, which is excluded here."""
    return {
        "unique_input_tokens": measured["input_tokens"] + measured["cache_creation_input_tokens"],
        "unique_output_tokens": measured["output_tokens"],
    }


def content_chars(content: object) -> int:
    """Character count of a message content field: plain string or content-block list."""
    if isinstance(content, str):
        return len(content)
    total = 0
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                total += len(str(block.get("text") or ""))
            elif block_type == "thinking":
                total += len(str(block.get("thinking") or ""))
            elif block_type == "tool_result":
                total += content_chars(block.get("content"))
            elif block_type == "tool_use":
                total += len(json.dumps(block.get("input", {}), ensure_ascii=False))
    return total


def estimate_block(entries: list[dict], calls: list[dict]) -> dict:
    """Chars/4 rule of thumb over the agent's actual traffic: user-side content
    (prompts and tool results) in, deduped assistant content out. Counts unique
    content once — no per-call history resends, no harness system prompt — so it
    understates; measured usage stays authoritative when present."""
    input_chars = sum(
        content_chars((entry.get("message") or {}).get("content"))
        for entry in entries
        if entry.get("type") == "user"
    )
    output_chars = sum(content_chars(call["content"]) for call in calls)
    return {
        "input_chars": input_chars,
        "output_chars": output_chars,
        "chars_per_token": CHARS_PER_TOKEN,
        "input_tokens": math.ceil(input_chars / CHARS_PER_TOKEN),
        "output_tokens": math.ceil(output_chars / CHARS_PER_TOKEN),
    }


def first_user_text(entries: list[dict]) -> str:
    """The dispatch prompt: first user message, or the enqueued prompt of a claude -p session."""
    for entry in entries:
        if entry.get("type") == "user":
            content = (entry.get("message") or {}).get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [str(block.get("text", "")) for block in content
                         if isinstance(block, dict) and block.get("type") == "text"]
                if parts:
                    return "\n".join(parts)
        if entry.get("type") == "queue-operation" and isinstance(entry.get("content"), str):
            return str(entry["content"])
    return ""


def entry_time_span(entries: list[dict]) -> tuple[datetime, datetime] | None:
    stamps = []
    for entry in entries:
        value = entry.get("timestamp")
        if isinstance(value, str):
            try:
                stamps.append(parse_timestamp(value))
            except ValueError:
                continue
    if not stamps:
        return None
    return min(stamps), max(stamps)


def transcript_files(root: Path) -> list[Path]:
    """All candidate transcripts: root sessions plus per-subagent files."""
    files = sorted(root.glob("*.jsonl"))
    files += sorted(root.glob("*/subagents/agent-*.jsonl"))
    return files


def probe_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return handle.read(PROBE_BYTES)


def prompt_matches(probe: str, record: dict) -> bool:
    """Join contract: the dispatch prompt names the instantiated agent file path.
    The run skill and the evaluation sweeps both dispatch with the file path,
    so a transcript that cannot name the path is never billed."""
    agent_file = str(record["agent_file"])
    keys = [agent_file]
    if not agent_file.startswith("/"):
        keys.append(str(REPO / agent_file))
    return any(key in probe for key in keys)


def contained_in_window(span: tuple[datetime, datetime], record: dict) -> bool:
    """True when the transcript's whole span lies inside the attempt's exact
    trace window — used to split same-agent retries whose slack windows overlap."""
    started = parse_timestamp(str(record["started_at"]))
    ended = parse_timestamp(str(record["ended_at"]))
    return span[0] >= started and span[1] <= ended


def match_transcript(root: Path, record: dict) -> tuple[Path, list[dict]]:
    """Find exactly one transcript for this step attempt. Several candidates
    under the slack window collapse to the one contained in the exact trace
    window (same-agent retry case); anything still ambiguous fails loudly."""
    window_start = parse_timestamp(str(record["started_at"])) - timedelta(seconds=MATCH_WINDOW_SECONDS)
    window_end = parse_timestamp(str(record["ended_at"])) + timedelta(seconds=MATCH_WINDOW_SECONDS)
    matches: list[tuple[Path, list[dict], tuple[datetime, datetime]]] = []
    for path in transcript_files(root):
        if not prompt_matches(probe_text(path), record):
            continue
        entries = load_jsonl(path)
        if not prompt_matches(first_user_text(entries), record):
            continue
        span = entry_time_span(entries)
        if span is None or span[0] > window_end or span[1] < window_start:
            continue
        matches.append((path, entries, span))
    if len(matches) > 1:
        contained = [match for match in matches if contained_in_window(match[2], record)]
        if len(contained) == 1:
            matches = contained
    if len(matches) == 0:
        raise SystemExit(
            f"no transcript matched step {record['step']} attempt {record['attempt']} "
            f"({record['agent_file']}) under {root} within its trace window"
        )
    if len(matches) > 1:
        listed = ", ".join(str(path) for path, _, _ in matches)
        raise SystemExit(
            f"ambiguous transcript match for step {record['step']} attempt {record['attempt']}: {listed}"
        )
    return matches[0][0], matches[0][1]


def load_pricing(config_path: Path) -> dict:
    if not config_path.is_file():
        raise SystemExit(f"factory config not found: {config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    pricing = config.get("pricing")
    if not isinstance(pricing, dict) or not isinstance(pricing.get("models"), dict):
        raise SystemExit(f"factory config has no pricing.models table: {config_path}")
    for name, entry in pricing["models"].items():
        for key in ("input_per_mtok", "output_per_mtok"):
            if not isinstance(entry.get(key), (int, float)) or entry[key] <= 0:
                raise SystemExit(f"pricing model '{name}' needs positive {key}")
    return pricing


def resolve_pricing(pricing: dict, short_model: str, model_ids: set[str]) -> tuple[str, dict]:
    """Resolve the pricing entry for a step; a transcript model id outside the trace
    model's registered ids is a contract violation (silent substitution), not a guess."""
    models = pricing["models"]
    if short_model in models:
        entry = models[short_model]
        registered = set(entry.get("model_ids", []))
        unknown = model_ids - registered
        if unknown:
            raise SystemExit(
                f"transcript model id(s) {sorted(unknown)} are not registered under "
                f"pricing model '{short_model}' — update pricing.models or investigate the run"
            )
        return short_model, entry
    for name, entry in models.items():
        if model_ids & set(entry.get("model_ids", [])):
            return name, entry
    raise SystemExit(f"no pricing entry covers model '{short_model}' / transcript ids {sorted(model_ids)}")


def cost_block(measured: dict[str, int], estimated: dict, pricing_model: str, entry: dict) -> dict:
    basis = "measured" if any(measured.values()) else "estimated"
    if basis == "measured":
        billable_input = (
            measured["input_tokens"] + measured["cache_creation_input_tokens"] + measured["cache_read_input_tokens"]
        )
        output_tokens = measured["output_tokens"]
    else:
        billable_input = estimated["input_tokens"]
        output_tokens = estimated["output_tokens"]
    input_usd = round(billable_input * float(entry["input_per_mtok"]) / 1_000_000, 6)
    output_usd = round(output_tokens * float(entry["output_per_mtok"]) / 1_000_000, 6)
    return {
        "basis": basis,
        "pricing_model": pricing_model,
        "billable_input_tokens": billable_input,
        "input": input_usd,
        "output": output_usd,
        "total": round(input_usd + output_usd, 6),
    }


def skipped_entry(record: dict) -> dict:
    zero_usage = {key: 0 for key in USAGE_KEYS}
    return {
        "step": int(record["step"]),
        "attempt": int(record["attempt"]),
        "agent_file": str(record["agent_file"]),
        "model": str(record["model"]),
        "model_ids": [],
        "status": str(record["status"]),
        "transcript_source": None,
        "transcript_path": None,
        "api_calls": 0,
        "measured": zero_usage,
        "session": {"unique_input_tokens": 0, "unique_output_tokens": 0},
        "estimated": {"input_chars": 0, "output_chars": 0, "chars_per_token": CHARS_PER_TOKEN,
                      "input_tokens": 0, "output_tokens": 0},
        "cost_usd": {"basis": "none", "pricing_model": None, "billable_input_tokens": 0,
                     "input": 0.0, "output": 0.0, "total": 0.0},
    }


def copy_transcript(run_dir: Path, record: dict, source: Path) -> str:
    """Persist the matched transcript inside the capsule: Claude Code prunes its
    transcript directory on a retention schedule, the capsule is kept forever."""
    target_dir = run_dir / "transcripts"
    target_dir.mkdir(exist_ok=True)
    name = f"step-{int(record['step']):02d}-attempt-{int(record['attempt'])}.jsonl"
    shutil.copy2(source, target_dir / name)
    return f"transcripts/{name}"


def build_totals(steps: list[dict]) -> dict:
    measured = {key: sum(step["measured"][key] for step in steps) for key in USAGE_KEYS}
    session = {
        "unique_input_tokens": sum(step["session"]["unique_input_tokens"] for step in steps),
        "unique_output_tokens": sum(step["session"]["unique_output_tokens"] for step in steps),
    }
    estimated = {
        "input_chars": sum(step["estimated"]["input_chars"] for step in steps),
        "output_chars": sum(step["estimated"]["output_chars"] for step in steps),
        "chars_per_token": CHARS_PER_TOKEN,
        "input_tokens": sum(step["estimated"]["input_tokens"] for step in steps),
        "output_tokens": sum(step["estimated"]["output_tokens"] for step in steps),
    }
    by_model: dict[str, float] = {}
    for step in steps:
        name = step["cost_usd"].get("pricing_model")
        if name:
            by_model[name] = round(by_model.get(name, 0.0) + step["cost_usd"]["total"], 6)
    cost = {
        "input": round(sum(step["cost_usd"]["input"] for step in steps), 6),
        "output": round(sum(step["cost_usd"]["output"] for step in steps), 6),
        "total": round(sum(step["cost_usd"]["total"] for step in steps), 6),
        "by_model": by_model,
    }
    return {"measured": measured, "session": session, "estimated": estimated, "cost_usd": cost}


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    run_dir = Path(args.run_dir).resolve()
    trace_path = run_dir / "trace.jsonl"
    if not trace_path.is_file():
        raise SystemExit(f"no trace.jsonl in {run_dir}")
    trace_records = load_jsonl(trace_path)
    if not trace_records:
        raise SystemExit(f"trace is empty: {trace_path}")
    for record in trace_records:
        missing = [key for key in TRACE_KEYS if key not in record]
        if missing:
            raise SystemExit(f"malformed trace record (missing {missing}) in {trace_path}")
    run_id = str(trace_records[0]["run_id"])

    pricing = load_pricing(Path(args.config))
    transcripts_root = Path(args.transcripts_root)
    if not transcripts_root.is_dir():
        raise SystemExit(f"transcripts root not found: {transcripts_root}")

    steps: list[dict] = []
    for record in trace_records:
        if record["status"] == "skipped":
            steps.append(skipped_entry(record))
            continue
        source_path, entries = match_transcript(transcripts_root, record)
        calls = deduped_assistant_calls(entries)
        measured = usage_totals(calls)
        estimated = estimate_block(entries, calls)
        model_ids = {call["model_id"] for call in calls}
        pricing_model, entry = resolve_pricing(pricing, str(record["model"]), model_ids)
        steps.append(
            {
                "step": int(record["step"]),
                "attempt": int(record["attempt"]),
                "agent_file": str(record["agent_file"]),
                "model": str(record["model"]),
                "model_ids": sorted(model_ids),
                "status": str(record["status"]),
                "transcript_source": str(source_path),
                "transcript_path": copy_transcript(run_dir, record, source_path),
                "api_calls": len(calls),
                "measured": measured,
                "session": session_block(measured),
                "estimated": estimated,
                "cost_usd": cost_block(measured, estimated, pricing_model, entry),
            }
        )

    sources = [step["transcript_source"] for step in steps if step["transcript_source"]]
    duplicates = sorted({source for source in sources if sources.count(source) > 1})
    if duplicates:
        raise SystemExit(f"transcript matched to more than one step attempt: {duplicates}")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "pricing_source": str(Path(args.config).resolve()),
        "steps": steps,
        "totals": build_totals(steps),
    }
    output = Path(args.output) if args.output else run_dir / "token_costs.json"
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "output": str(output),
                      "total_usd": payload["totals"]["cost_usd"]["total"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
