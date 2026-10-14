"""Data-quality gates for research output (human- or LLM-written).

Prior art: the prompt-only `india-equity-report` skill (MIT) enforces "every
number needs a source tag" and "never trust a single stale price" as written
instructions. These checks make those rules executable and testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# A number worth sourcing: currency amounts, percentages, or multiples such as 17.5x.
_CLAIM = re.compile(r"(?:(?:Rs\.?|INR|₹|\$)\s*\d[\d,]*\.?\d*(?:\s*(?:cr|crore|bn|mn|lakh))?|\d[\d,]*\.?\d*\s*(?:%|x\b))", re.I)
_SOURCE_TAG = re.compile(r"\[Source:\s*[^\]]+\]", re.I)


@dataclass(frozen=True)
class PriceCheck:
    ok: bool
    divergence_pct: float | None
    reasons: tuple[str, ...]


def check_price(
    quotes: dict[str, tuple[float, date]],
    today: date,
    max_divergence_pct: float = 3.0,
    max_age_days: int = 3,
) -> PriceCheck:
    """Cross-check a price quoted by several sources.

    Needs >= 2 independent sources; fails on stale quotes or when the highest
    and lowest quote differ by more than `max_divergence_pct`.
    """
    reasons: list[str] = []
    if len(quotes) < 2:
        reasons.append("need at least two independent price sources")
    for name, (px, as_of) in quotes.items():
        if px <= 0:
            reasons.append(f"{name}: non-positive price")
        age = (today - as_of).days
        if age < 0:
            reasons.append(f"{name}: dated in the future")
        elif age > max_age_days:
            reasons.append(f"{name}: stale by {age} days")
    prices = [p for p, _ in quotes.values() if p > 0]
    divergence = None
    if len(prices) >= 2:
        lo, hi = min(prices), max(prices)
        divergence = (hi / lo - 1.0) * 100.0
        if divergence > max_divergence_pct:
            reasons.append(f"sources diverge by {divergence:.1f}% (> {max_divergence_pct}%)")
    return PriceCheck(ok=not reasons, divergence_pct=divergence, reasons=tuple(reasons))


@dataclass(frozen=True)
class SourceAudit:
    claims: int
    sourced: int
    unsourced: tuple[str, ...]

    @property
    def coverage(self) -> float:
        return 1.0 if self.claims == 0 else self.sourced / self.claims


def audit_sources(report: str) -> SourceAudit:
    """Per line, require a [Source: ...] tag whenever a line contains numeric claims.

    Granularity is the line (table row or bullet), which is how analyst reports
    usually attach a citation.
    """
    claims = sourced = 0
    unsourced: list[str] = []
    for line in report.splitlines():
        found = _CLAIM.findall(line)
        if not found or line.lstrip().startswith("#"):
            continue
        claims += len(found)
        if _SOURCE_TAG.search(line):
            sourced += len(found)
        else:
            unsourced.append(line.strip())
    return SourceAudit(claims, sourced, tuple(unsourced))


def implied_upside_ok(target: float, price: float, max_pct: float = 120.0) -> bool:
    """Sanity bound used before a target is trusted; mirrors consensus.IMPLAUSIBLE_UPSIDE_PCT."""
    return price > 0 and target > 0 and (target / price - 1.0) * 100.0 <= max_pct
