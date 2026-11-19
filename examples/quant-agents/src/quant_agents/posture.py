"""Market posture: a ceiling on how much of the portfolio should be invested.

Combines the global-cues tone and market breadth into an exposure ceiling and a
new-entries-allowed flag. The numbers are judgment, not fitted: they encode
"invest less when the market is weak and narrow", and should be validated before
being trusted. Missing inputs leave the base ceiling unchanged and are listed.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Posture:
    ceiling_pct: float
    entries_allowed: bool
    reasons: tuple[str, ...]


def exposure_ceiling(tone: float | None, pct_above_50dma: float | None, base: float = 60.0,
                     floor: float = 10.0, top: float = 90.0) -> Posture:
    ceiling, reasons = base, [f"base ceiling {base:g}%"]
    if tone is None:
        reasons.append("cues tone unavailable: no adjustment")
    elif tone >= 0.25:
        ceiling += 20
        reasons.append(f"risk-on tone {tone:+.2f}: +20")
    elif tone <= -0.25:
        ceiling -= 30
        reasons.append(f"risk-off tone {tone:+.2f}: -30")
    if pct_above_50dma is None:
        reasons.append("breadth unavailable: no adjustment")
    elif pct_above_50dma < 30:
        ceiling -= 20
        reasons.append(f"narrow market, {pct_above_50dma:.0f}% above 50-day average: -20")
    elif pct_above_50dma > 60:
        ceiling += 10
        reasons.append(f"broad market, {pct_above_50dma:.0f}% above 50-day average: +10")
    ceiling = max(floor, min(top, ceiling))
    return Posture(ceiling, ceiling > 20, tuple(reasons))
