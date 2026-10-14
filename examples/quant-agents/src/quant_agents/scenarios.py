"""Scenario and stress testing on NSE bhavcopy history. Standard library only.

Key data fact: bhavcopy's previous close is adjusted by NSE for corporate
actions on the ex-date, so `close / prev_close - 1` is a clean daily return,
whereas raw close-to-close would show a 1:5 split as an 80% crash.

Everything here is analysis of past data under stated assumptions. None of it
predicts the future, and every output names its assumptions.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import mean, pstdev
from typing import Mapping, Sequence

from .backtest import Metrics, equity_to_metrics
from .nse import Bar

Returns = dict[date, float]


def daily_returns(bars: Sequence[Bar]) -> Returns:
    """Corporate-action-safe daily returns for one symbol."""
    return {b.day: b.close / b.prev_close - 1.0 for b in bars}


def by_symbol(bars: Sequence[Bar]) -> dict[str, list[Bar]]:
    out: dict[str, list[Bar]] = defaultdict(list)
    for b in bars:
        out[b.symbol].append(b)
    return {s: sorted(v, key=lambda b: b.day) for s, v in out.items()}


@dataclass(frozen=True)
class CorporateActionFlag:
    symbol: str
    day: date
    raw_change_pct: float  # what naive close-to-close would show
    adjusted_change_pct: float  # what the adjusted previous close shows


def corporate_action_days(bars: Sequence[Bar], threshold: float = 0.2) -> list[CorporateActionFlag]:
    """Days where the adjusted previous close differs from the prior session's close (split/bonus/etc.)."""
    flags: list[CorporateActionFlag] = []
    for sym, rows in by_symbol(bars).items():
        for prev, cur in zip(rows, rows[1:]):
            if abs(cur.prev_close / prev.close - 1.0) > threshold:
                flags.append(CorporateActionFlag(sym, cur.day, (cur.close / prev.close - 1) * 100, (cur.close / cur.prev_close - 1) * 100))
    return flags


def portfolio_returns(weights: Mapping[str, float], rets: Mapping[str, Returns]) -> tuple[Returns, int]:
    """Weighted daily returns over dates where every held symbol has data.

    Returns (series, dropped_days). Days with any missing symbol are dropped, not zero-filled,
    so a data gap cannot masquerade as a calm day.
    """
    syms = [s for s, w in weights.items() if w]
    if not syms or any(s not in rets for s in syms):
        missing = [s for s in syms if s not in rets]
        raise ValueError(f"no return history for: {missing}")
    all_days = set().union(*(rets[s].keys() for s in syms))
    common = set.intersection(*(set(rets[s].keys()) for s in syms))
    series = {d: sum(weights[s] * rets[s][d] for s in syms) for d in sorted(common)}
    return series, len(all_days) - len(common)


def worst_windows(series: Returns, window: int, k: int = 3) -> list[tuple[date, date, float]]:
    """k worst non-overlapping compounded windows of `window` trading days, as (start, end, return %)."""
    days = sorted(series)
    cands = []
    for i in range(len(days) - window + 1):
        r = math.prod(1 + series[d] for d in days[i : i + window]) - 1
        cands.append((r, i))
    chosen: list[tuple[date, date, float]] = []
    used: list[tuple[int, int]] = []
    for r, i in sorted(cands):
        if all(i + window <= a or i >= b for a, b in used):
            used.append((i, i + window))
            chosen.append((days[i], days[i + window - 1], r * 100))
            if len(chosen) == k:
                break
    return chosen


def var_cvar(returns: Sequence[float], level: float = 0.95) -> tuple[float, float]:
    """Historical one-day VaR and expected shortfall (CVaR) as positive loss fractions."""
    if len(returns) < 20:
        raise ValueError("need >= 20 returns")
    xs = sorted(returns)
    cut = max(1, int(len(xs) * (1 - level)))
    tail = xs[:cut]
    return -xs[cut - 1], -mean(tail)


def bootstrap_drawdown(
    returns: Sequence[float], horizon: int, halt_dd_pct: float = 15.0, n: int = 2000, block: int = 5, seed: int = 0
) -> tuple[float, float]:
    """Block-bootstrap the history: (P[max drawdown > halt within horizon], 5th percentile terminal return %).

    Assumes future days resemble resampled past blocks; it cannot produce a crash worse
    than the worst blocks in the sample.
    """
    if len(returns) < block * 2:
        raise ValueError("history too short")
    rng = random.Random(seed)
    breaches, terminals = 0, []
    for _ in range(n):
        path: list[float] = []
        while len(path) < horizon:
            s = rng.randrange(0, len(returns) - block + 1)
            path.extend(returns[s : s + block])
        eq, peak, mdd = 1.0, 1.0, 0.0
        for r in path[:horizon]:
            eq *= 1 + r
            peak = max(peak, eq)
            mdd = max(mdd, 1 - eq / peak)
        breaches += mdd * 100 > halt_dd_pct
        terminals.append((eq - 1) * 100)
    terminals.sort()
    return breaches / n, terminals[int(0.05 * n)]


