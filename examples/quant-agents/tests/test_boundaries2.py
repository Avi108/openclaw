"""Round 3 of mutation-driven tests: parsing, screening, workflow, paper trading, universe.

Every expected number here is derived by hand in the test (not read back from the code), so a changed formula, sign,
constant or boundary in these modules fails a test.
"""
import inspect
import sys
import types
from datetime import date, timedelta

import pytest

from quant_agents import (backtest, breadth, cues, markets, models, nse, papertrade, pdf_extract, scenarios, segmented,
                          universe, validation, workflow)
from quant_agents.consensus import ConsensusRow
from quant_agents.ingest import drop_stale, parse_calls
from quant_agents.models import BrokerCall, Rating
from quant_agents.nse import Bar
from quant_agents.papertrade import CostModel, PaperLedger, RoundTrip, equity_curve, post_mortem, report
from quant_agents.risk import PortfolioState, RiskPolicy
from quant_agents.segmented import Limits, SegmentRules, run_segmented
from quant_agents.universe import Instrument, Segment

T = date(2026, 10, 16)
D0 = date(2026, 1, 1)
NOCOST = CostModel(0, 0, 0, 0, 0, 0)


def defaults(fn):
    return {k: v.default for k, v in inspect.signature(fn).parameters.items() if v.default is not inspect.Parameter.empty}


# ================================ value objects stay immutable ================================
@pytest.mark.parametrize("cls", [
    models.BrokerCall, models.ConsensusRow, pdf_extract.Extraction, segmented.SegmentRules, segmented.Limits, segmented.Candidate,
    segmented.Rejected, papertrade.CostModel, papertrade.Trade, papertrade.PaperReport, papertrade.RoundTrip, papertrade.PostMortem,
    scenarios.CorporateActionFlag, scenarios.ScenarioReport, validation.Outcome, validation.Summary, backtest.Metrics,
    markets.MarketSpec, markets.ColumnReport, nse.Bar, workflow.Proposal, breadth.Breadth, universe.Instrument, cues.Cue,
    cues.Contribution, cues.Briefing, cues.ToneValidation,
])
def test_more_value_objects_are_frozen(cls):
    assert cls.__dataclass_params__.frozen, f"{cls.__name__} must be frozen"


# ================================ ingest ================================
def test_parser_numbers_lines_from_one_and_keeps_the_source():
    res = parse_calls("garbage\n2026-10-12 | A | B | buy | target 10 | cmp 5 | bt-562047")
    assert res.rejected[0][0] == 1 and res.calls[0].source == "bt-562047"


def test_parser_rejects_non_positive_numbers_but_accepts_small_positive_ones():
    for line in ("2026-10-12 | A | B | buy | target 0 | cmp 5", "2026-10-12 | A | B | buy | target 10 | cmp 0"):
        r = parse_calls(line)
        assert r.calls == [] and r.rejected[0][2] == "non-positive number"
    ok = parse_calls("2026-10-12 | A | B | buy | target 0.5 | cmp 0.25").calls[0]
    assert (ok.target, ok.price) == (0.5, 0.25)


def test_drop_stale_keeps_exactly_thirty_days_and_never_the_future():
    mk = lambda d: BrokerCall("A", "B", Rating.BUY, 10.0, 5.0, d)  # noqa: E731
    kept = drop_stale([mk(T - timedelta(days=30)), mk(T - timedelta(days=31)), mk(T), mk(T + timedelta(days=1))], T)
    assert [c.published for c in kept] == [T - timedelta(days=30), T]
    assert defaults(drop_stale) == {"max_age_days": 30}


# ================================ pdf extraction ================================
def test_single_price_is_used_without_notes():
    e = pdf_extract.extract_call("Rating: BUY\nTarget Price: 150\nCMP: 100", "A", "B", T)
    assert e.reasons == () and e.call.price == 100.0


def test_read_pdf_text_reads_three_pages_by_default_and_treats_empty_pages_as_blank(monkeypatch):
    class Page:
        def __init__(self, t):
            self.t = t

        def extract_text(self):
            return self.t

    class Reader:
        def __init__(self, path):
            self.pages = [Page("p1"), Page(None), Page("p3"), Page("p4")]

    fake = types.ModuleType("pypdf")
    fake.PdfReader = Reader
    monkeypatch.setitem(sys.modules, "pypdf", fake)
    assert pdf_extract.read_pdf_text("x.pdf") == "p1\n\np3"
    assert pdf_extract.read_pdf_text("x.pdf", max_pages=4) == "p1\n\np3\np4"


