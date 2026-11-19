from datetime import date

import pytest

from quant_agents.nse import Bar
from quant_agents.risk import PortfolioState, RiskPolicy
from quant_agents.segmented import Limits, run_segmented
from quant_agents.universe import Instrument, Segment, adv_from_bars, load_universe, segment_from_rank, with_adv

TODAY = date(2026, 10, 16)


def call(t, brokers, target=150, price=100, rating="buy"):
    return "\n".join(f"2026-10-14 | {t} | B{i} | {rating} | target {target} | cmp {price}" for i in range(brokers))


def inst(t, seg, sector="x", adv=50.0, pledge=0.0, de=0.3):
    return Instrument(t, seg, sector, adv, pledge, de)


def state(cash=100_000.0):
    return PortfolioState(cash=cash, positions={}, peak_value=cash)


def go(calls, universe, policy=RiskPolicy(max_orders_per_day=20, max_orders_per_week=50), **kw):
    return run_segmented(calls, state(), policy, universe, TODAY, **kw)


def test_segment_from_rank_boundaries():
    assert [segment_from_rank(r).value for r in (1, 100, 101, 250, 251, 500, 501)] == \
        ["large", "large", "mid", "mid", "small", "small", "micro"]
    with pytest.raises(ValueError):
        segment_from_rank(0)


def test_large_needs_two_brokers_small_may_have_one_with_caveat_and_smaller_cap():
    u = {"BIG": inst("BIG", Segment.LARGE), "TINY": inst("TINY", Segment.SMALL, "metals", adv=5.0)}
    r = go(call("BIG", 1) + "\n" + call("TINY", 1), u)
    assert [c.ticker for c in r.proposals] == ["TINY"]
    assert r.proposals[0].amount == pytest.approx(2000.0) and "thin coverage (1 broker)" in r.proposals[0].caveats  # 2% cap
    assert any(x.ticker == "BIG" and "needs >= 2" in x.reasons[0] for x in r.research)
    r2 = go(call("BIG", 2), u)
    assert r2.proposals[0].amount == pytest.approx(5000.0)  # 5% cap


def test_small_cap_without_evidence_or_liquidity_goes_to_research_list_not_proposals():
    u = {
        "NOEVID": Instrument("NOEVID", Segment.SMALL, "a", 5.0, None, None),
        "ILLIQ": inst("ILLIQ", Segment.SMALL, "b", adv=0.2),
        "PLEDGED": inst("PLEDGED", Segment.SMALL, "c", pledge=45.0),
        "LEVERED": inst("LEVERED", Segment.MICRO, "d", adv=1.0, de=3.0),
        "NOADV": Instrument("NOADV", Segment.MICRO, "e", None, 0.0, 0.2),
    }
    r = go("\n".join(call(t, 1) for t in u), u)
    assert r.proposals == []
    why = {x.ticker: " | ".join(x.reasons) for x in r.research}
    assert "evidence missing: promoter pledge, debt/equity" in why["NOEVID"]
    assert "illiquid" in why["ILLIQ"] and "promoter pledge 45%" in why["PLEDGED"]
    assert "debt/equity 3" in why["LEVERED"] and "liquidity unknown" in why["NOADV"]


def test_leverage_waived_for_financials():
    u = {"BANKCO": inst("BANKCO", Segment.SMALL, "banks", adv=5.0, de=8.0)}
    assert [c.ticker for c in go(call("BANKCO", 1), u).proposals] == ["BANKCO"]


def test_unknown_ticker_is_researched_not_proposed():
    r = go(call("MYSTERY", 3), {})
    assert r.proposals == [] and "not in universe" in r.research[0].reasons[0]


def test_small_micro_slice_cap_is_shared():
    u = {f"S{i}": inst(f"S{i}", Segment.SMALL, f"sec{i}", adv=5.0) for i in range(8)}
    r = go("\n".join(call(t, 1) for t in u), u)
    assert sum(c.amount for c in r.proposals) <= 10_000 + 1e-6  # 10% of 100k across all of them
    assert len(r.proposals) == 5 and any("cap reached" in x.reasons[0] for x in r.research)


def test_sector_cap_blocks_concentration():
    u = {f"A{i}": inst(f"A{i}", Segment.LARGE, "energy") for i in range(4)}
    r = go("\n".join(call(t, 2) for t in u), u, limits=Limits(sector_cap_pct=10.0))
    assert sum(c.amount for c in r.proposals) <= 10_000 + 1e-6 and len(r.proposals) == 2
    assert sum("cap reached" in x.reasons[0] for x in r.research) == 2


def test_daily_order_limit_queues_instead_of_discarding():
    u = {f"L{i}": inst(f"L{i}", Segment.LARGE, f"s{i}") for i in range(4)}
    r = run_segmented("\n".join(call(t, 2) for t in u), state(), RiskPolicy(max_orders_per_day=2), u, TODAY)
    assert len(r.proposals) == 2 and len(r.queued) == 2 and not r.research


