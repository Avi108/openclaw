"""Aggregate broker calls per ticker and flag suspicious upside figures."""
from __future__ import annotations

from collections import defaultdict
from statistics import median

from .models import BrokerCall, ConsensusRow, Rating

# Upside above this is far more often a data error or a stale/low price than a real call.
IMPLAUSIBLE_UPSIDE_PCT = 120.0
HIGH_DISPERSION = 0.5


def build_consensus(calls: list[BrokerCall]) -> list[ConsensusRow]:
    by_ticker: dict[str, list[BrokerCall]] = defaultdict(list)
    for c in calls:
        by_ticker[c.ticker].append(c)

    rows: list[ConsensusRow] = []
    for ticker, group in by_ticker.items():
        # One vote per broker: keep each broker's latest call.
        latest: dict[str, BrokerCall] = {}
        for c in sorted(group, key=lambda c: c.published):
            latest[c.broker] = c
        votes = list(latest.values())

        targets = [c.target for c in votes]
        med_target = median(targets)
        dispersion = (max(targets) - min(targets)) / med_target
        upsides = [u for c in votes if (u := c.upside_pct) is not None]
        med_upside = median(upsides) if upsides else None
        buy_ratio = sum(c.rating is Rating.BUY for c in votes) / len(votes)

        flags: list[str] = []
        if len(votes) == 1:
            flags.append("single-broker")
        if med_upside is None:
            flags.append("no-price")
        elif med_upside > IMPLAUSIBLE_UPSIDE_PCT:
            flags.append("implausible-upside")
        if dispersion > HIGH_DISPERSION:
            flags.append("high-dispersion")
        if any(c.rating is Rating.SELL for c in votes):
            flags.append("has-sell")

        rows.append(
            ConsensusRow(
                ticker=ticker,
                n_brokers=len(votes),
                buy_ratio=buy_ratio,
                median_target=med_target,
                target_dispersion=dispersion,
                median_upside_pct=med_upside,
                flags=tuple(flags),
                score=_score(len(votes), buy_ratio, med_upside, dispersion, flags),
            )
        )
    return sorted(rows, key=lambda r: r.score, reverse=True)


def _score(n: int, buy_ratio: float, upside: float | None, dispersion: float, flags: list[str]) -> float:
    """Confidence-weighted upside: breadth and agreement matter more than a headline number."""
    if upside is None or "implausible-upside" in flags:
        return 0.0
    breadth = min(n, 4) / 4  # saturates at 4 brokers
    agreement = max(0.0, 1.0 - dispersion)
    capped_upside = min(upside, 80.0)  # diminishing trust in very large targets
    return round(capped_upside * buy_ratio * (0.5 * breadth + 0.5 * agreement), 2)