def beta(asset: Returns, market: Returns) -> float | None:
    days = sorted(set(asset) & set(market))
    if len(days) < 30:
        return None
    a, m = [asset[d] for d in days], [market[d] for d in days]
    ma, mm = mean(a), mean(m)
    var = sum((x - mm) ** 2 for x in m)
    return None if var == 0 else sum((x - ma) * (y - mm) for x, y in zip(m, a)) / var


def market_proxy(rets: Mapping[str, Returns]) -> Returns:
    """Equal-weight average of the loaded universe. A proxy, NOT the Nifty."""
    by_day: dict[date, list[float]] = defaultdict(list)
    for r in rets.values():
        for d, x in r.items():
            by_day[d].append(x)
    return {d: mean(v) for d, v in by_day.items() if len(v) >= max(3, len(rets) // 2)}


def shock_pnl(weights: Mapping[str, float], betas: Mapping[str, float | None], market_move_pct: float) -> dict[str, float]:
    """Per-symbol contribution (% of portfolio) to a market shock, using beta; unknown beta assumed 1.0."""
    return {s: w * (betas.get(s) if betas.get(s) is not None else 1.0) * market_move_pct for s, w in weights.items()}


def regime_metrics(strategy: Returns, market: Returns, lookback: int = 60, band_pct: float = 5.0) -> dict[str, Metrics | None]:
    """Strategy performance split by market regime (bull/bear/sideways, from trailing market return)."""
    days = sorted(market)
    label: dict[date, str] = {}
    for i in range(lookback, len(days)):
        tr = math.prod(1 + market[d] for d in days[i - lookback : i]) - 1
        label[days[i]] = "bull" if tr * 100 > band_pct else "bear" if tr * 100 < -band_pct else "sideways"
    out: dict[str, Metrics | None] = {}
    for reg in ("bull", "sideways", "bear"):
        rs = [strategy[d] for d in sorted(strategy) if label.get(d) == reg]
        if len(rs) < 10:
            out[reg] = None
            continue
        eq = [1.0]
        for r in rs:
            eq.append(eq[-1] * (1 + r))
        out[reg] = equity_to_metrics(eq)
    return out


def days_to_exit(position_value: float, avg_daily_value: float, participation: float = 0.1) -> float:
    """Trading days needed to sell while staying under `participation` of average daily traded value."""
    if avg_daily_value <= 0:
        return math.inf
    return position_value / (avg_daily_value * participation)


@dataclass(frozen=True)
class ScenarioReport:
    n_days: int
    dropped_days: int
    corporate_actions: list[CorporateActionFlag]
    var95_pct: float
    cvar95_pct: float
    vol_ann_pct: float
    worst_1d: list[tuple[date, date, float]]
    worst_20d: list[tuple[date, date, float]]
    p_breach: float
    p5_terminal_pct: float
    shocks: dict[str, float]  # market move % -> portfolio P&L %
    liquidity: dict[str, float]  # symbol -> days to exit
    notes: tuple[str, ...]


def build_report(
    bars: Sequence[Bar],
    weights: Mapping[str, float],
    portfolio_value: float = 3800.0,
    horizon: int = 60,
    halt_dd_pct: float = 15.0,
    shocks: Sequence[float] = (-5.0, -10.0, -20.0),
    seed: int = 0,
) -> ScenarioReport:
    grouped = by_symbol(bars)
    rets = {s: daily_returns(v) for s, v in grouped.items()}
    series, dropped = portfolio_returns(weights, rets)
    rs = [series[d] for d in sorted(series)]
    var95, cvar95 = var_cvar(rs)
    p_breach, p5 = bootstrap_drawdown(rs, horizon, halt_dd_pct, seed=seed)
    mk = market_proxy(rets)
    betas = {s: beta(rets[s], mk) for s in weights if s in rets}
    shock_out = {f"{m:+.0f}%": sum(shock_pnl(weights, betas, m).values()) for m in shocks}
    liq: dict[str, float] = {}
    for s, w in weights.items():
        recent = grouped[s][-20:]
        adv = mean(b.close * b.volume for b in recent)
        liq[s] = days_to_exit(portfolio_value * w, adv)
    notes = (
        "market proxy is the equal-weight average of the loaded symbols, not the Nifty",
        "betas need >= 30 overlapping days, otherwise 1.0 is assumed",
        "bootstrap resamples past 5-day blocks; it cannot exceed the worst history in the sample",
        f"{dropped} day(s) dropped where a held symbol had no data",
    )
    return ScenarioReport(
        n_days=len(rs), dropped_days=dropped,
        corporate_actions=corporate_action_days(bars),
        var95_pct=var95 * 100, cvar95_pct=cvar95 * 100, vol_ann_pct=pstdev(rs) * math.sqrt(252) * 100,
        worst_1d=worst_windows(series, 1), worst_20d=worst_windows(series, 20),
        p_breach=p_breach, p5_terminal_pct=p5, shocks=shock_out, liquidity=liq, notes=notes,
    )
