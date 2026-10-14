from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .backtest import equity_to_metrics, momentum_signal, run_backtest, synthetic_prices
from .consensus import build_consensus
from .ingest import drop_stale, parse_calls
from .risk import PortfolioState, RiskPolicy
from .workflow import run


def _cmd_consensus(a: argparse.Namespace) -> int:
    res = parse_calls(Path(a.file).read_text())
    calls = drop_stale(res.calls, date.fromisoformat(a.today), a.max_age_days)
    print(f"parsed={len(res.calls)} rejected={len(res.rejected)} fresh={len(calls)}")
    print(f"{'ticker':<10}{'brokers':>8}{'buy%':>6}{'upside%':>9}{'score':>8}  flags")
    for r in build_consensus(calls):
        up = "n/a" if r.median_upside_pct is None else f"{r.median_upside_pct:.0f}"
        print(f"{r.ticker:<10}{r.n_brokers:>8}{r.buy_ratio * 100:>6.0f}{up:>9}{r.score:>8.1f}  {','.join(r.flags)}")
    return 0


def _cmd_propose(a: argparse.Namespace) -> int:
    state = PortfolioState(cash=a.cash, positions={}, peak_value=a.cash)
    result = run(Path(a.file).read_text(), state, RiskPolicy(), date.fromisoformat(a.today))
    print(f"proposals (nothing is placed; approval gate is deny-by-default): {len(result.proposals)}")
    for p in result.proposals:
        print(f"  {p.ticker:<10} {p.amount:>10.2f}  {p.reason}")
    if a.audit:
        Path(a.audit).write_text(result.audit.to_jsonl() + "\n")
        print(f"audit log: {a.audit}")
    return 0


def _cmd_backtest(a: argparse.Namespace) -> int:
    prices = synthetic_prices(a.days, seed=a.seed)
    m = equity_to_metrics(run_backtest(prices, momentum_signal(a.lookback)))
    bh = equity_to_metrics(run_backtest(prices, lambda _h: 1.0))
    print("SYNTHETIC prices (no real market data); demonstrates the engine, not a trading edge.")
    for name, x in (("momentum", m), ("buy&hold", bh)):
        print(f"{name:<9} ret={x.total_return_pct:7.1f}%  cagr={x.cagr_pct:6.1f}%  sharpe={x.sharpe:5.2f}  maxdd={x.max_drawdown_pct:5.1f}%")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="quant-agents")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("consensus", help="score broker calls from a file")
    c.add_argument("file")
    c.add_argument("--today", default=date.today().isoformat())
    c.add_argument("--max-age-days", type=int, default=30)
    c.set_defaults(fn=_cmd_consensus)
    pr = sub.add_parser("propose", help="build risk-checked order proposals (never places orders)")
    pr.add_argument("file")
    pr.add_argument("--cash", type=float, default=3800.0)
    pr.add_argument("--today", default=date.today().isoformat())
    pr.add_argument("--audit", help="write JSONL audit log here")
    pr.set_defaults(fn=_cmd_propose)
    b = sub.add_parser("backtest", help="momentum vs buy&hold on seeded synthetic prices")
    b.add_argument("--days", type=int, default=756)
    b.add_argument("--seed", type=int, default=7)
    b.add_argument("--lookback", type=int, default=20)
    b.set_defaults(fn=_cmd_backtest)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
