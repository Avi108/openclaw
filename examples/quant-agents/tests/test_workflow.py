import json
from datetime import date

from quant_agents.cli import main
from quant_agents.risk import PortfolioState, RiskPolicy
from quant_agents.workflow import RuleAnalyst, run

TODAY = date(2026, 10, 14)
CALLS = """\
2026-10-12 | GOOD | B1 | buy | target 150 | cmp 100
2026-10-11 | GOOD | B2 | buy | target 145 | cmp 100
2026-10-12 | GOOD2 | B1 | buy | target 140 | cmp 100
2026-10-12 | GOOD2 | B3 | buy | target 135 | cmp 100
2026-10-12 | LONE | B1 | buy | target 160 | cmp 100
2026-10-12 | MIX | B1 | buy | target 150 | cmp 100
2026-10-12 | MIX | B2 | sell | target 60 | cmp 100
2026-10-12 | HYPE | B1 | buy | target 900 | cmp 100
2026-10-12 | HYPE | B2 | buy | target 880 | cmp 100
2026-07-01 | OLD | B1 | buy | target 200 | cmp 100
2026-07-01 | OLD | B2 | buy | target 200 | cmp 100
"""


def fresh_state():
    return PortfolioState(cash=3800.0, positions={}, peak_value=3800.0)


class RecordingBroker:
    def __init__(self):
        self.orders = []

    def place_limit_buy(self, ticker, amount):
        self.orders.append((ticker, amount))
        return f"ORD-{len(self.orders)}"


def test_only_clean_multi_broker_buys_become_proposals():
    r = run(CALLS, fresh_state(), RiskPolicy(), TODAY)
    assert {p.ticker for p in r.proposals} == {"GOOD", "GOOD2"}


def test_default_gate_denies_and_broker_is_never_called():
    broker = RecordingBroker()
    r = run(CALLS, fresh_state(), RiskPolicy(), TODAY, broker=broker)
    assert r.approved == [] and r.placed == [] and broker.orders == []


def test_approved_orders_reach_broker_with_risk_sized_amounts():
    broker = RecordingBroker()
    r = run(CALLS, fresh_state(), RiskPolicy(), TODAY, approve=lambda p: p.ticker == "GOOD", broker=broker)
    assert [t for t, _ in broker.orders] == ["GOOD"]
    assert broker.orders[0][1] <= 3800 * 0.05 + 1e-9  # 5% per-order cap
    assert r.placed == [("GOOD", "ORD-1")]


def test_cash_is_reserved_across_proposals_and_daily_limit_holds():
    pol = RiskPolicy(max_orders_per_day=1)
    r = run(CALLS, fresh_state(), pol, TODAY)
    assert len(r.proposals) == 1
    assert any(e["stage"] == "risk" and "daily order limit reached" in e["blocked"] for e in r.audit.events)


def test_audit_log_explains_every_exclusion():
    r = run(CALLS, fresh_state(), RiskPolicy(), TODAY)
    lines = [json.loads(x) for x in r.audit.to_jsonl().splitlines()]
    screened = {e["ticker"] for e in lines if e["stage"] == "screen" and not e["kept"]}
    assert {"HYPE", "MIX"} <= screened  # implausible or too-low upside filtered before any agent sees it
    vetoed = {e["ticker"] for e in lines if e["stage"] == "analyst" and not e["kept"]}
    assert "LONE" in vetoed
    assert next(e for e in lines if e["stage"] == "freshness")["dropped"] == 2


def test_custom_analyst_can_veto_but_not_resize():
    class Veto:
        def review(self, row):
            return row.ticker != "GOOD", "test veto"

    r = run(CALLS, fresh_state(), RiskPolicy(), TODAY, analyst=Veto())
    assert "GOOD" not in {p.ticker for p in r.proposals}


def test_halted_portfolio_gets_no_proposals():
    s = PortfolioState(cash=3000.0, positions={}, peak_value=5000.0)  # 40% drawdown
    r = run(CALLS, s, RiskPolicy(), TODAY)
    assert r.proposals == []


def test_cli_consensus_and_propose(tmp_path, capsys):
    f = tmp_path / "calls.txt"
    f.write_text(CALLS)
    assert main(["consensus", str(f), "--today", "2026-10-14"]) == 0
    assert "GOOD" in capsys.readouterr().out
    audit = tmp_path / "audit.jsonl"
    assert main(["propose", str(f), "--today", "2026-10-14", "--audit", str(audit)]) == 0
    assert audit.read_text().strip()
    assert main(["backtest", "--days", "200"]) == 0
    assert "SYNTHETIC" in capsys.readouterr().out
