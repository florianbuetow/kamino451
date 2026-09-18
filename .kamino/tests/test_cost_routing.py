"""Cost is an optional penalty, never an eligibility requirement."""

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals" / "scripts"))
from cost_routing import score_with_costs


@pytest.mark.parametrize("costs", [[0, 0, 0], [12, 12, 12], [None, 3, 0]])
def test_equal_or_unavailable_cost_preserves_every_score_and_tie(costs):
    baseline = [Decimal("0.8"), Decimal("0.9"), Decimal("0.9")]
    scores, penalties = score_with_costs(baseline, costs, weight=9, scale_usd="0.1")
    assert scores == baseline
    assert penalties == [0, 0, 0]


def test_weight_zero_ignores_costs():
    assert score_with_costs([1, 2], [None, 999], weight=0, scale_usd=1) == ([1, 2], [0, 0])


def test_tradeoff_and_monotonic_cost():
    low, _ = score_with_costs(["0.85", "0.95"], ["0.02", "0.06"], weight="0.2", scale_usd="0.1")
    high, _ = score_with_costs(["0.85", "0.95"], ["0.02", "0.06"], weight="0.3", scale_usd="0.1")
    assert low == [Decimal("0.85"), Decimal("0.87")]
    assert high[0] > high[1]
    more, _ = score_with_costs(["0.85", "0.95"], ["0.02", "0.07"], weight="0.2", scale_usd="0.1")
    assert more[1] < low[1]


@pytest.mark.parametrize("weight,scale", [(-1, 1), (float("nan"), 1), (1, 0), (1, float("inf"))])
def test_invalid_policy_rejected(weight, scale):
    with pytest.raises(ValueError):
        score_with_costs([1], [0], weight=weight, scale_usd=scale)


def test_tiny_cost_difference_has_tiny_penalty():
    scores, _ = score_with_costs([1, 1], [0, "0.000001"], weight=1, scale_usd=1)
    assert scores == [1, Decimal("0.999999")]
