"""Liquidity: what it costs to trade a name, beyond "days to exit"."""
from __future__ import annotations

import math
from statistics import mean
from typing import Sequence

from .nse import Bar


def sqrt_impact_pct(daily_vol_pct: float, order_value: float, adv_value: float, coeff: float = 1.0) -> float:
    """Square-root market-impact estimate: coeff * daily volatility * sqrt(order / average daily traded value).

    A rule of thumb with coeff near 1; real impact depends on spread, urgency and depth, so treat it
    as an order of magnitude. Adds to slippage for illiquid small and micro caps.
    """
    if adv_value <= 0 or order_value < 0:
        raise ValueError("adv_value must be positive and order_value non-negative")
    return coeff * daily_vol_pct * math.sqrt(order_value / adv_value)


def amihud_per_crore(bars: Sequence[Bar], days: int = 60) -> float | None:
    """Amihud illiquidity: mean(|daily return| / traded value in Rs crore). Higher means the price moves more per rupee traded."""
    rows = sorted(bars, key=lambda b: b.day)[-days:]
    vals = [abs(b.close / b.prev_close - 1.0) / (b.close * b.volume / 1e7) for b in rows if b.volume > 0]
    return mean(vals) if len(vals) >= 20 else None
