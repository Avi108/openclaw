"""India broker-consensus analyst for virattt/ai-hedge-fund (MIT).

Plugs into the fund's `AlphaModel` interface without forking it:

    from india_broker import register
    register()                      # adds "india_broker" to ALPHA_MODEL_REGISTRY

The model is point-in-time (only calls published on or before `date`), uses one
vote per broker, and *abstains* rather than guessing when the data is thin or
implausible. Calls come from a file in the quant-agents line format; set
QUANT_AGENTS_CALLS to its path (the fund's data client is not used because
it has no Indian broker-call feed).
"""
from __future__ import annotations

import math
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from hedge_fund.data.protocol import DataClient
from hedge_fund.models import Signal
from hedge_fund.signals.base import QuantModel

from quant_agents.consensus import build_consensus
from quant_agents.ingest import parse_calls
from quant_agents.models import BrokerCall

CallLoader = Callable[[], list[BrokerCall]]
_ABSTAIN_FLAGS = {"implausible-upside", "no-price", "single-broker"}


def _default_loader() -> list[BrokerCall]:
    path = os.environ.get("QUANT_AGENTS_CALLS")
    if not path:
        raise RuntimeError("set QUANT_AGENTS_CALLS to a broker-call file (see quant-agents README)")
    return parse_calls(Path(path).read_text()).calls


class IndiaBrokerConsensusModel(QuantModel):
    investment_approach = "long_only"

    def __init__(self, *, loader: CallLoader | None = None, max_age_days: int = 45) -> None:
        self._loader = loader or _default_loader
        self._max_age = timedelta(days=max_age_days)
        self._calls: list[BrokerCall] | None = None

    @property
    def name(self) -> str:
        return "india_broker"

    def predict(self, ticker: str, date: str, data_client: DataClient | None = None) -> Signal:
        as_of = _parse(date)
        if self._calls is None:
            self._calls = self._loader()
        window = [
            c for c in self._calls
            if c.ticker == ticker.upper() and c.published <= as_of and as_of - c.published <= self._max_age
        ]
        if not window:
            return self._abstain(ticker, date, "no recent broker calls")
        row = build_consensus(window)[0]
        blockers = _ABSTAIN_FLAGS.intersection(row.flags)
        if blockers or row.median_upside_pct is None:
            return self._abstain(ticker, date, f"unreliable data: {', '.join(sorted(blockers)) or 'no price'}")
        agreement = max(0.0, 1.0 - row.target_dispersion) * min(row.n_brokers, 4) / 4.0
        value = math.tanh(row.median_upside_pct / 40.0) * agreement
        return Signal(
            model_name=self.name,
            ticker=ticker,
            date=date,
            value=float(max(-1.0, min(1.0, value))),
            reasoning=(
                f"{row.n_brokers} brokers, median upside {row.median_upside_pct:.0f}%, "
                f"buy ratio {row.buy_ratio:.0%}, target dispersion {row.target_dispersion:.0%}"
            ),
            components={"median_upside_pct": row.median_upside_pct, "agreement": agreement},
            metadata={"flags": list(row.flags), "n_brokers": row.n_brokers},
        )

    def _abstain(self, ticker: str, date: str, why: str) -> Signal:
        return Signal(model_name=self.name, ticker=ticker, date=date, value=0.0, reasoning=why,
                      metadata={"abstained": True})


def _parse(s: str) -> date:
    return date.fromisoformat(s)


def register() -> None:
    """Add the model to the fund's registry (idempotent)."""
    from hedge_fund.signals import ALPHA_MODEL_REGISTRY

    ALPHA_MODEL_REGISTRY["india_broker"] = IndiaBrokerConsensusModel
