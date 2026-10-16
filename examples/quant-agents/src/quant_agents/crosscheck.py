"""Cross-check NSE against BSE for the same stock on the same day.

A stock listed on both exchanges should close at nearly the same price and show
nearly the same daily return. A large gap means a bad row, a stale file, a
corporate-action mismatch, or a join error. Two independent official sources
agreeing is stronger evidence than either alone.

Join key is ISIN (UDiFF files carry it); rows without an ISIN cannot be matched
and are reported as such rather than matched by name.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median
from collections.abc import Sequence

from .nse import Bar


@dataclass(frozen=True)
class Mismatch:
    isin: str
    day: date
    nse_symbol: str
    bse_symbol: str
    close_diff_pct: float
    return_diff_pp: float  # percentage points difference in adjusted daily return


@dataclass(frozen=True)
class CrossCheck:
    matched: int
    unmatched_nse: int
    unmatched_bse: int
    no_isin: int
    median_close_diff_pct: float | None
    mismatches: tuple[Mismatch, ...]

    @property
    def clean(self) -> bool:
        return self.matched > 0 and not self.mismatches


def cross_check(
    nse: Sequence[Bar], bse: Sequence[Bar], close_tol_pct: float = 1.0, return_tol_pp: float = 1.0
) -> CrossCheck:
    no_isin = sum(1 for b in (*nse, *bse) if not b.isin)
    n = {(b.isin, b.day): b for b in nse if b.isin}
    e = {(b.isin, b.day): b for b in bse if b.isin}
    keys = sorted(set(n) & set(e), key=lambda k: (k[1], k[0]))
    diffs: list[float] = []
    bad: list[Mismatch] = []
    for k in keys:
        a, b = n[k], e[k]
        cd = abs(a.close / b.close - 1.0) * 100
        rd = abs((a.close / a.prev_close - 1.0) - (b.close / b.prev_close - 1.0)) * 100
        diffs.append(cd)
        if cd > close_tol_pct or rd > return_tol_pp:  # pragma: no mutate (equivalent: strict vs non-strict only differs at an exactly-equal float, which arithmetic does not produce)
            bad.append(Mismatch(k[0], k[1], a.symbol, b.symbol, cd, rd))
    return CrossCheck(
        matched=len(keys),
        unmatched_nse=len(set(n) - set(e)),
        unmatched_bse=len(set(e) - set(n)),
        no_isin=no_isin,
        median_close_diff_pct=median(diffs) if diffs else None,
        mismatches=tuple(bad),
    )


def agreed_close(nse: Bar, bse: Bar, tol_pct: float = 1.0) -> float | None:
    """A price to use only when both exchanges agree within tol; otherwise None (never pick one)."""
    if abs(nse.close / bse.close - 1.0) * 100 > tol_pct:
        return None
    return (nse.close + bse.close) / 2.0
