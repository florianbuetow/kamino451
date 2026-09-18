#!/usr/bin/env python3
"""Reproduce or reprice a saved token-cost artifact without reading transcripts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from cost_estimation import CALCULATOR_VERSION, CostEstimate, RateCard, TokenUsage, calculate_cost
from pricing_catalog import PricingCatalogError, load_catalog, resolve_rates


SCHEMA_VERSION = "kamino451.token-cost-evaluation.v1"


def timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"invalid ISO-8601 timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SystemExit(f"timestamp must be timezone-aware: {value}")
    return parsed


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    subparsers = root.add_subparsers(dest="mode", required=True)
    reproduce = subparsers.add_parser("reproduce", help="Recalculate from saved usage and rate snapshots.")
    reprice = subparsers.add_parser("reprice", help="Apply a supplied versioned catalog to saved usage.")
    for command in (reproduce, reprice):
        command.add_argument("--input", required=True, help="Existing token_costs.json artifact.")
        command.add_argument("--output", required=True, help="Separate output artifact path.")
        command.add_argument("--format", choices=["json"], required=True)
    reprice.add_argument("--catalog", required=True, help="Versioned pricing catalog.")
    reprice.add_argument(
        "--pricing-at",
        help="Optional ISO timestamp applied to every call. By default each call's timestamp is used.",
    )
    reprice.add_argument(
        "--known-at",
        help="Optional ISO knowledge cutoff; excludes rates recorded after this timestamp.",
    )
    return root


def load_source(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot load cost artifact {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("steps"), list):
        raise SystemExit(f"cost artifact has no steps array: {path}")
    if payload.get("calculator_version") != CALCULATOR_VERSION:
        raise SystemExit(f"unsupported calculator_version: {payload.get('calculator_version')!r}")
    for index, step in enumerate(payload["steps"]):
        if not isinstance(step, dict) or not isinstance(step.get("calls"), list):
            raise SystemExit(f"cost artifact step {index} has no normalized calls array")
    return payload


def evaluate_call(call: dict, *, catalog: dict | None, pricing_at: datetime | None, known_at: datetime | None) -> dict:
    try:
        usage = TokenUsage.from_dict(call["usage"])
        source_rates = RateCard.from_dict(call["rate_card"])
        if source_rates.currency != "USD":
            raise ValueError(
                f"cost evaluation writes cost_usd and cannot apply {source_rates.currency} rates"
            )
        if str(call["model_id"]) != source_rates.model_id:
            raise ValueError(
                f"call model_id {call['model_id']!r} does not match rate snapshot "
                f"model_id {source_rates.model_id!r}"
            )
        saved_estimate = CostEstimate.from_dict(call["cost_estimate"])
        if saved_estimate.usage != usage or saved_estimate.rate_card != source_rates:
            raise ValueError("call usage/rate snapshot disagrees with its saved cost estimate")
        if catalog is None:
            rates = source_rates
        else:
            effective_at = pricing_at or timestamp(str(call["timestamp"]))
            rates = resolve_rates(
                catalog,
                str(call["model_id"]),
                effective_at,
                known_at=known_at,
                billing_mode=source_rates.billing_mode,
                provider=source_rates.provider,
            )
            if rates.currency != "USD":
                raise ValueError(
                    f"cost evaluation writes cost_usd and cannot apply {rates.currency} rates"
                )
        estimate = calculate_cost(usage, rates)
    except (KeyError, TypeError, ValueError, PricingCatalogError) as exc:
        raise SystemExit(f"cannot evaluate call {call.get('call_id', '<unknown>')}: {exc}") from exc
    return {
        "call_id": str(call["call_id"]),
        "model_id": str(call["model_id"]),
        "timestamp": str(call["timestamp"]),
        "basis": str(call["basis"]),
        "usage": usage.to_dict(),
        "rate_card": rates.to_dict(),
        "cost_estimate": estimate.to_dict(),
    }


def legacy_cost(calls: list[dict]) -> dict:
    def amount(call: dict, field: str) -> Decimal:
        return Decimal(call["cost_estimate"]["cost"][field])

    input_cost = sum((amount(call, "input_cost") for call in calls), Decimal(0))
    output_cost = sum((amount(call, "output_cost") for call in calls), Decimal(0))
    input_usd = round(float(input_cost), 6)
    output_usd = round(float(output_cost), 6)
    by_model: dict[str, float] = {}
    for call in calls:
        model_id = call["model_id"]
        by_model[model_id] = round(by_model.get(model_id, 0.0) + float(amount(call, "total_cost")), 6)
    return {
        "input": input_usd,
        "output": output_usd,
        "total": round(input_usd + output_usd, 6),
        "by_model": by_model,
    }


def aggregate_step_costs(steps: list[dict]) -> dict:
    by_model: dict[str, float] = {}
    for step in steps:
        for model_id, amount in step["cost_usd"]["by_model"].items():
            by_model[model_id] = round(by_model.get(model_id, 0.0) + amount, 6)
    return {
        "input": round(sum(step["cost_usd"]["input"] for step in steps), 6),
        "output": round(sum(step["cost_usd"]["output"] for step in steps), 6),
        "total": round(sum(step["cost_usd"]["total"] for step in steps), 6),
        "by_model": by_model,
    }


def main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    same_file = input_path == output_path
    if not same_file and output_path.exists():
        try:
            same_file = input_path.samefile(output_path)
        except OSError:
            same_file = False
    if same_file:
        raise SystemExit("output must be separate from the source cost artifact")
    source = load_source(input_path)
    catalog = None
    pricing_at = None
    known_at = None
    if args.mode == "reprice":
        try:
            catalog = load_catalog(args.catalog)
        except PricingCatalogError as exc:
            raise SystemExit(str(exc)) from exc
        pricing_at = timestamp(args.pricing_at) if args.pricing_at else None
        known_at = timestamp(args.known_at) if args.known_at else None

    steps = []
    for source_step in source["steps"]:
        calls = [
            evaluate_call(call, catalog=catalog, pricing_at=pricing_at, known_at=known_at)
            for call in source_step["calls"]
        ]
        steps.append(
            {
                key: source_step[key]
                for key in ("step", "attempt", "agent_file", "model", "status")
                if key in source_step
            }
            | {"calls": calls, "cost_usd": legacy_cost(calls)}
        )

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "calculator_version": CALCULATOR_VERSION,
        "mode": args.mode,
        "source_artifact": str(input_path),
        "source_run_id": source.get("run_id"),
        "generated_at": generated_at,
        "pricing_catalog": str(Path(args.catalog).resolve()) if args.mode == "reprice" else None,
        "pricing_at": args.pricing_at if args.mode == "reprice" else None,
        "known_at": args.known_at if args.mode == "reprice" else None,
        "steps": steps,
        "totals": {"cost_usd": aggregate_step_costs(steps)},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output_path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise SystemExit(f"refusing to overwrite existing cost evaluation artifact: {output_path}") from exc
    print(json.dumps({"status": "ok", "output": str(output_path), "total_usd": payload["totals"]["cost_usd"]["total"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
