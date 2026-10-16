from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from .backtest import equity_to_metrics, momentum_signal, run_backtest, synthetic_prices
from .consensus import build_consensus
from .ingest import drop_stale, parse_calls
from .crosscheck import cross_check
from .cues import build_briefing, load_cues, render, validate_tone
from .corporate import unadjusted_jumps
from .markets import SPECS, day_from_filename, load_dir, load_file
from .nse import McpClient, bars_from_csv
from .papertrade import PaperLedger, post_mortem, report, round_trips
from .playbook import CATALOG, episodes, judge, known_sectors, measure_episode, winners_and_flops
from .pdf_extract import extract_call, read_pdf_text
from .risk import PortfolioState, RiskPolicy
from .scenarios import build_report
from .events import load_events
from .segmented import run_segmented
from .skillwatch import check as skillwatch_check, load_watchlist, render_digest
from .universe import adv_from_bars, load_universe, with_adv
from .validation import evaluate, summarize
from .workflow import run


def _cmd_consensus(a: argparse.Namespace) -> int:
    res = parse_calls(Path(a.file).read_text())
    calls = drop_stale(res.calls, _day(a.today, "--today"), a.max_age_days)
    print(f"parsed={len(res.calls)} rejected={len(res.rejected)} fresh={len(calls)}")
    print(f"{'ticker':<10}{'brokers':>8}{'buy%':>6}{'upside%':>9}{'score':>8}  flags")
    for r in build_consensus(calls):
        up = "n/a" if r.median_upside_pct is None else f"{r.median_upside_pct:.0f}"
        print(f"{r.ticker:<10}{r.n_brokers:>8}{r.buy_ratio * 100:>6.0f}{up:>9}{r.score:>8.1f}  {','.join(r.flags)}")
    return 0


