"""Pure, reproducible token-cost calculation domain.

The calculator deliberately performs no file, clock, or catalog access.  A
caller supplies both usage and the exact rate-card snapshot used to price it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping


MILLION = Decimal("1000000")
CALCULATOR_VERSION = "kamino451.cost-calculator.v1"


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise ValueError(f"{field} must be a decimal number") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    if result < 0:
        raise ValueError(f"{field} must be nonnegative")
    return result


def _aware_datetime(value: datetime | str | None, field: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(frozen=True)
class TokenUsage:
    """Billable token quantities; Decimal supports fractional forecasts."""

    input_tokens: Decimal
    output_tokens: Decimal
    cache_read_input_tokens: Decimal = Decimal(0)
    cache_write_5m_input_tokens: Decimal = Decimal(0)
    cache_write_1h_input_tokens: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        for field in self.__dataclass_fields__:
            object.__setattr__(self, field, _decimal(getattr(self, field), field))

    def to_dict(self) -> dict[str, str]:
        return {field: str(getattr(self, field)) for field in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TokenUsage":
        return cls(
            input_tokens=value["input_tokens"],
            output_tokens=value["output_tokens"],
            cache_read_input_tokens=value.get("cache_read_input_tokens", 0),
            cache_write_5m_input_tokens=value.get("cache_write_5m_input_tokens", 0),
            cache_write_1h_input_tokens=value.get("cache_write_1h_input_tokens", 0),
        )


@dataclass(frozen=True)
class RateCard:
    """An immutable snapshot of rates applied to one exact provider model."""

    model_id: str
    pricing_version: str
    pricing_hash: str
    currency: str
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cache_read_per_mtok: Decimal
    cache_write_5m_per_mtok: Decimal
    cache_write_1h_per_mtok: Decimal
    provider: str = "anthropic"
    billing_mode: str = "standard"
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    recorded_at: datetime | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        for field in ("model_id", "pricing_version", "pricing_hash", "currency", "provider", "billing_mode"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
        for field in (
            "input_per_mtok",
            "output_per_mtok",
            "cache_read_per_mtok",
            "cache_write_5m_per_mtok",
            "cache_write_1h_per_mtok",
        ):
            object.__setattr__(self, field, _decimal(getattr(self, field), field))
        for field in ("effective_from", "effective_to", "recorded_at"):
            object.__setattr__(self, field, _aware_datetime(getattr(self, field), field))
        if self.effective_from and self.effective_to and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be later than effective_from")
        if self.source is not None and (not isinstance(self.source, str) or not self.source.strip()):
            raise ValueError("source must be a non-empty string when provided")

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "pricing_version": self.pricing_version,
            "pricing_hash": self.pricing_hash,
            "currency": self.currency,
            "provider": self.provider,
            "billing_mode": self.billing_mode,
            "input_per_mtok": str(self.input_per_mtok),
            "output_per_mtok": str(self.output_per_mtok),
            "cache_read_per_mtok": str(self.cache_read_per_mtok),
            "cache_write_5m_per_mtok": str(self.cache_write_5m_per_mtok),
            "cache_write_1h_per_mtok": str(self.cache_write_1h_per_mtok),
            "effective_from": _timestamp(self.effective_from),
            "effective_to": _timestamp(self.effective_to),
            "recorded_at": _timestamp(self.recorded_at),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RateCard":
        return cls(**{field: value.get(field) for field in cls.__dataclass_fields__ if field in value})


@dataclass(frozen=True)
class CostEstimate:
    """Exact itemized cost together with all inputs needed to reproduce it."""

    usage: TokenUsage
    rate_card: RateCard
    uncached_input_cost: Decimal
    cache_read_input_cost: Decimal
    cache_write_5m_input_cost: Decimal
    cache_write_1h_input_cost: Decimal
    input_cost: Decimal
    output_cost: Decimal
    total_cost: Decimal

    def __post_init__(self) -> None:
        for field in (
            "uncached_input_cost",
            "cache_read_input_cost",
            "cache_write_5m_input_cost",
            "cache_write_1h_input_cost",
            "input_cost",
            "output_cost",
            "total_cost",
        ):
            object.__setattr__(self, field, _decimal(getattr(self, field), field))

    def to_dict(self) -> dict[str, Any]:
        return {
            "calculator_version": CALCULATOR_VERSION,
            "usage": self.usage.to_dict(),
            "rate_card": self.rate_card.to_dict(),
            "cost": {
                field: str(getattr(self, field))
                for field in (
                    "uncached_input_cost",
                    "cache_read_input_cost",
                    "cache_write_5m_input_cost",
                    "cache_write_1h_input_cost",
                    "input_cost",
                    "output_cost",
                    "total_cost",
                )
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CostEstimate":
        version = value.get("calculator_version")
        if version != CALCULATOR_VERSION:
            raise ValueError(f"unsupported calculator_version: {version!r}")
        costs = value["cost"]
        return cls(
            usage=TokenUsage.from_dict(value["usage"]),
            rate_card=RateCard.from_dict(value["rate_card"]),
            **{field: costs[field] for field in (
                "uncached_input_cost",
                "cache_read_input_cost",
                "cache_write_5m_input_cost",
                "cache_write_1h_input_cost",
                "input_cost",
                "output_cost",
                "total_cost",
            )},
        )


def calculate_cost(usage: TokenUsage, rates: RateCard) -> CostEstimate:
    """Calculate cost without rounding; presentation layers choose precision."""

    uncached = usage.input_tokens * rates.input_per_mtok / MILLION
    cache_read = usage.cache_read_input_tokens * rates.cache_read_per_mtok / MILLION
    cache_write_5m = usage.cache_write_5m_input_tokens * rates.cache_write_5m_per_mtok / MILLION
    cache_write_1h = usage.cache_write_1h_input_tokens * rates.cache_write_1h_per_mtok / MILLION
    input_cost = uncached + cache_read + cache_write_5m + cache_write_1h
    output_cost = usage.output_tokens * rates.output_per_mtok / MILLION
    return CostEstimate(
        usage=usage,
        rate_card=rates,
        uncached_input_cost=uncached,
        cache_read_input_cost=cache_read,
        cache_write_5m_input_cost=cache_write_5m,
        cache_write_1h_input_cost=cache_write_1h,
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=input_cost + output_cost,
    )
