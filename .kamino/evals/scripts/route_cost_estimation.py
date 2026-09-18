"""Routing adapter: retained attempt evidence -> usage forecast -> priced estimate."""

from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path

from cost_estimation import CALCULATOR_VERSION, TokenUsage, calculate_cost
from pricing_catalog import load_catalog, resolve_rates
from usage_forecast import UsageObservation, forecast_usage


def estimate_route_cost(records, *, task_type, difficulty, config_path, artifact_base,
                        pricing_at, known_at, min_samples, historical=False):
    """Estimate comparable whole attempts, including failures, under one price basis.

    File/schema/pricing gaps yield unavailable evidence, never a free candidate.
    No ledger paths are guessed: only explicitly linked capsules are consumed.
    """
    observations = []
    problems = []
    seen_runs = set()
    for record in records:
        if record["task_type"] != task_type:
            continue
        try:
            reference = record.get("cost_artifact")
            if reference is None:
                raise ValueError("attempt has no cost artifact reference")
            path = Path(reference["path"])
            if not path.is_absolute():
                path = Path(artifact_base) / path
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != "kamino451.token-costs.v1" or payload.get("calculator_version") != CALCULATOR_VERSION:
                raise ValueError("unsupported cost artifact schema or calculator version")
            if payload["run_id"] != reference["run_id"]:
                raise ValueError("cost artifact run_id does not match ledger reference")
            if known_at is not None and _timestamp(payload["generated_at"]) > known_at:
                raise ValueError("cost artifact was generated after knowledge cutoff")
            if payload["run_id"] in seen_runs:
                continue
            usage_by_model = {}
            bases = set()
            for step in payload["steps"]:
                if str(Path(step["agent_file"]).resolve()) not in {
                    str(Path(agent_file).resolve()) for agent_file in record["agent_files_used"]
                }:
                    raise ValueError("cost artifact agent does not match ledger attempt")
                if step["status"] == "skipped":
                    continue
                calls = step.get("calls")
                if not calls:
                    raise ValueError("attempt lacks normalized per-call usage")
                for call in calls:
                    if known_at is not None and _timestamp(call["timestamp"]) > known_at:
                        raise ValueError("attempt contains usage after knowledge cutoff")
                    model = call["model_id"]
                    usage = TokenUsage.from_dict(call["usage"])
                    previous = usage_by_model.get(model, TokenUsage(0, 0))
                    usage_by_model[model] = TokenUsage(**{
                        field: getattr(previous, field) + getattr(usage, field)
                        for field in usage.__dataclass_fields__
                    })
                    bases.add(call["basis"])
            if not usage_by_model:
                raise ValueError("skipped-only attempt cannot forecast solver usage")
            distance = abs(Decimal(str(difficulty)) - Decimal(str(record["pairwise_difficulty_score"])))
            observations.append(UsageObservation(
                attempt_id=payload["run_id"], usage_by_model=usage_by_model,
                weight=Decimal(1) / (1 + distance),
                basis=next(iter(bases)) if len(bases) == 1 else "mixed",
            ))
            seen_runs.add(payload["run_id"])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            problems.append(f"{record['record_id']}: {exc}")

    forecast = forecast_usage(observations, min_samples=min_samples)
    evidence = {
        "status": forecast.status, "amount_usd": None,
        "sample_count": forecast.sample_count, "method": forecast.method,
        "basis_counts": dict(forecast.basis_counts), "excluded_attempts": problems,
        "pricing_at": pricing_at.isoformat(),
        "known_at": known_at.isoformat() if known_at is not None else None,
    }
    if forecast.status != "available":
        return {**evidence, "reason": "insufficient comparable usage history"}
    try:
        catalog = load_catalog(config_path)
        legacy = "rate_cards" not in catalog["pricing"]
        if legacy and historical:
            raise ValueError("legacy prices have no historical effective dates")
        estimates = []
        for model_id, usage in forecast.usage_by_model.items():
            rates = resolve_rates(catalog, model_id, pricing_at,
                                  known_at=pricing_at if legacy else known_at,
                                  allow_legacy_current=legacy and not historical)
            if rates.currency != "USD":
                raise ValueError("routing cost_scale is USD; currency conversion is not configured")
            estimates.append(calculate_cost(usage, rates))
        amount = sum((estimate.total_cost for estimate in estimates), Decimal(0))
        return {**evidence, "status": "available", "amount_usd": str(amount.normalize()),
                "estimates": [estimate.to_dict() for estimate in estimates]}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {**evidence, "status": "unavailable", "reason": str(exc)}


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return parsed
