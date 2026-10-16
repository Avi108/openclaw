"""Staged entries: split a position into tranches at progressively lower limit prices."""
from __future__ import annotations

import math


def entry_ladder(total_amount: float, price: float, tranches: int = 3, step_pct: float = 4.0,
                 tick: float = 0.05) -> list[tuple[float, int]]:
    """Return (limit price, whole shares) per tranche, first at `price`, each next `step_pct`% lower.

    Money is split equally; leftover that cannot buy a whole share stays unspent (never rounded up).
    The ladder only improves average cost if price actually falls: a rising stock fills the first rung only,
    which is the intended behavior, and the thesis check (not the ladder) decides whether to add on weakness.
    """
    if tranches < 1 or price <= 0 or total_amount <= 0 or not (0 < step_pct < 100 / max(tranches, 1)):
        raise ValueError("invalid ladder inputs")
    if tick <= 0:
        raise ValueError("tick must be positive")
    each = total_amount / tranches
    out: list[tuple[float, int]] = []
    for i in range(tranches):
        raw = price * (1 - step_pct / 100.0 * i)
        limit = round(round(raw / tick) * tick, 4)  # orders must sit on the exchange tick (NSE equities: Rs 0.05 for most)  # pragma: no mutate (equivalent: ticks are multiples of 0.05, so 4 or 5 decimals give the same value)
        if limit <= 0 or (out and limit >= out[-1][0]):
            raise ValueError(f"step {step_pct:g}% is too small for price {price:g} at tick {tick:g}: rungs collapse; widen the step")
        out.append((limit, math.floor(each / limit)))
    return out


def average_cost(fills: list[tuple[float, int]]) -> float:
    shares = sum(q for _, q in fills)
    if shares == 0:
        raise ValueError("no shares filled")
    return sum(p * q for p, q in fills) / shares
