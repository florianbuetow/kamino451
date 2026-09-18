"""Load and resolve exact, reproducible model pricing snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from cost_estimation import RateCard


RATE_KEYS = (
    "input_per_mtok",
    "output_per_mtok",
    "cache_read_per_mtok",
    "cache_write_5m_per_mtok",
    "cache_write_1h_per_mtok",
)
CACHE_RATE_KEYS = RATE_KEYS[2:]


class PricingCatalogError(ValueError):
    """Catalog is invalid or cannot answer the requested pricing question."""


def load_catalog(path: str | Path) -> dict[str, Any]:
    """Load JSON once; rate lookup itself remains pure and has no file access."""

    catalog_path = Path(path)
    try:
        value = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PricingCatalogError(f"cannot load pricing catalog {catalog_path}: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("pricing"), dict):
        raise PricingCatalogError("catalog must contain a pricing object")
    return value


def _timestamp(value: Any, field: str, *, required: bool = False) -> datetime | None:
    if value is None:
        if required:
            raise PricingCatalogError(f"{field} is required")
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PricingCatalogError(f"{field} must be an ISO-8601 timestamp") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PricingCatalogError(f"{field} must be timezone-aware")
    return value


def _hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _rates(entry: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in RATE_KEYS[:2] if key not in entry]
    if missing:
        raise PricingCatalogError(f"pricing entry missing {', '.join(missing)}")
    present_cache = [key for key in CACHE_RATE_KEYS if key in entry]
    if present_cache and len(present_cache) != len(CACHE_RATE_KEYS):
        raise PricingCatalogError(f"pricing entry must set all of {CACHE_RATE_KEYS} or none")
    result = {key: entry[key] for key in RATE_KEYS[:2]}
    if present_cache:
        result.update({key: entry[key] for key in CACHE_RATE_KEYS})
    else:
        result.update({key: entry["input_per_mtok"] for key in CACHE_RATE_KEYS})
    return result


def _rate_card(entry: Mapping[str, Any], *, model_id: str, defaults: Mapping[str, Any]) -> RateCard:
    rates = _rates(entry)
    snapshot = {**defaults, **entry, **rates, "model_id": model_id}
    # Identity follows the actual contents, even when a caller reuses a label.
    snapshot.pop("pricing_hash", None)
    return RateCard(
        model_id=model_id,
        pricing_version=snapshot["pricing_version"],
        pricing_hash=_hash(snapshot),
        currency=snapshot["currency"],
        provider=snapshot.get("provider", "anthropic"),
        billing_mode=snapshot.get("billing_mode", "standard"),
        effective_from=snapshot.get("effective_from"),
        effective_to=snapshot.get("effective_to"),
        recorded_at=snapshot.get("recorded_at"),
        source=snapshot.get("source"),
        **rates,
    )


def _resolve_legacy(
    pricing: Mapping[str, Any],
    model_id: str,
    pricing_at: datetime,
    known_at: datetime | None,
    billing_mode: str,
    provider: str | None,
    allow_legacy_current: bool,
) -> RateCard:
    if not allow_legacy_current or known_at is None or pricing_at != known_at:
        raise PricingCatalogError(
            "legacy pricing has no effective dates; resolve it only as an explicit current "
            "snapshot by passing allow_legacy_current=True and known_at equal to pricing_at"
        )
    configured_mode = pricing.get("billing_mode", "standard")
    if configured_mode != billing_mode:
        raise PricingCatalogError(f"legacy pricing has no {billing_mode!r} billing mode")
    configured_provider = pricing.get("provider", "anthropic")
    if provider is not None and configured_provider != provider:
        raise PricingCatalogError(f"legacy pricing has no {provider!r} provider")
    models = pricing.get("models")
    if not isinstance(models, dict):
        raise PricingCatalogError("legacy pricing.models must be an object")
    matches: list[tuple[str, Mapping[str, Any]]] = []
    aliases: dict[str, str] = {}
    for name, entry in models.items():
        if not isinstance(entry, dict):
            raise PricingCatalogError(f"pricing model {name!r} must be an object")
        model_ids = entry.get("model_ids", [])
        if not isinstance(model_ids, list) or not model_ids or not all(isinstance(item, str) and item for item in model_ids):
            raise PricingCatalogError(f"pricing model {name!r} needs non-empty string model_ids")
        for alias in [name, *model_ids]:
            owner = aliases.setdefault(alias, name)
            if owner != name:
                raise PricingCatalogError(f"pricing alias {alias!r} belongs to both {owner!r} and {name!r}")
        if model_id == name or model_id in model_ids:
            matches.append((name, entry))
    if len(matches) != 1:
        raise PricingCatalogError(f"expected one legacy pricing entry for {model_id!r}, found {len(matches)}")
    _, entry = matches[0]
    defaults = {
        "pricing_version": pricing.get("pricing_version", "legacy-current"),
        "currency": pricing.get("currency", "USD"),
        "provider": configured_provider,
        "billing_mode": configured_mode,
        "recorded_at": known_at,
        "source": pricing.get("source"),
    }
    return _rate_card(entry, model_id=model_id, defaults=defaults)


def resolve_rates(
    catalog: Mapping[str, Any],
    model_id: str,
    pricing_at: datetime,
    known_at: datetime | None = None,
    *,
    billing_mode: str = "standard",
    provider: str | None = None,
    allow_legacy_current: bool = False,
) -> RateCard:
    """Resolve one exact model at a time and under a caller-supplied knowledge cutoff.

    Canonical versioned format uses ``pricing.rate_cards``.  Intervals are
    half-open: effective_from <= pricing_at < effective_to.  Entries learned
    after known_at are excluded, enabling honest historical backtests.
    """

    pricing_at = _timestamp(pricing_at, "pricing_at", required=True)  # type: ignore[assignment]
    known_at = _timestamp(known_at, "known_at")
    if not isinstance(model_id, str) or not model_id:
        raise PricingCatalogError("model_id must be a non-empty exact model id")
    if not isinstance(billing_mode, str) or not billing_mode:
        raise PricingCatalogError("billing_mode must be a non-empty string")
    if provider is not None and (not isinstance(provider, str) or not provider):
        raise PricingCatalogError("provider must be a non-empty string when provided")
    pricing = catalog.get("pricing")
    if not isinstance(pricing, dict):
        raise PricingCatalogError("catalog must contain a pricing object")
    if "rate_cards" not in pricing:
        return _resolve_legacy(
            pricing, model_id, pricing_at, known_at, billing_mode, provider, allow_legacy_current
        )
    entries = pricing["rate_cards"]
    if not isinstance(entries, list):
        raise PricingCatalogError("pricing.rate_cards must be an array")
    defaults = {
        key: pricing[key]
        for key in ("currency", "provider", "billing_mode", "source")
        if key in pricing
    }
    matches: list[RateCard] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise PricingCatalogError(f"rate_cards[{index}] must be an object")
        start = _timestamp(entry.get("effective_from"), f"rate_cards[{index}].effective_from", required=True)
        end = _timestamp(entry.get("effective_to"), f"rate_cards[{index}].effective_to")
        recorded = _timestamp(entry.get("recorded_at"), f"rate_cards[{index}].recorded_at", required=True)
        if end is not None and end <= start:
            raise PricingCatalogError(f"rate_cards[{index}].effective_to must be later than effective_from")
        entry_mode = entry.get("billing_mode", pricing.get("billing_mode", "standard"))
        entry_provider = entry.get("provider", pricing.get("provider", "anthropic"))
        if (entry.get("model_id") != model_id or entry_mode != billing_mode
                or (provider is not None and entry_provider != provider)):
            continue
        if start <= pricing_at and (end is None or pricing_at < end) and (known_at is None or recorded <= known_at):
            try:
                matches.append(_rate_card(entry, model_id=model_id, defaults=defaults))
            except (KeyError, ValueError) as exc:
                raise PricingCatalogError(f"invalid rate_cards[{index}]: {exc}") from exc
    if not matches:
        qualifier = f" known by {known_at.isoformat()}" if known_at else ""
        raise PricingCatalogError(f"no price for {model_id!r} at {pricing_at.isoformat()}{qualifier}")
    if len(matches) > 1:
        raise PricingCatalogError(f"ambiguous overlapping prices for {model_id!r} at {pricing_at.isoformat()}")
    return matches[0]
