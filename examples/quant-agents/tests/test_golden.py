"""Golden-output regression tests.

Each test pins the exact text of an analysis on seeded synthetic inputs. If a number changes, the diff shows
exactly which one. After a *reviewed, intended* change, regenerate with:  UPDATE_GOLDEN=1 pytest tests/test_golden.py
"""
import pytest

from conftest import DATA, golden
from quant_agents.cli import main
from quant_agents.fundamentals import Statements, quality_report
from quant_agents.validation import simulate_world


def out(capsys, *args):
    assert main([str(a) for a in args]) in (0, 1)
    c = capsys.readouterr()
    return c.out + (("\n--- stderr ---\n" + c.err) if c.err else "")


def test_consensus_sample(capsys):
    golden("consensus_sample", out(capsys, "consensus", DATA / "sample_broker_calls.txt", "--today", "2026-10-14"))


def test_propose_plain(capsys):
    golden("propose_plain", out(capsys, "propose", DATA / "sample_broker_calls.txt", "--today", "2026-10-14"))


def test_propose_with_universe(capsys):
    golden("propose_universe", out(capsys, "propose", DATA / "sample_broker_calls.txt", "--today", "2026-10-14",
                                   "--universe", DATA / "sample_universe.csv", "--cash", "100000"))


def test_backtest_seeded(capsys):
    golden("backtest_seeded", out(capsys, "backtest", "--days", "300", "--seed", "7"))


def test_briefing_sample(capsys):
    golden("briefing_sample", out(capsys, "briefing", DATA / "sample_cues.json"))


@pytest.mark.parametrize("edge", [0.0, 0.6])
def test_validate_simulated_world(capsys, tmp_path, edge):
    calls, paths = simulate_world(n_stocks=60, edge=edge, seed=11)
    cf, pf = tmp_path / "c.txt", tmp_path / "p.csv"
    cf.write_text("\n".join(f"{c.published} | {c.ticker} | {c.broker} | buy | target {c.target:.2f} | cmp {c.price:.2f}" for c in calls))
    pf.write_text("ticker,date,close\n" + "\n".join(f"{t},{d},{px:.4f}" for t, p in paths.items() for d, px in p))
    golden(f"validate_edge_{edge}", out(capsys, "validate", cf, pf, "--horizon", "180"))


def test_scenarios_on_synthetic_bhavcopy(capsys, world):
    golden("scenarios_nse", out(capsys, "scenarios", world / "nse", "--weights", "AAA=0.4,SPL=0.3,BBB=0.3", "--horizon", "30"))


def test_crosscheck_and_markets_verify(capsys, world):
    golden("crosscheck_clean", out(capsys, "crosscheck", world / "nse", world / "bse"))
    golden("markets_verify_krx", out(capsys, "markets-verify", world / "krx_20261015.csv", "--market", "krx"))


def test_quality_report_fixed_statements():
    base = {"sales": 1000, "cogs": 600, "sga": 100, "depreciation": 50, "ebit": 150, "net_income": 100, "cfo": 120, "receivables": 100,
                "current_assets": 400, "current_liabilities": 200, "ppe": 500, "total_assets": 1000, "total_liabilities": 500,
                "long_term_debt": 200, "retained_earnings": 300, "equity": 500, "shares_outstanding": 100}
    prev = Statements(**base)
    cur = Statements(**{**base, "sales": 1150, "cogs": 650, "net_income": 130, "cfo": 160, "long_term_debt": 180})
    q = quality_report(cur, prev)
    lines = [f"piotroski={q.piotroski.score}", *(f"  {k}={v}" for k, v in q.piotroski.signals.items()),
             f"altman_z={q.altman.z:.6f} zone={q.altman.zone}", f"beneish_m={q.beneish.m:.6f} flagged={q.beneish.flagged}",
             *(f"  {k}={v:.6f}" for k, v in q.beneish.indices.items()), f"red_flags={list(q.red_flags)}"]
    golden("quality_report", "\n".join(lines) + "\n")