def test_conflicting_broker_views_are_blocked_even_for_small_caps():
    u = {"MIX": inst("MIX", Segment.SMALL, "x", adv=5.0)}
    calls = "2026-10-14 | MIX | B1 | buy | target 150 | cmp 100\n2026-10-14 | MIX | B2 | sell | target 70 | cmp 100"
    assert go(calls, u).proposals == []


def test_universe_csv_and_adv_from_bars(tmp_path):
    f = tmp_path / "u.csv"
    f.write_text("# comment\nticker,sector,amfi_rank,segment,adv_value_cr,promoter_pledge_pct,debt_to_equity\n"
                 "abc,Metals,300,,,,\nxyz,Banks,,large,40,0,\n")
    u = load_universe(f)
    assert u["ABC"].segment is Segment.SMALL and u["ABC"].adv_value_cr is None and u["XYZ"].segment is Segment.LARGE
    bars = [Bar("ABC", date(2026, 10, d), 100, 100, 100, 100.0, 100, 2_000_000) for d in range(1, 6)]
    adv = adv_from_bars(bars)
    assert adv["ABC"] == pytest.approx(20.0)  # 100 * 2m = Rs 20 crore a day
    filled = with_adv(u, adv)
    assert filled["ABC"].adv_value_cr == pytest.approx(20.0) and filled["XYZ"].adv_value_cr == 40.0  # given value kept


def test_pending_data_separates_missing_inputs_from_real_failures():
    from quant_agents.segmented import Rejected  # noqa: F401

    u = {"NEEDS": Instrument("NEEDS", Segment.SMALL, "a", None, None, None), "BAD": inst("BAD", Segment.SMALL, "b", pledge=60.0)}
    r = go(call("NEEDS", 1) + "\n" + call("BAD", 1), u)
    by = {x.ticker: x for x in r.research}
    assert by["NEEDS"].pending_data and not by["BAD"].pending_data


def test_cli_propose_with_universe(tmp_path, capsys):
    from pathlib import Path

    from quant_agents.cli import main

    root = Path(__file__).resolve().parent.parent / "data"
    assert main(["propose", str(root / "sample_broker_calls.txt"), "--universe", str(root / "sample_universe.csv"),
                 "--cash", "100000", "--today", "2026-10-14"]) == 0
    out = capsys.readouterr().out
    assert "PROPOSALS" in out and "large" in out and "(nothing is placed)" in out


def test_quality_scores_block_weak_names_when_supplied_and_waive_altman_for_financials():
    u = {
        "WEAK": Instrument("WEAK", Segment.SMALL, "x", 5.0, 0.0, 0.3, piotroski_f=2),
        "DISTRESS": Instrument("DISTRESS", Segment.SMALL, "x", 5.0, 0.0, 0.3, altman_z=0.5),
        "GOOD": Instrument("GOOD", Segment.SMALL, "y", 5.0, 0.0, 0.3, piotroski_f=7, altman_z=3.2),
        "BANK": Instrument("BANK", Segment.SMALL, "banks", 5.0, 0.0, 0.3, altman_z=0.5),
    }
    r = go("\n".join(call(t, 1) for t in u), u)
    assert {c.ticker for c in r.proposals} == {"GOOD", "BANK"}
    why = {x.ticker: x.reasons[0] for x in r.research}
    assert "Piotroski" in why["WEAK"] and "Altman" in why["DISTRESS"]


def test_event_risk_is_a_caveat_not_a_block():
    from quant_agents.events import Event

    u = {"EV": inst("EV", Segment.LARGE, "x")}
    ev = (Event(date(2026, 10, 18), "results", "EV"), Event(date(2026, 10, 19), "rbi_mpc", None))
    r = run_segmented(call("EV", 2), state(), RiskPolicy(max_orders_per_day=5), u, TODAY, events=ev)
    assert r.proposals and any("event risk: results EV in 2d; rbi_mpc in 3d" in c for c in r.proposals[0].caveats)


def test_universe_csv_reads_quality_columns(tmp_path):
    f = tmp_path / "u.csv"
    f.write_text("ticker,sector,segment,piotroski_f,altman_z\nabc,metals,small,6,2.4\nxyz,metals,small,,\n")
    u = load_universe(f)
    assert u["ABC"].piotroski_f == 6 and u["ABC"].altman_z == 2.4 and u["XYZ"].piotroski_f is None


def test_cli_max_invested_pct_caps_total_proposals(capsys):
    from pathlib import Path

    from quant_agents.cli import main

    root = Path(__file__).resolve().parent.parent / "data"
    args = ["propose", str(root / "sample_broker_calls.txt"), "--universe", str(root / "sample_universe.csv"),
            "--cash", "100000", "--today", "2026-10-14"]
    assert main(args + ["--max-invested-pct", "3"]) == 0
    out = capsys.readouterr().out
    amounts = [int(line.split()[3]) for line in out.splitlines() if line.startswith("  ") and line.split()[0] in ("large", "mid", "small", "micro")
               and line.split()[3].isdigit()]
    assert sum(amounts) <= 3000
    assert "exposure cap reached" in out
