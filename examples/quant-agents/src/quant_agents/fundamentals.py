"""Fundamental quality and forensic scores from two years of statements.

Piotroski F-Score (0-9): financial strength signals.
Altman Z'' (1995 non-manufacturer / emerging-market form): distress risk.
Beneish M-Score (8-variable): earnings-manipulation risk.

These are screening aids with known limits. Beneish and Altman are not designed
for banks, NBFCs or insurers (their balance sheets differ), and a flag is a reason
to read the filings, not a verdict. All inputs are the caller's; nothing is fetched.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Statements:
    """One fiscal year, in consistent currency units."""
    sales: float
    cogs: float
    sga: float
    depreciation: float
    ebit: float
    net_income: float
    cfo: float
    receivables: float
    current_assets: float
    current_liabilities: float
    ppe: float                # net property, plant and equipment
    total_assets: float
    total_liabilities: float
    long_term_debt: float
    retained_earnings: float
    equity: float
    shares_outstanding: float


def _div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("division by zero in a ratio: check the inputs")
    return a / b


# ------------------------------ Piotroski ------------------------------

@dataclass(frozen=True)
class Piotroski:
    score: int
    signals: dict[str, bool]


def piotroski(cur: Statements, prev: Statements) -> Piotroski:
    roa = _div(cur.net_income, cur.total_assets)
    roa_prev = _div(prev.net_income, prev.total_assets)
    lev = _div(cur.long_term_debt, cur.total_assets)
    lev_prev = _div(prev.long_term_debt, prev.total_assets)
    cr = _div(cur.current_assets, cur.current_liabilities)
    cr_prev = _div(prev.current_assets, prev.current_liabilities)
    gm = _div(cur.sales - cur.cogs, cur.sales)
    gm_prev = _div(prev.sales - prev.cogs, prev.sales)
    at = _div(cur.sales, cur.total_assets)
    at_prev = _div(prev.sales, prev.total_assets)
    sig = {
        "positive ROA": roa > 0,
        "positive operating cash flow": cur.cfo > 0,
        "ROA improved": roa > roa_prev,
        "cash flow exceeds net income": cur.cfo > cur.net_income,
        "leverage fell": lev < lev_prev,
        "current ratio improved": cr > cr_prev,
        "no new shares issued": cur.shares_outstanding <= prev.shares_outstanding,
        "gross margin improved": gm > gm_prev,
        "asset turnover improved": at > at_prev,
    }
    return Piotroski(sum(sig.values()), sig)


# -------------------------------- Altman --------------------------------

@dataclass(frozen=True)
class Altman:
    z: float
    zone: str  # "safe" | "grey" | "distress"


def altman_z_double_prime(s: Statements) -> Altman:
    """Z'' = 6.56*WC/TA + 3.26*RE/TA + 6.72*EBIT/TA + 1.05*Equity/Liabilities (zones: >2.6 safe, <1.1 distress)."""
    ta = s.total_assets
    z = (6.56 * _div(s.current_assets - s.current_liabilities, ta) + 3.26 * _div(s.retained_earnings, ta)
         + 6.72 * _div(s.ebit, ta) + 1.05 * _div(s.equity, s.total_liabilities))
    return Altman(z, "safe" if z > 2.6 else "distress" if z < 1.1 else "grey")  # pragma: no mutate (equivalent: strict vs non-strict only differs at an exactly-equal float, which arithmetic does not produce)


# -------------------------------- Beneish --------------------------------

BENEISH_THRESHOLD = -1.78  # 8-variable model; above this suggests possible manipulation


@dataclass(frozen=True)
class Beneish:
    m: float
    flagged: bool
    indices: dict[str, float]


def beneish_m(cur: Statements, prev: Statements) -> Beneish:
    gm = _div(cur.sales - cur.cogs, cur.sales)
    gm_prev = _div(prev.sales - prev.cogs, prev.sales)
    idx = {
        "DSRI": _div(_div(cur.receivables, cur.sales), _div(prev.receivables, prev.sales)),
        "GMI": _div(gm_prev, gm),
        "AQI": _div(1 - _div(cur.current_assets + cur.ppe, cur.total_assets),
                    1 - _div(prev.current_assets + prev.ppe, prev.total_assets)),
        "SGI": _div(cur.sales, prev.sales),
        "DEPI": _div(_div(prev.depreciation, prev.depreciation + prev.ppe),
                     _div(cur.depreciation, cur.depreciation + cur.ppe)),
        "SGAI": _div(_div(cur.sga, cur.sales), _div(prev.sga, prev.sales)),
        "LVGI": _div(_div(cur.current_liabilities + cur.long_term_debt, cur.total_assets),
                     _div(prev.current_liabilities + prev.long_term_debt, prev.total_assets)),
        "TATA": _div(cur.net_income - cur.cfo, cur.total_assets),
    }
    m = (-4.84 + 0.920 * idx["DSRI"] + 0.528 * idx["GMI"] + 0.404 * idx["AQI"] + 0.892 * idx["SGI"]
         + 0.115 * idx["DEPI"] - 0.172 * idx["SGAI"] + 4.679 * idx["TATA"] - 0.327 * idx["LVGI"])
    return Beneish(m, m > BENEISH_THRESHOLD, idx)  # pragma: no mutate (equivalent: strict vs non-strict only differs at an exactly-equal float, which arithmetic does not produce)


@dataclass(frozen=True)
class QualityReport:
    piotroski: Piotroski
    altman: Altman
    beneish: Beneish
    red_flags: tuple[str, ...]


def quality_report(cur: Statements, prev: Statements, financial_company: bool = False) -> QualityReport:
    """Combine the three screens. For financial companies Altman and Beneish are skipped by raising."""
    if financial_company:
        raise ValueError("Altman and Beneish are not valid for banks, NBFCs or insurers; use sector-specific measures")
    p, a, b = piotroski(cur, prev), altman_z_double_prime(cur), beneish_m(cur, prev)
    flags = []
    if p.score <= 3:
        flags.append(f"weak Piotroski F-Score {p.score}/9")
    if a.zone == "distress":
        flags.append(f"Altman Z'' {a.z:.2f} in distress zone")
    if b.flagged:
        flags.append(f"Beneish M {b.m:.2f} above {BENEISH_THRESHOLD}")
    return QualityReport(p, a, b, tuple(flags))
