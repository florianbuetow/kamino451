#!/usr/bin/env python3
"""Recommend an agent/model/effort binding from historical success rates, with weighted-majority fallback."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from task_outcome_ledger_common import (
    load_json_file,
    load_ledger_records,
    load_routing_config,
    parse_difficulty_placement,
    parse_task_evaluation,
)

RECOMMENDATION_SCHEMA_VERSION = "kamino451.route-recommendation.v2"

# Cheap-first order used to rank qualified combinations, break weight ties, and seed cold starts.
MODEL_LADDER = ["haiku", "sonnet", "opus"]
EFFORT_LADDER = ["low", "medium", "high"]


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
    parser.add_argument("--format", choices=["json"], required=True, help="Output format.")
    return parser.parse_args(argv)


def record_weight(task_evaluation: dict[str, object], difficulty: dict[str, object], record: dict[str, object]) -> float:
    """Weight one historical record by task-type match and difficulty proximity."""
    type_weight = 1.0 if record["task_type"] == task_evaluation["task_type"] else 0.3
    current_score = float(str(difficulty["estimated_difficulty_score"]))
    record_score = float(str(record["pairwise_difficulty_score"]))
    proximity = 1.0 / (1.0 + abs(current_score - record_score))
    return type_weight * proximity


def ladder_rank(model: str) -> int:
    """Rank a model on the cheap-first ladder; unknown models sort last."""
    return MODEL_LADDER.index(model) if model in MODEL_LADDER else len(MODEL_LADDER)


def effort_rank(effort: str) -> int:
    """Rank an effort on the cheap-first ladder; unknown efforts sort last."""
    return EFFORT_LADDER.index(effort) if effort in EFFORT_LADDER else len(EFFORT_LADDER)


def routing_config_payload(routing_config: dict[str, object]) -> dict[str, object]:
    """Echo the routing config values used for this recommendation."""
    return {
        "success_rate_threshold": routing_config["success_rate_threshold"],
        "min_attempts_for_rate": routing_config["min_attempts_for_rate"],
        "config_source": routing_config["config_source"],
        "config_path": routing_config["config_path"],
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
    strictly above success_rate_threshold. Qualifiers are ranked cheap-first (model ladder,
    then effort ladder, then similarity support) — deliberately NOT by highest rate.
    """
    threshold = float(str(routing_config["success_rate_threshold"]))
    min_attempts = int(str(routing_config["min_attempts_for_rate"]))
    qualified = [
        combo
        for combo in combos.values()
        if combo.same_type_attempts >= min_attempts and combo.same_type_successes / combo.same_type_attempts > threshold
    ]
    qualified.sort(
        key=lambda combo: (
            ladder_rank(combo.model),
            effort_rank(combo.effort),
            -combo.support,
            combo.agent_blueprints,
            combo.effort,
        ),
    )
    return qualified


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
        chosen = qualified[0]
        return {
            **base_payload,
            "recommended_model": chosen.model,
            "recommended_effort": chosen.effort,
            "recommended_agent_blueprints": list(chosen.agent_blueprints),
            "source": "success_rate_policy",
            "selected_combination": combo_payload(chosen),
            "qualified_combinations": [combo_payload(combo) for combo in qualified],
            "rationale": (
                f"Success-rate policy: agent+model+effort combinations with a success rate above "
                f"{threshold} over at least {min_attempts} same-task-type attempts qualify; "
                "qualifiers are ranked cheap-first (model ladder, then effort, then similarity support), "
                "not by highest rate."
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
            "rationale": "No successful historical records; cheap-first escalation policy applies.",
        }

    best_key = min(
        support,
        key=lambda key: (-support[key], ladder_rank(key[0]), key[1]),
    )
    return {
        **base_payload,
        "recommended_model": best_key[0],
        "recommended_effort": best_key[1],
        "recommended_agent_blueprints": [],
        "source": "weighted_majority",
        "qualified_combinations": [],
        "support": {f"{model}/{effort}": round(weight, 6) for (model, effort), weight in sorted(support.items())},
        "rationale": (
            "No combination cleared the success-rate qualification "
            f"(rate above {threshold} over at least {min_attempts} same-task-type attempts). "
            "Weighted majority over successful outcomes: weight = task-type match (1.0 same / 0.3 different) "
            "x 1/(1+|pairwise difficulty distance|); ties break cheap-first."
        ),
    }


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
        print(format_json(recommend(ledger_records, task_evaluation, difficulty, routing_config)))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
