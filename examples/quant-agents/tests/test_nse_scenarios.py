import json
import math
import random
import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from quant_agents import scenarios as sc
from quant_agents.nse import Bar, McpClient, McpError, bars_from_csv, bars_from_records

D0 = date(2026, 1, 1)


def synth_bars(symbol, n=300, seed=1, vol=0.015, split_at=None, crash_at=None, base_volume=1_000_000):
    rng = random.Random(seed)
    px, bars, prev = 100.0, [], 100.0
    for i in range(n):
        r = rng.gauss(0.0004, vol)
        if crash_at is not None and i == crash_at:
            r = -0.12
        close = px * (1 + r)
        adj_prev = px
        if split_at is not None and i == split_at:
            # 1:5 split: share price falls to a fifth, NSE adjusts prev_close accordingly
            adj_prev, close = px / 5, close / 5
        bars.append(Bar(symbol, D0 + timedelta(days=i), close, close * 1.01, close * 0.99, close, adj_prev, base_volume))
        px = close
    return bars


def test_parse_legacy_and_udiff_columns_and_filters_series():
    legacy = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,PREVCLOSE,TOTTRDQTY,TIMESTAMP\nABC,EQ,10,11,9,10.5,10,1000,09-OCT-2026\nABC,BE,10,11,9,10.5,10,1000,09-OCT-2026"
    bars, problems = bars_from_csv(legacy)
    assert len(bars) == 1 and bars[0].day == date(2026, 10, 9) and not problems
    udiff = "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,PrvsClsgPric,TtlTradgVol\n2026-10-09,xyz,EQ,1,2,1,1.5,1.2,50"
    assert bars_from_csv(udiff)[0][0].symbol == "XYZ"


def test_parser_reports_bad_rows_instead_of_guessing():
    bars, problems = bars_from_records([
        {"SYMBOL": "A", "SERIES": "EQ", "OPEN": 1, "HIGH": 1, "LOW": 1, "CLOSE": 0, "PREVCLOSE": 1, "TOTTRDQTY": 1, "TIMESTAMP": "2026-01-01"},
        {"SYMBOL": "B", "SERIES": "EQ", "OPEN": "x", "HIGH": 1, "LOW": 1, "CLOSE": 1, "PREVCLOSE": 1, "TOTTRDQTY": 1, "TIMESTAMP": "2026-01-01"},
    ])
    assert bars == [] and len(problems) == 2


def test_split_is_not_a_crash_when_using_adjusted_prev_close():
    bars = synth_bars("SPL", split_at=100)
    flags = sc.corporate_action_days(bars)
    assert len(flags) == 1 and flags[0].raw_change_pct < -75 and abs(flags[0].adjusted_change_pct) < 10
    worst = sc.worst_windows(sc.daily_returns(bars), 1, 1)[0][2]
    assert worst > -10  # naive close-to-close would show about -80%


def test_worst_windows_find_planted_crash_and_do_not_overlap():
    bars = synth_bars("CRA", crash_at=150)
    ww = sc.worst_windows(sc.daily_returns(bars), 5, 3)
    assert ww[0][0] <= D0 + timedelta(days=150) <= ww[0][1] and ww[0][2] < -10
    spans = sorted((a, b) for a, b, _ in ww)
    assert all(spans[i][1] < spans[i + 1][0] for i in range(len(spans) - 1))


def test_var_cvar_known_values():
    rs = [-0.10] * 5 + [0.01] * 95
    var, cvar = sc.var_cvar(rs, 0.95)
    assert var == pytest.approx(0.10) and cvar == pytest.approx(0.10)
    with pytest.raises(ValueError):
        sc.var_cvar([0.0] * 5)


def test_portfolio_drops_gaps_rather_than_zero_filling():
    a = {D0 + timedelta(days=i): 0.01 for i in range(10)}
    b = {D0 + timedelta(days=i): 0.02 for i in range(5)}
    series, dropped = sc.portfolio_returns({"A": 0.5, "B": 0.5}, {"A": a, "B": b})
    assert len(series) == 5 and dropped == 5 and series[D0] == pytest.approx(0.015)
    with pytest.raises(ValueError):
        sc.portfolio_returns({"A": 1.0, "Z": 0.0, "Q": 1.0}, {"A": a})


def test_bootstrap_more_risk_means_higher_breach_probability_and_is_deterministic():
    rng = random.Random(1)
    calm = [rng.gauss(0.0005, 0.005) for _ in range(300)]
    wild = [rng.gauss(0.0, 0.03) for _ in range(300)]
    p_calm, _ = sc.bootstrap_drawdown(calm, 60, n=400)
    p_wild, t_wild = sc.bootstrap_drawdown(wild, 60, n=400)
    assert p_calm < 0.05 < p_wild and t_wild < 0
    assert sc.bootstrap_drawdown(wild, 60, n=100, seed=3) == sc.bootstrap_drawdown(wild, 60, n=100, seed=3)


def test_beta_and_shock():
    m = {D0 + timedelta(days=i): (0.01 if i % 2 else -0.01) for i in range(60)}
    asset = {d: 2 * r for d, r in m.items()}
    assert sc.beta(asset, m) == pytest.approx(2.0)
    assert sc.beta({D0: 0.1}, m) is None
    pnl = sc.shock_pnl({"A": 0.5, "B": 0.5}, {"A": 2.0, "B": None}, -10.0)
    assert sum(pnl.values()) == pytest.approx(-15.0)  # 0.5*2*-10 + 0.5*1*-10


