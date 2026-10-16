"""Market breadth and sector strength from bhavcopy bars.

Breadth asks how many stocks participate, which an index level alone hides.
All figures are descriptive; none is validated as a timing signal.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import mean
from typing import Mapping, Sequence

from .nse import Bar
from .universe import Instrument


@dataclass(frozen=True)
class Breadth:
    day: date
    advancing: int
    declining: int
    pct_above_50dma: float | None   # None when too few symbols have 50 sessions of history
    pct_above_200dma: float | None
    new_highs: int                  # at a 252-session closing high
    new_lows: int
    n_symbols: int

    @property
    def advance_decline_ratio(self) -> float | None:
        return None if self.declining == 0 else self.advancing / self.declining


def _series(bars: Sequence[Bar]) -> dict[str, list[Bar]]:
    out: dict[str, list[Bar]] = defaultdict(list)
    for b in bars:
        out[b.symbol].append(b)
    return {s: sorted(v, key=lambda b: b.day) for s, v in out.items()}


def breadth(bars: Sequence[Bar], day: date, min_symbols: int = 10) -> Breadth:
    adv = dec = hi = lo = 0
    above50: list[bool] = []
    above200: list[bool] = []
    n = 0
    for sym, rows in _series(bars).items():
        hist = [b for b in rows if b.day <= day]
        if not hist or hist[-1].day != day:
            continue
        n += 1
        last = hist[-1]
        ret = last.close / last.prev_close - 1.0  # adjusted previous close: splits do not count as declines
        adv += ret > 0
        dec += ret < 0
        if len(hist) >= 50:
            above50.append(last.close > mean(b.close for b in hist[-50:]))
        if len(hist) >= 200:
            above200.append(last.close > mean(b.close for b in hist[-200:]))
        window = [b.close for b in hist[-252:]]
        if len(window) >= 60:
            hi += last.close >= max(window)
            lo += last.close <= min(window)
    pct = lambda xs: (100.0 * sum(xs) / len(xs)) if len(xs) >= min_symbols else None
    return Breadth(day, adv, dec, pct(above50), pct(above200), hi, lo, n)


def sector_strength(bars: Sequence[Bar], universe: Mapping[str, Instrument], day: date,
                    windows: Sequence[int] = (20, 60)) -> dict[str, dict[int, float]]:
    """Equal-weight average return (%) per sector over each window of sessions ending on `day`."""
    acc: dict[str, dict[int, list[float]]] = defaultdict(lambda: {w: [] for w in windows})
    for sym, rows in _series(bars).items():
        inst = universe.get(sym)
        hist = [b for b in rows if b.day <= day]
        if inst is None or not hist or hist[-1].day != day:
            continue
        for w in windows:
            if len(hist) > w:
                acc[inst.sector][w].append((hist[-1].close / hist[-1 - w].close - 1.0) * 100.0)
    return {s: {w: mean(v) for w, v in d.items() if v} for s, d in acc.items() if any(d.values())}
