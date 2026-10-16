"""Agent workflow: ingest -> validate -> consensus -> size -> risk check -> human approval.

Design rules:
  * The pipeline only *proposes* orders. Execution is a separate, injected
    `Broker` that is called solely for orders a human approved.
  * The approval gate defaults to deny, so a missing or broken gate cannot place trades.
  * Every stage appends to an audit log (JSON lines), including rejections.
  * An optional `Analyst` (e.g. an LLM) may *veto or annotate* a candidate but
    can never raise a size above what the risk layer allows.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Protocol
from collections.abc import Callable

from .consensus import build_consensus
from .ingest import drop_stale, parse_calls
from .models import ConsensusRow
from .risk import PortfolioState, RiskPolicy, check_order, size_position


@dataclass(frozen=True)
class Proposal:
    ticker: str
    amount: float
    reason: str
    consensus: ConsensusRow


class Analyst(Protocol):
    def review(self, row: ConsensusRow) -> tuple[bool, str]:
        """Return (keep, note). Return keep=False to veto the candidate."""


class Broker(Protocol):
    def place_limit_buy(self, ticker: str, amount: float) -> str: ...


class RuleAnalyst:
    """Deterministic default analyst; swap for an LLM-backed implementation."""

    def __init__(self, min_brokers: int = 2, min_buy_ratio: float = 0.66) -> None:
        self.min_brokers, self.min_buy_ratio = min_brokers, min_buy_ratio

    def review(self, row: ConsensusRow) -> tuple[bool, str]:
        if row.n_brokers < self.min_brokers:
            return False, f"needs >= {self.min_brokers} brokers"
        if row.buy_ratio < self.min_buy_ratio:
            return False, f"buy ratio {row.buy_ratio:.0%} below {self.min_buy_ratio:.0%}"
        if row.flags and any(f in row.flags for f in ("has-sell", "high-dispersion")):
            return False, f"flags: {', '.join(row.flags)}"
        return True, "passes rule checks"


def deny_all(_: Proposal) -> bool:
    return False


@dataclass
class AuditLog:
    events: list[dict] = field(default_factory=list)

    def add(self, stage: str, **data: object) -> None:
        self.events.append({"stage": stage, **data})

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(e, sort_keys=True, default=str) for e in self.events)


@dataclass
class RunResult:
    proposals: list[Proposal]
    approved: list[Proposal]
    placed: list[tuple[str, str]]  # (ticker, broker order id)
    audit: AuditLog


def run(
    raw_calls: str,
    state: PortfolioState,
    policy: RiskPolicy,
    today: date,
    analyst: Analyst | None = None,
    approve: Callable[[Proposal], bool] = deny_all,
    broker: Broker | None = None,
    max_age_days: int = 30,
    min_upside_pct: float = 20.0,
    vol_lookup: Callable[[str], float | None] = lambda _t: None,
) -> RunResult:
    analyst = analyst or RuleAnalyst()
    audit = AuditLog()

    ingested = parse_calls(raw_calls)
    audit.add("ingest", parsed=len(ingested.calls), rejected=[list(r) for r in ingested.rejected])
    fresh = drop_stale(ingested.calls, today, max_age_days)
    audit.add("freshness", kept=len(fresh), dropped=len(ingested.calls) - len(fresh))

    rows = build_consensus(fresh)
    proposals: list[Proposal] = []
    sim = PortfolioState(state.cash, dict(state.positions), state.peak_value, state.orders_today, state.orders_this_week)

    for row in rows:
        if row.score <= 0 or row.median_upside_pct is None or row.median_upside_pct < min_upside_pct:
            audit.add("screen", ticker=row.ticker, kept=False, flags=list(row.flags), score=row.score)
            continue
        keep, note = analyst.review(row)
        audit.add("analyst", ticker=row.ticker, kept=keep, note=note)
        if not keep:
            continue
        amount = size_position(row.ticker, sim, policy, vol_lookup(row.ticker))
        violations = check_order(row.ticker, amount, sim, policy, today)
        if violations:
            audit.add("risk", ticker=row.ticker, amount=amount, blocked=violations)
            continue
        proposals.append(
            Proposal(row.ticker, round(amount, 2), f"median upside {row.median_upside_pct:.0f}%, {row.n_brokers} brokers", row)
        )
        # Reserve the capital so later proposals in the same run see the reduced cash.
        sim.cash -= amount
        sim.positions[row.ticker] = sim.positions.get(row.ticker, 0.0) + amount
        sim.orders_today += 1
        sim.orders_this_week += 1
        audit.add("proposal", ticker=row.ticker, amount=round(amount, 2))

    approved: list[Proposal] = []
    placed: list[tuple[str, str]] = []
    for p in proposals:
        ok = approve(p)
        audit.add("approval", ticker=p.ticker, approved=ok)
        if not ok:
            continue
        approved.append(p)
        if broker is not None:
            order_id = broker.place_limit_buy(p.ticker, p.amount)
            placed.append((p.ticker, order_id))
            audit.add("order", ticker=p.ticker, amount=p.amount, order_id=order_id)
    return RunResult(proposals, approved, placed, audit)


def proposal_dict(p: Proposal) -> dict:
    d = asdict(p)
    d["consensus"]["flags"] = list(p.consensus.flags)
    return d
