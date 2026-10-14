"""Do broker targets predict returns? A point-in-time evaluation harness.

For each call: enter at the first price strictly AFTER the publication date
(no look-ahead), exit after `horizon_days`, and compare realized return with
the broker's implied upside. Reports hit rate, overstatement, rank IC against
forward return, and a permutation p-value. Standard library only.

Needs real call/price history to say anything about real brokers. The
simulator at the bottom exists to prove the harness detects an edge when one
exists and stays quiet when none does.
"""
from __future__ import annotations

import math
import random
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean
from typing import Mapping, Sequence

from .models import BrokerCall, Rating

PricePath = Sequence[tuple[date, float]]  # sorted by date


@dataclass(frozen=True)
class Outcome:
    ticker: str
    broker: str
    published: date
    implied_upside_pct: float
    realized_return_pct: float
    target_hit: bool


@dataclass(frozen=True)
class Summary:
    n: int
    mean_implied_pct: float
    mean_realized_pct: float
    overstatement_pct: float  # implied - realized: how optimistic targets were on average
    hit_rate: float
    rank_ic: float
    p_value: float
    bucket_realized_pct: tuple[float, float, float]  # low / mid / high implied-upside terciles


def _entry_index(path: PricePath, published: date) -> int | None:
    dates = [d for d, _ in path]
    i = bisect_right(dates, published)  # first date strictly after publication
    return i if i < len(path) else None


def evaluate_call(call: BrokerCall, path: PricePath, horizon_days: int) -> Outcome | None:
    upside = call.upside_pct
    if upside is None:
        return None
    i = _entry_index(path, call.published)
    if i is None:
        return None
    entry_date, entry_px = path[i]
    end_date = entry_date + timedelta(days=horizon_days)
    if path[-1][0] < end_date:
        return None  # horizon not yet observable; never score a partial window
    window = [(d, p) for d, p in path[i:] if d <= end_date]
    exit_px = window[-1][1]
    hit = any(p >= call.target for _, p in window)
    return Outcome(
        call.ticker, call.broker, call.published, upside, (exit_px / entry_px - 1.0) * 100.0, hit
    )


def _ranks(xs: Sequence[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    if len(x) != len(y) or len(x) < 3:
        raise ValueError("need >= 3 paired observations")
    rx, ry = _ranks(x), _ranks(y)
    mx, my = mean(rx), mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return 0.0 if den == 0 else num / den


def permutation_p_value(x: Sequence[float], y: Sequence[float], n_perm: int = 2000, seed: int = 0) -> float:
    """One-sided: probability of a rank correlation at least this large if x and y were unrelated."""
    observed = spearman(x, y)
    rng = random.Random(seed)
    y2 = list(y)
    ge = 0
    for _ in range(n_perm):
        rng.shuffle(y2)
        if spearman(x, y2) >= observed:
            ge += 1
    return (ge + 1) / (n_perm + 1)


def summarize(outcomes: Sequence[Outcome], n_perm: int = 2000, seed: int = 0) -> Summary:
    if len(outcomes) < 9:
        raise ValueError("need >= 9 scored calls for terciles; collect more history")
    imp = [o.implied_upside_pct for o in outcomes]
    real = [o.realized_return_pct for o in outcomes]
    order = sorted(range(len(outcomes)), key=lambda i: imp[i])
    third = len(order) // 3
    groups = (order[:third], order[third : 2 * third], order[2 * third :])
    buckets = tuple(mean(real[i] for i in g) for g in groups)
    return Summary(
        n=len(outcomes),
        mean_implied_pct=mean(imp),
        mean_realized_pct=mean(real),
        overstatement_pct=mean(imp) - mean(real),
        hit_rate=sum(o.target_hit for o in outcomes) / len(outcomes),
        rank_ic=spearman(imp, real),
        p_value=permutation_p_value(imp, real, n_perm, seed),
        bucket_realized_pct=buckets,  # type: ignore[arg-type]
    )


def evaluate(
    calls: Sequence[BrokerCall], paths: Mapping[str, PricePath], horizon_days: int = 180
) -> list[Outcome]:
    out: list[Outcome] = []
    for c in calls:
        path = paths.get(c.ticker)
        if not path:
            continue
        o = evaluate_call(c, path, horizon_days)
        if o is not None:
            out.append(o)
    return out


# --------------------------- simulator (synthetic only) ---------------------------

def simulate_world(
    n_stocks: int = 120, n_days: int = 420, edge: float = 0.0, seed: int = 1, horizon_days: int = 180
) -> tuple[list[BrokerCall], dict[str, PricePath]]:
    """Synthetic calls and prices. `edge` is the share of a call's implied upside that is real.

    edge=0: targets are pure noise relative to future returns.
    edge=1: realized drift matches implied upside (the unrealistically good case).
    """
    rng = random.Random(seed)
    start = date(2025, 1, 1)
    calls: list[BrokerCall] = []
    paths: dict[str, PricePath] = {}
    for k in range(n_stocks):
        t = f"S{k:03d}"
        upside = rng.uniform(5, 80)  # broker-implied upside, percent
        px0 = 100.0
        daily_vol = 0.018
        # Daily drift so that expected return over the horizon is edge * upside.
        drift = math.log(1 + edge * upside / 100.0) / horizon_days if edge else 0.0
        p, path = px0, []
        for d in range(n_days):
            p *= math.exp(drift - 0.5 * daily_vol**2 + daily_vol * rng.gauss(0, 1))
            path.append((start + timedelta(days=d), p))
        paths[t] = path
        calls.append(
            BrokerCall(t, "SimBroker", Rating.BUY, target=px0 * (1 + upside / 100.0), price=px0,
                       published=start - timedelta(days=1))
        )
    return calls, paths
