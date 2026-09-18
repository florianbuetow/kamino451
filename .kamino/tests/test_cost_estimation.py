"""Tests for the pure token-cost domain."""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "evals" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cost_estimation import CostEstimate, RateCard, TokenUsage, calculate_cost  # noqa: E402


def rate_card(**overrides) -> RateCard:
    values = {
        "model_id": "provider-model-v1",
        "pricing_version": "prices-2026-01",
        "pricing_hash": "sha256:abc",
        "currency": "USD",
        "input_per_mtok": "2",
        "output_per_mtok": "10",
        "cache_read_per_mtok": "0.2",
        "cache_write_5m_per_mtok": "2.5",
        "cache_write_1h_per_mtok": "4",
        "effective_from": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "recorded_at": datetime(2025, 12, 20, tzinfo=timezone.utc),
        "source": "provider price sheet",
    }
    values.update(overrides)
    return RateCard(**values)


def test_calculate_cost_is_exact_and_itemized():
    usage = TokenUsage(
        input_tokens="1000000.5",
        output_tokens=200_000,
        cache_read_input_tokens=500_000,
        cache_write_5m_input_tokens=400_000,
        cache_write_1h_input_tokens=250_000,
    )

    estimate = calculate_cost(usage, rate_card())

    assert estimate.uncached_input_cost == Decimal("2.0000010")
    assert estimate.cache_read_input_cost == Decimal("0.1")
    assert estimate.cache_write_5m_input_cost == Decimal("1.0")
    assert estimate.cache_write_1h_input_cost == Decimal("1")
    assert estimate.input_cost == Decimal("4.1000010")
    assert estimate.output_cost == Decimal("2")
    assert estimate.total_cost == Decimal("6.1000010")


def test_zero_rates_are_valid_and_have_zero_cost():
    rates = rate_card(
        input_per_mtok=0,
        output_per_mtok=0,
        cache_read_per_mtok=0,
        cache_write_5m_per_mtok=0,
        cache_write_1h_per_mtok=0,
    )

    estimate = calculate_cost(TokenUsage(10, 20, 30, 40, 50), rates)

    assert estimate.total_cost == Decimal(0)


@pytest.mark.parametrize("value", [-1, "NaN", "Infinity", float("inf")])
def test_usage_rejects_negative_or_nonfinite_values(value):
    with pytest.raises(ValueError):
        TokenUsage(value, 0)


@pytest.mark.parametrize("value", [-1, "NaN", "Infinity", float("inf")])
def test_rates_reject_negative_or_nonfinite_values(value):
    with pytest.raises(ValueError):
        rate_card(input_per_mtok=value)


def test_values_are_immutable_and_json_roundtrip_is_lossless():
    estimate = calculate_cost(TokenUsage("1.125", "2.25"), rate_card())

    restored = CostEstimate.from_dict(estimate.to_dict())

    assert restored == estimate
    assert restored.to_dict()["usage"]["input_tokens"] == "1.125"
    with pytest.raises(FrozenInstanceError):
        restored.total_cost = Decimal(0)  # type: ignore[misc]


def test_rate_card_rejects_naive_timestamps_and_empty_identity():
    with pytest.raises(ValueError, match="timezone-aware"):
        rate_card(recorded_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="model_id"):
        rate_card(model_id="")

