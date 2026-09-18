"""Forecast token usage from comparable whole-attempt observations.

This module deliberately knows nothing about prices, providers, persistence, or
routing.  Callers select comparable attempts and assign their similarity
weights; this unit only computes a deterministic weighted usage forecast.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Mapping

from cost_estimation import TokenUsage


@dataclass(frozen=True)
class UsageObservation:
    """Normalized usage for one complete attempt.

    ``usage_by_model`` may contain several exact model IDs when an attempt used
    more than one model.  A model missing from an observation is treated as zero
    usage for that attempt, preserving expected total spend across attempts.
    """

    attempt_id: str
    usage_by_model: Mapping[str, TokenUsage]
    weight: Decimal = Decimal("1")
    basis: str = "measured"

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, str) or not self.attempt_id.strip():
            raise ValueError("attempt_id must be a non-empty string")
        if not isinstance(self.basis, str) or not self.basis.strip():
            raise ValueError("basis must be a non-empty string")
        if not isinstance(self.usage_by_model, Mapping):
            raise TypeError("usage_by_model must be a mapping")

        normalized_usage: dict[str, TokenUsage] = {}
        for model_id, usage in self.usage_by_model.items():
            if not isinstance(model_id, str) or not model_id.strip():
                raise ValueError("model IDs must be non-empty strings")
            if not isinstance(usage, TokenUsage):
                raise TypeError("usage_by_model values must be TokenUsage instances")
            normalized_usage[model_id] = usage

        try:
            weight = Decimal(str(self.weight))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("weight must be a finite positive number") from exc
        if not weight.is_finite() or weight <= 0:
            raise ValueError("weight must be a finite positive number")

        object.__setattr__(self, "usage_by_model", normalized_usage)
        object.__setattr__(self, "weight", weight)


@dataclass(frozen=True)
class UsageForecast:
    """A forecast or an explicit indication that evidence is insufficient."""

    status: str
    usage_by_model: Mapping[str, TokenUsage] | None
    sample_count: int
    method: str
    basis_counts: Mapping[str, int] = field(default_factory=dict)


def forecast_usage(
    observations: list[UsageObservation],
    min_samples: int = 3,
) -> UsageForecast:
    """Return similarity-weighted mean usage for each exact model ID.

    The denominator is the total weight of all attempts, including attempts in
    which a particular model was absent.  The sample count is therefore a count
    of attempts, never API calls or model entries.
    """

    if isinstance(min_samples, bool) or not isinstance(min_samples, int) or min_samples <= 0:
        raise ValueError("min_samples must be a positive integer")
    if not isinstance(observations, list):
        raise TypeError("observations must be a list")
    if any(not isinstance(observation, UsageObservation) for observation in observations):
        raise TypeError("observations must contain UsageObservation instances")

    attempt_ids = [observation.attempt_id for observation in observations]
    if len(set(attempt_ids)) != len(attempt_ids):
        raise ValueError("observations must contain unique attempt IDs")

    basis_counts: dict[str, int] = {}
    for observation in observations:
        basis_counts[observation.basis] = basis_counts.get(observation.basis, 0) + 1

    sample_count = len(observations)
    if sample_count < min_samples:
        return UsageForecast(
            status="unavailable",
            usage_by_model=None,
            sample_count=sample_count,
            method="similarity_weighted_mean",
            basis_counts=basis_counts,
        )

    total_weight = sum((observation.weight for observation in observations), Decimal("0"))
    model_ids = sorted(
        {model_id for observation in observations for model_id in observation.usage_by_model}
    )
    usage_by_model: dict[str, TokenUsage] = {}
    for model_id in model_ids:
        weighted_usage = _zero_usage()
        for observation in observations:
            usage = observation.usage_by_model.get(model_id, _zero_usage())
            weighted_usage = _add_usage(weighted_usage, _scale_usage(usage, observation.weight))
        # Divide once, rather than multiplying by an already rounded reciprocal.
        # Identical attempts then retain their exact usage even with 3 samples.
        usage_by_model[model_id] = TokenUsage(**{
            name: getattr(weighted_usage, name) / total_weight
            for name in TokenUsage.__dataclass_fields__
        })

    return UsageForecast(
        status="available",
        usage_by_model=usage_by_model,
        sample_count=sample_count,
        method="similarity_weighted_mean",
        basis_counts=basis_counts,
    )


def _scale_usage(usage: TokenUsage, factor: Decimal) -> TokenUsage:
    return TokenUsage(
        input_tokens=usage.input_tokens * factor,
        output_tokens=usage.output_tokens * factor,
        cache_read_input_tokens=usage.cache_read_input_tokens * factor,
        cache_write_5m_input_tokens=usage.cache_write_5m_input_tokens * factor,
        cache_write_1h_input_tokens=usage.cache_write_1h_input_tokens * factor,
    )


def _zero_usage() -> TokenUsage:
    return TokenUsage(input_tokens=0, output_tokens=0)


def _add_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    return TokenUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        cache_read_input_tokens=left.cache_read_input_tokens + right.cache_read_input_tokens,
        cache_write_5m_input_tokens=(
            left.cache_write_5m_input_tokens + right.cache_write_5m_input_tokens
        ),
        cache_write_1h_input_tokens=(
            left.cache_write_1h_input_tokens + right.cache_write_1h_input_tokens
        ),
    )
