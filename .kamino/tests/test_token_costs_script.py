"""Contract tests for the post-run token usage and cost accounting script."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[2]


def test_factory_config_has_pricing_for_model_ladder():
    config = json.loads((repo_root() / ".kamino" / "factory-config.json").read_text(encoding="utf-8"))
    pricing = config["pricing"]
    assert pricing["currency"] == "USD"
    for name in ("haiku", "sonnet", "opus"):
        entry = pricing["models"][name]
        assert entry["model_ids"], f"{name} needs at least one API model id"
        assert entry["input_per_mtok"] > 0
        assert entry["output_per_mtok"] > 0


RUN_ID = "260720-100000"
MODEL_ID = "claude-haiku-4-5-20251001"


def write_jsonl(path: Path, entries: list[dict]) -> None:
    """Write JSONL fixture data, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")


def pricing_config(path: Path) -> Path:
    """Write a minimal factory config with easy-math pricing (haiku 1.0/5.0 per Mtok)."""
    payload = {
        "schema_version": "kamino451.factory-config.v1",
        "routing": {"success_rate_threshold": 0.9, "min_attempts_for_rate": 3},
        "pricing": {
            "currency": "USD",
            "models": {
                "haiku": {"model_ids": [MODEL_ID], "input_per_mtok": 1.0, "output_per_mtok": 5.0},
            },
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def trace_record(agent_file: str, *, step: int = 1, attempt: int = 1, status: str = "ok") -> dict:
    """Build one schema-valid run-trace record."""
    return {
        "schema_version": "kamino451.run-trace.v1",
        "run_id": RUN_ID,
        "step": step,
        "attempt": attempt,
        "agent_file": agent_file,
        "model": "haiku",
        "effort": "medium",
        "started_at": "2026-07-20T10:00:00Z",
        "ended_at": "2026-07-20T10:05:00Z",
        "duration_seconds": 300.0,
        "status": status,
        "output_path": "outputs/01-agent.md",
        "verdict": None,
        "error": None,
        "subagent_summary": None if status == "skipped" else "done",
        "verification": {"output_non_empty": True, "no_template_tokens": True},
    }


def transcript_entries(agent_file: str, *, model_id: str = MODEL_ID) -> list[dict]:
    """A subagent transcript: prompt naming the agent file, then streamed duplicates of two API calls."""
    usage_a = {"input_tokens": 10, "output_tokens": 200, "cache_creation_input_tokens": 500, "cache_read_input_tokens": 1000}
    usage_b = {"input_tokens": 5, "output_tokens": 100, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 2000}
    return [
        {"type": "user", "timestamp": "2026-07-20T10:00:05Z",
         "message": {"role": "user", "content": f"You are the agent defined in the file {agent_file}. Read that file NOW."}},
        {"type": "assistant", "timestamp": "2026-07-20T10:00:10Z",
         "message": {"id": "msg_a", "model": model_id, "usage": dict(usage_a, output_tokens=1),
                     "content": [{"type": "text", "text": "wor"}]}},
        {"type": "assistant", "timestamp": "2026-07-20T10:00:12Z",
         "message": {"id": "msg_a", "model": model_id, "usage": usage_a,
                     "content": [{"type": "text", "text": "working now"}]}},
        {"type": "assistant", "timestamp": "2026-07-20T10:00:20Z",
         "message": {"id": "msg_b", "model": "<synthetic>", "usage": {"input_tokens": 0, "output_tokens": 0},
                     "content": [{"type": "text", "text": "synthetic"}]}},
        {"type": "assistant", "timestamp": "2026-07-20T10:00:30Z",
         "message": {"id": "msg_c", "model": model_id, "usage": usage_b,
                     "content": [{"type": "text", "text": "done, wrote the file"}]}},
    ]


def build_capsule(tmp_path: Path, *, records: list[dict]) -> Path:
    """Build a run capsule directory with a trace and the agent files the records name."""
    run_dir = tmp_path / "dispatch" / RUN_ID
    run_dir.mkdir(parents=True)
    write_jsonl(run_dir / "trace.jsonl", records)
    for record in records:
        agent = Path(record["agent_file"])
        agent.parent.mkdir(parents=True, exist_ok=True)
        agent.write_text("---\nagent_name: demo\nmodel: haiku\n---\nbody", encoding="utf-8")
    return run_dir


def run_token_costs(run_dir: Path, transcripts_root: Path, config: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "uv", "run", ".kamino/evals/scripts/token_costs_write.py",
            "--run-dir", str(run_dir),
            "--transcripts-root", str(transcripts_root),
            "--config", str(config),
            *extra,
            "--format", "json",
        ],
        capture_output=True,
        text=True,
        cwd=repo_root(),
        check=False,
    )


def test_measured_usage_and_cost_from_subagent_transcript(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl", transcript_entries(agent_file))
    config = pricing_config(tmp_path / "config.json")

    result = run_token_costs(run_dir, transcripts_root, config)
    assert result.returncode == 0, result.stderr

    payload = json.loads((run_dir / "token_costs.json").read_text(encoding="utf-8"))
    step = payload["steps"][0]
    # Dedupe: msg_a counted once (last snapshot), synthetic ignored, msg_c counted.
    assert step["api_calls"] == 2
    assert step["measured"] == {
        "input_tokens": 15,
        "output_tokens": 300,
        "cache_creation_input_tokens": 500,
        "cache_read_input_tokens": 3000,
    }
    # Billable input 15+500+3000=3515 @ 1.0/Mtok; output 300 @ 5.0/Mtok.
    assert step["cost_usd"]["basis"] == "measured"
    assert step["cost_usd"]["billable_input_tokens"] == 3515
    assert step["cost_usd"]["input"] == 0.003515
    assert step["cost_usd"]["output"] == 0.0015
    assert step["cost_usd"]["total"] == 0.005015
    assert payload["totals"]["cost_usd"]["total"] == 0.005015
    assert payload["totals"]["cost_usd"]["by_model"] == {"haiku": 0.005015}


def test_missing_transcript_fails_loudly(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    transcripts_root.mkdir()
    config = pricing_config(tmp_path / "config.json")

    result = run_token_costs(run_dir, transcripts_root, config)
    assert result.returncode != 0
    assert "no transcript matched" in result.stderr


def test_unpriced_model_fails_loudly(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(
        transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl",
        transcript_entries(agent_file, model_id="claude-unknown-9"),
    )
    config = pricing_config(tmp_path / "config.json")

    result = run_token_costs(run_dir, transcripts_root, config)
    assert result.returncode != 0
    assert "pricing" in result.stderr
