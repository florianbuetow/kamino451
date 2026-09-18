"""Tests for dated model pricing resolution."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "evals" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from pricing_catalog import PricingCatalogError, load_catalog, resolve_rates  # noqa: E402


UTC = timezone.utc


def at(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def entry(version: str, start: str, end: str | None, *, recorded_at: str, input_rate: str = "1") -> dict:
    return {
        "model_id": "exact-model-id",
        "pricing_version": version,
        "pricing_hash": f"sha256:{version}",
        "effective_from": start,
        "effective_to": end,
        "recorded_at": recorded_at,
        "input_per_mtok": input_rate,
        "output_per_mtok": "5",
        "cache_read_per_mtok": "0.1",
        "cache_write_5m_per_mtok": "1.25",
        "cache_write_1h_per_mtok": "2",
    }


def catalog(*entries: dict) -> dict:
    return {
        "pricing": {
            "currency": "USD",
            "billing_mode": "standard",
            "source": "archived provider price sheet",
            "rate_cards": list(entries),
        }
    }


def test_snapshot_hash_tracks_rates_even_if_catalog_reuses_a_hash_label():
    value = catalog(entry("v1", "2026-01-01T00:00:00Z", None,
                          recorded_at="2025-12-20T00:00:00Z"))
    original = resolve_rates(value, "exact-model-id", at(2026, 6, 30))
    value["pricing"]["rate_cards"][0]["input_per_mtok"] = "2"
    changed = resolve_rates(value, "exact-model-id", at(2026, 6, 30))
    assert original.pricing_hash != changed.pricing_hash
    assert original.input_per_mtok == Decimal("1")


def test_versioned_resolution_uses_half_open_date_boundaries():
    value = catalog(
        entry("v1", "2026-01-01T00:00:00Z", "2026-07-01T00:00:00Z", recorded_at="2025-12-20T00:00:00Z"),
        entry("v2", "2026-07-01T00:00:00Z", None, recorded_at="2026-06-20T00:00:00Z", input_rate="2"),
    )

    before = resolve_rates(value, "exact-model-id", at(2026, 6, 30))
    boundary = resolve_rates(value, "exact-model-id", at(2026, 7, 1))

    assert before.pricing_version == "v1"
    assert boundary.pricing_version == "v2"
    assert boundary.input_per_mtok == Decimal("2")


def test_known_at_excludes_prices_recorded_in_the_future():
    value = catalog(
        entry("late-record", "2026-01-01T00:00:00Z", None, recorded_at="2026-08-01T00:00:00Z")
    )

    with pytest.raises(PricingCatalogError, match="no price"):
        resolve_rates(value, "exact-model-id", at(2026, 5, 1), known_at=at(2026, 5, 1))
    assert resolve_rates(value, "exact-model-id", at(2026, 5, 1), known_at=at(2026, 9, 1)).pricing_version == "late-record"


def test_overlapping_rates_fail_instead_of_guessing():
    value = catalog(
        entry("v1", "2026-01-01T00:00:00Z", None, recorded_at="2025-12-20T00:00:00Z"),
        entry("v2", "2026-05-01T00:00:00Z", None, recorded_at="2026-04-20T00:00:00Z"),
    )

    with pytest.raises(PricingCatalogError, match="ambiguous overlapping"):
        resolve_rates(value, "exact-model-id", at(2026, 6, 1))


def test_unknown_model_and_naive_timestamp_fail_explicitly():
    value = catalog(entry("v1", "2026-01-01T00:00:00Z", None, recorded_at="2025-12-20T00:00:00Z"))

    with pytest.raises(PricingCatalogError, match="no price"):
        resolve_rates(value, "other-model", at(2026, 2, 1))
    with pytest.raises(PricingCatalogError, match="timezone-aware"):
        resolve_rates(value, "exact-model-id", datetime(2026, 2, 1))


def test_legacy_alias_resolution_requires_explicit_current_snapshot_and_falls_back_cache_rates():
    value = {
        "pricing": {
            "currency": "USD",
            "models": {
                "haiku": {
                    "model_ids": ["claude-haiku-exact"],
                    "input_per_mtok": 1,
                    "output_per_mtok": 5,
                }
            },
        }
    }
    now = at(2026, 9, 17)

    with pytest.raises(PricingCatalogError, match="explicit current snapshot"):
        resolve_rates(value, "claude-haiku-exact", now)
    rates = resolve_rates(value, "haiku", now, known_at=now, allow_legacy_current=True)

    assert rates.model_id == "haiku"
    assert rates.cache_read_per_mtok == rates.input_per_mtok == Decimal("1")
    assert rates.cache_write_5m_per_mtok == Decimal("1")
    assert rates.cache_write_1h_per_mtok == Decimal("1")
    assert rates.pricing_hash.startswith("sha256:")


def test_duplicate_legacy_aliases_and_partial_cache_rates_are_invalid():
    duplicated = {
        "pricing": {
            "models": {
                "a": {"model_ids": ["same"], "input_per_mtok": 1, "output_per_mtok": 2},
                "b": {"model_ids": ["same"], "input_per_mtok": 1, "output_per_mtok": 2},
            }
        }
    }
    now = at(2026, 9, 17)
    with pytest.raises(PricingCatalogError, match="belongs to both"):
        resolve_rates(duplicated, "same", now, known_at=now, allow_legacy_current=True)

    partial = catalog(entry("v1", "2026-01-01T00:00:00Z", None, recorded_at="2025-12-20T00:00:00Z"))
    del partial["pricing"]["rate_cards"][0]["cache_write_1h_per_mtok"]
    with pytest.raises(PricingCatalogError, match="must set all"):
        resolve_rates(partial, "exact-model-id", at(2026, 2, 1))


def test_load_catalog_reads_factory_wrapper_and_rate_card_snapshot_restores_without_catalog(tmp_path):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(catalog(entry(
        "v1", "2026-01-01T00:00:00Z", None, recorded_at="2025-12-20T00:00:00Z"
    ))), encoding="utf-8")

    loaded = load_catalog(path)
    snapshot = resolve_rates(loaded, "exact-model-id", at(2026, 2, 1)).to_dict()

    from cost_estimation import RateCard
    assert RateCard.from_dict(snapshot).to_dict() == snapshot


def test_billing_modes_are_resolved_independently():
    standard = entry("standard", "2026-01-01T00:00:00Z", None, recorded_at="2025-12-20T00:00:00Z")
    batch = entry("batch", "2026-01-01T00:00:00Z", None, recorded_at="2025-12-20T00:00:00Z")
    batch["billing_mode"] = "batch"
    batch["input_per_mtok"] = "0.5"

    resolved = resolve_rates(catalog(standard, batch), "exact-model-id", at(2026, 2, 1), billing_mode="batch")

    assert resolved.billing_mode == "batch"
    assert resolved.input_per_mtok == Decimal("0.5")


def test_invalid_interval_fails_even_when_entry_would_not_be_selected():
    invalid_other_model = entry(
        "bad", "2026-02-01T00:00:00Z", "2026-01-01T00:00:00Z", recorded_at="2025-12-20T00:00:00Z"
    )
    invalid_other_model["model_id"] = "other-model"

    with pytest.raises(PricingCatalogError, match="must be later"):
        resolve_rates(catalog(invalid_other_model), "exact-model-id", at(2026, 2, 1))


def test_provider_selector_distinguishes_same_model_and_billing_mode():
    first = entry("provider-a", "2026-01-01T00:00:00Z", None, recorded_at="2025-12-20T00:00:00Z")
    first["provider"] = "provider-a"
    second = entry("provider-b", "2026-01-01T00:00:00Z", None, recorded_at="2025-12-20T00:00:00Z")
    second["provider"] = "provider-b"

    resolved = resolve_rates(
        catalog(first, second), "exact-model-id", at(2026, 2, 1), provider="provider-b"
    )

    assert resolved.provider == "provider-b"
    with pytest.raises(PricingCatalogError, match="no price"):
        resolve_rates(catalog(first), "exact-model-id", at(2026, 2, 1), provider="provider-c")
