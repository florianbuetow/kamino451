"""Contract tests for the post-run token usage and cost accounting script."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path


def repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[2]


def test_factory_config_has_pricing_for_model_ladder():
    config = json.loads((repo_root() / ".kamino" / "factory-config.json").read_text(encoding="utf-8"))
    pricing = config["pricing"]
    assert pricing["currency"] == "USD"
    for name in ("haiku", "sonnet", "opus", "fable"):
        entry = pricing["models"][name]
        assert entry["model_ids"], f"{name} needs at least one API model id"
        for key in (
            "input_per_mtok",
            "output_per_mtok",
            "cache_read_per_mtok",
            "cache_write_5m_per_mtok",
            "cache_write_1h_per_mtok",
        ):
            assert entry[key] > 0, f"{name} needs positive {key}"
    assert "claude-opus-5" in pricing["models"]["opus"]["model_ids"]
    assert "claude-fable-5" in pricing["models"]["fable"]["model_ids"]


RUN_ID = "260720-100000"
MODEL_ID = "claude-haiku-4-5-20251001"


def write_jsonl(path: Path, entries: list[dict]) -> None:
    """Write JSONL fixture data, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")


def pricing_config(path: Path, *, cache_rates: bool = False) -> Path:
    """Write a minimal factory config with easy-math pricing (haiku 1.0/5.0 per Mtok)."""
    entry = {"model_ids": [MODEL_ID], "input_per_mtok": 1.0, "output_per_mtok": 5.0}
    if cache_rates:
        entry.update(
            {
                "cache_read_per_mtok": 0.10,
                "cache_write_5m_per_mtok": 1.25,
                "cache_write_1h_per_mtok": 2.0,
            }
        )
    payload = {
        "schema_version": "kamino451.factory-config.v1",
        "routing": {"success_rate_threshold": 0.9, "min_attempts_for_rate": 3},
        "pricing": {
            "currency": "USD",
            "models": {"haiku": entry},
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


def run_cost_evaluation(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", ".kamino/evals/scripts/token_costs_evaluate.py", *args, "--format", "json"],
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


def test_malformed_middle_transcript_line_fails_loudly(tmp_path):
    """A corrupt completed record must not silently reduce token accounting."""
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    transcript = transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl"
    entries = transcript_entries(agent_file)
    transcript.parent.mkdir(parents=True)
    lines = [json.dumps(entry) for entry in entries]
    transcript.write_text("\n".join([lines[0], "{malformed", *lines[1:]]) + "\n", encoding="utf-8")
    config = pricing_config(tmp_path / "config.json")

    result = run_token_costs(run_dir, transcripts_root, config)

    assert result.returncode != 0
    assert "malformed JSONL line 2" in result.stderr
    assert not (run_dir / "token_costs.json").exists()


def test_torn_trailing_transcript_line_is_tolerated(tmp_path):
    """An interrupted final append may leave one incomplete, non-terminated line."""
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    transcript = transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl"
    transcript.parent.mkdir(parents=True)
    valid = "\n".join(json.dumps(entry) for entry in transcript_entries(agent_file))
    transcript.write_text(valid + "\n{\"type\":", encoding="utf-8")
    config = pricing_config(tmp_path / "config.json")

    result = run_token_costs(run_dir, transcripts_root, config)

    assert result.returncode == 0, result.stderr
    assert (run_dir / "token_costs.json").is_file()


def test_unregistered_model_id_fails_loudly(tmp_path):
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


def test_char_estimate_counts_actual_agent_traffic(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl", transcript_entries(agent_file))
    config = pricing_config(tmp_path / "config.json")

    result = run_token_costs(run_dir, transcripts_root, config)
    assert result.returncode == 0, result.stderr

    payload = json.loads((run_dir / "token_costs.json").read_text(encoding="utf-8"))
    step = payload["steps"][0]
    prompt = f"You are the agent defined in the file {agent_file}. Read that file NOW."
    # Input side: the one user message. Output side: final snapshots of the two
    # real calls ("working now" + "done, wrote the file"); duplicates and synthetic excluded.
    assert step["estimated"]["input_chars"] == len(prompt)
    assert step["estimated"]["output_chars"] == len("working now") + len("done, wrote the file")
    assert step["estimated"]["chars_per_token"] == 4
    assert step["estimated"]["input_tokens"] == -(-len(prompt) // 4)
    assert payload["totals"]["estimated"]["input_chars"] == len(prompt)


def test_estimated_basis_when_usage_is_absent(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    entries = transcript_entries(agent_file)
    for entry in entries:
        if entry["type"] == "assistant":
            entry["message"]["usage"] = {key: 0 for key in entry["message"]["usage"]}
    write_jsonl(transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl", entries)
    config = pricing_config(tmp_path / "config.json")

    result = run_token_costs(run_dir, transcripts_root, config)
    assert result.returncode == 0, result.stderr

    step = json.loads((run_dir / "token_costs.json").read_text(encoding="utf-8"))["steps"][0]
    assert step["cost_usd"]["basis"] == "estimated"
    assert step["cost_usd"]["billable_input_tokens"] == step["estimated"]["input_tokens"]
    assert step["cost_usd"]["total"] > 0


def test_multi_step_run_aggregates_and_copies_transcripts(tmp_path):
    agent_1 = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    agent_2 = str(tmp_path / "dispatch" / RUN_ID / "02-agent.md")
    records = [
        trace_record(agent_1, step=1),
        trace_record(agent_2, step=2),
        trace_record(str(tmp_path / "dispatch" / RUN_ID / "03-agent.md"), step=3, status="skipped"),
    ]
    run_dir = build_capsule(tmp_path, records=records)
    transcripts_root = tmp_path / "projects"
    write_jsonl(transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl", transcript_entries(agent_1))
    write_jsonl(transcripts_root / "aaaa-session" / "subagents" / "agent-a2.jsonl", transcript_entries(agent_2))
    config = pricing_config(tmp_path / "config.json")

    result = run_token_costs(run_dir, transcripts_root, config)
    assert result.returncode == 0, result.stderr

    payload = json.loads((run_dir / "token_costs.json").read_text(encoding="utf-8"))
    assert len(payload["steps"]) == 3
    # Two identical billed steps; skipped step costs nothing.
    assert payload["totals"]["cost_usd"]["total"] == 0.01003
    skipped = payload["steps"][2]
    assert skipped["cost_usd"]["basis"] == "none"
    assert skipped["cost_usd"]["total"] == 0.0
    assert skipped["transcript_source"] is None
    # Matched transcripts are copied into the capsule for durability.
    assert (run_dir / "transcripts" / "step-01-attempt-1.jsonl").is_file()
    assert (run_dir / "transcripts" / "step-02-attempt-1.jsonl").is_file()
    assert payload["steps"][0]["transcript_path"] == "transcripts/step-01-attempt-1.jsonl"


def test_ambiguous_transcript_match_fails_loudly(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl", transcript_entries(agent_file))
    write_jsonl(transcripts_root / "bbbb-session" / "subagents" / "agent-b1.jsonl", transcript_entries(agent_file))
    config = pricing_config(tmp_path / "config.json")

    result = run_token_costs(run_dir, transcripts_root, config)
    assert result.returncode != 0
    assert "ambiguous transcript match" in result.stderr


def test_record_run_payload_reports_token_costs_status(tmp_path):
    # record_run must accept --transcripts-root and surface token accounting
    # without letting a token failure block outcome recording.
    result = subprocess.run(
        ["uv", "run", ".kamino/evals/scripts/record_run.py", "--help"],
        capture_output=True,
        text=True,
        cwd=repo_root(),
        check=False,
    )
    assert result.returncode == 0
    assert "--transcripts-root" in result.stdout


def test_retry_attempts_disambiguate_by_exact_window(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    first = trace_record(agent_file)
    second = trace_record(agent_file, attempt=2)
    second["started_at"] = "2026-07-20T10:05:30Z"
    second["ended_at"] = "2026-07-20T10:09:00Z"
    run_dir = build_capsule(tmp_path, records=[first, second])
    transcripts_root = tmp_path / "projects"
    write_jsonl(transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl", transcript_entries(agent_file))
    retry_entries = transcript_entries(agent_file)
    for entry in retry_entries:
        entry["timestamp"] = entry["timestamp"].replace("T10:00:", "T10:06:")
    write_jsonl(transcripts_root / "aaaa-session" / "subagents" / "agent-a2.jsonl", retry_entries)
    config = pricing_config(tmp_path / "config.json")

    result = run_token_costs(run_dir, transcripts_root, config)
    assert result.returncode == 0, result.stderr

    payload = json.loads((run_dir / "token_costs.json").read_text(encoding="utf-8"))
    assert [step["attempt"] for step in payload["steps"]] == [1, 2]
    assert payload["steps"][0]["transcript_source"].endswith("agent-a1.jsonl")
    assert payload["steps"][1]["transcript_source"].endswith("agent-a2.jsonl")
    assert (run_dir / "transcripts" / "step-01-attempt-2.jsonl").is_file()


def test_unpriced_short_model_fails_loudly(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    records = [trace_record(agent_file)]
    records[0]["model"] = "sonnet"
    run_dir = build_capsule(tmp_path, records=records)
    transcripts_root = tmp_path / "projects"
    write_jsonl(
        transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl",
        transcript_entries(agent_file, model_id="claude-sonnet-5"),
    )
    config = pricing_config(tmp_path / "config.json")

    result = run_token_costs(run_dir, transcripts_root, config)
    assert result.returncode != 0
    assert "no pricing entry covers model" in result.stderr


def test_session_block_counts_unique_tokens_once(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    records = [
        trace_record(agent_file),
        trace_record(str(tmp_path / "dispatch" / RUN_ID / "02-agent.md"), step=2, status="skipped"),
    ]
    run_dir = build_capsule(tmp_path, records=records)
    transcripts_root = tmp_path / "projects"
    write_jsonl(transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl", transcript_entries(agent_file))
    config = pricing_config(tmp_path / "config.json")

    result = run_token_costs(run_dir, transcripts_root, config)
    assert result.returncode == 0, result.stderr

    payload = json.loads((run_dir / "token_costs.json").read_text(encoding="utf-8"))
    # Fixture usage: input 15, cache_creation 500, cache_read 3000, output 300.
    # unique input = 15 + 500 (history resends in cache_read are excluded).
    assert payload["steps"][0]["session"] == {"unique_input_tokens": 515, "unique_output_tokens": 300}
    assert payload["steps"][1]["session"] == {"unique_input_tokens": 0, "unique_output_tokens": 0}
    assert payload["totals"]["session"] == {"unique_input_tokens": 515, "unique_output_tokens": 300}


def transcript_entries_with_ttl_split(agent_file: str) -> list[dict]:
    """The standard transcript, with msg_a's 500 cache-creation tokens split 100 (5m) / 400 (1h)."""
    entries = transcript_entries(agent_file)
    entries[2]["message"]["usage"] = dict(
        entries[2]["message"]["usage"],
        cache_creation={"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 400},
    )
    return entries


def test_cache_aware_rates_bill_cache_reads_and_writes(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl", transcript_entries(agent_file))
    config = pricing_config(tmp_path / "config.json", cache_rates=True)

    result = run_token_costs(run_dir, transcripts_root, config)
    assert result.returncode == 0, result.stderr

    step = json.loads((run_dir / "token_costs.json").read_text(encoding="utf-8"))["steps"][0]
    # input 15 @ 1.0 + cache_read 3000 @ 0.10 + cache_creation 500 (no TTL
    # breakdown -> all 5m) @ 1.25 = 0.000015 + 0.0003 + 0.000625 = 0.00094
    assert step["cost_usd"]["cache_aware"] is True
    assert step["cost_usd"]["input"] == 0.00094
    assert step["cost_usd"]["output"] == 0.0015
    assert step["cost_usd"]["total"] == 0.00244
    assert step["cost_usd"]["billable_input_tokens"] == 3515


def test_cache_creation_ttl_split_uses_configured_write_rates(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(
        transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl",
        transcript_entries_with_ttl_split(agent_file),
    )
    config = pricing_config(tmp_path / "config.json", cache_rates=True)

    result = run_token_costs(run_dir, transcripts_root, config)
    assert result.returncode == 0, result.stderr

    step = json.loads((run_dir / "token_costs.json").read_text(encoding="utf-8"))["steps"][0]
    # input 15 @ 1.0 + cache_read 3000 @ 0.10 + 100 @ 1.25 + 400 @ 2.0
    # = 0.000015 + 0.0003 + 0.000125 + 0.0008 = 0.00124
    assert step["cost_usd"]["input"] == 0.00124
    assert step["cost_usd"]["total"] == 0.00274


def test_partial_cache_rates_fail_loudly(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl", transcript_entries(agent_file))
    config_path = tmp_path / "config.json"
    payload = json.loads(pricing_config(config_path).read_text(encoding="utf-8"))
    payload["pricing"]["models"]["haiku"]["cache_read_per_mtok"] = 0.10  # partial: only one of three
    config_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    result = run_token_costs(run_dir, transcripts_root, config_path)
    assert result.returncode != 0
    assert "all of" in result.stderr


def test_artifact_persists_reproducible_per_call_inputs_and_rates(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(
        transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl",
        transcript_entries_with_ttl_split(agent_file),
    )
    config = pricing_config(tmp_path / "config.json", cache_rates=True)

    result = run_token_costs(run_dir, transcripts_root, config)
    assert result.returncode == 0, result.stderr

    payload = json.loads((run_dir / "token_costs.json").read_text(encoding="utf-8"))
    assert payload["calculator_version"]
    calls = payload["steps"][0]["calls"]
    assert [call["model_id"] for call in calls] == [MODEL_ID, MODEL_ID]
    assert [call["timestamp"] for call in calls] == [
        "2026-07-20T10:00:10Z",
        "2026-07-20T10:00:30Z",
    ]
    assert calls[0]["basis"] == "measured"
    assert calls[0]["usage"] == {
        "input_tokens": "10",
        "output_tokens": "200",
        "cache_read_input_tokens": "1000",
        "cache_write_5m_input_tokens": "100",
        "cache_write_1h_input_tokens": "400",
    }
    snapshot = calls[0]["rate_card"]
    assert snapshot["model_id"] == MODEL_ID
    assert snapshot["currency"] == "USD"
    assert snapshot["pricing_hash"]
    assert snapshot["input_per_mtok"] == "1.0"
    assert calls[0]["cost_estimate"]["rate_card"] == snapshot


def test_zero_rates_are_valid_and_recorded_as_real_zero_cost(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(
        transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl",
        transcript_entries(agent_file),
    )
    config = pricing_config(tmp_path / "config.json", cache_rates=True)
    config_payload = json.loads(config.read_text(encoding="utf-8"))
    for key in (
        "input_per_mtok",
        "output_per_mtok",
        "cache_read_per_mtok",
        "cache_write_5m_per_mtok",
        "cache_write_1h_per_mtok",
    ):
        config_payload["pricing"]["models"]["haiku"][key] = 0
    config.write_text(json.dumps(config_payload), encoding="utf-8")

    result = run_token_costs(run_dir, transcripts_root, config)
    assert result.returncode == 0, result.stderr
    payload = json.loads((run_dir / "token_costs.json").read_text(encoding="utf-8"))
    assert payload["totals"]["cost_usd"]["total"] == 0.0
    assert all(Decimal(call["cost_estimate"]["cost"]["total_cost"]) == 0 for call in payload["steps"][0]["calls"])


def test_reproduce_uses_saved_inputs_and_rate_snapshots_only(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(
        transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl",
        transcript_entries(agent_file),
    )
    config = pricing_config(tmp_path / "config.json", cache_rates=True)
    accounting = run_token_costs(run_dir, transcripts_root, config)
    assert accounting.returncode == 0, accounting.stderr
    source = run_dir / "token_costs.json"
    source_payload = json.loads(source.read_text(encoding="utf-8"))

    # Replay must not consult either mutable external source.
    config.unlink()
    for transcript in transcripts_root.rglob("*.jsonl"):
        transcript.unlink()
    output = run_dir / "token_costs.reproduced.json"
    result = run_cost_evaluation("reproduce", "--input", str(source), "--output", str(output))
    assert result.returncode == 0, result.stderr

    replay = json.loads(output.read_text(encoding="utf-8"))
    assert replay["mode"] == "reproduce"
    assert replay["source_run_id"] == RUN_ID
    assert replay["totals"]["cost_usd"]["total"] == source_payload["totals"]["cost_usd"]["total"]
    assert source_payload == json.loads(source.read_text(encoding="utf-8"))


def test_reprice_writes_separate_artifact_and_uses_each_call_timestamp(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    entries = transcript_entries(agent_file)
    entries[-1]["timestamp"] = "2026-08-20T10:00:30Z"
    # Keep transcript matching inside the trace window while calls cross a price boundary.
    records = json.loads((run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()[0])
    records["ended_at"] = "2026-08-20T10:05:00Z"
    write_jsonl(run_dir / "trace.jsonl", [records])
    write_jsonl(transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl", entries)
    config = pricing_config(tmp_path / "config.json", cache_rates=True)
    accounting = run_token_costs(run_dir, transcripts_root, config)
    assert accounting.returncode == 0, accounting.stderr
    source = run_dir / "token_costs.json"
    original = source.read_text(encoding="utf-8")

    catalog = tmp_path / "historical-pricing.json"
    catalog.write_text(
        json.dumps(
            {
                "pricing": {
                    "currency": "USD",
                    "billing_mode": "standard",
                    "source": "test-catalog",
                    "rate_cards": [
                        {
                            "model_id": MODEL_ID,
                            "pricing_version": "summer-1",
                            "effective_from": "2026-07-01T00:00:00Z",
                            "effective_to": "2026-08-01T00:00:00Z",
                            "recorded_at": "2026-07-01T00:00:00Z",
                            "input_per_mtok": 2,
                            "output_per_mtok": 10,
                            "cache_read_per_mtok": 0.2,
                            "cache_write_5m_per_mtok": 2.5,
                            "cache_write_1h_per_mtok": 4,
                        },
                        {
                            "model_id": MODEL_ID,
                            "pricing_version": "summer-2",
                            "effective_from": "2026-08-01T00:00:00Z",
                            "effective_to": None,
                            "recorded_at": "2026-08-01T00:00:00Z",
                            "input_per_mtok": 3,
                            "output_per_mtok": 15,
                            "cache_read_per_mtok": 0.3,
                            "cache_write_5m_per_mtok": 3.75,
                            "cache_write_1h_per_mtok": 6,
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    output = run_dir / "token_costs.repriced.json"
    result = run_cost_evaluation(
        "reprice", "--input", str(source), "--catalog", str(catalog), "--output", str(output)
    )
    assert result.returncode == 0, result.stderr

    repriced = json.loads(output.read_text(encoding="utf-8"))
    assert repriced["mode"] == "reprice"
    assert [call["rate_card"]["pricing_version"] for call in repriced["steps"][0]["calls"]] == [
        "summer-1",
        "summer-2",
    ]
    assert source.read_text(encoding="utf-8") == original

    explicit_output = run_dir / "token_costs.repriced-at-july.json"
    explicit = run_cost_evaluation(
        "reprice",
        "--input",
        str(source),
        "--catalog",
        str(catalog),
        "--pricing-at",
        "2026-07-15T00:00:00Z",
        "--output",
        str(explicit_output),
    )
    assert explicit.returncode == 0, explicit.stderr
    explicit_payload = json.loads(explicit_output.read_text(encoding="utf-8"))
    assert [call["rate_card"]["pricing_version"] for call in explicit_payload["steps"][0]["calls"]] == [
        "summer-1",
        "summer-1",
    ]


def test_historical_reprice_rejects_undated_legacy_prices(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(
        transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl",
        transcript_entries(agent_file),
    )
    legacy_config = pricing_config(tmp_path / "config.json", cache_rates=True)
    accounting = run_token_costs(run_dir, transcripts_root, legacy_config)
    assert accounting.returncode == 0, accounting.stderr

    result = run_cost_evaluation(
        "reprice",
        "--input",
        str(run_dir / "token_costs.json"),
        "--catalog",
        str(legacy_config),
        "--output",
        str(run_dir / "token_costs.repriced.json"),
    )
    assert result.returncode != 0
    assert "legacy pricing has no effective dates" in result.stderr
    assert not (run_dir / "token_costs.repriced.json").exists()


def test_reproduce_rejects_unsupported_saved_calculator_version(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(
        transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl",
        transcript_entries(agent_file),
    )
    config = pricing_config(tmp_path / "config.json", cache_rates=True)
    accounting = run_token_costs(run_dir, transcripts_root, config)
    assert accounting.returncode == 0, accounting.stderr
    source = run_dir / "token_costs.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["calculator_version"] = "future-calculator-v99"
    source.write_text(json.dumps(payload), encoding="utf-8")

    output = run_dir / "token_costs.reproduced.json"
    result = run_cost_evaluation("reproduce", "--input", str(source), "--output", str(output))
    assert result.returncode != 0
    assert "unsupported calculator_version" in result.stderr
    assert not output.exists()


def test_reproduce_rejects_call_and_rate_snapshot_model_mismatch(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(
        transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl",
        transcript_entries(agent_file),
    )
    config = pricing_config(tmp_path / "config.json", cache_rates=True)
    accounting = run_token_costs(run_dir, transcripts_root, config)
    assert accounting.returncode == 0, accounting.stderr
    source = run_dir / "token_costs.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["steps"][0]["calls"][0]["rate_card"]["model_id"] = "different-exact-model"
    source.write_text(json.dumps(payload), encoding="utf-8")

    output = run_dir / "token_costs.reproduced.json"
    result = run_cost_evaluation("reproduce", "--input", str(source), "--output", str(output))
    assert result.returncode != 0
    assert "does not match rate snapshot model_id" in result.stderr
    assert not output.exists()


def test_reproduce_totals_follow_writer_per_step_rounding(tmp_path):
    agent_1 = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    agent_2 = str(tmp_path / "dispatch" / RUN_ID / "02-agent.md")
    run_dir = build_capsule(
        tmp_path,
        records=[trace_record(agent_1), trace_record(agent_2, step=2)],
    )
    transcripts_root = tmp_path / "projects"
    write_jsonl(transcripts_root / "a" / "subagents" / "agent-a.jsonl", transcript_entries(agent_1))
    write_jsonl(transcripts_root / "b" / "subagents" / "agent-b.jsonl", transcript_entries(agent_2))
    config = pricing_config(tmp_path / "config.json", cache_rates=True)
    config_payload = json.loads(config.read_text(encoding="utf-8"))
    entry = config_payload["pricing"]["models"]["haiku"]
    for key in (
        "input_per_mtok",
        "cache_read_per_mtok",
        "cache_write_5m_per_mtok",
        "cache_write_1h_per_mtok",
    ):
        entry[key] = 0
    entry["output_per_mtok"] = 0.001
    config.write_text(json.dumps(config_payload), encoding="utf-8")
    accounting = run_token_costs(run_dir, transcripts_root, config)
    assert accounting.returncode == 0, accounting.stderr
    source = run_dir / "token_costs.json"
    recorded = json.loads(source.read_text(encoding="utf-8"))
    assert recorded["totals"]["cost_usd"]["total"] == 0.0

    output = run_dir / "token_costs.reproduced.json"
    result = run_cost_evaluation("reproduce", "--input", str(source), "--output", str(output))
    assert result.returncode == 0, result.stderr
    replay = json.loads(output.read_text(encoding="utf-8"))
    assert replay["totals"]["cost_usd"]["total"] == recorded["totals"]["cost_usd"]["total"]


def test_reproduce_rejects_output_hardlink_to_source(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(
        transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl",
        transcript_entries(agent_file),
    )
    config = pricing_config(tmp_path / "config.json", cache_rates=True)
    accounting = run_token_costs(run_dir, transcripts_root, config)
    assert accounting.returncode == 0, accounting.stderr
    source = run_dir / "token_costs.json"
    original = source.read_text(encoding="utf-8")
    output = run_dir / "hardlink.json"
    os.link(source, output)

    result = run_cost_evaluation("reproduce", "--input", str(source), "--output", str(output))
    assert result.returncode != 0
    assert "separate from the source" in result.stderr
    assert source.read_text(encoding="utf-8") == original


def test_default_accounting_rerun_preserves_original_snapshot_without_sources(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(
        transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl",
        transcript_entries(agent_file),
    )
    config = pricing_config(tmp_path / "config.json", cache_rates=True)
    first = run_token_costs(run_dir, transcripts_root, config)
    assert first.returncode == 0, first.stderr
    artifact = run_dir / "token_costs.json"
    original = artifact.read_bytes()

    changed = json.loads(config.read_text(encoding="utf-8"))
    changed["pricing"]["models"]["haiku"]["output_per_mtok"] = 999
    config.write_text(json.dumps(changed), encoding="utf-8")
    shutil.rmtree(transcripts_root)

    second = run_token_costs(run_dir, transcripts_root, config)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["reused"] is True
    assert artifact.read_bytes() == original


def test_default_accounting_rejects_stale_snapshot_after_trace_append(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(
        transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl",
        transcript_entries(agent_file),
    )
    config = pricing_config(tmp_path / "config.json", cache_rates=True)
    first = run_token_costs(run_dir, transcripts_root, config)
    assert first.returncode == 0, first.stderr
    artifact = run_dir / "token_costs.json"
    original = artifact.read_bytes()

    trace = run_dir / "trace.jsonl"
    appended = trace_record(agent_file, attempt=2)
    trace.write_text(
        trace.read_text(encoding="utf-8") + json.dumps(appended) + "\n",
        encoding="utf-8",
    )
    second = run_token_costs(run_dir, transcripts_root, config)
    assert second.returncode != 0
    assert "current trace differs" in second.stderr
    assert "use --output" in second.stderr
    assert artifact.read_bytes() == original


def test_accounting_never_overwrites_an_explicit_existing_output(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(
        transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl",
        transcript_entries(agent_file),
    )
    config = pricing_config(tmp_path / "config.json", cache_rates=True)
    output = run_dir / "alternate-costs.json"
    output.write_text("historical evidence\n", encoding="utf-8")

    result = run_token_costs(run_dir, transcripts_root, config, "--output", str(output))
    assert result.returncode != 0
    assert "refusing to overwrite" in result.stderr
    assert output.read_text(encoding="utf-8") == "historical evidence\n"


def test_default_accounting_preserves_legacy_artifact_and_requests_new_output(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    legacy = {
        "schema_version": "kamino451.token-costs.v1",
        "run_id": RUN_ID,
        "totals": {"cost_usd": {"total": 1.25}},
        "steps": [],
    }
    artifact = run_dir / "token_costs.json"
    original = json.dumps(legacy)
    artifact.write_text(original, encoding="utf-8")

    result = run_token_costs(run_dir, tmp_path / "missing-transcripts", tmp_path / "missing-config.json")
    assert result.returncode != 0
    assert "preserve it and use --output" in result.stderr
    assert artifact.read_text(encoding="utf-8") == original


def test_reprice_preserves_source_provider_and_billing_mode_selectors(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(
        transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl",
        transcript_entries(agent_file),
    )
    config = pricing_config(tmp_path / "config.json", cache_rates=True)
    accounting = run_token_costs(run_dir, transcripts_root, config)
    assert accounting.returncode == 0, accounting.stderr
    source = run_dir / "token_costs.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    for call in payload["steps"][0]["calls"]:
        call["rate_card"]["provider"] = "anthropic"
        call["rate_card"]["billing_mode"] = "batch"
        call["cost_estimate"]["rate_card"]["provider"] = "anthropic"
        call["cost_estimate"]["rate_card"]["billing_mode"] = "batch"
    source.write_text(json.dumps(payload), encoding="utf-8")

    def card(provider: str, input_rate: int) -> dict:
        return {
            "provider": provider,
            "billing_mode": "batch",
            "model_id": MODEL_ID,
            "pricing_version": f"{provider}-batch",
            "effective_from": "2026-07-01T00:00:00Z",
            "effective_to": None,
            "recorded_at": "2026-07-01T00:00:00Z",
            "input_per_mtok": input_rate,
            "output_per_mtok": input_rate,
            "cache_read_per_mtok": input_rate,
            "cache_write_5m_per_mtok": input_rate,
            "cache_write_1h_per_mtok": input_rate,
        }

    catalog = tmp_path / "provider-pricing.json"
    catalog.write_text(
        json.dumps(
            {
                "pricing": {
                    "currency": "USD",
                    "rate_cards": [card("anthropic", 2), card("reseller", 99)],
                }
            }
        ),
        encoding="utf-8",
    )
    output = run_dir / "token_costs.repriced.json"
    result = run_cost_evaluation(
        "reprice", "--input", str(source), "--catalog", str(catalog), "--output", str(output)
    )
    assert result.returncode == 0, result.stderr
    repriced = json.loads(output.read_text(encoding="utf-8"))
    assert {
        (call["rate_card"]["provider"], call["rate_card"]["billing_mode"], call["rate_card"]["pricing_version"])
        for call in repriced["steps"][0]["calls"]
    } == {("anthropic", "batch", "anthropic-batch")}


def test_reprice_rejects_non_usd_rates_before_writing_output(tmp_path):
    agent_file = str(tmp_path / "dispatch" / RUN_ID / "01-agent.md")
    run_dir = build_capsule(tmp_path, records=[trace_record(agent_file)])
    transcripts_root = tmp_path / "projects"
    write_jsonl(
        transcripts_root / "aaaa-session" / "subagents" / "agent-a1.jsonl",
        transcript_entries(agent_file),
    )
    config = pricing_config(tmp_path / "config.json", cache_rates=True)
    accounting = run_token_costs(run_dir, transcripts_root, config)
    assert accounting.returncode == 0, accounting.stderr

    catalog = tmp_path / "eur-pricing.json"
    catalog.write_text(
        json.dumps(
            {
                "pricing": {
                    "currency": "EUR",
                    "rate_cards": [
                        {
                            "model_id": MODEL_ID,
                            "pricing_version": "eur-v1",
                            "effective_from": "2026-07-01T00:00:00Z",
                            "effective_to": None,
                            "recorded_at": "2026-07-01T00:00:00Z",
                            "input_per_mtok": 1,
                            "output_per_mtok": 5,
                            "cache_read_per_mtok": 0.1,
                            "cache_write_5m_per_mtok": 1.25,
                            "cache_write_1h_per_mtok": 2,
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    output = run_dir / "token_costs.repriced.json"
    result = run_cost_evaluation(
        "reprice",
        "--input",
        str(run_dir / "token_costs.json"),
        "--catalog",
        str(catalog),
        "--output",
        str(output),
    )
    assert result.returncode != 0
    assert "cannot apply EUR rates" in result.stderr
    assert not output.exists()