# ================================ screening (segmented) ================================
def lines(t, n, target=150.0, price=100.0, rating="buy"):
    return "\n".join(f"2026-10-14 | {t} | B{i} | {rating} | target {target} | cmp {price}" for i in range(n))


def inst(t, seg, sector="x", adv=50.0, pledge=0.0, de=0.3, f=None, z=None):
    return Instrument(t, seg, sector, adv, pledge, de, f, z)


def go(calls, universe, state=None, policy=None, **kw):
    state = state or PortfolioState(cash=100_000.0, positions={}, peak_value=100_000.0)
    policy = policy or RiskPolicy(max_orders_per_day=50, max_orders_per_week=50)
    return run_segmented(calls, state, policy, universe, T, **kw)


def test_segment_rules_and_limits_are_pinned():
    assert segmented.DEFAULT_RULES == {Segment.LARGE: SegmentRules(2, 5.0, None, False), Segment.MID: SegmentRules(2, 4.0, 5.0, False),
                                       Segment.SMALL: SegmentRules(1, 2.0, 1.0, True), Segment.MICRO: SegmentRules(1, 1.0, 0.25, True)}
    lim = Limits()
    assert (lim.small_micro_slice_pct, lim.sector_cap_pct, lim.max_pledge_pct, lim.max_debt_to_equity, lim.min_buy_ratio,
            lim.min_piotroski, lim.min_altman_z, lim.event_window_days) == (10.0, 30.0, 20.0, 1.5, 0.66, 4, 1.1, 3)
    assert {k: v for k, v in defaults(run_segmented).items() if k in ("max_age_days", "min_upside_pct")} == {"max_age_days": 30, "min_upside_pct": 20.0}


def small(t="S", **kw):
    return inst(t, Segment.SMALL, "metals", adv=kw.pop("adv", 5.0), **kw)


@pytest.mark.parametrize("kw,passes", [
    ({"adv": 1.0}, True), ({"adv": 0.99}, False),                 # liquidity floor for small caps is exactly Rs 1 crore
    ({"pledge": 20.0}, True), ({"pledge": 20.1}, False),          # promoter pledge limit
    ({"de": 1.5}, True), ({"de": 1.6}, False),                    # debt/equity limit
    ({"f": 4}, True), ({"f": 3}, False),                          # Piotroski minimum
    ({"z": 1.1}, True), ({"z": 1.09}, False),                     # Altman distress threshold
])
def test_each_screening_gate_is_exact_at_its_limit(kw, passes):
    r = go(lines("S", 1), {"S": small(**kw)})
    assert bool(r.proposals) is passes, r.research


def test_buy_ratio_gate_and_upside_gate_boundaries():
    half = lines("A", 1) + "\n" + "2026-10-14 | A | B9 | hold | target 120 | cmp 100"
    u = {"A": inst("A", Segment.LARGE)}
    assert go(half, u, limits=Limits(min_buy_ratio=0.5)).proposals                  # a 50% buy ratio meets a 50% minimum
    assert any("buy ratio 50%" in x.reasons[0] for x in go(half, u).research)       # but not the default 66%
    c = lines("A", 2, target=125.0)                                                   # exactly +25.0%
    assert go(c, u, min_upside_pct=25.0).proposals
    assert not go(c, u, min_upside_pct=25.5).proposals
    tiny = lines("A", 2, target=101.0)                                                # +1%: score 0.625, below 1 but still valid
    assert go(tiny, u, min_upside_pct=0.0).proposals


def test_weekly_order_limit_counts_one_per_proposal():
    u = {f"L{i}": inst(f"L{i}", Segment.LARGE, f"s{i}") for i in range(3)}
    r = go("\n".join(lines(t, 2) for t in u), u, policy=RiskPolicy(max_orders_per_day=50, max_orders_per_week=2))
    assert len(r.proposals) == 2 and len(r.queued) == 1


