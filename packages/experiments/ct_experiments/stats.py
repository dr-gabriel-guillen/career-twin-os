"""Small, dependency-free statistics used by the experiment lab.

Everything here is deterministic given its inputs (permutation tests take an
explicit seed). Functions are checked against closed-form / textbook values in
tests/test_experiments.py (TestStats).
"""
from __future__ import annotations

import math
import random
from statistics import NormalDist
from typing import Sequence

_N = NormalDist()


def norm_ppf(p: float) -> float:
    return _N.inv_cdf(p)


def norm_cdf(x: float) -> float:
    return _N.cdf(x)


def _betacf(a: float, b: float, x: float) -> float:
    tiny, eps = 1e-300, 1e-14
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    x = df / (df + t * t)
    tail = 0.5 * betainc(df / 2.0, 0.5, x)
    return 1.0 - tail if t > 0 else tail


def t_ppf(p: float, df: float) -> float:
    if not (0.0 < p < 1.0) or df <= 0:
        raise ValueError("t_ppf needs 0<p<1 and df>0")
    lo, hi = -1e6, 1e6
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def wilson_interval(successes: int, n: int, conf: float = 0.95) -> tuple[float, float] | None:
    if n <= 0:
        return None
    z = norm_ppf(1 - (1 - conf) / 2)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def newcombe_interval(x1: int, n1: int, x0: int, n0: int, conf: float = 0.95) -> tuple[float, float]:
    """Newcombe hybrid-score interval for p1 - p0 (two independent proportions)."""
    l1, u1 = wilson_interval(x1, n1, conf)  # type: ignore[misc]
    l0, u0 = wilson_interval(x0, n0, conf)  # type: ignore[misc]
    p1, p0 = x1 / n1, x0 / n0
    d = p1 - p0
    return (d - math.sqrt((p1 - l1) ** 2 + (u0 - p0) ** 2),
            d + math.sqrt((u1 - p1) ** 2 + (p0 - l0) ** 2))


def two_proportion_power(p0: float, p1: float, n_per_arm: float, alpha: float = 0.05) -> float:
    """Normal-approximation power of a two-sided test of two proportions."""
    if n_per_arm <= 0:
        return 0.0
    z = norm_ppf(1 - alpha / 2)
    pbar = (p0 + p1) / 2
    se0 = math.sqrt(2 * pbar * (1 - pbar) / n_per_arm)
    se1 = math.sqrt(p0 * (1 - p0) / n_per_arm + p1 * (1 - p1) / n_per_arm)
    if se1 == 0:
        return 1.0 if p0 != p1 else alpha
    d = abs(p1 - p0)
    return norm_cdf((d - z * se0) / se1) + norm_cdf((-d - z * se0) / se1)


Stratum = tuple[Sequence[float], Sequence[float]]  # (treatment values, control values)


def stratified_difference(strata: Sequence[Stratum]) -> float:
    """Sum_s w_s * (mean_T,s - mean_C,s) with w_s proportional to stratum size."""
    total = sum(len(t) + len(c) for t, c in strata)
    return sum((len(t) + len(c)) / total * (sum(t) / len(t) - sum(c) / len(c)) for t, c in strata)


def stratified_permutation_test(strata: Sequence[Stratum], *, seed: int, n_perm: int = 5000) -> dict[str, float]:
    """Two-sided randomisation test that re-randomises within each stratum.

    This mirrors the actual assignment mechanism (fixed arm sizes per stratum),
    so it is valid for any outcome distribution and any sample size.
    p = (1 + #{|T*| >= |T|}) / (n_perm + 1), which can never be 0.
    """
    for t, c in strata:
        if not t or not c:
            raise ValueError("every stratum needs units in both arms")
    observed = stratified_difference(strata)
    rng = random.Random(seed)
    total = sum(len(t) + len(c) for t, c in strata)
    pools = [(list(t) + list(c), len(t), len(c), (len(t) + len(c)) / total) for t, c in strata]
    sums = [sum(pool) for pool, *_ in pools]
    hits, tol = 0, 1e-12
    for _ in range(n_perm):
        stat = 0.0
        for (pool, nt, nc, w), tot in zip(pools, sums):
            st = sum(rng.sample(pool, nt))
            stat += w * (st / nt - (tot - st) / nc)
        if abs(stat) >= abs(observed) - tol:
            hits += 1
    return {"statistic": observed, "p_value": (hits + 1) / (n_perm + 1), "n_perm": n_perm}


def stratified_t_interval(strata: Sequence[Stratum], conf: float = 0.95) -> dict[str, float] | None:
    """Approximate CI for the stratified difference in means; None if a variance is not estimable."""
    if any(len(t) < 2 or len(c) < 2 for t, c in strata):
        return None
    total = sum(len(t) + len(c) for t, c in strata)
    var = 0.0

    def s2(xs: Sequence[float]) -> float:
        m = sum(xs) / len(xs)
        return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)

    denom = 0.0
    for t, c in strata:
        w = (len(t) + len(c)) / total
        for xs in (t, c):
            term = w * w * s2(xs) / len(xs)
            var += term
            denom += term * term / (len(xs) - 1)
    est = stratified_difference(strata)
    if var <= 0.0:
        return {"low": est, "high": est, "df": 0.0, "se": 0.0}
    df = var * var / denom  # Welch-Satterthwaite
    half = t_ppf(1 - (1 - conf) / 2, df) * math.sqrt(var)
    return {"low": est - half, "high": est + half, "df": df, "se": math.sqrt(var)}


def anova_icc(clusters: Sequence[Sequence[float]]) -> float | None:
    """One-way ANOVA intraclass correlation; None when it cannot be estimated."""
    clusters = [c for c in clusters if len(c) > 0]
    k, n = len(clusters), sum(len(c) for c in clusters)
    if k < 2 or n <= k:
        return None
    grand = sum(sum(c) for c in clusters) / n
    ssb = sum(len(c) * (sum(c) / len(c) - grand) ** 2 for c in clusters)
    ssw = sum(sum((x - sum(c) / len(c)) ** 2 for x in c) for c in clusters)
    msb, msw = ssb / (k - 1), ssw / (n - k)
    m0 = (n - sum(len(c) ** 2 for c in clusters) / n) / (k - 1)
    denom = msb + (m0 - 1) * msw
    if denom <= 0:
        return None
    return max(0.0, (msb - msw) / denom)


def holm(p_values: Sequence[float]) -> list[float]:
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    out, running = [0.0] * len(p_values), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(p_values) - rank) * p_values[i]))
        out[i] = running
    return out


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i], reverse=True)
    out, running = [0.0] * m, 1.0
    for k, i in enumerate(order):
        running = min(running, p_values[i] * m / (m - k))
        out[i] = running
    return out
