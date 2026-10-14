from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from .backtest import equity_to_metrics, momentum_signal, run_backtest, synthetic_prices
from .consensus import build_consensus
from .ingest import drop_stale, parse_calls
from .nse import McpClient, bars_from_csv
from .pdf_extract import extract_call, read_pdf_text
from .risk import PortfolioState, RiskPolicy
from .scenarios import build_report
from .validation import evaluate, summarize
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


def _cmd_extract(a: argparse.Namespace) -> int:
    """Folder of reports named TICKER__BROKER__YYYY-MM-DD.(pdf|txt) -> call lines on stdout."""
    ok = refused = 0
    for f in sorted(Path(a.folder).iterdir()):
        if f.suffix.lower() not in (".pdf", ".txt"):
            continue
        try:
            ticker, broker, day = f.stem.split("__")
            published = date.fromisoformat(day)
        except ValueError:
            print(f"# SKIP {f.name}: name must be TICKER__BROKER__YYYY-MM-DD", file=sys.stderr)
            refused += 1
            continue
        text = read_pdf_text(f) if f.suffix.lower() == ".pdf" else f.read_text(errors="ignore")
        ex = extract_call(text, ticker, broker.replace("_", " "), published, source=f.name)
        if ex.call is None:
            print(f"# REFUSED {f.name}: {'; '.join(ex.reasons)}", file=sys.stderr)
            refused += 1
            continue
        c = ex.call
        px = f" | cmp {c.price:g}" if c.price else ""
        print(f"{c.published} | {c.ticker} | {c.broker} | {c.rating.value} | target {c.target:g}{px} | {f.name.replace(' ', '_')}")
        ok += 1
    print(f"# extracted={ok} refused={refused}", file=sys.stderr)
    return 0


def _load_prices(path: str) -> dict[str, list[tuple[date, float]]]:
    out: dict[str, list[tuple[date, float]]] = defaultdict(list)
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            out[row["ticker"].upper()].append((date.fromisoformat(row["date"]), float(row["close"])))
    return {t: sorted(v) for t, v in out.items()}


def _cmd_validate(a: argparse.Namespace) -> int:
    calls = parse_calls(Path(a.calls).read_text()).calls
    outcomes = evaluate(calls, _load_prices(a.prices), a.horizon)
    print(f"calls={len(calls)} scored={len(outcomes)} (unscored: missing price/history or window not finished)")
    s = summarize(outcomes)
    lo, mid, hi = s.bucket_realized_pct
    print(f"mean implied upside {s.mean_implied_pct:.1f}% | mean realized {s.mean_realized_pct:.1f}% | overstatement {s.overstatement_pct:.1f} pts")
    print(f"target hit rate {s.hit_rate:.0%} | rank IC {s.rank_ic:+.3f} | permutation p={s.p_value:.3f}")
    print(f"realized by implied-upside tercile: low {lo:.1f}% / mid {mid:.1f}% / high {hi:.1f}%")
    verdict = "no evidence that higher targets earned higher returns" if s.p_value >= 0.05 or s.rank_ic <= 0 else "higher targets did rank higher on returns (check sample size and overlap)"
    print(f"verdict: {verdict}")
    return 0


def _cmd_nse_tools(a: argparse.Namespace) -> int:
    """List the tools an MCP server offers (e.g. NSE's bhavcopy server) so schemas are never guessed."""
    for t in McpClient(a.url).list_tools():
        print(f"{t['name']}: {t.get('description', '')[:100]}")
        print(f"    inputSchema: {t.get('inputSchema', {})}")
    return 0


def _cmd_scenarios(a: argparse.Namespace) -> int:
    weights = {k.upper(): float(v) for k, v in (p.split("=") for p in a.weights.split(","))}
    if abs(sum(weights.values()) - 1.0) > 1e-6:
        print(f"weights sum to {sum(weights.values()):g}, expected 1.0", file=sys.stderr)
        return 2
    bars = []
    for f in sorted(Path(a.dir).glob("*.csv")):
        b, problems = bars_from_csv(f.read_text(errors="ignore"))
        bars += [x for x in b if x.symbol in weights or a.all_symbols]
        for pr in problems[:3]:
            print(f"# {f.name}: {pr}", file=sys.stderr)
    r = build_report(bars, weights, portfolio_value=a.value, horizon=a.horizon)
    print(f"days analysed={r.n_days} (dropped {r.dropped_days}) | annualised vol {r.vol_ann_pct:.1f}%")
    print(f"1-day VaR95 {r.var95_pct:.2f}% | CVaR95 {r.cvar95_pct:.2f}%")
    for label, ws in (("worst 1-day", r.worst_1d), ("worst 20-day", r.worst_20d)):
        print(f"{label}: " + "; ".join(f"{s}..{e} {x:.1f}%" for s, e, x in ws))
    print(f"over {a.horizon} days: P(drawdown > 15% halt) = {r.p_breach:.1%}; 5th-percentile return {r.p5_terminal_pct:.1f}%")
    print("market-shock P&L: " + ", ".join(f"{k}: {v:+.1f}%" for k, v in r.shocks.items()))
    print("days to exit at 10% of daily value: " + ", ".join(f"{k} {v:.2f}" for k, v in r.liquidity.items()))
    for c in r.corporate_actions:
        print(f"corporate action? {c.symbol} {c.day}: raw {c.raw_change_pct:+.0f}% vs adjusted {c.adjusted_change_pct:+.1f}%")
    for n in r.notes:
        print(f"note: {n}")
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
    e = sub.add_parser("extract", help="turn a folder of broker reports (PDF/txt) into call lines")
    e.add_argument("folder")
    e.set_defaults(fn=_cmd_extract)
    v = sub.add_parser("validate", help="score past calls against later prices (CSV: ticker,date,close)")
    v.add_argument("calls")
    v.add_argument("prices")
    v.add_argument("--horizon", type=int, default=180)
    v.set_defaults(fn=_cmd_validate)
    n = sub.add_parser("nse-tools", help="list tools of an MCP server (needs network access to it)")
    n.add_argument("url", nargs="?", default="https://mcp.nseindia.in/bhavcopy/cm/mcp")
    n.set_defaults(fn=_cmd_nse_tools)
    sc = sub.add_parser("scenarios", help="stress-test a portfolio on a folder of bhavcopy CSVs")
    sc.add_argument("dir")
    sc.add_argument("--weights", required=True, help="e.g. RELIANCE=0.5,INFY=0.5")
    sc.add_argument("--value", type=float, default=3800.0)
    sc.add_argument("--horizon", type=int, default=60)
    sc.add_argument("--all-symbols", action="store_true")
    sc.set_defaults(fn=_cmd_scenarios)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