def test_regime_metrics_labels_bear_market():
    days = [D0 + timedelta(days=i) for i in range(200)]
    market = {d: (0.002 if i < 100 else -0.003) for i, d in enumerate(days)}
    strat = {d: market[d] for d in days}
    out = sc.regime_metrics(strat, market)
    assert out["bull"] and out["bull"].total_return_pct > 0
    assert out["bear"] and out["bear"].total_return_pct < 0


def test_days_to_exit():
    assert sc.days_to_exit(1_000_000, 10_000_000) == pytest.approx(1.0)
    assert sc.days_to_exit(100, 0) == math.inf


def test_build_report_end_to_end_flags_split_and_reports_assumptions():
    bars = sum((synth_bars(s, seed=i, split_at=120 if s == "SPL" else None) for i, s in enumerate(["AAA", "BBB", "SPL", "CCC"])), [])
    rep = sc.build_report(bars, {"AAA": 0.4, "SPL": 0.3, "BBB": 0.3}, horizon=40, shocks=(-10.0,))
    assert [f.symbol for f in rep.corporate_actions] == ["SPL"]
    assert rep.var95_pct > 0 and rep.cvar95_pct >= rep.var95_pct
    assert rep.shocks["-10%"] < 0 and all(v < 1 for v in rep.liquidity.values())
    assert any("not the Nifty" in n for n in rep.notes)


# ------------------------- MCP client against a fake server -------------------------

def serve(handler_cls):
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/mcp"


class FakeMcp(BaseHTTPRequestHandler):
    sse = False
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        msg = json.loads(self.rfile.read(int(self.headers["content-length"])))
        FakeMcp.seen.append((msg.get("method"), self.headers.get("mcp-session-id")))
        if "id" not in msg:
            self.send_response(202); self.end_headers(); return
        m = msg["method"]
        if m == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {}, "serverInfo": {"name": "fake"}}
        elif m == "tools/list":
            result = {"tools": [{"name": "get_bhavcopy", "description": "x"}]}
        elif m == "tools/call" and msg["params"]["name"] == "get_bhavcopy":
            rows = [{"SYMBOL": "ABC", "SERIES": "EQ", "OPEN": 1, "HIGH": 2, "LOW": 1, "CLOSE": 1.5, "PREVCLOSE": 1.2, "TOTTRDQTY": 10, "TIMESTAMP": "2026-10-09"}]
            result = {"content": [{"type": "text", "text": json.dumps(rows)}]}
        else:
            result = {"isError": True, "content": [{"type": "text", "text": "unknown tool"}]}
        body = json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result})
        ctype = "application/json"
        if FakeMcp.sse:
            body, ctype = f"event: message\ndata: {body}\n\n", "text/event-stream"
        self.send_response(200)
        self.send_header("content-type", ctype)
        if m == "initialize":
            self.send_header("mcp-session-id", "sess-1")
        self.end_headers()
        self.wfile.write(body.encode())


@pytest.mark.parametrize("sse", [False, True])
def test_mcp_client_handshake_session_and_tool_call(sse):
    FakeMcp.sse, FakeMcp.seen = sse, []
    srv, url = serve(FakeMcp)
    try:
        c = McpClient(url, timeout=5)
        assert c.list_tools()[0]["name"] == "get_bhavcopy"
        rows = c.call_tool("get_bhavcopy", {"date": "2026-10-09"})
        bars, problems = bars_from_records(rows)
        assert len(bars) == 1 and not problems
        methods = [m for m, _ in FakeMcp.seen]
        assert methods[:2] == ["initialize", "notifications/initialized"]
        assert all(sess == "sess-1" for m, sess in FakeMcp.seen[1:])  # session id echoed after initialize
        with pytest.raises(McpError):
            c.call_tool("nope")
    finally:
        srv.shutdown()


def test_cli_scenarios_on_bhavcopy_folder(tmp_path, capsys):
    from quant_agents.cli import main

    rows = {}
    for i, sym in enumerate(["AAA", "BBB", "SPL", "CCC"]):
        for b in synth_bars(sym, n=150, seed=i, split_at=60 if sym == "SPL" else None):
            rows.setdefault(b.day, []).append(b)
    for day, bs in rows.items():
        txt = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,PREVCLOSE,TOTTRDQTY,TIMESTAMP\n" + "\n".join(
            f"{b.symbol},EQ,{b.open},{b.high},{b.low},{b.close},{b.prev_close},{b.volume},{day}" for b in bs)
        (tmp_path / f"bhav_{day}.csv").write_text(txt)
    assert main(["scenarios", str(tmp_path), "--weights", "AAA=0.4,SPL=0.3,BBB=0.3", "--horizon", "30"]) == 0
    out = capsys.readouterr().out
    assert "VaR95" in out and "corporate action? SPL" in out and "market-shock P&L" in out
    assert main(["scenarios", str(tmp_path), "--weights", "AAA=0.5,BBB=0.4"]) == 2


def test_regression_beta_uses_each_series_own_mean():
    """Bug found in proofreading: beta once subtracted the other series' mean, which only worked when both means were 0."""
    import random

    rng = random.Random(3)
    days = [D0 + timedelta(days=i) for i in range(200)]
    market = {d: 0.002 + rng.gauss(0, 0.01) for d in days}          # market drifts +0.2% a day
    asset = {d: -0.003 + 1.5 * r for d, r in market.items()}        # true beta 1.5, with a large negative intercept
    assert sc.beta(asset, market) == pytest.approx(1.5, abs=1e-9)
    shifted = {d: 0.05 + 1.5 * r for d, r in market.items()}        # intercept must never change beta
    assert sc.beta(shifted, market) == pytest.approx(1.5, abs=1e-9)
