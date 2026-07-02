"""Detour selection: free chunks auto-selected, then 0/1 knapsack DP over time buckets.

Maximizes avoided highway seconds subject to the rider's extra-time budget.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Item:
    key: int
    cost_s: float  # extra_cost: detour duration - baseline (can be <= 0)
    value_s: float  # avoided highway seconds


def select(items: Sequence[Item], budget_s: float, bucket_s: int) -> set[int]:
    """Return the keys of the selected items.

    Items with cost_s <= 0 are always selected (free wins). The rest is a 0/1 knapsack
    over `bucket_s`-second buckets; costs are rounded UP to buckets so the real budget is
    never exceeded.
    """
    chosen = {it.key for it in items if it.cost_s <= 0}
    paid = [it for it in items if it.cost_s > 0 and it.value_s > 0]
    capacity = int(budget_s // bucket_s) if budget_s > 0 else 0
    if not paid or capacity <= 0:
        return chosen

    weights = [max(1, math.ceil(it.cost_s / bucket_s)) for it in paid]
    n = len(paid)
    # dp[i][c] = best value using items[:i] with capacity c
    dp = [[0.0] * (capacity + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        w = weights[i - 1]
        v = paid[i - 1].value_s
        for c in range(capacity + 1):
            best = dp[i - 1][c]
            if w <= c and dp[i - 1][c - w] + v > best:
                best = dp[i - 1][c - w] + v
            dp[i][c] = best

    c = capacity
    for i in range(n, 0, -1):
        if dp[i][c] != dp[i - 1][c]:
            chosen.add(paid[i - 1].key)
            c -= weights[i - 1]
    return chosen
