"""Behavioral guardrails: a pre-trade gate and a loss circuit breaker.

Most account damage comes from process failures (no plan, chasing losses, trading
through a drawdown) rather than from analysis. These checks are mechanical and
write a journal line for every decision, allowed or blocked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from collections.abc import Sequence

from .thesis import Thesis


@dataclass(frozen=True)
class Closed:
    day: date
    pnl: float  # realized profit or loss, rupees


@dataclass(frozen=True)
class DisciplinePolicy:
    max_losing_streak: int = 3
    cooldown_days: int = 2            # pause after a losing streak
    weekly_loss_limit_pct: float = 5.0
    monthly_loss_limit_pct: float = 10.0
    cool_off_days_after_loss: int = 1  # no new entry the day of or after a realized loss


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reasons: tuple[str, ...]


@dataclass
class Journal:
    entries: list[dict] = field(default_factory=list)


def circuit_breaker(closed: Sequence[Closed], portfolio_value: float, today: date,
                    policy: DisciplinePolicy = DisciplinePolicy()) -> Decision:
    ordered = sorted(closed, key=lambda c: c.day)
    reasons: list[str] = []
    streak = 0
    for c in reversed(ordered):
        if c.pnl < 0:
            streak += 1
        else:
            break
    if streak >= policy.max_losing_streak:
        last_loss = ordered[-1].day
        if (today - last_loss).days < policy.cooldown_days:
            reasons.append(f"losing streak of {streak}: cooling down until {last_loss + timedelta(days=policy.cooldown_days)}")
    for days, limit, label in ((7, policy.weekly_loss_limit_pct, "weekly"), (30, policy.monthly_loss_limit_pct, "monthly")):
        net = -sum(c.pnl for c in ordered if 0 <= (today - c.day).days < days)
        if portfolio_value > 0 and max(net, 0.0) / portfolio_value * 100 >= limit:
            reasons.append(f"{label} net loss {max(net, 0.0) / portfolio_value * 100:.1f}% reached limit {limit:g}%")
    return Decision(not reasons, tuple(reasons))


def pre_trade_gate(ticker: str, thesis: Thesis | None, closed: Sequence[Closed], portfolio_value: float, today: date,
                   regime_blocked: bool = False, policy: DisciplinePolicy = DisciplinePolicy(),
                   journal: Journal | None = None) -> Decision:
    reasons: list[str] = []
    if thesis is None or not thesis.statement.strip():
        reasons.append("planless: no written thesis")
    elif not thesis.bear_case.strip():
        reasons.append("planless: no bear case written")
    elif not thesis.invalidations:
        reasons.append("planless: no invalidation condition")
    if any(0 <= (today - c.day).days <= policy.cool_off_days_after_loss and c.pnl < 0 for c in closed):
        reasons.append("cooling off after a realized loss")
    if regime_blocked:
        reasons.append("market regime blocks new entries")
    reasons += list(circuit_breaker(closed, portfolio_value, today, policy).reasons)
    d = Decision(not reasons, tuple(reasons))
    if journal is not None:
        journal.entries.append({"day": today.isoformat(), "ticker": ticker, "allowed": d.allowed, "reasons": list(d.reasons)})
    return d
