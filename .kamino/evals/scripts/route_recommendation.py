#!/usr/bin/env python3
"""Recommend an agent/model/effort binding from historical success rates, with weighted-majority fallback."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from cost_routing import score_with_costs
from route_cost_estimation import estimate_route_cost

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from task_outcome_ledger_common import (  # noqa: E402 - direct CLI/module loading share this bootstrap
    load_json_file,
    load_ledger_records,
    load_routing_config,
    DEFAULT_COST_POLICY,
    parse_difficulty_placement,
    parse_task_evaluation,
)

RECOMMENDATION_SCHEMA_VERSION = "kamino451.route-recommendation.v2"

# Explicit cold-start policy only; evidence-based scores never use model prices implicitly.
MODEL_LADDER = ["haiku", "sonnet", "opus"]


@dataclass
class ComboStats:
    """Attempt statistics for one (agent blueprints, model, effort) combination."""

    agent_blueprints: tuple[str, ...]
    model: str
    effort: str
    same_type_attempts: int = 0
    same_type_successes: int = 0
    support: float = 0.0
    records: list[dict[str, object]] = field(default_factory=list)

    def same_type_success_rate(self) -> float | None:
        """Return the success rate over same-task-type attempts, or None without attempts."""
        if self.same_type_attempts == 0:
            return None
        return self.same_type_successes / self.same_type_attempts


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Success-rate agent/model/effort recommendation from the outcome ledger.")
    parser.add_argument("--ledger", required=True, help="Path to the task outcome ledger JSONL.")
    parser.add_argument("--task-eval", required=True, help="Path to the current task evaluation JSON.")
    parser.add_argument("--difficulty", required=True, help="Path to the current difficulty placement JSON.")
    parser.add_argument("--config", required=False, help="Path to the central factory config JSON (default: .kamino/factory-config.json).")
    parser.add_argument("--pricing-at", help="ISO timestamp for historical pricing; requires dated rates.")
    parser.add_argument("--known-at", help="ISO knowledge cutoff for historical outcomes and prices.")
    parser.add_argument("--format", choices=["json"], required=True, help="Output format.")
    return parser.parse_args(argv)


def record_weight(task_evaluation: dict[str, object], difficulty: dict[str, object], record: dict[str, object]) -> float:
    """Weight one historical record by task-type match and difficulty proximity."""
    type_weight = 1.0 if record["task_type"] == task_evaluation["task_type"] else 0.3
    current_score = float(str(difficulty["estimated_difficulty_score"]))
    record_score = float(str(record["pairwise_difficulty_score"]))
    proximity = 1.0 / (1.0 + abs(current_score - record_score))
    return type_weight * proximity


def routing_config_payload(routing_config: dict[str, object]) -> dict[str, object]:
    """Echo the routing config values used for this recommendation."""
    return {
        "success_rate_threshold": routing_config["success_rate_threshold"],
        "min_attempts_for_rate": routing_config["min_attempts_for_rate"],
        "config_source": routing_config["config_source"],
        "config_path": routing_config["config_path"],
        "cost": routing_config.get("cost", DEFAULT_COST_POLICY),
    }


def build_combo_stats(
    ledger_records: list[dict[str, object]],
    task_evaluation: dict[str, object],
    difficulty: dict[str, object],
) -> dict[tuple[tuple[str, ...], str, str], ComboStats]:
    """Aggregate per-combination attempt statistics over all ledger records, successes and failures alike."""
    combos: dict[tuple[tuple[str, ...], str, str], ComboStats] = {}
    current_task_type = str(task_evaluation["task_type"])
    for record in ledger_records:
        blueprints = tuple(str(item) for item in record["agent_blueprints_used"])
        model = str(record["model"]).strip()
        effort = str(record["effort"]).strip()
        if len(blueprints) == 0 or model == "" or effort == "":
            continue
        key = (blueprints, model, effort)
        if key not in combos:
            combos[key] = ComboStats(agent_blueprints=blueprints, model=model, effort=effort)
        combo = combos[key]
        combo.records.append(record)
        if str(record["task_type"]) == current_task_type:
            combo.same_type_attempts += 1
            if record["success"] is True:
                combo.same_type_successes += 1
        if record["success"] is True:
            combo.support += record_weight(task_evaluation, difficulty, record)
    return combos


def qualified_combos(
    combos: dict[tuple[tuple[str, ...], str, str], ComboStats],
    routing_config: dict[str, object],
) -> list[ComboStats]:
    """Return combinations whose same-task-type success rate clears the configured threshold.

    Qualification needs at least min_attempts_for_rate same-task-type attempts and a rate
    strictly above success_rate_threshold. Cost is applied after qualification.
    """
    threshold = float(str(routing_config["success_rate_threshold"]))
    min_attempts = int(str(routing_config["min_attempts_for_rate"]))
    return [
        combo
        for combo in combos.values()
        if combo.same_type_attempts >= min_attempts and combo.same_type_successes / combo.same_type_attempts > threshold
    ]


def combo_payload(combo: ComboStats) -> dict[str, object]:
    """Build the auditable payload for one qualified combination."""
    rate = combo.same_type_success_rate()
    return {
        "agent_blueprints": list(combo.agent_blueprints),
        "model": combo.model,
        "effort": combo.effort,
        "same_task_type_attempts": combo.same_type_attempts,
        "same_task_type_successes": combo.same_type_successes,
        "same_task_type_success_rate": round(rate, 6) if rate is not None else None,
    }


def recommend(
    ledger_records: list[dict[str, object]],
    task_evaluation: dict[str, object],
    difficulty: dict[str, object],
    routing_config: dict[str, object],
    cost_estimator=None,
) -> dict[str, object]:
    """Build the recommendation: success-rate policy, then weighted-majority fallback, then cold start."""
    combos = build_combo_stats(ledger_records, task_evaluation, difficulty)
    successful_records_considered = sum(1 for record in ledger_records if record["success"] is True)
    threshold = float(str(routing_config["success_rate_threshold"]))
    min_attempts = int(str(routing_config["min_attempts_for_rate"]))

    base_payload = {
        "schema_version": RECOMMENDATION_SCHEMA_VERSION,
        "task_id": task_evaluation["task_id"],
        "task_type": task_evaluation["task_type"],
        "successful_records_considered": successful_records_considered,
        "routing_config": routing_config_payload(routing_config),
    }

    qualified = qualified_combos(combos, routing_config)
    if len(qualified) > 0:
        ranked, cost_policy = rank_candidates(qualified, routing_config, cost_estimator)
        qualified = [item[0] for item in ranked]
        chosen = qualified[0]
        return {
            **base_payload,
            "recommended_model": chosen.model,
            "recommended_effort": chosen.effort,
            "recommended_agent_blueprints": list(chosen.agent_blueprints),
            "source": "success_rate_policy",
            "selected_combination": combo_payload(chosen),
            "qualified_combinations": [combo_payload(combo) for combo in qualified],
            "cost_policy": cost_policy,
            "candidate_scores": [item[1] for item in ranked],
            "rationale": (
                f"Success-rate policy: agent+model+effort combinations with a success rate above "
                f"{threshold} over at least {min_attempts} same-task-type attempts qualify; "
                "qualifiers are ranked by normalized similarity support minus the optional weighted cost penalty."
            ),
        }

    support: dict[tuple[str, str], float] = {}
    for record in ledger_records:
        if record["success"] is not True:
            continue
        key = (str(record["model"]), str(record["effort"]))
        support[key] = support.get(key, 0.0) + record_weight(task_evaluation, difficulty, record)

    if not support:
        return {
            **base_payload,
            "recommended_model": MODEL_LADDER[0],
            "recommended_effort": "medium",
            "recommended_agent_blueprints": [],
            "source": "cold_start_policy",
            "qualified_combinations": [],
            "support": {},
            "cost_policy": {"status": "not_applicable", "reason": "no historical candidates"},
            "candidate_scores": [],
            "rationale": "No successful historical records; cheap-first escalation policy applies.",
        }

    # Fallback retains model/effort aggregation because blueprint selection is
    # not supported by the success-rate gate. Its usage forecast is explicitly
    # a historical blueprint mixture, not a forecast for a prescribed agent.
    fallback = []
    for (model, effort), weight in support.items():
        fallback.append(ComboStats(
            agent_blueprints=(), model=model, effort=effort, support=weight,
            records=[record for record in ledger_records
                     if record["model"] == model and record["effort"] == effort],
        ))
    ranked, cost_policy = rank_candidates(fallback, routing_config, cost_estimator)
    best_key = (ranked[0][0].model, ranked[0][0].effort)
    return {
        **base_payload,
        "recommended_model": best_key[0],
        "recommended_effort": best_key[1],
        "recommended_agent_blueprints": [],
        "source": "weighted_majority",
        "qualified_combinations": [],
        "support": {f"{model}/{effort}": round(weight, 6) for (model, effort), weight in sorted(support.items())},
        "cost_policy": cost_policy,
        "candidate_scores": [item[1] for item in ranked],
        "rationale": (
            "No combination cleared the success-rate qualification "
            f"(rate above {threshold} over at least {min_attempts} same-task-type attempts). "
            "Weighted majority over successful outcomes: weight = task-type match (1.0 same / 0.3 different) "
            "x 1/(1+|pairwise difficulty distance|), normalized by maximum support; "
            "the optional cost penalty is subtracted, with stable identity tie-breaking."
        ),
    }


def rank_candidates(candidates, routing_config, estimator):
    """Compute the baseline independently, then optionally apply cost evidence."""
    policy = routing_config.get("cost", DEFAULT_COST_POLICY)
    enabled = policy["enabled"] and policy["weight"] > 0
    maximum = max(candidate.support for candidate in candidates)
    baseline = [Decimal(str(candidate.support)) / Decimal(str(maximum))
                if maximum else Decimal(0) for candidate in candidates]
    estimates = []
    for candidate in candidates:
        if not enabled:
            estimates.append({"status": "disabled", "amount_usd": None})
        elif estimator is None:
            estimates.append({"status": "unavailable", "amount_usd": None, "reason": "no estimator"})
        else:
            estimates.append(estimator(candidate))
    costs = [estimate["amount_usd"] if estimate["status"] == "available" else None
             for estimate in estimates]
    scores, penalties = score_with_costs(baseline, costs, weight=policy["weight"] if enabled else 0,
                                         scale_usd=policy["scale_usd"])
    status = "disabled" if not enabled else ("applied" if all(cost is not None for cost in costs) else "unavailable")
    ranked = []
    for candidate, base, score, penalty, estimate in zip(candidates, baseline, scores, penalties, estimates):
        ranked.append((candidate, {
            **combo_payload(candidate), "base_score": str(base), "final_score": str(score),
            "cost_penalty": str(penalty), "cost_estimate": estimate,
            "forecast_scope": "agent_model_effort" if candidate.agent_blueprints else "model_effort_blueprint_mixture",
        }))
    ranked.sort(key=lambda item: (-Decimal(item[1]["final_score"]), item[0].agent_blueprints,
                                 item[0].model, item[0].effort))
    return ranked, {**policy, "status": status,
                    "reason": "all candidates need estimates; otherwise baseline scoring applies"}


def parse_time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("pricing and knowledge timestamps must include a timezone")
    return parsed


def format_json(payload: dict[str, object]) -> str:
    """Render stable JSON."""
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: list[str]) -> int:
    """Run the recommendation CLI."""
    try:
        args = parse_args(argv)
        routing_config = load_routing_config(args.config)
        ledger_path = Path(args.ledger)
        if ledger_path.exists():
            # allow_empty: a present-but-empty ledger is the virgin-factory cold
            # start (the reports pipeline touches the file before any sweep runs).
            ledger_records = load_ledger_records(ledger_path, allow_empty=True)
        else:
            # Cold start: no ledger yet means no history, not an error.
            ledger_records = []
        task_evaluation = parse_task_evaluation(load_json_file(args.task_eval, "task evaluation"))
        difficulty = parse_difficulty_placement(load_json_file(args.difficulty, "difficulty placement"))
        now = datetime.now(timezone.utc)
        pricing_at = parse_time(args.pricing_at) if args.pricing_at else (parse_time(args.known_at) if args.known_at else now)
        known_at = parse_time(args.known_at) if args.known_at else now
        ledger_records = [record for record in ledger_records if parse_time(record["timestamp"]) <= known_at]
        def estimator(combo):
            return estimate_route_cost(
                combo.records, task_type=task_evaluation["task_type"],
                difficulty=difficulty["estimated_difficulty_score"], config_path=routing_config["config_path"],
                artifact_base=ledger_path.resolve().parent, pricing_at=pricing_at, known_at=known_at,
                min_samples=routing_config["cost"]["min_samples"], historical=bool(args.pricing_at or args.known_at),
            )
        print(format_json(recommend(ledger_records, task_evaluation, difficulty, routing_config, estimator)))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
