"""Event-risk awareness: results, policy meetings and other dated catalysts.

A buy placed the day before results is a bet on the result, not on the thesis.
Events are supplied by the caller (a CSV); nothing here fetches a calendar.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from collections.abc import Sequence


@dataclass(frozen=True)
class Event:
    day: date
    kind: str           # e.g. "results", "rbi_mpc", "ex_dividend"
    ticker: str | None  # None means market-wide


def load_events(path: str | Path) -> list[Event]:
    """CSV columns: date, kind, ticker (blank for market-wide)."""
    with open(path, newline="") as fh:
        return [Event(date.fromisoformat(r["date"]), r["kind"].strip(), (r.get("ticker") or "").strip().upper() or None)
                for r in csv.DictReader(r for r in fh if not r.lstrip().startswith("#"))]


def upcoming(ticker: str, today: date, events: Sequence[Event], window_days: int = 3) -> list[Event]:
    """Events for the ticker or the whole market within the next `window_days` days (inclusive of today)."""
    t = ticker.upper()
    return sorted((e for e in events if (e.ticker in (None, t)) and 0 <= (e.day - today).days <= window_days), key=lambda e: e.day)


def describe(events: Sequence[Event], today: date) -> str:
    return "; ".join(f"{e.kind}{'' if e.ticker is None else ' ' + e.ticker} in {(e.day - today).days}d" for e in events)
