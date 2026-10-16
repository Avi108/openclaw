"""India equity capital-gains tax estimate for listed shares held in a demat account.

THE RATES CHANGE. The defaults below are the rates in force after the July 2024
Union Budget as I know them; public reports in Oct 2026 mention a Finance Minister
statement on LTCG and STT, so confirm the current rates (and holding period) with
the Income Tax department or a tax professional before relying on any output.
`TaxRules` is deliberately a plain dataclass so you can set the right values.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence


@dataclass(frozen=True)
class TaxRules:
    stcg_rate: float = 0.20          # short-term gains on listed equity
    ltcg_rate: float = 0.125         # long-term gains above the exemption
    ltcg_exemption: float = 125_000  # per financial year, rupees
    ltcg_holding_days: int = 365     # held more than this many days is long-term
    cess: float = 0.04               # health and education cess on the tax


@dataclass(frozen=True)
class TaxEstimate:
    stcg: float
    ltcg: float
    taxable_ltcg: float
    tax: float


def estimate_tax(gains: Sequence[tuple[date, date, float]], rules: TaxRules = TaxRules()) -> TaxEstimate:
    """`gains` = (buy date, sell date, gain in rupees; negative for a loss) for ONE financial year.

    Set-off modelled: a net short-term loss offsets long-term gains; a net long-term loss does not
    offset short-term gains. Brought-forward losses are not modelled.
    """
    stcg = ltcg = 0.0
    for buy, sell, g in gains:
        if sell < buy:
            raise ValueError("sell date before buy date")
        if (sell - buy).days > rules.ltcg_holding_days:
            ltcg += g
        else:
            stcg += g
    if stcg < 0:
        ltcg += stcg
        stcg = 0.0
    taxable_ltcg = max(0.0, ltcg - rules.ltcg_exemption)
    base = max(stcg, 0.0) * rules.stcg_rate + taxable_ltcg * rules.ltcg_rate
    return TaxEstimate(max(stcg, 0.0), max(ltcg, 0.0), taxable_ltcg, base * (1 + rules.cess))
