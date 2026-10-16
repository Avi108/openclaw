"""Segment- and sector-aware screening and sizing.

Large and mid caps need two agreeing brokers. Small and micro caps are allowed
a single broker (they are thinly covered by nature) but must clear stricter
gates instead: liquidity, promoter pledge, leverage, a smaller position cap, and a
shared cap on the whole small/micro slice. Anything that cannot be verified goes
to a research list with the exact reason, not into a proposal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Mapping

from .consensus import build_consensus
from .ingest import drop_stale, parse_calls
from .models import ConsensusRow
from .risk import PortfolioState, RiskPolicy, check_order, size_position
from .universe import Instrument, Segment
from .workflow import AuditLog

FINANCIAL_SECTORS = {"financials", "banks", "nbfc", "insurance", "financial services"}
SOFT_LIMITS = ("daily order limit reached", "weekly order limit reached")


@dataclass(frozen=True)
class SegmentRules:
    min_brokers: int
    max_order_pct: float
    min_adv_cr: float | None  # liquidity floor, Rs crore traded per day
    require_evidence: bool    # needs pledge and leverage data


DEFAULT_RULES: dict[Segment, SegmentRules] = {
    Segment.LARGE: SegmentRules(2, 5.0, None, False),
    Segment.MID: SegmentRules(2, 4.0, 5.0, False),
    Segment.SMALL: SegmentRules(1, 2.0, 1.0, True),
    Segment.MICRO: SegmentRules(1, 1.0, 0.25, True),
}


@dataclass(frozen=True)
class Limits:
    small_micro_slice_pct: float = 10.0  # all small + micro positions together
    sector_cap_pct: float = 30.0
    max_pledge_pct: float = 20.0
    max_debt_to_equity: float = 1.5      # waived for financial sectors
    min_buy_ratio: float = 0.66


@dataclass(frozen=True)
class Candidate:
    ticker: str
    segment: Segment
    sector: str
    amount: float
    row: ConsensusRow
    caveats: tuple[str, ...]


_MISSING_DATA_MARKERS = ("liquidity unknown", "evidence missing")


@dataclass(frozen=True)
class Rejected:
    ticker: str
    segment: Segment | None
    sector: str | None
    reasons: tuple[str, ...]
    row: ConsensusRow

    @property
    def pending_data(self) -> bool:
        """True when every blocker is missing data: it may qualify once the inputs are supplied."""
        return all(any(m in r for m in _MISSING_DATA_MARKERS) for r in self.reasons)


@dataclass
class SegmentedResult:
    proposals: list[Candidate] = field(default_factory=list)
    queued: list[Candidate] = field(default_factory=list)  # fine, but over today's order limit
    research: list[Rejected] = field(default_factory=list)
    audit: AuditLog = field(default_factory=AuditLog)


def screen(row: ConsensusRow, inst: Instrument | None, rules: Mapping[Segment, SegmentRules], lim: Limits) -> tuple[list[str], list[str]]:
    """Return (blocking reasons, caveats)."""
    if inst is None:
        return ["not in universe: segment and sector unknown"], []
    r = rules[inst.segment]
    reasons: list[str] = []
    caveats: list[str] = []
    if row.score <= 0 or row.median_upside_pct is None:
        reasons.append(f"unscorable ({', '.join(row.flags) or 'no usable upside'})")
    if row.n_brokers < r.min_brokers:
        reasons.append(f"needs >= {r.min_brokers} broker(s)")
    elif row.n_brokers == 1:
        caveats.append("thin coverage (1 broker)")
    if row.buy_ratio < lim.min_buy_ratio:
        reasons.append(f"buy ratio {row.buy_ratio:.0%} below {lim.min_buy_ratio:.0%}")
    for f in ("has-sell", "high-dispersion"):
        if f in row.flags:
            reasons.append(f"flag: {f}")
    if r.min_adv_cr is not None:
        if inst.adv_value_cr is None:
            reasons.append("liquidity unknown (need average daily traded value)")
        elif inst.adv_value_cr < r.min_adv_cr:
            reasons.append(f"illiquid: Rs {inst.adv_value_cr:.2f} cr/day < Rs {r.min_adv_cr:g} cr")
    if r.require_evidence:
        missing = [n for n, v in (("promoter pledge", inst.promoter_pledge_pct), ("debt/equity", inst.debt_to_equity)) if v is None]
        if missing:
            reasons.append("evidence missing: " + ", ".join(missing))
        if inst.promoter_pledge_pct is not None and inst.promoter_pledge_pct > lim.max_pledge_pct:
            reasons.append(f"promoter pledge {inst.promoter_pledge_pct:g}% > {lim.max_pledge_pct:g}%")
        if (inst.debt_to_equity is not None and inst.debt_to_equity > lim.max_debt_to_equity
                and inst.sector not in FINANCIAL_SECTORS):
            reasons.append(f"debt/equity {inst.debt_to_equity:g} > {lim.max_debt_to_equity:g}")
    return reasons, caveats


def run_segmented(
    raw_calls: str,
    state: PortfolioState,
    policy: RiskPolicy,
    universe: Mapping[str, Instrument],
    today: date,
    rules: Mapping[Segment, SegmentRules] = DEFAULT_RULES,
    limits: Limits = Limits(),
    max_age_days: int = 30,
    min_upside_pct: float = 20.0,
) -> SegmentedResult:
    res = SegmentedResult()
    ing = parse_calls(raw_calls)
    fresh = drop_stale(ing.calls, today, max_age_days)
    res.audit.add("ingest", parsed=len(ing.calls), rejected=len(ing.rejected), fresh=len(fresh))
    rows = build_consensus(fresh)
    sim = PortfolioState(state.cash, dict(state.positions), state.peak_value, state.orders_today, state.orders_this_week)
    value = sim.value

    def sector_total(sector: str) -> float:
        return sum(v for t, v in sim.positions.items() if universe.get(t) and universe[t].sector == sector)

    def slice_total() -> float:
        return sum(v for t, v in sim.positions.items()
                   if universe.get(t) and universe[t].segment in (Segment.SMALL, Segment.MICRO))

    for row in rows:
        inst = universe.get(row.ticker)
        if row.median_upside_pct is not None and row.median_upside_pct < min_upside_pct and row.score > 0:
            res.audit.add("screen", ticker=row.ticker, kept=False, why="upside below minimum")
            continue
        reasons, caveats = screen(row, inst, rules, limits)
        if reasons:
            res.research.append(Rejected(row.ticker, inst.segment if inst else None, inst.sector if inst else None, tuple(reasons), row))
            continue
        assert inst is not None
        cap_pct = rules[inst.segment].max_order_pct
        amount = min(size_position(row.ticker, sim, policy), value * cap_pct / 100.0,
                     value * limits.sector_cap_pct / 100.0 - sector_total(inst.sector))
        if inst.segment in (Segment.SMALL, Segment.MICRO):
            amount = min(amount, value * limits.small_micro_slice_pct / 100.0 - slice_total())
        if amount <= 0:
            why = "sector or small/micro slice cap reached"
            res.research.append(Rejected(row.ticker, inst.segment, inst.sector, (why,), row))
            continue
        violations = check_order(row.ticker, amount, sim, policy, today)
        hard = [v for v in violations if v not in SOFT_LIMITS]
        cand = Candidate(row.ticker, inst.segment, inst.sector, round(amount, 2), row, tuple(caveats))
        if hard:
            res.research.append(Rejected(row.ticker, inst.segment, inst.sector, tuple(hard), row))
            continue
        if violations:  # only the per-day / per-week order limits: valid, just not today
            res.queued.append(cand)
            res.audit.add("queued", ticker=row.ticker, reason=violations)
            continue
        res.proposals.append(cand)
        sim.cash -= amount
        sim.positions[row.ticker] = sim.positions.get(row.ticker, 0.0) + amount
        sim.orders_today += 1
        sim.orders_this_week += 1
        res.audit.add("proposal", ticker=row.ticker, segment=inst.segment.value, sector=inst.sector, amount=round(amount, 2))
    return res