def test_amounts_are_rounded_to_paise_and_valid_for_tiny_books():
    st = PortfolioState(cash=12345.678, positions={}, peak_value=12345.678)
    r = go(lines("A", 2), {"A": inst("A", Segment.LARGE)}, state=st)
    assert r.proposals[0].amount == 617.28                                            # 5% of 12,345.678 = 617.2839
    prop = next(e for e in r.audit.events if e["stage"] == "proposal")
    assert prop["amount"] == 617.28
    tiny = go(lines("A", 2), {"A": inst("A", Segment.LARGE)}, state=PortfolioState(cash=10.0, peak_value=10.0))
    assert tiny.proposals[0].amount == pytest.approx(0.5)                             # a 50-paise order is still an order


def test_sector_slice_and_exposure_caps_are_exact_headrooms():
    uni = {"X1": inst("X1", Segment.LARGE, "s1"), "L": inst("L", Segment.LARGE, "s1"), "OTHER": inst("OTHER", Segment.LARGE, "s2")}
    held = PortfolioState(cash=80_000.0, positions={"X1": 20_000.0}, peak_value=100_000.0)
    r = go(lines("L", 2), uni, state=held, limits=Limits(sector_cap_pct=22.0))
    assert r.proposals[0].amount == pytest.approx(2000.0)                            # 22,000 - 20,000 already in the sector
    # a second proposal in the same sector must see the first one's full amount (no off-by-one in the running total)
    two = go(lines("L", 2) + "\n" + lines("X2", 2), {**uni, "X2": inst("X2", Segment.LARGE, "s1")},
             limits=Limits(sector_cap_pct=10.0))
    assert [round(c.amount, 6) for c in two.proposals] == [5000.0, 5000.0]
    sm = go(lines("S", 1), {"S": small()}, limits=Limits(small_micro_slice_pct=1.5))
    assert sm.proposals[0].amount == pytest.approx(1500.0)                            # slice headroom beats the 2% order cap
    held2 = PortfolioState(cash=94_000.0, positions={"X1": 6000.0}, peak_value=100_000.0)
    e = go(lines("OTHER", 2), uni, state=held2, max_invested_pct=10.0)
    assert e.proposals[0].amount == pytest.approx(4000.0)                             # 10,000 allowed - 6,000 invested


def test_positions_outside_the_universe_are_ignored_by_cap_arithmetic():
    held = PortfolioState(cash=99_000.0, positions={"UNKNOWN": 1000.0}, peak_value=100_000.0)
    r = go(lines("S", 1), {"S": small()}, state=held)                                  # must not raise KeyError
    assert r.proposals and r.proposals[0].amount == pytest.approx(2000.0)


def test_upside_below_minimum_is_logged_as_not_kept():
    r = go(lines("A", 2, target=110.0), {"A": inst("A", Segment.LARGE)})
    ev = next(e for e in r.audit.events if e["stage"] == "screen")
    assert ev["kept"] is False and not r.proposals


# ================================ workflow ================================
def crow(n=2, ratio=1.0, flags=()):
    return ConsensusRow("A", n, ratio, 150.0, 0.0, 50.0, tuple(flags), 10.0)


def test_rule_analyst_reasons_are_exact_and_benign_flags_do_not_veto():
    a = workflow.RuleAnalyst()
    assert a.review(crow(n=1)) == (False, "needs >= 2 brokers")
    assert a.review(crow(ratio=0.5)) == (False, "buy ratio 50% below 66%")
    assert a.review(crow(flags=("has-sell",))) == (False, "flags: has-sell")
    assert a.review(crow(flags=("high-dispersion", "has-sell")))[0] is False
    assert a.review(crow(flags=("single-broker",)))[0] is True          # a flag that is not a veto reason
    assert workflow.RuleAnalyst(min_buy_ratio=0.5).review(crow(ratio=0.5))[0] is True


def wf(calls, **kw):
    st = kw.pop("state", PortfolioState(cash=3800.0, positions={}, peak_value=3800.0))
    return workflow.run(calls, st, kw.pop("policy", RiskPolicy()), T, **kw)


