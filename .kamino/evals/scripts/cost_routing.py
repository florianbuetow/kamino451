"""Optional cost penalties over already eligible, independently scored candidates."""

from decimal import Decimal


def score_with_costs(base_scores, costs, *, weight, scale_usd):
    """Return scores and penalties without changing equal-cost baseline ordering.

    A missing estimate disables cost for the whole comparison: an unknown price
    must neither look free nor exclude an otherwise suitable candidate.
    """
    weight = Decimal(str(weight))
    scale = Decimal(str(scale_usd))
    if not weight.is_finite() or weight < 0:
        raise ValueError("cost weight must be finite and nonnegative")
    if not scale.is_finite() or scale <= 0:
        raise ValueError("cost scale_usd must be finite and positive")
    scores = [Decimal(str(score)) for score in base_scores]
    if any(not score.is_finite() for score in scores):
        raise ValueError("base scores must be finite")
    if len(scores) != len(costs):
        raise ValueError("one cost estimate is required per score")
    penalties = [Decimal(0)] * len(scores)
    if weight == 0 or not scores or any(cost is None for cost in costs):
        return scores, penalties
    prices = [Decimal(str(cost)) for cost in costs]
    if any(not price.is_finite() or price < 0 for price in prices):
        raise ValueError("costs must be finite and nonnegative")
    cheapest = min(prices)
    if all(price == cheapest for price in prices):
        return scores, penalties
    penalties = [(price - cheapest) / scale for price in prices]
    return [score - weight * penalty for score, penalty in zip(scores, penalties)], penalties
