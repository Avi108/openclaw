"""Paper trading: record would-be trades, then judge them against a benchmark.

The ledger is an append-only list of trades; positions, cash and the equity curve
are derived from it, so every number is auditable. Entries execute at the first
close strictly AFTER the decision day (no look-ahead), at a price worsened by
slippage, with Indian delivery-trade costs applied.

Cost defaults are approximations. Check your broker's contract note and set
`CostModel` to match before trusting any result.
"""
from __future__ import annotations

import json
import math
from bisect import bisect_right
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

from .backtest import Metrics, equity_to_metrics

PricePath = Sequence[tuple[date, float]]


@dataclass(frozen=True)
class CostModel:
    stt_pct: float = 0.1            # securities transaction tax, delivery, each side
    stamp_buy_pct: float = 0.015    # stamp duty, buy side
    exchange_other_pct: float = 0.011  # exchange, SEBI and GST on them, approximate, each side
    brokerage_pct: float = 0.0      # many discount brokers charge nil on delivery
    dp_charge_sell: float = 15.0    # flat depository charge per sell, approximate
    slippage_bps: float = 10.0      # worsens each execution price

    def buy_fees(self, value: float) -> float:
        return value * (self.stt_pct + self.stamp_buy_pct + self.exchange_other_pct + self.brokerage_pct) / 100.0

    def sell_fees(self, value: float) -> float:
        return value * (self.stt_pct + self.exchange_other_pct + self.brokerage_pct) / 100.0 + self.dp_charge_sell


@dataclass(frozen=True)
class Trade:
    side: str  # "buy" | "sell"
    ticker: str
    day: date  # execution day (the first close after the decision day)
    price: float  # execution price after slippage
    qty: int
    fees: float