def test_workflow_defaults_rounding_weekly_limit_and_audit_format():
    assert {k: v for k, v in defaults(workflow.run).items() if k in ("max_age_days", "min_upside_pct")} == {"max_age_days": 30, "min_upside_pct": 20.0}
    r = wf(lines("A", 2), state=PortfolioState(cash=12345.678, positions={}, peak_value=12345.678))
    assert r.proposals[0].amount == 617.28
    assert next(e for e in r.audit.events if e["stage"] == "proposal")["amount"] == 617.28
    many = "\n".join(lines(f"T{i}", 2) for i in range(3))
    r2 = wf(many, policy=RiskPolicy(max_orders_per_day=50, max_orders_per_week=2))
    assert len(r2.proposals) == 2
    log = workflow.AuditLog()
    log.add("x", b=1, a=2)
    assert log.to_jsonl() == '{"a": 2, "b": 1, "stage": "x"}'              # keys sorted for stable diffs


def test_workflow_tiny_upside_and_minimum_upside_boundary():
    assert wf(lines("A", 2, target=101.0), min_upside_pct=0.0).proposals
    exact = lines("A", 2, target=125.0)
    assert wf(exact, min_upside_pct=25.0).proposals and not wf(exact, min_upside_pct=25.5).proposals


# ================================ paper trading: exact money ================================
def path(prices):
    return [(D0 + timedelta(days=i), p) for i, p in enumerate(prices)]


def test_cost_model_amounts_are_exact():
    c = CostModel()
    assert (c.stt_pct, c.stamp_buy_pct, c.exchange_other_pct, c.brokerage_pct, c.dp_charge_sell, c.slippage_bps) == (0.1, 0.015, 0.011, 0.0, 15.0, 10.0)
    assert c.buy_fees(1000.0) == pytest.approx(1.26)                       # (0.1 + 0.015 + 0.011) % of 1,000
    assert c.sell_fees(1000.0) == pytest.approx(16.11)                     # (0.1 + 0.011) % plus Rs 15 depository charge
    b = CostModel(brokerage_pct=0.05)
    assert b.buy_fees(1000.0) == pytest.approx(1.76) and b.sell_fees(1000.0) == pytest.approx(16.61)


def test_entry_and_exit_fills_match_hand_arithmetic():
    L = PaperLedger(10_000.0)
    p = path([100, 100, 120, 120])
    buy = L.enter("A", 10_000.0, D0, p)
    assert buy.price == pytest.approx(100.1) and buy.qty == 99            # 100 * (1 + 10 bps); floor(10,000 / (100.1 * 1.00126))
    assert buy.fees == pytest.approx(99 * 100.1 * 0.00126)
    sell = L.exit("A", D0 + timedelta(days=1), p)
    assert sell.price == pytest.approx(119.88) and sell.qty == 99         # 120 * (1 - 10 bps)
    assert sell.fees == pytest.approx(99 * 119.88 * 0.00111 + 15.0)
    cash = 10_000 - 99 * 100.1 - 99 * 100.1 * 0.00126 + 99 * 119.88 - (99 * 119.88 * 0.00111 + 15.0)
    assert L.cash() == pytest.approx(cash)


def test_slippage_scale_is_basis_points_of_ten_thousand():
    L = PaperLedger(100_000.0, CostModel(0, 0, 0, 0, 0, 100))             # 100 bps = 1%
    p = path([100, 100, 100, 100])
    assert L.enter("A", 10_000.0, D0, p).price == pytest.approx(101.0)
    assert L.exit("A", D0 + timedelta(days=1), p).price == pytest.approx(99.0)


def test_share_and_cash_boundaries():
    p = path([100] * 4)
    assert PaperLedger(1000.0, NOCOST).enter("A", 1000.0, D0, p).qty == 10               # value equal to cash is allowed
    with pytest.raises(ValueError, match="insufficient"):
        PaperLedger(999.99, NOCOST).enter("A", 1000.0, D0, p)                             # a single paisa short is not
    assert PaperLedger(1000.0, NOCOST).enter("A", 100.0, D0, p).qty == 1                  # exactly one share
    with pytest.raises(ValueError, match="cannot buy one share"):
        PaperLedger(1000.0, NOCOST).enter("A", 99.99, D0, p)
    L = PaperLedger(1000.0, NOCOST)
    L.enter("A", 100.0, D0, p)
    assert L.exit("A", D0 + timedelta(days=1), p).qty == 1                                # a one-share position can be sold
    with pytest.raises(ValueError, match="no open paper position"):
        L.exit("A", D0 + timedelta(days=1), p)


