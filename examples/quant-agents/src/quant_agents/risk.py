"""Position sizing and hard risk limits.

Limits mirror `examples/stock-analyst-agent/RISK-POLICY.md`. Sizing returns the
*smallest* of every applicable cap, so a limit can never be exceeded by a
sizing heuristic. Policy checks are separate and report every violation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class RiskPolicy:
    max_position_pct: float = 15.0      # of portfolio value, per ticker
    max_order_pct: float = 5.0          # new capital per order
    min_cash_pct: float = 10.0
    max_orders_per_day: int = 2
    max_orders_per_week: int = 5
    halt_drawdown_pct: float = 15.0     # halt all trading beyond this drawdown from peak
    target_daily_vol_pct: float = 1.0   # per-position volatility budget, as % of portfolio


@dataclass
class PortfolioState:
    cash: float
    positions: dict[str, float] = field(default_factory=dict)  # ticker -> market value
    peak_value: float = 0.0
    orders_today: int = 0
    orders_this_week: int = 0

    @property
    def value(self) -> float:
        return self.cash + sum(self.positions.values())

    @property
    def drawdown_pct(self) -> float:
        peak = max(self.peak_value, self.value)
        return 0.0 if peak <= 0 else (1.0 - self.value / peak) * 100.0


def size_position(
    ticker: str,
    state: PortfolioState,
    policy: RiskPolicy,
    daily_vol_pct: float | None = None,
) -> float:
    """Return the maximum buy amount (currency) allowed for `ticker`; never negative."""
    value = state.value
    caps = [
        value * policy.max_order_pct / 100.0,
        value * policy.max_position_pct / 100.0 - state.positions.get(ticker, 0.0),
        state.cash - value * policy.min_cash_pct / 100.0,
    ]
    if daily_vol_pct and daily_vol_pct > 0:
        # Volatile names get smaller slots: size so a 1-sigma day costs ~target_daily_vol_pct of the portfolio.
        caps.append(value * policy.target_daily_vol_pct / daily_vol_pct)
    return max(0.0, min(caps))


def check_order(
    ticker: str,
    amount: float,
    state: PortfolioState,
    policy: RiskPolicy,
    today: date | None = None,
) -> list[str]:
    """Return every policy violation for a proposed buy; empty list means allowed."""
    value = state.value
    violations: list[str] = []
    if amount <= 0:
        violations.append("non-positive amount")
    if state.drawdown_pct > policy.halt_drawdown_pct:
        violations.append(f"trading halted: drawdown {state.drawdown_pct:.1f}% > {policy.halt_drawdown_pct}%")
    if amount > value * policy.max_order_pct / 100.0 + 1e-9:
        violations.append(f"order exceeds {policy.max_order_pct}% of portfolio")
    new_pos = state.positions.get(ticker, 0.0) + amount
    if new_pos > value * policy.max_position_pct / 100.0 + 1e-9:
        violations.append(f"position would exceed {policy.max_position_pct}% of portfolio")
    if state.cash - amount < value * policy.min_cash_pct / 100.0 - 1e-9:
        violations.append(f"cash would fall below {policy.min_cash_pct}% reserve")
    if state.orders_today >= policy.max_orders_per_day:
        violations.append("daily order limit reached")
    if state.orders_this_week >= policy.max_orders_per_week:
        violations.append("weekly order limit reached")
    return violations


def risk_based_qty(portfolio_value: float, risk_pct: float, entry: float, stop: float) -> int:
    """Whole shares so that being stopped out loses `risk_pct` of the portfolio (e.g. 1.0 = 1%).

    Position size follows the stop distance, not a hunch: a wide stop means a smaller position.
    """
    if not (0 < stop < entry):
        raise ValueError("need 0 < stop < entry for a long position")
    return int(portfolio_value * risk_pct / 100.0 // (entry - stop))


def kelly_fraction(win_prob: float, win_loss_ratio: float, fraction: float = 0.25, cap: float = 0.10) -> float:
    """Fractional Kelly bet size, capped. Returns 0 when the edge is not positive.

    Kelly f* = p - (1-p)/b. Estimating p and b from a small sample overstates them, so the default is a
    quarter of Kelly with a hard cap; use it as an upper bound only.
    """
    if not (0 < win_prob < 1) or win_loss_ratio <= 0:
        raise ValueError("need 0 < win_prob < 1 and win_loss_ratio > 0")
    f = win_prob - (1 - win_prob) / win_loss_ratio
    return max(0.0, min(cap, f * fraction))
