"""Turn semi-structured broker-call text into validated BrokerCall records.

Real news snippets are messy: missing prices, mixed currency symbols, one call
per line or several per paragraph. The parser extracts what it can and reports
what it rejected, so downstream agents never see silently-guessed numbers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from .models import BrokerCall, Rating

_NUM = r"(\d[\d,]*\.?\d*)"
_CCY = r"(?:(?:Rs\.?|INR|₹|\$|USD)\s*)"
_LINE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s*\|\s*(?P<ticker>[A-Z0-9&.\-]+)\s*\|\s*(?P<broker>[^|]+?)\s*\|\s*"
    r"(?P<rating>buy|add|accumulate|hold|neutral|sell|outperform|overweight|reduce|underperform)\s*\|\s*"
    rf"target\s*{_CCY}?(?P<target>{_NUM})\s*(?:\|\s*(?:cmp|price)\s*{_CCY}?(?P<price>{_NUM}))?"
    r"(?:\s*\|\s*(?P<source>\S+))?\s*$",
    re.IGNORECASE,
)
RATING_MAP = {
    "buy": Rating.BUY, "outperform": Rating.BUY, "overweight": Rating.BUY,
    "add": Rating.BUY, "accumulate": Rating.BUY,  # common Indian-broker wording
    "hold": Rating.HOLD, "neutral": Rating.HOLD,
    "sell": Rating.SELL, "reduce": Rating.SELL, "underperform": Rating.SELL,
}


@dataclass
class IngestResult:
    calls: list[BrokerCall]
    rejected: list[tuple[int, str, str]]  # (line number, text, reason)


def _num(raw: str) -> float:
    return float(raw.replace(",", ""))


def parse_calls(text: str) -> IngestResult:
    calls: list[BrokerCall] = []
    rejected: list[tuple[int, str, str]] = []
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            rejected.append((i, line, "unparseable"))
            continue
        try:
            published = datetime.strptime(m["date"], "%Y-%m-%d").date()
        except ValueError:
            rejected.append((i, line, "bad date"))
            continue
        target = _num(m["target"])
        price = _num(m["price"]) if m["price"] else None
        if target <= 0 or (price is not None and price <= 0):
            rejected.append((i, line, "non-positive number"))
            continue
        calls.append(
            BrokerCall(
                ticker=m["ticker"].upper(),
                broker=m["broker"].strip(),
                rating=RATING_MAP[m["rating"].lower()],
                target=target,
                price=price,
                published=published,
                source=m["source"] or "",
            )
        )
    return IngestResult(calls=calls, rejected=rejected)


def drop_stale(calls: list[BrokerCall], today: date, max_age_days: int = 30) -> list[BrokerCall]:
    return [c for c in calls if 0 <= (today - c.published).days <= max_age_days]
