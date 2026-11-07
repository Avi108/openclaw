"""Thesis tracker: every position carries its reason and what would prove it wrong.

A holding without a written invalidation condition is held by hope. `evaluate`
returns intact / review / broken from facts the caller supplies, so a thesis can
be checked mechanically each week instead of being re-argued emotionally.
"""
from __future__ import annotations

import json
import operator
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from collections.abc import Mapping

_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


@dataclass(frozen=True)
class Invalidation:
    metric: str   # e.g. "operating_margin_pct", "promoter_pledge_pct", "debt_to_equity"
    op: str       # one of < <= > >=
    threshold: float
    note: str = ""


@dataclass(frozen=True)
class Thesis:
    ticker: str
    opened: date
    statement: str
    entry_price: float
    target_price: float
    review_by: date
    stop_pct: float = 20.0  # review when price is this far below entry
    invalidations: tuple[Invalidation, ...] = ()
    bear_case: str = ""     # the strongest argument against, written before buying


@dataclass(frozen=True)
class ThesisStatus:
    ticker: str
    status: str  # "intact" | "review" | "broken"
    reasons: tuple[str, ...]


def evaluate(t: Thesis, facts: Mapping[str, float], price: float, today: date) -> ThesisStatus:
    broken, review = [], []
    for inv in t.invalidations:
        if inv.op not in _OPS:
            raise ValueError(f"unknown operator {inv.op!r}")
        if inv.metric not in facts:
            review.append(f"no data for {inv.metric}: cannot test invalidation")
        elif _OPS[inv.op](facts[inv.metric], inv.threshold):
            broken.append(f"{inv.metric} {facts[inv.metric]:g} {inv.op} {inv.threshold:g}" + (f" ({inv.note})" if inv.note else ""))
    drawdown = (1 - price / t.entry_price) * 100
    if drawdown >= t.stop_pct:
        review.append(f"price {drawdown:.0f}% below entry (review trigger {t.stop_pct:g}%)")
    if today >= t.review_by:
        review.append(f"review date {t.review_by} reached")
    if not t.bear_case.strip():
        review.append("no bear case written")
    if price >= t.target_price:
        review.append("target reached: decide whether the thesis has played out")
    if broken:
        return ThesisStatus(t.ticker, "broken", tuple(broken + review))
    return ThesisStatus(t.ticker, "review" if review else "intact", tuple(review))


def save(theses: list[Thesis], path: str | Path) -> None:
    def enc(t: Thesis) -> dict:
        d = asdict(t)
        d["opened"], d["review_by"] = t.opened.isoformat(), t.review_by.isoformat()
        return d
    Path(path).write_text(json.dumps([enc(t) for t in theses], indent=2))  # pragma: no mutate (equivalent: indent width is cosmetic)


def load(path: str | Path) -> list[Thesis]:
    out = []
    for d in json.loads(Path(path).read_text()):
        d["opened"], d["review_by"] = date.fromisoformat(d["opened"]), date.fromisoformat(d["review_by"])
        d["invalidations"] = tuple(Invalidation(**i) for i in d["invalidations"])
        out.append(Thesis(**d))
    return out
