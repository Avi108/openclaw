"""Small, dependency-free backtest engine with verified performance metrics."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from collections.abc import Callable, Sequence

TRADING_DAYS = 252


@dataclass(frozen=True)
class Metrics:
    total_return_pct: float
    cagr_pct: float
    sharpe: float
    max_drawdown_pct: float
    n_days: int


def _cagr_pct(ratio: float, n_days: int) -> float:
    """Annualised growth in percent. Total loss is -100%; an overflow on a very short, very large move is +inf.

    Annualising fewer than a few weeks of data is not meaningful, so read CAGR alongside `n_days`.
    """
    if ratio <= 0:
        return -100.0
    try:
        return (ratio ** (TRADING_DAYS / n_days) - 1.0) * 100.0
    except OverflowError:
        return math.inf


def equity_to_metrics(equity: Sequence[float], rf_annual: float = 0.0) -> Metrics:
    if len(equity) < 2 or equity[0] <= 0:
        raise ValueError("need at least two positive equity points")
    rets = [equity[i] / equity[i - 1] - 1.0 for i in range(1, len(equity))]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(var)
    rf_daily = rf_annual / TRADING_DAYS
    sharpe = 0.0 if std == 0 else (mean - rf_daily) / std * math.sqrt(TRADING_DAYS)
    peak, mdd = equity[0], 0.0  # pragma: no mutate (equivalent: the first loop iteration sets peak to equity[0] anyway)
    for v in equity:
        peak = max(peak, v)
        mdd = max(mdd, 1.0 - v / peak)
    total = equity[-1] / equity[0] - 1.0
    return Metrics(total * 100, _cagr_pct(equity[-1] / equity[0], n), sharpe, mdd * 100, n)


def run_backtest(
    prices: Sequence[float],
    signal: Callable[[Sequence[float]], float],
    cost_bps: float = 10.0,
    start: int = 30,
) -> list[float]:
    """Long/flat strategy. `signal(history)` returns target exposure in [0, 1].

    The signal sees prices up to and including day t-1 and trades at day t's
    return, so there is no look-ahead. Costs are charged on exposure changes.
    """
    equity = [1.0]
    exposure = 0.0
    for t in range(start, len(prices)):
        target = min(1.0, max(0.0, signal(prices[:t])))
        cost = abs(target - exposure) * cost_bps / 10_000.0
        day_ret = prices[t] / prices[t - 1] - 1.0
        equity.append(equity[-1] * (1.0 + target * day_ret - cost))
        exposure = target
    return equity


def momentum_signal(lookback: int = 20) -> Callable[[Sequence[float]], float]:
    def sig(history: Sequence[float]) -> float:
        if len(history) <= lookback:
            return 0.0
        return 1.0 if history[-1] > history[-1 - lookback] else 0.0

    return sig


def synthetic_prices(n: int = 756, drift: float = 0.0004, vol: float = 0.015, seed: int = 7) -> list[float]:
    """Seeded geometric random walk for demos and tests. Contains no real market data."""
    rng = random.Random(seed)
    p, out = 100.0, [100.0]
    for _ in range(n - 1):
        p *= math.exp(drift - 0.5 * vol**2 + vol * rng.gauss(0, 1))
        out.append(p)
    return out