def test_equity_curve_exact_including_fees_same_day_trades_and_missing_prices():
    cost = CostModel(0, 0, 0, 0, 15.0, 0)                                 # only the Rs 15 depository charge on sells
    L = PaperLedger(10_000.0, cost)
    a, b = path([100, 100, 110, 110, 120]), path([50, 50, 50, 50, 50])
    L.enter("A", 5000.0, D0, a)                                           # 50 shares at 100 on day 1
    L.enter("B", 1000.0, D0, b)                                           # 20 shares at 50 on day 1, same day
    L.exit("A", D0 + timedelta(days=3), a)                                # sells 50 at 120 on day 4, fee 15
    curve = dict(equity_curve(L, {"A": a, "B": b}, D0 + timedelta(days=1), D0 + timedelta(days=4)))
    cash0 = 10_000 - 5000 - 1000
    assert curve[D0 + timedelta(days=1)] == pytest.approx(cash0 + 50 * 100 + 20 * 50)    # both same-day fills applied
    assert curve[D0 + timedelta(days=2)] == pytest.approx(cash0 + 50 * 110 + 20 * 50)
    assert curve[D0 + timedelta(days=4)] == pytest.approx(cash0 + 50 * 120 - 15 + 20 * 50)  # the sale and its fee, on its own day
    with pytest.raises(ValueError, match="no price history for traded"):
        equity_curve(L, {"A": a}, D0, D0 + timedelta(days=4))                              # B was traded but has no prices


def test_report_needs_three_days_and_matches_hand_computed_returns():
    L = PaperLedger(10_000.0)
    stock = path([100] * 6)
    idx = path([100, 100, 100, 100, 100, 110])
    L.enter("A", 9000.0, D0, stock)
    short = {"A": stock[:3], "IDX": idx[:3]}                              # fills day 1 -> only 2 curve points
    with pytest.raises(ValueError, match="at least 3"):
        report(L, short, "IDX")
    ok = report(L, {"A": stock[:4], "IDX": idx[:4]}, "IDX")               # exactly 3 curve points is enough
    assert ok.days == 2
    r = report(L, {"A": stock, "IDX": idx}, "IDX")
    # benchmark: bought on day 1 at 100.1 with the same costs: floor(10,000 / (100.1 * 1.00126)) = 99 shares
    b_cash = 10_000 - 99 * 100.1 - 99 * 100.1 * 0.00126
    first = b_cash + 99 * 100                                              # curve starts on the buy day, valued at its close
    assert r.benchmark.total_return_pct == pytest.approx(((b_cash + 99 * 110) / first - 1) * 100)
    # portfolio: floor(9,000 / (100.1 * 1.00126)) = 89 shares of a flat stock
    p_cash = 10_000 - 89 * 100.1 - 89 * 100.1 * 0.00126
    assert r.final_value == pytest.approx(p_cash + 89 * 100) and r.n_trades == 1
    assert r.portfolio.total_return_pct == pytest.approx(0.0, abs=1e-9)    # flat stock; returns are measured from the fill-day equity
    assert r.excess_return_pct == pytest.approx(r.portfolio.total_return_pct - r.benchmark.total_return_pct)


def test_report_uses_the_next_benchmark_price_when_the_start_day_is_missing():
    L = PaperLedger(10_000.0, NOCOST)
    stock = path([100] * 5)
    L.enter("A", 5000.0, D0, stock)                                       # fills on day 1
    idx = [(D0 + timedelta(days=i), 100.0 + i) for i in (0, 2, 3, 4)]      # no benchmark price on day 1
    r = report(L, {"A": stock, "IDX": idx}, "IDX")
    assert r.benchmark.total_return_pct == pytest.approx(1.96, abs=1e-6)   # 98 whole shares at the day-2 price 102, 4 left over: (4 + 98*104) / 10,000 - 1


def bulk_ledger(n_buys, n_points, final=100.0):
    L = PaperLedger(100_000.0, NOCOST)
    prices = {}
    for i in range(n_buys):
        p = [100.0] * (n_points - 1) + [final]
        prices[f"S{i}"] = path(p)
        L.enter(f"S{i}", 3000.0, D0, prices[f"S{i}"])
    prices["IDX"] = path([1000.0] * n_points)
    return L, prices


