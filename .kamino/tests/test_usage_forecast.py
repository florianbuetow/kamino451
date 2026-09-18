"""Tests for the pure historical token-usage forecaster."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "evals" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cost_estimation import TokenUsage  # noqa: E402
from usage_forecast import UsageObservation, forecast_usage  # noqa: E402


def usage(
    input_tokens: object = 0,
    output_tokens: object = 0,
    cache_read: object = 0,
    cache_write_5m: object = 0,
    cache_write_1h: object = 0,
) -> TokenUsage:
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
        cache_write_5m_input_tokens=cache_write_5m,
        cache_write_1h_input_tokens=cache_write_1h,
    )


def test_forecast_is_weighted_by_attempt_similarity_and_preserves_cache_ttls():
    observations = [
        UsageObservation("success", {"model-a": usage(10, 20, 30, 40, 50)}, weight=1),
        UsageObservation("failed", {"model-a": usage(40, 50, 60, 70, 80)}, weight=2),
        UsageObservation("another", {"model-a": usage(70, 80, 90, 100, 110)}, weight=1),
    ]

    result = forecast_usage(observations)

    assert result.status == "available"
    assert result.sample_count == 3
    assert result.method == "similarity_weighted_mean"
    assert result.basis_counts == {"measured": 3}
    assert result.usage_by_model == {"model-a": usage(40, 50, 60, 70, 80)}


def test_sample_count_is_attempts_not_underlying_calls_or_models():
    # Each usage value is already the normalized sum of any calls in that attempt.
    observations = [
        UsageObservation("attempt-1", {"model-a": usage(30), "model-b": usage(60)}),
        UsageObservation("attempt-2", {"model-a": usage(60), "model-b": usage(90)}),
    ]

    result = forecast_usage(observations, min_samples=3)

    assert result.status == "unavailable"
    assert result.sample_count == 2
    assert result.usage_by_model is None


def test_model_usage_is_separate_and_absence_in_an_attempt_counts_as_zero():
    observations = [
        UsageObservation("attempt-1", {"model-a": usage(30)}),
        UsageObservation("attempt-2", {"model-b": usage(60)}),
        UsageObservation("attempt-3", {"model-a": usage(60), "model-b": usage(30)}),
    ]

    result = forecast_usage(observations)

    assert result.usage_by_model == {
        "model-a": usage(30),
        "model-b": usage(30),
    }


def test_no_history_is_unknown_while_real_zero_usage_is_available():
    unavailable = forecast_usage([], min_samples=1)
    zero = forecast_usage(
        [UsageObservation("a", {"model-a": usage(0)}), UsageObservation("b", {"model-a": usage(0)})],
        min_samples=2,
    )

    assert unavailable.status == "unavailable"
    assert unavailable.usage_by_model is None
    assert zero.status == "available"
    assert zero.usage_by_model == {"model-a": usage(0)}


def test_fractional_weighted_forecast_is_not_rounded_to_integer_tokens():
    result = forecast_usage(
        [
            UsageObservation("a", {"model-a": usage(1)}, weight=Decimal("1")),
            UsageObservation("b", {"model-a": usage(2)}, weight=Decimal("1")),
        ],
        min_samples=2,
    )

    assert result.usage_by_model == {"model-a": usage(Decimal("1.5"))}


@pytest.mark.parametrize("bad_weight", [-1, 0, Decimal("NaN"), Decimal("Infinity")])
def test_invalid_similarity_weights_are_rejected(bad_weight):
    with pytest.raises(ValueError, match="finite positive"):
        UsageObservation("attempt", {"model-a": usage(1)}, weight=bad_weight)


def test_basis_counts_describe_attempts_even_when_forecast_is_unavailable():
    result = forecast_usage(
        [
            UsageObservation("a", {"model-a": usage(1)}, basis="measured"),
            UsageObservation("b", {"model-a": usage(2)}, basis="estimated"),
        ],
        min_samples=3,
    )

    assert result.basis_counts == {"measured": 1, "estimated": 1}


def test_duplicate_attempts_cannot_inflate_sample_count():
    duplicate = [
        UsageObservation("same", {"model-a": usage(1)}),
        UsageObservation("same", {"model-a": usage(2)}),
    ]

    with pytest.raises(ValueError, match="unique attempt IDs"):
        forecast_usage(duplicate, min_samples=2)


@pytest.mark.parametrize("count", [3, 7, 11])
def test_identical_attempts_retain_exact_usage(count):
    result = forecast_usage([
        UsageObservation(str(index), {"model-a": usage(60000)}) for index in range(count)
    ])
    assert result.usage_by_model == {"model-a": usage(60000)}
