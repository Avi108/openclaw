from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class Rating(str, Enum):
    BUY = "buy"
    HOLD = "hold"
    SELL = "sell"


@dataclass(frozen=True)
class BrokerCall:
    ticker: str
    broker: str
    rating: Rating
    target: float
    price: float | None  # price when the call was made; None if the source omitted it
    published: date
    source: str = ""

    @property
    def upside_pct(self) -> float | None:
        if not self.price:
            return None
        return (self.target / self.price - 1.0) * 100.0


@dataclass(frozen=True)
class ConsensusRow:
    ticker: str
    n_brokers: int
    buy_ratio: float
    median_target: float
    target_dispersion: float  # (max - min) / median
    median_upside_pct: float | None
    flags: tuple[str, ...]
    score: float