@dataclass
class PaperLedger:
    start_cash: float
    cost: CostModel = field(default_factory=CostModel)
    trades: list[Trade] = field(default_factory=list)

    # ---------------- derived state ----------------
    def cash(self) -> float:
        c = self.start_cash
        for t in self.trades:
            c += (-1 if t.side == "buy" else 1) * t.price * t.qty - t.fees
        return c

    def positions(self) -> dict[str, int]:
        pos: dict[str, int] = {}
        for t in self.trades:
            pos[t.ticker] = pos.get(t.ticker, 0) + (t.qty if t.side == "buy" else -t.qty)
        return {k: v for k, v in pos.items() if v}

    # ---------------- actions ----------------
    def enter(self, ticker: str, amount: float, decision_day: date, path: PricePath) -> Trade:
        day, px = _next_close(path, decision_day)
        exec_px = px * (1 + self.cost.slippage_bps / 10_000.0)
        qty = math.floor(amount / (exec_px * (1 + (self.cost.buy_fees(1.0)))))
        if qty < 1:
            raise ValueError(f"amount {amount:g} cannot buy one share of {ticker} at {exec_px:.2f}")
        value = exec_px * qty
        fees = self.cost.buy_fees(value)
        if value + fees > self.cash() + 1e-9:
            raise ValueError(f"insufficient paper cash: need {value + fees:.2f}, have {self.cash():.2f}")
        t = Trade("buy", ticker, day, exec_px, qty, fees)
        self.trades.append(t)
        return t

    def exit(self, ticker: str, decision_day: date, path: PricePath) -> Trade:
        qty = self.positions().get(ticker, 0)
        if qty <= 0:
            raise ValueError(f"no open paper position in {ticker}")
        day, px = _next_close(path, decision_day)
        exec_px = px * (1 - self.cost.slippage_bps / 10_000.0)
        t = Trade("sell", ticker, day, exec_px, qty, self.cost.sell_fees(exec_px * qty))
        self.trades.append(t)
        return t

    # ---------------- persistence ----------------
    def save(self, path: str | Path) -> None:
        d = {"start_cash": self.start_cash, "cost": asdict(self.cost),
             "trades": [{**asdict(t), "day": t.day.isoformat()} for t in self.trades]}
        Path(path).write_text(json.dumps(d, indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "PaperLedger":
        d = json.loads(Path(path).read_text())
        return cls(d["start_cash"], CostModel(**d["cost"]),
                   [Trade(**{**t, "day": date.fromisoformat(t["day"])}) for t in d["trades"]])


def _next_close(path: PricePath, decision_day: date) -> tuple[date, float]:
    i = bisect_right([d for d, _ in path], decision_day)
    if i >= len(path):
        raise ValueError(f"no price after {decision_day}")
    return path[i]


# --------------------------------- reporting ---------------------------------

@dataclass(frozen=True)
class PaperReport:
    days: int
    n_trades: int
    portfolio: Metrics
    benchmark: Metrics
    excess_return_pct: float
    final_value: float
    verdict: str


MIN_DAYS, MIN_TRADES = 60, 20


def equity_curve(ledger: PaperLedger, prices: Mapping[str, PricePath], start: date, end: date) -> list[tuple[date, float]]:
    dates = sorted({d for p in prices.values() for d, _ in p if start <= d <= end})
    maps = {t: dict(p) for t, p in prices.items()}
    last: dict[str, float] = {}
    ti = 0
    trades = sorted(ledger.trades, key=lambda t: t.day)
    cash, pos = ledger.start_cash, {}
    out = []
    for d in dates:
        for t, m in maps.items():  # carry forward last known close (a missing day is not a zero)
            if d in m:
                last[t] = m[d]
        while ti < len(trades) and trades[ti].day <= d:
            tr = trades[ti]
            sign = -1 if tr.side == "buy" else 1
            cash += sign * tr.price * tr.qty - tr.fees
            pos[tr.ticker] = pos.get(tr.ticker, 0) + (tr.qty if tr.side == "buy" else -tr.qty)
            ti += 1
        out.append((d, cash + sum(q * last[t] for t, q in pos.items() if q and t in last)))
    return out


def report(ledger: PaperLedger, prices: Mapping[str, PricePath], benchmark: str, end: date | None = None) -> PaperReport:
    if not ledger.trades:
        raise ValueError("no trades recorded")
    if benchmark not in prices:
        raise ValueError(f"no prices for benchmark {benchmark}")
    start = min(t.day for t in ledger.trades)
    last_day = max(d for p in prices.values() for d, _ in p)
    end = min(end, last_day) if end else last_day
    curve = equity_curve(ledger, prices, start, end)
    if len(curve) < 3:
        raise ValueError("need at least 3 price days after the first trade")
    # Benchmark: same capital, same costs, bought at the same first execution day.
    b0 = dict(prices[benchmark]).get(start) or next(px for d, px in prices[benchmark] if d >= start)
    b_exec = b0 * (1 + ledger.cost.slippage_bps / 10_000.0)
    b_qty = math.floor(ledger.start_cash / (b_exec * (1 + ledger.cost.buy_fees(1.0))))
    b_cash = ledger.start_cash - b_exec * b_qty - ledger.cost.buy_fees(b_exec * b_qty)
    bmap = dict(prices[benchmark])
    b_last, b_eq = b0, []
    for d, _ in curve:
        b_last = bmap.get(d, b_last)
        b_eq.append(b_cash + b_qty * b_last)
    pm = equity_to_metrics([v for _, v in curve])
    bm = equity_to_metrics(b_eq)
    excess = pm.total_return_pct - bm.total_return_pct
    n_buys = sum(t.side == "buy" for t in ledger.trades)
    if pm.n_days < MIN_DAYS or n_buys < MIN_TRADES:
        verdict = (f"too early to judge: {pm.n_days} days and {n_buys} trades "
                   f"(need >= {MIN_DAYS} days and >= {MIN_TRADES} trades)")
    elif excess > 0:
        verdict = "ahead of benchmark after costs; still check whether the margin is larger than noise"
    else:
        verdict = "behind or level with the benchmark after costs: a plain index holding did as well or better"
    return PaperReport(pm.n_days, len(ledger.trades), pm, bm, excess, curve[-1][1], verdict)


# ------------------------------- post-mortem -------------------------------

@dataclass(frozen=True)
class RoundTrip:
    ticker: str
    entry_day: date
    exit_day: date
    return_pct: float  # net of fees and slippage
    net_pnl: float
    days_held: int


def round_trips(ledger: PaperLedger) -> list[RoundTrip]:
    """Pair each full exit with the buys that opened the position (average cost). Open positions are excluded."""
    out: list[RoundTrip] = []
    open_buys: dict[str, list[Trade]] = {}
    for t in sorted(ledger.trades, key=lambda t: t.day):
        if t.side == "buy":
            open_buys.setdefault(t.ticker, []).append(t)
            continue
        buys = open_buys.pop(t.ticker, [])
        if not buys:
            continue
        cost = sum(b.price * b.qty + b.fees for b in buys)
        proceeds = t.price * t.qty - t.fees
        out.append(RoundTrip(t.ticker, buys[0].day, t.day, (proceeds / cost - 1) * 100, proceeds - cost, (t.day - buys[0].day).days))
    return out


@dataclass(frozen=True)
class PostMortem:
    n: int
    win_rate: float
    avg_win_pct: float | None
    avg_loss_pct: float | None
    expectancy_pct: float
    profit_factor: float | None  # gross profit / gross loss; None when there were no losses
    worst: RoundTrip


def post_mortem(trips: Sequence[RoundTrip]) -> PostMortem:
    if not trips:
        raise ValueError("no closed round trips to review")
    wins = [t for t in trips if t.return_pct > 0]
    losses = [t for t in trips if t.return_pct <= 0]
    gp = sum(t.net_pnl for t in wins)
    gl = -sum(t.net_pnl for t in losses)
    return PostMortem(
        n=len(trips), win_rate=len(wins) / len(trips),
        avg_win_pct=(sum(t.return_pct for t in wins) / len(wins)) if wins else None,
        avg_loss_pct=(sum(t.return_pct for t in losses) / len(losses)) if losses else None,
        expectancy_pct=sum(t.return_pct for t in trips) / len(trips),
        profit_factor=(gp / gl) if gl > 0 else None, worst=min(trips, key=lambda t: t.return_pct),
    )
