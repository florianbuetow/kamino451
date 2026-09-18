"""Routing consumes optional measured usage without coupling eligibility to pricing."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals" / "scripts"))
from route_recommendation import recommend
from cost_estimation import CALCULATOR_VERSION
from task_outcome_ledger_common import validate_ledger_record
from test_route_recommendation_script import (
    factory_config, rate_records, run_recommendation, task_evaluation, difficulty_placement,
    run_recommendation_process,
)


def cost_config(weight=0.2):
    config = factory_config()
    config["routing"]["cost"] = {"enabled": True, "weight": weight, "scale_usd": 0.1, "min_samples": 3}
    config["pricing"] = {"currency": "USD", "models": {
        model: {"model_ids": [f"id-{model}"], "input_per_mtok": 1, "output_per_mtok": 1}
        for model in ("haiku", "sonnet")
    }}
    return config


def records_with_usage(tmp_path, *, haiku_tokens=60000, sonnet_tokens=20000):
    records = rate_records(haiku_successes=3, haiku_failures=0, sonnet_successes=3, sonnet_failures=0)
    for record in records:
        run_id = record["record_id"]
        model = record["model"]
        artifact = tmp_path / f"{run_id}.json"
        artifact.write_text(json.dumps({"run_id": run_id, "schema_version": "kamino451.token-costs.v1",
            "calculator_version": CALCULATOR_VERSION, "generated_at": record["timestamp"], "steps": [{
            "agent_file": record["agent_files_used"][0], "model": model, "status": "ok", "calls": [{
                "model_id": f"id-{model}", "timestamp": record["timestamp"], "basis": "measured",
                "usage": {"input_tokens": haiku_tokens if model == "haiku" else sonnet_tokens,
                          "output_tokens": 0},
            }],
        }]}))
        record["cost_artifact"] = {"run_id": run_id, "path": str(artifact)}
    return records


def test_real_usage_cost_breaks_equal_suitability_scores(tmp_path):
    records = records_with_usage(tmp_path)
    result = run_recommendation(tmp_path, records, config=cost_config())
    assert result["recommended_model"] == "sonnet"
    assert result["cost_policy"]["status"] == "applied"
    scores = result["candidate_scores"]
    assert scores[0]["base_score"] == scores[1]["base_score"] == "1"
    assert scores[0]["cost_estimate"]["sample_count"] == 3
    assert scores[0]["cost_estimate"]["amount_usd"] == "0.02"


@pytest.mark.parametrize("tokens", [0, 50000])
def test_equal_cost_matches_disabled_routing_exactly(tmp_path, tokens):
    records = records_with_usage(tmp_path, haiku_tokens=tokens, sonnet_tokens=tokens)
    active = run_recommendation(tmp_path, records, config=cost_config())
    disabled = run_recommendation(tmp_path, records, config=cost_config(weight=0))
    assert active["recommended_model"] == disabled["recommended_model"]
    assert active["qualified_combinations"] == disabled["qualified_combinations"]
    for left, right in zip(active["candidate_scores"], disabled["candidate_scores"]):
        assert left["final_score"] == right["final_score"]
        assert left["cost_penalty"] == "0"


@pytest.mark.parametrize("enabled,weight", [(False, 1), (True, 0)])
def test_disabled_cost_never_calls_estimator(enabled, weight):
    def broken_estimator(_):
        raise AssertionError("disabled estimator was called")
    config = cost_config()["routing"] | {"config_source": "test", "config_path": "missing.json"}
    config["cost"].update(enabled=enabled, weight=weight)
    records = rate_records(haiku_successes=3, haiku_failures=0, sonnet_successes=4, sonnet_failures=0)
    result = recommend(records, task_evaluation(), difficulty_placement(), config, broken_estimator)
    assert result["recommended_model"] == "sonnet"
    assert result["cost_policy"]["status"] == "disabled"


def test_unavailable_candidate_disables_comparison_not_candidate(tmp_path):
    records = records_with_usage(tmp_path)
    for record in records:
        if record["model"] == "haiku":
            Path(record["cost_artifact"]["path"]).unlink()
    result = run_recommendation(tmp_path, records, config=cost_config())
    assert result["recommended_model"] == "haiku"
    assert result["cost_policy"]["status"] == "unavailable"
    assert all(item["final_score"] == item["base_score"] for item in result["candidate_scores"])
    assert result["candidate_scores"][0]["cost_estimate"]["amount_usd"] is None


def test_wrong_run_reference_cannot_supply_cost(tmp_path):
    records = records_with_usage(tmp_path)
    records[0]["cost_artifact"]["run_id"] = "wrong"
    result = run_recommendation(tmp_path, records, config=cost_config())
    assert result["cost_policy"]["status"] == "unavailable"
    assert "does not match" in result["candidate_scores"][0]["cost_estimate"]["excluded_attempts"][0]


def test_price_does_not_qualify_a_failing_candidate(tmp_path):
    records = records_with_usage(tmp_path, haiku_tokens=0)
    records[0]["success"] = False
    result = run_recommendation(tmp_path, records, config=cost_config(weight=100))
    assert result["recommended_model"] == "sonnet"
    assert len(result["qualified_combinations"]) == 1


def test_invalid_cost_config_rejected(tmp_path):
    config = cost_config()
    config["routing"]["cost"]["scale_usd"] = 0
    result = run_recommendation_process(tmp_path, [], config=config)
    assert result.returncode == 1
    assert "scale_usd" in result.stderr


def test_ledger_preserves_explicit_cost_join(tmp_path):
    record = records_with_usage(tmp_path)[0]
    assert validate_ledger_record(record, "record")["cost_artifact"] == record["cost_artifact"]
