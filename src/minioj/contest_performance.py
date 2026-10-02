from __future__ import annotations

from collections.abc import Sequence
from math import fsum

DEFAULT_PROBLEM_RATING = 1200
MIN_PERFORMANCE = 0
MAX_PERFORMANCE = 4000
ELO_SCALE = 400


def calculate_performance(results: Sequence[tuple[int | None, bool]]) -> int:
    """Estimate a single-contest performance with a weighted logistic model."""
    if not results:
        return MIN_PERFORMANCE
    # Normalize only this estimate; never modify the underlying Problem rating.
    items = [
        (
            min(
                MAX_PERFORMANCE,
                max(MIN_PERFORMANCE, DEFAULT_PROBLEM_RATING if d is None else d),
            ),
            solved,
        )
        for d, solved in results
    ]
    if not any(solved for _, solved in items):
        return max(MIN_PERFORMANCE, min(d for d, _ in items) - ELO_SCALE)
    if all(solved for _, solved in items):
        return min(MAX_PERFORMANCE, max(d for d, _ in items) + ELO_SCALE)

    # Ordinary equal-slope Bernoulli MLE depends only on solved count.
    # Difficulty weights retain which problems were solved: maximize
    # sum w_i * [y_i log(p_i) + (1-y_i) log(1-p_i)], w_i = 1 + d_i/400.
    weighted = [(d, 1 + d / ELO_SCALE, solved) for d, solved in items]
    target = fsum(w for _, w, solved in weighted if solved)
    low, high = float(MIN_PERFORMANCE), float(MAX_PERFORMANCE)
    for _ in range(60):
        ability = (low + high) / 2
        expected = fsum(
            w / (1 + 10 ** ((d - ability) / ELO_SCALE)) for d, w, _ in weighted
        )
        if expected < target:
            low = ability
        else:
            high = ability
    return round((low + high) / 2)
