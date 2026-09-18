"""Historical and fallback contracts for cost-aware routing."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "evals" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from route_cost_estimation import estimate_route_cost  # noqa: E402
from test_route_cost_integration import cost_config, records_with_usage  # noqa: E402
from test_route_recommendation_script import (  # noqa: E402
    difficulty_placement,
    ledger_record,
    repo_root,
    run_recommendation,
    task_evaluation,
)


UTC = timezone.utc


def at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def dated_config(*rate_cards: dict) -> dict:
    config = cost_config()
    config["pricing"] = {
        "currency": "USD",
        "billing_mode": "standard",
        "source": "archived provider price sheet",
        "rate_cards": list(rate_cards),
    }
    return config


def rate_card(
    version: str,
    *,
    effective_from: str,
    effective_to: str | None,
    recorded_at: str,
    input_rate: str,
    model_id: str = "id-haiku",
) -> dict:
    return {
        "model_id": model_id,
        "pricing_version": version,
        "pricing_hash": f"sha256:{version}",
        "effective_from": effective_from,
        "effective_to": effective_to,
        "recorded_at": recorded_at,
        "input_per_mtok": input_rate,
        "output_per_mtok": "1",
        "cache_read_per_mtok": input_rate,
        "cache_write_5m_per_mtok": input_rate,
        "cache_write_1h_per_mtok": input_rate,
    }


def write_config(tmp_path: Path, config: dict) -> Path:
    path = tmp_path / "factory-config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def estimate(
    tmp_path: Path,
    records: list[dict],
    config: dict,
    *,
    pricing_at: str,
    known_at: str,
    historical: bool = True,
) -> dict:
    return estimate_route_cost(
        records,
        task_type="code_generation",
        difficulty="0.5",
        config_path=write_config(tmp_path, config),
        artifact_base=tmp_path,
        pricing_at=at(pricing_at),
        known_at=at(known_at),
        min_samples=3,
        historical=historical,
    )


def test_dated_prices_use_half_open_effective_boundary(tmp_path):
    records = [record for record in records_with_usage(tmp_path) if record["model"] == "haiku"]
    config = dated_config(
        rate_card(
            "before", effective_from="2026-01-01T00:00:00Z",
            effective_to="2026-07-01T00:00:00Z",
            recorded_at="2025-12-01T00:00:00Z", input_rate="1",
        ),
        rate_card(
            "after", effective_from="2026-07-01T00:00:00Z",
            effective_to=None, recorded_at="2026-06-01T00:00:00Z", input_rate="2",
        ),
    )

    before = estimate(
        tmp_path, records, config,
        pricing_at="2026-06-30T23:59:59Z", known_at="2026-07-10T00:00:00Z",
    )
    boundary = estimate(
        tmp_path, records, config,
        pricing_at="2026-07-01T00:00:00Z", known_at="2026-07-10T00:00:00Z",
    )

    assert before["status"] == boundary["status"] == "available"
    assert Decimal(before["amount_usd"]).quantize(Decimal("0.000001")) == Decimal("0.060000")
    assert Decimal(boundary["amount_usd"]).quantize(Decimal("0.000001")) == Decimal("0.120000")
    assert before["estimates"][0]["rate_card"]["pricing_version"] == "before"
    assert boundary["estimates"][0]["rate_card"]["pricing_version"] == "after"


def test_rate_recorded_after_knowledge_cutoff_is_unavailable(tmp_path):
    records = [record for record in records_with_usage(tmp_path) if record["model"] == "haiku"]
    config = dated_config(rate_card(
        "learned-later", effective_from="2026-01-01T00:00:00Z", effective_to=None,
        recorded_at="2026-08-01T00:00:00Z", input_rate="1",
    ))

    result = estimate(
        tmp_path, records, config,
        pricing_at="2026-07-03T00:00:00Z", known_at="2026-07-10T00:00:00Z",
    )

    assert result["status"] == "unavailable"
    assert result["amount_usd"] is None
    assert "no price" in result["reason"]


def test_legacy_prices_are_explicitly_unavailable_for_historical_estimates(tmp_path):
    records = [record for record in records_with_usage(tmp_path) if record["model"] == "haiku"]

    result = estimate(
        tmp_path, records, cost_config(),
        pricing_at="2026-07-03T00:00:00Z", known_at="2026-07-10T00:00:00Z",
    )

    assert result["status"] == "unavailable"
    assert result["amount_usd"] is None
    assert result["reason"] == "legacy prices have no historical effective dates"


def test_artifacts_generated_after_cutoff_are_excluded(tmp_path):
    records = [record for record in records_with_usage(tmp_path) if record["model"] == "haiku"]
    for record in records:
        path = Path(record["cost_artifact"]["path"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["generated_at"] = "2026-08-01T00:00:00Z"
        path.write_text(json.dumps(payload), encoding="utf-8")

    result = estimate(
        tmp_path, records, cost_config(),
        pricing_at="2026-07-03T00:00:00Z", known_at="2026-07-10T00:00:00Z",
        historical=False,
    )

    assert result["status"] == "unavailable"
    assert result["sample_count"] == 0
    assert len(result["excluded_attempts"]) == 3
    assert all("generated after knowledge cutoff" in item for item in result["excluded_attempts"])


def test_failed_attempt_usage_contributes_to_forecast(tmp_path):
    records = records_with_usage(tmp_path, haiku_tokens=0, sonnet_tokens=0)
    records = [record for record in records if record["model"] == "haiku"]
    failed = records[-1]
    failed["success"] = False
    failed["execution_status"] = "failed"
    artifact_path = Path(failed["cost_artifact"]["path"])
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["steps"][0]["calls"][0]["usage"]["input_tokens"] = 300_000
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    result = estimate(
        tmp_path, records, cost_config(),
        pricing_at="2026-07-10T00:00:00Z", known_at="2026-07-10T00:00:00Z",
        historical=False,
    )

    assert result["status"] == "available"
    assert result["sample_count"] == 3
    assert Decimal(result["amount_usd"]).quantize(Decimal("0.000001")) == Decimal("0.100000")


def test_future_outcomes_are_excluded_by_cli_knowledge_cutoff(tmp_path):
    before = ledger_record(
        1, model="haiku", effort="medium", success=True,
        task_type="code_generation", pairwise=0.5,
    )
    after = [
        ledger_record(
            sequence, model="sonnet", effort="medium", success=True,
            task_type="code_generation", pairwise=0.5,
        )
        for sequence in range(2, 6)
    ]
    before["timestamp"] = "2026-07-01T00:00:00Z"
    for record in after:
        record["timestamp"] = "2026-08-01T00:00:00Z"

    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text(
        "".join(json.dumps(record) + "\n" for record in [before, *after]),
        encoding="utf-8",
    )
    evaluation_path = tmp_path / "evaluation.json"
    evaluation_path.write_text(json.dumps(task_evaluation()), encoding="utf-8")
    difficulty_path = tmp_path / "difficulty.json"
    difficulty_path.write_text(json.dumps(difficulty_placement()), encoding="utf-8")
    config = cost_config(weight=0)
    config_path = write_config(tmp_path, config)

    process = subprocess.run(
        [
            "uv", "run", ".kamino/evals/scripts/route_recommendation.py",
            "--ledger", str(ledger_path), "--task-eval", str(evaluation_path),
            "--difficulty", str(difficulty_path), "--config", str(config_path),
            "--known-at", "2026-07-10T00:00:00Z", "--format", "json",
        ],
        cwd=repo_root(), capture_output=True, text=True, check=False,
    )

    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["recommended_model"] == "haiku"
    assert result["successful_records_considered"] == 1


def test_weighted_majority_fallback_uses_cost_estimates(tmp_path):
    records = records_with_usage(tmp_path, haiku_tokens=60_000, sonnet_tokens=20_000)
    config = cost_config()
    config["routing"]["min_attempts_for_rate"] = 10

    result = run_recommendation(tmp_path, records, config=config)

    assert result["source"] == "weighted_majority"
    assert result["cost_policy"]["status"] == "applied"
    assert result["recommended_model"] == "sonnet"
    assert all(item["forecast_scope"] == "model_effort_blueprint_mixture"
               for item in result["candidate_scores"])