def _day(text: str, flag: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{flag}: expected a date like 2026-10-16, got {text!r}") from None


def _parse_weights(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in text.split(","):
        ticker, sep, value = item.partition("=")
        try:
            if not sep or not ticker.strip():
                raise ValueError
            out[ticker.strip().upper()] = float(value)
        except ValueError:
            raise ValueError(f"--weights: bad item {item!r}; use TICKER=fraction, e.g. RELIANCE=0.5,INFY=0.5") from None
    return out


def _print_segmented(a: argparse.Namespace) -> int:
    uni = load_universe(a.universe)
    if a.bhavcopy:
        bars = []
        for f in sorted(Path(a.bhavcopy).glob("*.csv")):
            bars += bars_from_csv(f.read_text(errors="ignore"))[0]
        uni = with_adv(uni, adv_from_bars(bars))
    state = PortfolioState(cash=a.cash, positions={}, peak_value=a.cash)
    events = tuple(load_events(a.events)) if a.events else ()
    r = run_segmented(Path(a.file).read_text(), state, RiskPolicy(), uni, _day(a.today, "--today"), events=events,
                      max_invested_pct=a.max_invested_pct)
    print(f"portfolio {a.cash:,.0f}: proposals={len(r.proposals)} queued={len(r.queued)} research={len(r.research)} (nothing is placed)")
    for title, items in (("PROPOSALS (today)", r.proposals), ("QUEUED (valid; over today's order limit)", r.queued)):
        if items:
            print(f"\n{title}")
            for seg in ("large", "mid", "small", "micro"):
                for c in (x for x in items if x.segment.value == seg):
                    cav = f"  [{'; '.join(c.caveats)}]" if c.caveats else ""
                    print(f"  {seg:<6} {c.sector:<18} {c.ticker:<14} {c.amount:>9.0f}  upside {c.row.median_upside_pct:.0f}%, {c.row.n_brokers} broker(s){cav}")
    pend = [x for x in r.research if x.pending_data]
    fail = [x for x in r.research if not x.pending_data]
    if pend:
        print("\nPENDING DATA (blocked only by missing inputs; may qualify once filled)")
        for x in pend:
            print(f"  {(x.segment.value if x.segment else '?'):<6} {(x.sector or '?'):<18} {x.ticker:<14} needs: {' | '.join(x.reasons)}")
    if fail:
        print("\nDID NOT QUALIFY")
        for x in fail:
            print(f"  {(x.segment.value if x.segment else '?'):<6} {(x.sector or '?'):<18} {x.ticker:<14} {' | '.join(x.reasons)}")
    if a.audit:
        Path(a.audit).write_text(r.audit.to_jsonl() + "\n")
    return 0


def _cmd_propose(a: argparse.Namespace) -> int:
    if a.universe:
        return _print_segmented(a)
    state = PortfolioState(cash=a.cash, positions={}, peak_value=a.cash)
    result = run(Path(a.file).read_text(), state, RiskPolicy(), _day(a.today, "--today"))
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


def _overrides(pairs: list[str] | None) -> dict[str, str]:
    return dict(p.split("=", 1) for p in (pairs or []))


def _cmd_markets_verify(a: argparse.Namespace) -> int:
    """Show how a real HKEX/KRX file maps onto our fields before trusting any loader output."""
    spec = SPECS[a.market]
    bars, problems, rep = load_file(a.file, spec, default_day=_day(a.day, "--day") if a.day else day_from_filename(Path(a.file).name),
                                    overrides=_overrides(a.map))
    print(f"{spec.name}: {len(bars)} rows loaded, {len(problems)} problems; previous close from: {rep.prev_close_source}")
    print("mapped: " + ", ".join(f"{k}<-{v}" for k, v in rep.mapped.items()))
    print("missing optional: " + (", ".join(rep.missing_optional) or "none"))
    print("unmapped columns: " + (", ".join(rep.unmapped_columns) or "none"))
    for pr in problems[:5]:
        print(f"  problem: {pr}")
    jumps = unadjusted_jumps(bars, price_limit_pct=spec.price_limit_pct)
    if jumps:
        print(f"{len(jumps)} suspicious jump(s) on unadjusted data, e.g. {jumps[0].symbol} {jumps[0].day} {jumps[0].change_pct:+.0f}%")
    return 0


def _cmd_scenarios(a: argparse.Namespace) -> int:
    weights = _parse_weights(a.weights)
    if abs(sum(weights.values()) - 1.0) > 1e-6:
        print(f"weights sum to {sum(weights.values()):g}, expected 1.0", file=sys.stderr)
        return 2
    bars = []
    if a.market != "nse":
        b, problems = load_dir(a.dir, SPECS[a.market], _overrides(a.map))
        bars = [x for x in b if x.symbol in weights or a.all_symbols]
        for pr in problems[:3]:
            print(f"# {pr}", file=sys.stderr)
    for f in (sorted(Path(a.dir).glob("*.csv")) if a.market == "nse" else []):
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


def _load_dir(folder: str, exchange: str, series: str | None) -> list:
    bars = []
    for f in sorted(Path(folder).glob("*.csv")):
        b, problems = bars_from_csv(f.read_text(errors="ignore"), series=series, exchange=exchange)
        bars += b
        for pr in problems[:3]:
            print(f"# {f.name}: {pr}", file=sys.stderr)
    return bars


def _cmd_crosscheck(a: argparse.Namespace) -> int:
    r = cross_check(_load_dir(a.nse_dir, "NSE", "EQ"), _load_dir(a.bse_dir, "BSE", None), a.close_tol, a.return_tol)
    med = "n/a" if r.median_close_diff_pct is None else f"{r.median_close_diff_pct:.3f}%"
    print(f"matched={r.matched} unmatched NSE={r.unmatched_nse} BSE={r.unmatched_bse} rows without ISIN={r.no_isin}")
    print(f"median |NSE-BSE| close difference {med}; mismatches beyond tolerance: {len(r.mismatches)}")
    for m in r.mismatches[:20]:
        print(f"  {m.day} {m.isin} {m.nse_symbol}/{m.bse_symbol}: close diff {m.close_diff_pct:.2f}%, return diff {m.return_diff_pp:.2f} pp")
    return 0 if r.clean else 1


def _ledger(path: str, cash: float | None = None) -> PaperLedger:
    if Path(path).exists():
        return PaperLedger.load(path)
    if cash is None:
        raise SystemExit(f"{path} does not exist; pass --cash to create it")
    return PaperLedger(cash)


def _cmd_paper_enter(a: argparse.Namespace) -> int:
    prices = _load_prices(a.prices)
    L = _ledger(a.ledger, a.cash)
    t = L.enter(a.ticker.upper(), a.amount, _day(a.day, "--day"), prices[a.ticker.upper()])
    L.save(a.ledger)
    print(f"paper BUY {t.qty} {t.ticker} @ {t.price:.2f} on {t.day} (fees {t.fees:.2f}); cash left {L.cash():.2f}")
    return 0


def _cmd_paper_exit(a: argparse.Namespace) -> int:
    prices = _load_prices(a.prices)
    L = _ledger(a.ledger)
    t = L.exit(a.ticker.upper(), _day(a.day, "--day"), prices[a.ticker.upper()])
    L.save(a.ledger)
    print(f"paper SELL {t.qty} {t.ticker} @ {t.price:.2f} on {t.day} (fees {t.fees:.2f}); cash {L.cash():.2f}")
    return 0


def _cmd_paper_report(a: argparse.Namespace) -> int:
    r = report(_ledger(a.ledger), _load_prices(a.prices), a.benchmark.upper())
    print(f"days={r.days} trades={r.n_trades} final value {r.final_value:,.0f}")
    print(f"portfolio: {r.portfolio.total_return_pct:+.1f}% | maxdd {r.portfolio.max_drawdown_pct:.1f}% | sharpe {r.portfolio.sharpe:.2f}")
    print(f"benchmark {a.benchmark.upper()}: {r.benchmark.total_return_pct:+.1f}% | maxdd {r.benchmark.max_drawdown_pct:.1f}% | sharpe {r.benchmark.sharpe:.2f}")
    print(f"excess return after costs: {r.excess_return_pct:+.1f} pts")
    print(f"verdict: {r.verdict}")
    return 0


def _cmd_paper_review(a: argparse.Namespace) -> int:
    trips = round_trips(_ledger(a.ledger))
    pm = post_mortem(trips)
    pf = "n/a (no losses)" if pm.profit_factor is None else f"{pm.profit_factor:.2f}"
    def fmt(v: float | None) -> str:
        return "n/a" if v is None else f"{v:+.1f}%"

    print(f"closed trades={pm.n} win rate {pm.win_rate:.0%} | avg win {fmt(pm.avg_win_pct)} | avg loss {fmt(pm.avg_loss_pct)}")
    print(f"expectancy per trade {pm.expectancy_pct:+.2f}% | profit factor {pf}")
    print(f"worst: {pm.worst.ticker} {pm.worst.return_pct:+.1f}% over {pm.worst.days_held} days")
    return 0


def _cmd_briefing(a: argparse.Namespace) -> int:
    cues, headlines, now = load_cues(a.cues)
    print(render(build_briefing(cues, now, headlines)))
    return 0


def _cmd_validate_tone(a: argparse.Namespace) -> int:
    with open(a.history, newline="") as fh:
        rows = [(float(r["tone"]), float(r["nifty_return"])) for r in csv.DictReader(fh)]
    v = validate_tone(rows)
    print(f"days={v.n} direction hit rate {v.direction_hit_rate:.0%} | rank corr {v.rank_corr:+.3f} | permutation p={v.p_value:.3f}")
    return 0


def _cmd_playbook(a: argparse.Namespace) -> int:
    if a.measure:
        by_id = {e.id: e for e in CATALOG}
        if a.episode not in by_id:
            raise ValueError(f"unknown episode {a.episode!r}; run `playbook --list`")
        series: dict[str, dict[date, float]] = defaultdict(dict)
        with open(a.measure, newline="") as fh:
            for r in csv.DictReader(fh):
                series[r["sector"]][date.fromisoformat(r["date"])] = float(r["return"])
        m = measure_episode(series, by_id[a.episode], a.after_days)
        best, worst = winners_and_flops(m)
        print(f"{by_id[a.episode].name}: measured on your data (returns compounded, %)")
        for sec in best:
            v = m[sec]
            after = "n/a" if v["after_pct"] is None else f"{v['after_pct']:+.1f}%"
            print(f"  {sec:<24} during {v['during_pct']:+.1f}%  next {a.after_days}d {after}")
        print(f"winners: {', '.join(best[:3]) or 'none'} | flops: {', '.join(worst[:3]) or 'none'}")
        print("no data in window: " + (", ".join(sorted(s for s, v in m.items() if v["during_pct"] is None)) or "none"))
        return 0
    if a.list or not a.events:
        for e in episodes(a.events[0] if a.events else None):
            print(f"{e.id:<22} {e.category:<22} {e.start}..{e.end} {e.region:<7} conf={e.confidence}  {e.name}")
        print("sectors in catalog: " + ", ".join(known_sectors()))
        return 0
    if not a.sectors:
        raise ValueError("--sectors is required with --events")
    print("Historical tilt only, from a small hand-recalled catalog (docs/TESTING.md, playbook.py). Not a forecast.")
    for j in judge(a.sectors, a.events, a.region):
        print(f"  {j.sector:<24} {j.stance:<26} score {j.score:+.2f} (win {j.wins} / flop {j.flops} / mixed {j.mixed}, n={j.n_episodes}) - {j.caveat}")
    return 0


def _cmd_skillwatch(a: argparse.Namespace) -> int:
    changes = skillwatch_check(load_watchlist(a.watchlist), a.state)
    today = date.today().isoformat()
    text = render_digest(changes, today)
    print(text)
    if a.digest_dir:
        out = Path(a.digest_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"digest-{today}.md").write_text(text + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quant-agents")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("consensus", help="score broker calls from a file")
    c.add_argument("file", help="broker-call file: date | TICKER | broker | rating | target X | cmp Y | source")
    c.add_argument("--today", default=date.today().isoformat(), help="as-of date YYYY-MM-DD for the freshness filter (default: today)")
    c.add_argument("--max-age-days", type=int, default=30, help="drop calls older than this many days (default 30)")
    c.set_defaults(fn=_cmd_consensus)
    pr = sub.add_parser("propose", help="build risk-checked order proposals (never places orders)")
    pr.add_argument("file", help="broker-call file, same format as for consensus")
    pr.add_argument("--cash", type=float, default=3800.0, help="portfolio value in rupees used for sizing (default 3800)")
    pr.add_argument("--today", default=date.today().isoformat(), help="as-of date YYYY-MM-DD (default: today)")
    pr.add_argument("--audit", help="write JSONL audit log here")
    pr.add_argument("--universe", help="CSV of ticker,sector,segment|amfi_rank[,adv_value_cr,promoter_pledge_pct,debt_to_equity]: enables segment and sector rules")
    pr.add_argument("--bhavcopy", help="folder of bhavcopy CSVs used to fill liquidity (with --universe)")
    pr.add_argument("--max-invested-pct", type=float, help="cap total invested capital at this percent of the portfolio, e.g. the "
                    "ceiling from posture.exposure_ceiling (with --universe; default: no cap)")
    pr.add_argument("--events", help="CSV date,kind,ticker of results/policy dates; adds an event-risk caveat (with --universe)")
    pr.set_defaults(fn=_cmd_propose)
    b = sub.add_parser("backtest", help="momentum vs buy&hold on seeded synthetic prices")
    b.add_argument("--days", type=int, default=756, help="length of the synthetic price series in days (default 756)")
    b.add_argument("--seed", type=int, default=7, help="random seed so runs are repeatable (default 7)")
    b.add_argument("--lookback", type=int, default=20, help="momentum lookback in days (default 20)")
    b.set_defaults(fn=_cmd_backtest)
    e = sub.add_parser("extract", help="turn a folder of broker reports (PDF/txt) into call lines")
    e.add_argument("folder", help="folder of reports named TICKER__BROKER__YYYY-MM-DD.pdf or .txt")
    e.set_defaults(fn=_cmd_extract)
    v = sub.add_parser("validate", help="score past calls against later prices (CSV: ticker,date,close)")
    v.add_argument("calls", help="call file with prices, same format as for consensus")
    v.add_argument("prices", help="price CSV with columns ticker,date,close")
    v.add_argument("--horizon", type=int, default=180, help="days to hold after each call (default 180)")
    v.set_defaults(fn=_cmd_validate)
    n = sub.add_parser("nse-tools", help="list tools of an MCP server (needs network access to it)")
    n.add_argument("url", nargs="?", default="https://mcp.nseindia.in/bhavcopy/cm/mcp", help="MCP server URL (default: NSE bhavcopy server)")
    n.set_defaults(fn=_cmd_nse_tools)
    sc = sub.add_parser("scenarios", help="stress-test a portfolio (VaR, shocks, bootstrap, liquidity) on a folder of daily price CSVs")
    sc.add_argument("dir", help="folder of daily CSV files for the chosen --market")
    sc.add_argument("--weights", required=True, help="e.g. RELIANCE=0.5,INFY=0.5")
    sc.add_argument("--value", type=float, default=3800.0, help="portfolio value in the market currency, used for liquidity (default 3800)")
    sc.add_argument("--horizon", type=int, default=60, help="days for the drawdown simulation (default 60)")
    sc.add_argument("--all-symbols", action="store_true", help="also load symbols not in --weights (needed for the market proxy and betas)")
    sc.add_argument("--market", choices=["nse", *SPECS], default="nse", help="file layout: nse (default), hkex or krx")
    sc.add_argument("--map", action="append", metavar="FIELD=COLUMN", help="override a column mapping, e.g. close=종가 (repeatable)")
    sc.set_defaults(fn=_cmd_scenarios)
    mv = sub.add_parser("markets-verify", help="check how an HKEX or KRX file maps onto our fields")
    mv.add_argument("file", help="one real HKEX or KRX CSV file")
    mv.add_argument("--market", choices=list(SPECS), required=True, help="which exchange layout to check against")
    mv.add_argument("--day", help="trade date YYYY-MM-DD when the file has no date column")
    mv.add_argument("--map", action="append", metavar="FIELD=COLUMN", help="override a column mapping, e.g. close=종가 (repeatable)")
    mv.set_defaults(fn=_cmd_markets_verify)
    x = sub.add_parser("crosscheck", help="compare NSE and BSE bhavcopy folders (joined by ISIN)")
    x.add_argument("nse_dir", help="folder of NSE bhavcopy CSVs")
    x.add_argument("bse_dir", help="folder of BSE UDiFF bhavcopy CSVs (needs an ISIN column)")
    x.add_argument("--close-tol", type=float, default=1.0, help="max close difference, percent")
    x.add_argument("--return-tol", type=float, default=1.0, help="max daily-return difference, percentage points")
    x.set_defaults(fn=_cmd_crosscheck)
    for name, fn, hlp in (("paper-enter", _cmd_paper_enter, "record a paper buy at the next close after --day"),
                          ("paper-exit", _cmd_paper_exit, "record a paper sell of the whole position")):
        q = sub.add_parser(name, help=hlp)
        q.add_argument("ledger", help="paper ledger JSON file (created by paper-enter with --cash)")
        q.add_argument("--ticker", required=True, help="ticker as it appears in the prices CSV")
        q.add_argument("--day", required=True, help="decision day YYYY-MM-DD; fills at the next close")
        q.add_argument("--prices", required=True, help="CSV ticker,date,close")
        if name == "paper-enter":
            q.add_argument("--amount", type=float, required=True, help="rupees to invest (whole shares only; leftover stays cash)")
            q.add_argument("--cash", type=float, help="starting paper cash, used when creating the ledger")
        q.set_defaults(fn=fn)
    pr2 = sub.add_parser("paper-report", help="paper results vs a benchmark, after costs")
    pr2.add_argument("ledger", help="paper ledger JSON file")
    pr2.add_argument("--prices", required=True, help="price CSV ticker,date,close including the benchmark")
    pr2.add_argument("--benchmark", required=True, help="ticker of an index fund/ETF present in the prices CSV")
    pr2.set_defaults(fn=_cmd_paper_report)
    rv = sub.add_parser("paper-review", help="post-mortem of closed paper trades: win rate, expectancy, worst trade")
    rv.add_argument("ledger", help="paper ledger JSON file")
    rv.set_defaults(fn=_cmd_paper_review)
    br = sub.add_parser("briefing", help="pre-market global-cues tone from a JSON file")
    br.add_argument("cues", help="JSON file of overnight cues (see data/sample_cues.json)")
    br.set_defaults(fn=_cmd_briefing)
    vt = sub.add_parser("validate-tone", help="did the tone predict the Nifty move? CSV: tone,nifty_return")
    vt.add_argument("history", help="CSV with columns tone,nifty_return (at least 30 days)")
    vt.set_defaults(fn=_cmd_validate_tone)
    pb = sub.add_parser("playbook", help="how sectors tended to behave in past wars, pandemics, weather and other shocks")
    pb.add_argument("--list", action="store_true", help="list catalog episodes (optionally only the first --events category)")
    pb.add_argument("--events", nargs="+", metavar="CATEGORY", help="event categories to pool, e.g. war drought_weak_monsoon")
    pb.add_argument("--sectors", nargs="+", metavar="SECTOR", help="sectors to judge, e.g. banks it fmcg")
    pb.add_argument("--region", choices=["india", "asia", "global"], help="only episodes from this region plus global ones")
    pb.add_argument("--measure", metavar="CSV", help="measure real sector behavior from a CSV with columns date,sector,return (daily fractions)")
    pb.add_argument("--episode", default="covid-2020", help="episode id for --measure (default covid-2020)")
    pb.add_argument("--after-days", type=int, default=60, help="calendar days after the episode to also measure (default 60)")
    pb.set_defaults(fn=_cmd_playbook)
    sw = sub.add_parser("skillwatch", help="check reference skill repos for changes and possible gaps vs ours")
    sw.add_argument("--watchlist", default="data/watchlist.json", help="JSON list of repos to watch (default data/watchlist.json)")
    sw.add_argument("--state", default="watch/state.json", help="where last-seen commits and skills are saved (default watch/state.json)")
    sw.add_argument("--digest-dir", default="watch", help="folder for the dated digest (default watch)")
    sw.set_defaults(fn=_cmd_skillwatch)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (OSError, ValueError, KeyError, RuntimeError) as e:  # user-facing problems: say what is wrong, exit 2
        if os.environ.get("QUANT_AGENTS_DEBUG") == "1":
            raise
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
