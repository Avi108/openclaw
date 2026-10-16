"""Pre-market global-cues briefing for Indian equities.

Combines overnight moves in Asian indices, GIFT Nifty, US futures, USD/INR, crude,
volatility, US yields and FII flows into a transparent tone score in [-1, +1].

The weights and scales are judgment calls, NOT fitted to data. `validate_tone`
measures whether the tone actually predicted the next Nifty move on history you
supply; until that has been run, treat the label as a summary, not a signal.
Inputs are supplied by the caller (a JSON file); nothing here fetches data, and a
missing or stale input is excluded and listed, never filled in.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Mapping, Sequence

from .validation import permutation_p_value, spearman

# name -> (label, weight, sign, kind, scale)
#   kind "pct": change is % move; "bps": change in basis points; "level": value itself (FII net, Rs crore)
#   sign: +1 if a rise is good for Indian equities, -1 if bad
#   scale: size of a "typical large move"; contribution is clipped to [-1, 1] after dividing by it
SPEC: dict[str, tuple[str, float, int, str, float]] = {
    "gift_nifty":   ("GIFT Nifty", 3.0, +1, "pct", 1.0),
    "nikkei":       ("Nikkei 225 (Japan)", 1.0, +1, "pct", 1.5),
    "hang_seng":    ("Hang Seng (Hong Kong)", 1.0, +1, "pct", 1.5),
    "kospi":        ("KOSPI (Korea)", 1.0, +1, "pct", 1.5),
    "sti":          ("Straits Times (Singapore)", 1.0, +1, "pct", 1.0),
    "us_futures":   ("US index futures", 2.0, +1, "pct", 1.0),
    "usd_inr":      ("USD/INR", 1.5, -1, "pct", 0.5),
    "brent":        ("Brent crude", 1.5, -1, "pct", 3.0),
    "india_vix":    ("India VIX", 1.5, -1, "pct", 8.0),
    "us10y":        ("US 10-year yield (bps)", 1.0, -1, "bps", 10.0),
    "fii_net_cr":   ("FII net flow, Rs crore (prior day)", 1.5, +1, "level", 2000.0),
}
RISK_ON, RISK_OFF = 0.25, -0.25
EVENT_KEYWORDS = ("rbi", "fed", "fomc", "tariff", "war", "election", "budget", "cpi", "gdp", "default", "sanction")


@dataclass(frozen=True)
class Cue:
    name: str
    value: float
    prev: float | None = None   # previous close; not needed for "level" cues
    as_of: datetime | None = None


@dataclass(frozen=True)
class Contribution:
    name: str
    label: str
    change: float
    score: float  # weight * sign * clipped(change / scale)


@dataclass(frozen=True)
class Briefing:
    tone: float | None
    label: str
    coverage: float  # share of total weight actually present
    contributions: tuple[Contribution, ...]
    excluded: tuple[str, ...]
    event_flags: tuple[str, ...]
    headlines: tuple[str, ...]


def _change(c: Cue, kind: str) -> float | None:
    if kind == "level":
        return c.value
    if c.prev in (None, 0):
        return None
    if kind == "pct":
        return (c.value / c.prev - 1.0) * 100.0
    return (c.value - c.prev) * 100.0  # bps: yields quoted in percent


def build_briefing(
    cues: Mapping[str, Cue],
    now: datetime,
    headlines: Sequence[str] = (),
    max_age: timedelta = timedelta(hours=18),
    min_coverage: float = 0.5,
) -> Briefing:
    contribs: list[Contribution] = []
    excluded: list[str] = []
    present_weight = 0.0
    total_weight = sum(w for _, w, _, _, _ in SPEC.values())
    for name, (label, weight, sign, kind, scale) in SPEC.items():
        c = cues.get(name)
        if c is None:
            excluded.append(f"{label}: missing")
            continue
        if c.as_of is not None and now - c.as_of > max_age:
            excluded.append(f"{label}: stale ({(now - c.as_of).total_seconds() / 3600:.0f}h old)")
            continue
        ch = _change(c, kind)
        if ch is None:
            excluded.append(f"{label}: no previous close")
            continue
        score = weight * sign * max(-1.0, min(1.0, ch / scale))
        present_weight += weight
        contribs.append(Contribution(name, label, ch, score))
    coverage = present_weight / total_weight
    flags = tuple(sorted({k for h in headlines for k in EVENT_KEYWORDS if k in h.lower()}))
    if coverage < min_coverage:
        return Briefing(None, "insufficient data", coverage, tuple(contribs), tuple(excluded), flags, tuple(headlines))
    tone = sum(c.score for c in contribs) / present_weight
    label = "risk-on" if tone >= RISK_ON else "risk-off" if tone <= RISK_OFF else "neutral"  # pragma: no mutate (equivalent: a tone landing exactly on a threshold is unreachable with float arithmetic)
    return Briefing(tone, label, coverage, tuple(contribs), tuple(excluded), flags, tuple(headlines))


def load_cues(path: str | Path) -> tuple[dict[str, Cue], list[str], datetime]:
    """JSON: {"now": iso, "cues": {name: {"value":..,"prev":..,"as_of": iso}}, "headlines": [..]}"""
    d = json.loads(Path(path).read_text())
    now = datetime.fromisoformat(d["now"]) if "now" in d else datetime.now(timezone.utc)
    cues = {
        k: Cue(k, float(v["value"]), None if v.get("prev") is None else float(v["prev"]),
               datetime.fromisoformat(v["as_of"]) if v.get("as_of") else None)
        for k, v in d.get("cues", {}).items()
    }
    return cues, list(d.get("headlines", [])), now


def render(b: Briefing) -> str:
    lines = [f"tone: {b.label}" + ("" if b.tone is None else f" ({b.tone:+.2f})") + f" | data coverage {b.coverage:.0%}"]
    for c in sorted(b.contributions, key=lambda c: -abs(c.score)):
        lines.append(f"  {c.label:<38} change {c.change:+8.2f}   contribution {c.score:+.2f}")
    for e in b.excluded:
        lines.append(f"  excluded: {e}")
    if b.event_flags:
        lines.append("event keywords in headlines (for a human to read, not scored): " + ", ".join(b.event_flags))
    lines.append("weights are unvalidated judgment; run validate-tone on history before relying on this")
    return "\n".join(lines)


@dataclass(frozen=True)
class ToneValidation:
    n: int
    direction_hit_rate: float  # share of non-flat days where tone sign matched the next-day move
    rank_corr: float
    p_value: float


def validate_tone(rows: Sequence[tuple[float, float]], n_perm: int = 2000, seed: int = 0) -> ToneValidation:
    """rows = (tone score at the pre-market, Nifty return of that session). Does tone predict the move?"""
    if len(rows) < 30:
        raise ValueError("need >= 30 days of history")
    tones, rets = [r[0] for r in rows], [r[1] for r in rows]
    decided = [(t, r) for t, r in rows if t != 0 and r != 0]
    hit = sum((t > 0) == (r > 0) for t, r in decided) / len(decided) if decided else float("nan")
    return ToneValidation(len(rows), hit, spearman(tones, rets), permutation_p_value(tones, rets, n_perm, seed))
