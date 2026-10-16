"""Valuation helpers: DCF, reverse DCF, Graham number, margin of safety.

A DCF is only as good as its assumptions. The useful output is usually the
*reverse* DCF: what growth the current price already assumes, which you can then
judge for plausibility instead of trusting a single point estimate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def dcf_per_share(fcf0: float, growth: float, years: int, terminal_growth: float, wacc: float,
                  net_debt: float = 0.0, shares: float = 1.0) -> float:
    """Value per share: `years` of FCF growing at `growth`, then a Gordon terminal value."""
    if wacc <= terminal_growth:
        raise ValueError("wacc must exceed terminal growth")
    if years < 1 or shares <= 0:
        raise ValueError("years >= 1 and shares > 0 required")
    pv, fcf = 0.0, fcf0
    for t in range(1, years + 1):
        fcf *= 1 + growth
        pv += fcf / (1 + wacc) ** t
    tv = fcf * (1 + terminal_growth) / (wacc - terminal_growth)
    pv += tv / (1 + wacc) ** years
    return (pv - net_debt) / shares


def reverse_dcf_growth(price: float, fcf0: float, years: int, terminal_growth: float, wacc: float,
                       net_debt: float = 0.0, shares: float = 1.0, lo: float = -0.5, hi: float = 1.0) -> float:
    """Constant growth over `years` that makes DCF value equal `price` (bisection; value rises with growth)."""
    f = lambda g: dcf_per_share(fcf0, g, years, terminal_growth, wacc, net_debt, shares) - price
    if f(lo) > 0 or f(hi) < 0:
        raise ValueError(f"no growth in [{lo:.0%}, {hi:.0%}] reproduces price {price}: check inputs")
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < 0 else (lo, mid)
    return (lo + hi) / 2


def sensitivity(fcf0: float, growth: float, years: int, waccs: list[float], terminal_growths: list[float],
                net_debt: float = 0.0, shares: float = 1.0) -> dict[tuple[float, float], float]:
    """Value per share across a WACC x terminal-growth grid; invalid cells (wacc <= g) are omitted."""
    return {(w, g): dcf_per_share(fcf0, growth, years, g, w, net_debt, shares)
            for w in waccs for g in terminal_growths if w > g}


def graham_number(eps: float, bvps: float) -> float:
    """sqrt(22.5 * EPS * BVPS): Graham's ceiling price for a defensive investor. Zero when either input is not positive."""
    return math.sqrt(22.5 * eps * bvps) if eps > 0 and bvps > 0 else 0.0


def margin_of_safety(price: float, intrinsic: float) -> float:
    """Fraction below intrinsic value the price sits (positive is a discount); needs intrinsic > 0."""
    if intrinsic <= 0:
        raise ValueError("intrinsic value must be positive")
    return 1.0 - price / intrinsic


@dataclass(frozen=True)
class ValuationView:
    intrinsic_base: float
    margin_of_safety_pct: float
    implied_growth_pct: float | None
    note: str


def valuation_view(price: float, fcf0: float, growth: float, years: int, terminal_growth: float, wacc: float,
                   net_debt: float = 0.0, shares: float = 1.0) -> ValuationView:
    base = dcf_per_share(fcf0, growth, years, terminal_growth, wacc, net_debt, shares)
    try:
        implied = reverse_dcf_growth(price, fcf0, years, terminal_growth, wacc, net_debt, shares) * 100
    except ValueError:
        implied = None
    mos = margin_of_safety(price, base) * 100 if base > 0 else float("nan")
    note = ("price already assumes more growth than your base case" if implied is not None and implied > growth * 100
            else "price assumes less growth than your base case" if implied is not None else "implied growth not solvable")
    return ValuationView(base, mos, implied, note)