def test_verdict_boundaries_sixty_days_twenty_buys_and_excess_sign():
    assert (papertrade.MIN_DAYS, papertrade.MIN_TRADES) == (60, 20)
    L, px = bulk_ledger(20, 62)                                            # 62 prices -> first fill day 1 -> exactly 60 daily returns
    r = report(L, px, "IDX")
    assert r.days == 60 and "too early" not in r.verdict and r.verdict.startswith("behind or level")   # excess exactly 0 is not 'ahead'
    assert "too early" in report(*bulk_ledger(19, 62), "IDX").verdict      # 19 buys
    assert "too early" in report(*bulk_ledger(20, 61), "IDX").verdict      # 59 days
    L2, px2 = bulk_ledger(20, 62, final=100.5)
    ahead = report(L2, px2, "IDX")
    assert ahead.excess_return_pct == pytest.approx(0.3) and ahead.verdict.startswith("ahead of benchmark")
    assert ahead.final_value == pytest.approx(100_300.0)                   # the last day's value, not the day before


def test_post_mortem_classification_and_statistics_are_exact():
    mk = lambda t, ret, pnl: RoundTrip(t, D0, D0 + timedelta(days=5), ret, pnl, 5)  # noqa: E731
    trips = [mk("A", 10.0, 100.0), mk("B", 30.0, 300.0), mk("C", -20.0, -200.0), mk("D", 0.0, 0.0)]
    pm = post_mortem(trips)
    assert pm.n == 4 and pm.win_rate == 0.5                                # a 0% trade is not a win
    assert pm.avg_win_pct == pytest.approx(20.0) and pm.avg_loss_pct == pytest.approx(-10.0)
    assert pm.expectancy_pct == pytest.approx(5.0) and pm.profit_factor == pytest.approx(2.0) and pm.worst.ticker == "C"
    small_loss = post_mortem([mk("A", 5.0, 100.0), mk("B", -0.1, -0.5)])
    assert small_loss.profit_factor == pytest.approx(200.0)               # a tiny loss still defines a profit factor


def test_round_trip_return_includes_every_cost():
    L = PaperLedger(10_000.0)
    p = path([100, 100, 120, 120])
    L.enter("A", 10_000.0, D0, p)
    L.exit("A", D0 + timedelta(days=1), p)
    (trip,) = papertrade.round_trips(L)
    cost = 99 * 100.1 + 99 * 100.1 * 0.00126
    proceeds = 99 * 119.88 - (99 * 119.88 * 0.00111 + 15.0)
    assert trip.return_pct == pytest.approx((proceeds / cost - 1) * 100) and trip.net_pnl == pytest.approx(proceeds - cost)
    assert trip.entry_day == D0 + timedelta(days=1) and trip.exit_day == D0 + timedelta(days=2) and trip.days_held == 1


# ================================ universe ================================
def test_average_traded_value_window_scaling_and_precedence():
    old = [Bar("X", D0 + timedelta(days=i), 100, 100, 100, 100.0, 100, 9_000_000.0) for i in range(5)]
    recent = [Bar("X", D0 + timedelta(days=5 + i), 100, 100, 100, 100.0, 100, 2_000_000.0) for i in range(20)]
    assert defaults(universe.adv_from_bars) == {"days": 20} and defaults(universe.avg_traded_value) == {"days": 20}
    assert universe.adv_from_bars(old + recent)["X"] == pytest.approx(20.0, rel=1e-12)          # last 20 days only; Rs crore = /1e7
    assert universe.avg_traded_value(old + recent)["X"] == pytest.approx(200_000_000.0, rel=1e-12)
    given = {"X": Instrument("X", Segment.LARGE, "s", adv_value_cr=40.0)}
    assert universe.with_adv(given, {"X": 99.0})["X"].adv_value_cr == 40.0                      # a supplied value is never overwritten
    blank = {"X": Instrument("X", Segment.LARGE, "s")}
    assert universe.with_adv(blank, {"X": 99.0})["X"].adv_value_cr == 99.0
    assert universe.with_adv(blank, {"Y": 99.0})["X"].adv_value_cr is None
