"""Instrument metadata: size segment, sector, liquidity and balance-sheet evidence.

Segments follow the common Indian convention: AMFI ranks 1-100 are large caps,
101-250 mid caps, 251+ small caps. "Micro" (rank > 500) is a widely used but
unofficial label. AMFI re-publishes the list every six months, so supply current
ranks; nothing here knows or guesses a company's size.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from statistics import mean
from typing import Mapping, Sequence

from .nse import Bar


class Segment(str, Enum):
    LARGE = "large"
    MID = "mid"
    SMALL = "small"
    MICRO = "micro"


@dataclass(frozen=True)
class Instrument:
    ticker: str
    segment: Segment
    sector: str
    adv_value_cr: float | None = None        # average daily traded value, Rs crore
    promoter_pledge_pct: float | None = None  # % of promoter holding pledged
    debt_to_equity: float | None = None
    piotroski_f: int | None = None            # 0-9, from quant_agents.fundamentals
    altman_z: float | None = None             # Altman Z'' (not valid for financials)


def segment_from_rank(rank: int) -> Segment:
    if rank < 1:
        raise ValueError("rank starts at 1")
    return Segment.LARGE if rank <= 100 else Segment.MID if rank <= 250 else Segment.SMALL if rank <= 500 else Segment.MICRO


def _opt(row: Mapping[str, str], key: str) -> float | None:
    v = (row.get(key) or "").strip()
    return float(v) if v else None


def load_universe(path: str | Path) -> dict[str, Instrument]:
    """CSV columns: ticker, sector, and either segment or amfi_rank; optional adv_value_cr, promoter_pledge_pct, debt_to_equity, piotroski_f, altman_z."""
    out: dict[str, Instrument] = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(r for r in fh if not r.lstrip().startswith("#")):
            seg_raw = (row.get("segment") or "").strip().lower()
            if seg_raw:
                seg = Segment(seg_raw)
            elif (row.get("amfi_rank") or "").strip():
                seg = segment_from_rank(int(row["amfi_rank"]))
            else:
                raise ValueError(f"{row.get('ticker')}: need segment or amfi_rank")
            t = row["ticker"].strip().upper()
            f = _opt(row, "piotroski_f")
            out[t] = Instrument(t, seg, row["sector"].strip().lower(), _opt(row, "adv_value_cr"),
                                _opt(row, "promoter_pledge_pct"), _opt(row, "debt_to_equity"),
                                None if f is None else int(f), _opt(row, "altman_z"))
    return out


def adv_from_bars(bars: Sequence[Bar], days: int = 20) -> dict[str, float]:
    """Average daily traded value (Rs crore) per symbol over the last `days` sessions of bhavcopy."""
    by: dict[str, list[Bar]] = {}
    for b in bars:
        by.setdefault(b.symbol, []).append(b)
    return {s: mean(x.close * x.volume for x in sorted(v, key=lambda b: b.day)[-days:]) / 1e7 for s, v in by.items()}


def with_adv(universe: Mapping[str, Instrument], adv: Mapping[str, float]) -> dict[str, Instrument]:
    """Fill liquidity from bhavcopy where the universe file left it blank (never overwrites a given value)."""
    return {t: (replace(i, adv_value_cr=adv[t]) if i.adv_value_cr is None and t in adv else i) for t, i in universe.items()}
