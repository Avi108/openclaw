"""Corporate-action handling for markets whose previous close is NOT adjusted.

NSE adjusts the previous close on the ex-date, so close/prev_close is a clean return. Where
a file gives only raw prices (derived or raw previous close), a split looks like a crash.
`unadjusted_jumps` finds suspicious days; `apply_actions` fixes returns once you supply the
ratios. Ratios must come from the exchange or company notices, never be guessed from the jump.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from collections.abc import Sequence

from .nse import Bar


@dataclass(frozen=True)
class Action:
    symbol: str
    ex_date: date
    factor: float  # shares after per share before: 5 for a 1:5 split, 0.5 for a 2:1 reverse split


@dataclass(frozen=True)
class Jump:
    symbol: str
    day: date
    change_pct: float
    basis: str


def unadjusted_jumps(bars: Sequence[Bar], threshold_pct: float = 25.0, price_limit_pct: float | None = None) -> list[Jump]:
    """Days whose raw move exceeds the threshold on bars whose previous close is not exchange-adjusted.

    With a daily price limit (KRX 30%), any move beyond the limit cannot be trading and is almost certainly
    a corporate action; without one (HKEX) a large move is only a candidate to check.
    """
    cut = min(threshold_pct, price_limit_pct) if price_limit_pct else threshold_pct
    out = []
    for b in bars:
        if b.prev_close_basis in ("exchange", "adjusted_by_actions"):
            continue
        chg = (b.close / b.prev_close - 1.0) * 100.0
        if abs(chg) > cut + 1e-6:  # a move exactly at the limit is legal; tolerate float noise  # pragma: no mutate (equivalent: the 1e-6 tolerance makes the boundary case unreachable)
            out.append(Jump(b.symbol, b.day, chg, b.prev_close_basis))
    return sorted(out, key=lambda j: (j.symbol, j.day))


def apply_actions(bars: Sequence[Bar], actions: Sequence[Action]) -> list[Bar]:
    """Return bars with the previous close on each ex-date divided by the factor, so returns are clean."""
    fix = {(a.symbol.upper(), a.ex_date): a.factor for a in actions}
    if any(factor <= 0 for factor in fix.values()):
        raise ValueError("factor must be positive")
    out = []
    for b in bars:
        factor = fix.get((b.symbol, b.day))
        out.append(replace(b, prev_close=b.prev_close / factor, prev_close_basis="adjusted_by_actions") if factor else b)
    return out
