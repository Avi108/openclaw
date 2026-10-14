"""Extract one broker call from a research-report's text (or PDF).

Indian broker PDFs differ wildly in layout, so the extractor is deliberately
conservative: it returns a call only when rating, target and (optionally)
price are unambiguous, and otherwise returns the reasons it refused. A wrong
number is worse than a missing one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .ingest import RATING_MAP
from .models import BrokerCall

_AMT = r"(?:Rs\.?|INR|₹)?\s*(\d[\d,]*(?:\.\d+)?)"
_TARGET = re.compile(rf"(?:target\s*price|price\s*target|\bTP\b|\btarget\b)\s*(?:\(\s*12\s*[- ]?m(?:onths?)?\s*\))?\s*[:\-–]?\s*{_AMT}", re.I)
_PRICE = re.compile(rf"(?:\bCMP\b|current\s*market\s*price|\bLTP\b|closing\s*price)\s*(?:\(.*?\))?\s*[:\-–]?\s*{_AMT}", re.I)
_RATING = re.compile(r"\b(?:rating|recommendation|reco)\s*[:\-–]?\s*(buy|add|accumulate|hold|neutral|sell|reduce|outperform|overweight|underperform)\b", re.I)
_RATING_BARE = re.compile(r"\b(BUY|ADD|ACCUMULATE|HOLD|NEUTRAL|SELL|REDUCE|OUTPERFORM|OVERWEIGHT|UNDERPERFORM)\b")


@dataclass(frozen=True)
class Extraction:
    call: BrokerCall | None
    reasons: tuple[str, ...]


def _unique_numbers(matches: list[str]) -> set[float]:
    return {float(m.replace(",", "")) for m in matches}


def extract_call(text: str, ticker: str, broker: str, published: date, source: str = "") -> Extraction:
    head = text[:6000]  # the front page carries the call; later pages quote old ones
    reasons: list[str] = []

    # All-caps tokens count too, so "Rating: BUY ... SELL" is caught as a conflict
    # while lowercase prose ("sell-side") is ignored.
    ratings = {m.lower() for m in _RATING.findall(head)} | {m.lower() for m in _RATING_BARE.findall(head)}
    mapped = {RATING_MAP[r] for r in ratings}
    if not ratings:
        reasons.append("no rating found")
    elif len(mapped) > 1:
        reasons.append(f"conflicting ratings: {sorted(ratings)}")

    targets = _unique_numbers(_TARGET.findall(head))
    if not targets:
        reasons.append("no target price found")
    elif len(targets) > 1:
        reasons.append(f"multiple target prices: {sorted(targets)}")

    if reasons:
        return Extraction(None, tuple(reasons))

    # An ambiguous price is recorded as unknown (never guessed); consensus then flags `no-price`.
    prices = _unique_numbers(_PRICE.findall(head))
    price = next(iter(prices)) if len(prices) == 1 else None
    notes = (f"multiple prices: {sorted(prices)}; price left unknown",) if len(prices) > 1 else ()

    call = BrokerCall(
        ticker=ticker.upper(),
        broker=broker,
        rating=next(iter(mapped)),
        target=next(iter(targets)),
        price=price,
        published=published,
        source=source,
    )
    return Extraction(call, notes)


def read_pdf_text(path: str | Path, max_pages: int = 3) -> str:
    """Return text of the first pages. Needs the optional `pypdf` dependency."""
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover - environment dependent
        raise RuntimeError("install pypdf: pip install 'quant-agents[pdf]'") from e
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages[:max_pages])
