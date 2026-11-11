"""Small, dependency-free statistics used by the integrity checks.

Implemented here (rather than importing scipy) so that lane E's verdicts do not
depend on the same library a lane under review might use.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Z_95 = 1.959963984540054


def gamma_q(a: float, x: float) -> float:
    """Regularised upper incomplete gamma function Q(a, x)."""
    if a <= 0:
        raise ValueError("a must be positive")
    if x <= 0:
        return 1.0
    log_prefix = -x + a * math.log(x) - math.lgamma(a)
    if x < a + 1:
        term = 1.0 / a
        total = term
        n = a
        for _ in range(10_000):
            n += 1
            term *= x / n
            total += term
            if abs(term) < abs(total) * 1e-16:
                break
        return max(0.0, 1.0 - total * math.exp(log_prefix))
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 10_000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-16:
            break
    return min(1.0, math.exp(log_prefix) * h)


def chi2_sf(statistic: float, dof: int) -> float:
    """P(X >= statistic) for a chi-square distribution with ``dof`` degrees of freedom."""
    if dof < 1:
        raise ValueError("dof must be >= 1")
    return gamma_q(dof / 2.0, statistic / 2.0)


def chi_square_uniform(counts: Sequence[int]) -> tuple[float, int, float]:
    """Goodness of fit against equal allocation. Returns (statistic, dof, p)."""
    total = sum(counts)
    if len(counts) < 2 or total == 0:
        raise ValueError("need >=2 cells and a positive total")
    expected = total / len(counts)
    statistic = sum((c - expected) ** 2 / expected for c in counts)
    return statistic, len(counts) - 1, chi2_sf(statistic, len(counts) - 1)


def chi_square_independence(table: Sequence[Sequence[int]]) -> tuple[float, int, float]:
    """Pearson independence test for an r x c table. Returns (statistic, dof, p)."""
    rows = [sum(r) for r in table]
    cols = [sum(c) for c in zip(*table, strict=True)]
    total = sum(rows)
    if total == 0 or len(rows) < 2 or len(cols) < 2:
        raise ValueError("need an r x c table with r,c >= 2 and a positive total")
    statistic = 0.0
    for i, row in enumerate(table):
        for j, observed in enumerate(row):
            expected = rows[i] * cols[j] / total
            if expected > 0:
                statistic += (observed - expected) ** 2 / expected
    dof = (len(rows) - 1) * (len(cols) - 1)
    return statistic, dof, chi2_sf(statistic, dof)


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n <= 0:
        return 0.0, 1.0
    if not 0 <= successes <= n:
        raise ValueError("successes must be within [0, n]")
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def newcombe_diff_interval(
    x_a: int, n_a: int, x_b: int, n_b: int, z: float = Z_95
) -> tuple[float, float, float]:
    """Newcombe hybrid-score interval for ``p_b - p_a``. Returns (estimate, low, high)."""
    if n_a <= 0 or n_b <= 0:
        return 0.0, -1.0, 1.0
    p_a, p_b = x_a / n_a, x_b / n_b
    low_a, high_a = wilson_interval(x_a, n_a, z)
    low_b, high_b = wilson_interval(x_b, n_b, z)
    estimate = p_b - p_a
    low = estimate - math.sqrt((p_b - low_b) ** 2 + (high_a - p_a) ** 2)
    high = estimate + math.sqrt((high_b - p_b) ** 2 + (p_a - low_a) ** 2)
    return estimate, max(-1.0, low), min(1.0, high)
