"""Boundary, default-value and exact-number tests, written to kill mutants that earlier tests let survive.

Mutation testing (scripts/mutate.py) showed that the first test suite ran this code without noticing small breaks:
a `<` that became `<=`, a threshold nudged by one, a multiplication swapped for a division. These tests pin the
behavior exactly at limits and the numbers exactly, so such a change now fails a test.
"""
import dataclasses
import inspect
from datetime import date, timedelta

import pytest

from quant_agents import (consensus, corporate, crosscheck, discipline, events, fundamentals, ladder, liquidity, posture,
                          quality, risk, tax, thesis, valuation)
from quant_agents.ingest import parse_calls
from quant_agents.nse import Bar

T = date(2026, 10, 16)


def bar(close, prev, day=T, basis="exchange", isin="I", vol=1.0, sym="S", ex="NSE"):
    return Bar(sym, day, close, close, close, close, prev, vol, isin, ex, "INR", basis)


# ================================ policies are immutable ================================
@pytest.mark.parametrize("cls", [
    risk.RiskPolicy, discipline.Closed, discipline.DisciplinePolicy, discipline.Decision, posture.Posture, tax.TaxRules,
    tax.TaxEstimate, thesis.Thesis, thesis.Invalidation, thesis.ThesisStatus, quality.PriceCheck, quality.SourceAudit,
    crosscheck.Mismatch, crosscheck.CrossCheck, events.Event, corporate.Action, corporate.Jump, fundamentals.Statements,
    fundamentals.Piotroski, fundamentals.Altman, fundamentals.Beneish, fundamentals.QualityReport, valuation.ValuationView,
])
def test_value_objects_are_frozen(cls):
    """A limit or result that can be edited after creation can be silently loosened. These must stay frozen."""
    assert cls.__dataclass_params__.frozen, f"{cls.__name__} must be frozen"


# ================================ defaults are part of the contract ================================
def defaults(fn):
    return {k: v.default for k, v in inspect.signature(fn).parameters.items() if v.default is not inspect.Parameter.empty}


def test_documented_defaults_are_pinned():
    p = risk.RiskPolicy()
    assert (p.max_position_pct, p.max_order_pct, p.min_cash_pct, p.max_orders_per_day, p.max_orders_per_week,
            p.halt_drawdown_pct, p.target_daily_vol_pct) == (15.0, 5.0, 10.0, 2, 5, 15.0, 1.0)
    s = risk.PortfolioState(cash=1.0)
    assert (s.peak_value, s.orders_today, s.orders_this_week, s.positions) == (0.0, 0, 0, {})
    d = discipline.DisciplinePolicy()
    assert (d.max_losing_streak, d.cooldown_days, d.weekly_loss_limit_pct, d.monthly_loss_limit_pct,
            d.cool_off_days_after_loss) == (3, 2, 5.0, 10.0, 1)
    t = tax.TaxRules()
    assert (t.stcg_rate, t.ltcg_rate, t.ltcg_exemption, t.ltcg_holding_days, t.cess) == (0.20, 0.125, 125_000, 365, 0.04)
    assert defaults(posture.exposure_ceiling) == {"base": 60.0, "floor": 10.0, "top": 90.0}
    assert defaults(risk.kelly_fraction) == {"fraction": 0.25, "cap": 0.10}
    assert defaults(ladder.entry_ladder) == {"tranches": 3, "step_pct": 4.0, "tick": 0.05}
    assert defaults(quality.check_price) == {"max_divergence_pct": 3.0, "max_age_days": 3}
    assert defaults(quality.implied_upside_ok) == {"max_pct": 120.0}
    assert defaults(crosscheck.cross_check) == {"close_tol_pct": 1.0, "return_tol_pp": 1.0}
    assert defaults(crosscheck.agreed_close) == {"tol_pct": 1.0}
    assert defaults(events.upcoming) == {"window_days": 3}
    assert defaults(liquidity.amihud_per_crore) == {"days": 60}
    assert defaults(corporate.unadjusted_jumps)["threshold_pct"] == 25.0
    assert thesis.Thesis(ticker="A", opened=T, statement="s", entry_price=1, target_price=2, review_by=T).stop_pct == 20.0
    assert consensus.IMPLAUSIBLE_UPSIDE_PCT == 120.0 and consensus.HIGH_DISPERSION == 0.5
    for fn in (valuation.dcf_per_share, valuation.reverse_dcf_growth, valuation.sensitivity, valuation.valuation_view):
        assert {k: v for k, v in defaults(fn).items() if k in ("net_debt", "shares")} == {"net_debt": 0.0, "shares": 1.0}
    assert {k: v for k, v in defaults(valuation.reverse_dcf_growth).items() if k in ("lo", "hi")} == {"lo": -0.5, "hi": 1.0}


# ================================ risk ================================
def clean_state(cash=9000.0, held=None, peak=10_000.0):
    return risk.PortfolioState(cash=cash, positions={"AAA": 1000.0} if held is None else held, peak_value=peak)


WIDE = risk.RiskPolicy(max_order_pct=100.0, max_position_pct=100.0)


def test_check_order_is_exact_at_each_limit_and_blocks_tiny_overruns():
    s = clean_state()                                             # value 10,000
    pol = risk.RiskPolicy()
    assert risk.check_order("BBB", 500.0, s, pol) == []           # exactly 5% of the portfolio
    assert any("order exceeds" in v for v in risk.check_order("BBB", 500.5, s, pol))  # 50 paise over must fail
    only_pos = risk.RiskPolicy(max_order_pct=100.0)               # position cap 15% -> AAA may hold 1,500
    assert risk.check_order("AAA", 500.0, s, only_pos) == []
    assert any("position would exceed" in v for v in risk.check_order("AAA", 500.5, s, only_pos))
    assert risk.check_order("BBB", 8000.0, s, WIDE) == []         # leaves exactly the 10% cash reserve
    assert any("cash would fall" in v for v in risk.check_order("BBB", 8000.5, s, WIDE))


def test_check_order_drawdown_halt_and_nonpositive_amounts():
    pol = risk.RiskPolicy(max_order_pct=100.0, max_position_pct=100.0, halt_drawdown_pct=25.0)
    at_limit = risk.PortfolioState(cash=7500.0, peak_value=10_000.0)    # exactly 25.0% below peak (0.75 is exact in binary)
    assert at_limit.drawdown_pct == 25.0
    assert risk.check_order("A", 100.0, at_limit, pol) == []            # at the limit, trading is still allowed
    beyond = risk.PortfolioState(cash=7499.0, peak_value=10_000.0)
    assert any("halted" in v for v in risk.check_order("A", 100.0, beyond, pol))
    for amt in (0.0, -5.0):
        assert "non-positive amount" in risk.check_order("A", amt, clean_state(), WIDE)


def test_drawdown_pct_exact_and_safe_when_everything_is_zero():
    assert risk.PortfolioState(cash=90.0, peak_value=100.0).drawdown_pct == pytest.approx(10.0)
    assert risk.PortfolioState(cash=120.0, peak_value=100.0).drawdown_pct == 0.0   # above a stale peak
    assert risk.PortfolioState(cash=0.0, peak_value=0.0).drawdown_pct == 0.0        # must not divide by zero


def test_size_position_volatility_cap_formula_and_ignored_when_not_positive():
    s = risk.PortfolioState(cash=10_000.0, peak_value=10_000.0)
    assert risk.size_position("A", s, risk.RiskPolicy(), daily_vol_pct=50.0) == pytest.approx(200.0)  # value*1%/50
    base = risk.size_position("A", s, risk.RiskPolicy())
    assert base == pytest.approx(500.0)
    for v in (0.0, -3.0, None):
        assert risk.size_position("A", s, risk.RiskPolicy(), daily_vol_pct=v) == pytest.approx(500.0)
    # the position cap subtracts what is already held and the reserve is subtracted from cash
    held = risk.PortfolioState(cash=5000.0, positions={"A": 1400.0}, peak_value=6400.0)
    assert risk.size_position("A", held, risk.RiskPolicy()) == 0.0                 # 15% of 6,400 is 960: already over, never negative
    assert risk.size_position("A", held, risk.RiskPolicy(max_position_pct=40.0)) == pytest.approx(320.0)  # order cap 5% binds
    assert risk.size_position("A", held, risk.RiskPolicy(max_position_pct=25.0, max_order_pct=50.0)) == pytest.approx(200.0)  # 1,600 - 1,400


def test_risk_based_qty_and_kelly_exact_numbers_and_guards():
    assert risk.risk_based_qty(100_000, 2.0, 100, 90) == 200 and risk.risk_based_qty(100_000, 0.5, 100, 90) == 50
    for stop in (100.0, 0.0, -1.0, 101.0):
        with pytest.raises(ValueError):
            risk.risk_based_qty(100_000, 1.0, 100, stop)
    assert risk.kelly_fraction(0.55, 1.5) == pytest.approx(0.0625)          # f* = 0.25, quarter Kelly
    assert risk.kelly_fraction(0.55, 1.5, fraction=1.0, cap=1.0) == pytest.approx(0.25)
    assert risk.kelly_fraction(0.9, 5.0, fraction=1.0, cap=0.2) == 0.2       # cap binds
    for p, b in ((0.0, 1.0), (1.0, 1.0), (0.5, 0.0), (0.5, -1.0)):
        with pytest.raises(ValueError):
            risk.kelly_fraction(p, b)


# ================================ consensus ================================
def row(*lines):
    return {r.ticker: r for r in consensus.build_consensus(parse_calls("\n".join(lines)).calls)}


def test_implausible_upside_threshold_is_120_percent():
    assert "implausible-upside" not in row("2026-10-12 | A | B1 | buy | target 219 | cmp 100")["A"].flags      # +119%
    assert "implausible-upside" in row("2026-10-12 | A | B1 | buy | target 220.5 | cmp 100")["A"].flags         # +120.5%


def test_dispersion_threshold_is_exactly_half():
    ok = row("2026-10-12 | A | B1 | buy | target 125 | cmp 50", "2026-10-12 | A | B2 | buy | target 75 | cmp 50")["A"]
    assert ok.target_dispersion == 0.5 and "high-dispersion" not in ok.flags
    bad = row("2026-10-12 | A | B1 | buy | target 126 | cmp 50", "2026-10-12 | A | B2 | buy | target 74 | cmp 50")["A"]
    assert "high-dispersion" in bad.flags


def test_score_caps_upside_at_80_and_breadth_at_four_brokers():
    a = row("2026-10-12 | A | B1 | buy | target 180 | cmp 100")["A"]      # +80%
    b = row("2026-10-12 | A | B1 | buy | target 200 | cmp 100")["A"]      # +100%, capped to the same
    assert a.score == b.score == 50.0                                      # 80 * (0.5*0.25 + 0.5*1.0)
    four = row(*[f"2026-10-12 | A | B{i} | buy | target 150 | cmp 100" for i in range(4)])["A"].score
    five = row(*[f"2026-10-12 | A | B{i} | buy | target 150 | cmp 100" for i in range(5)])["A"].score
    three = row(*[f"2026-10-12 | A | B{i} | buy | target 150 | cmp 100" for i in range(3)])["A"].score
    assert four == five == 50.0 and three < four


def test_rating_target_mismatch_at_equality():
    assert "rating-target-mismatch" in row("2026-10-12 | A | B1 | buy | target 100 | cmp 100")["A"].flags
    assert "rating-target-mismatch" in row("2026-10-12 | A | B1 | sell | target 100 | cmp 100")["A"].flags
    assert "rating-target-mismatch" not in row("2026-10-12 | A | B1 | buy | target 101 | cmp 100")["A"].flags


# ================================ tax ================================
def test_holding_period_boundary_is_strictly_more_than_365_days():
    d = date(2025, 1, 1)
    assert tax.estimate_tax([(d, d + timedelta(days=365), 1000.0)]).stcg == 1000.0
    assert tax.estimate_tax([(d, d + timedelta(days=366), 1000.0)]).ltcg == 1000.0
    assert tax.estimate_tax([(d, d, 50.0)]).stcg == 50.0                  # same-day round trip is valid
    with pytest.raises(ValueError):
        tax.estimate_tax([(d + timedelta(days=1), d, 1.0)])


def test_tax_exact_amounts_with_one_kind_of_gain_only():
    d = date(2025, 1, 1)
    only_long = tax.estimate_tax([(d, d + timedelta(days=400), 225_000.0)])
    assert only_long.stcg == 0.0 and only_long.tax == pytest.approx(100_000 * 0.125 * 1.04)
    only_short = tax.estimate_tax([(d, d + timedelta(days=30), 100_000.0)])
    assert only_short.ltcg == 0.0 and only_short.taxable_ltcg == 0.0 and only_short.tax == pytest.approx(100_000 * 0.20 * 1.04)
    assert tax.estimate_tax([]).tax == 0.0


# ================================ quality gates ================================
def test_price_check_thresholds_are_exact():
    ok = quality.check_price({"a": (100.0, T), "b": (102.9, T)}, T)                 # 2.9% apart: inside the 3% default
    assert ok.ok is True and ok.divergence_pct == pytest.approx(2.9)
    assert not quality.check_price({"a": (100.0, T), "b": (103.1, T)}, T).ok        # 3.1%: outside
    assert not quality.check_price({"a": (100.0, T), "b": (103.5, T)}, T).ok        # also outside a 4% default would not be
    assert quality.check_price({"a": (100.0, T - timedelta(days=3)), "b": (100.0, T)}, T).ok
    assert not quality.check_price({"a": (100.0, T - timedelta(days=4)), "b": (100.0, T)}, T).ok
    bad = quality.check_price({"a": (0.0, T), "b": (100.0, T)}, T)
    assert any("non-positive" in r for r in bad.reasons)
    assert quality.check_price({"a": (50.0, T), "b": (100.0, T)}, T).divergence_pct == pytest.approx(100.0)


def test_implied_upside_ok_boundaries():
    assert quality.implied_upside_ok(219.0, 100.0) and not quality.implied_upside_ok(221.0, 100.0)
    assert not quality.implied_upside_ok(0.0, 100.0) and not quality.implied_upside_ok(100.0, 0.0)
    assert quality.implied_upside_ok(150.0, 100.0, max_pct=50.0) and not quality.implied_upside_ok(151.0, 100.0, max_pct=50.0)


# ================================ discipline ================================
def losses(*pairs):
    return [discipline.Closed(T - timedelta(days=d), p) for d, p in pairs]


BIG = 1_000_000.0  # portfolio value large enough that tiny losses never hit the percentage limits


def test_losing_streak_needs_three_and_cooldown_ends_after_two_days():
    two = losses((3, -1), (1, -1))
    assert discipline.circuit_breaker(two, BIG, T).allowed                         # 2 losses: no streak
    three = losses((5, -1), (3, -1), (1, -1))
    r = discipline.circuit_breaker(three, BIG, T)
    assert not r.allowed and "losing streak of 3" in r.reasons[0] and str(T + timedelta(days=1)) in r.reasons[0]
    assert discipline.circuit_breaker(losses((6, -1), (4, -1), (2, -1)), BIG, T).allowed   # exactly cooldown_days later
    assert discipline.circuit_breaker(losses((5, -1), (3, 0), (1, -1)), BIG, T).allowed    # a zero is not a loss; breaks the streak


def test_weekly_and_monthly_windows_and_exact_limits():
    pv = 100_000.0
    assert not discipline.circuit_breaker(losses((6, -5000.0)), pv, T).allowed                 # 6 days ago, exactly 5%
    assert discipline.circuit_breaker(losses((7, -5000.0)), pv, T).allowed                     # 7 days ago is outside the week
    assert discipline.circuit_breaker(losses((6, -4990.0)), pv, T).allowed                     # just under
    assert not discipline.circuit_breaker(losses((0, -5000.0)), pv, T).allowed                 # today counts
    assert discipline.circuit_breaker([discipline.Closed(T + timedelta(days=1), -50_000.0)], pv, T).allowed  # future is ignored
    assert not discipline.circuit_breaker(losses((29, -10_000.0)), pv, T).allowed              # inside 30 days, exactly 10%
    assert discipline.circuit_breaker(losses((30, -10_000.0)), pv, T).allowed                  # outside
    msg = discipline.circuit_breaker(losses((2, -6000.0)), pv, T).reasons[0]
    assert "weekly net loss 6.0% reached limit 5%" in msg
    assert discipline.circuit_breaker(losses((2, 8.0)), 10.0, T).allowed                       # a profit never blocks, even on a tiny book
    assert discipline.circuit_breaker(losses((2, -5000.0)), 0.0, T).allowed                    # no divide by zero when value is 0


def good_thesis():
    return thesis.Thesis("A", T, "s", 100, 150, T + timedelta(days=30), invalidations=(thesis.Invalidation("m", "<", 1),), bear_case="b")


def test_cooling_off_after_a_loss_is_today_and_yesterday_only():
    for ago, allowed in ((0, False), (1, False), (2, True)):
        d = discipline.pre_trade_gate("A", good_thesis(), losses((ago, -1)), BIG, T)
        assert d.allowed is allowed, f"loss {ago} days ago"
    assert discipline.pre_trade_gate("A", good_thesis(), losses((0, 5)), BIG, T).allowed     # a gain does not trigger it


# ================================ posture ================================
@pytest.mark.parametrize("tone,breadth,expected", [
    (0.25, 50, 80.0), (0.2499, 50, 60.0), (-0.25, 50, 30.0), (-0.2499, 50, 60.0),
    (0.0, 29.99, 40.0), (0.0, 30.0, 60.0), (0.0, 60.0, 60.0), (0.0, 60.01, 70.0),
    (0.5, 70, 90.0), (-0.6, 20, 10.0), (None, None, 60.0),
])
def test_posture_adjustments_and_boundaries_are_exact(tone, breadth, expected):
    assert posture.exposure_ceiling(tone, breadth).ceiling_pct == expected


def test_posture_clamps_and_entries_flag_boundary():
    assert posture.exposure_ceiling(0.5, 70, top=85.0).ceiling_pct == 85.0
    assert posture.exposure_ceiling(-0.6, 20, floor=15.0).ceiling_pct == 15.0
    assert posture.exposure_ceiling(None, None, base=20.0).entries_allowed is False
    assert posture.exposure_ceiling(None, None, base=21.0).entries_allowed is True


# ================================ liquidity ================================
def liq_bars(n, vol=1_000_000.0, close=101.0, prev=100.0):
    d0 = date(2026, 1, 1)
    return [Bar("X", d0 + timedelta(days=i), close, close, close, close, prev, vol) for i in range(n)]


def test_amihud_exact_value_and_thresholds():
    expected = (101.0 / 100.0 - 1.0) / (101.0 * 1_000_000.0 / 1e7)          # |return| / traded value in Rs crore
    assert liquidity.amihud_per_crore(liq_bars(25)) == pytest.approx(expected)
    assert liquidity.amihud_per_crore(liq_bars(19)) is None and liquidity.amihud_per_crore(liq_bars(20)) == pytest.approx(expected)
    mixed = liq_bars(20) + [dataclasses.replace(b, volume=0.0) for b in liq_bars(5)]
    assert liquidity.amihud_per_crore(mixed) == pytest.approx(expected)       # zero-volume days are skipped, not divided by
    sparse = liq_bars(15) + [dataclasses.replace(b, volume=0.0) for b in liq_bars(10)]
    assert liquidity.amihud_per_crore(sparse) is None                         # only 15 usable days


def test_amihud_uses_only_the_last_60_sessions():
    old = [dataclasses.replace(b, volume=1000.0) for b in liq_bars(1)]       # a very illiquid first day
    bars = [dataclasses.replace(old[0], day=date(2025, 12, 31))] + liq_bars(60)
    expected = (101.0 / 100.0 - 1.0) / (101.0 * 1_000_000.0 / 1e7)
    assert liquidity.amihud_per_crore(bars) == pytest.approx(expected)       # the old day falls outside the window


def test_sqrt_impact_edges():
    assert liquidity.sqrt_impact_pct(2.0, 0.0, 100.0) == 0.0                 # a zero order is valid
    assert liquidity.sqrt_impact_pct(2.0, 0.5, 0.5) == pytest.approx(2.0)    # small ADV is valid
    for order, adv in ((-1.0, 100.0), (1.0, 0.0), (1.0, -5.0)):
        with pytest.raises(ValueError):
            liquidity.sqrt_impact_pct(2.0, order, adv)


# ================================ thesis ================================
def test_thesis_review_triggers_are_inclusive_at_the_boundary():
    t = thesis.Thesis("A", T, "s", 100, 1000, T + timedelta(days=10), stop_pct=25.0,
                      invalidations=(thesis.Invalidation("m", "<", 1),), bear_case="b")
    facts = {"m": 5}
    assert "below entry" in thesis.evaluate(t, facts, 75.0, T).reasons[0]            # exactly 25% below
    assert thesis.evaluate(t, facts, 75.5, T).status == "intact"
    assert thesis.evaluate(t, facts, 100.0, T + timedelta(days=10)).status == "review"  # on the review date
    assert thesis.evaluate(t, facts, 100.0, T + timedelta(days=9)).status == "intact"
    wide = dataclasses.replace(t, stop_pct=50.25)
    assert thesis.evaluate(wide, facts, 50.0, T).status == "intact"                  # a 50.0% fall is under a 50.25% trigger


# ================================ valuation ================================
def test_dcf_hand_computed_value():
    # FCF 110 and 121 discounted at 10%, terminal 121*1.05/0.05, less net debt 300, over 10 shares
    assert valuation.dcf_per_share(100, 0.10, 2, 0.05, 0.10, 300, 10) == pytest.approx(200.0)


def test_dcf_input_guards_are_exact():
    with pytest.raises(ValueError):
        valuation.dcf_per_share(100, 0.1, 5, 0.10, 0.10)                              # wacc == terminal growth
    with pytest.raises(ValueError):
        valuation.dcf_per_share(100, 0.1, 0, 0.02, 0.10)
    with pytest.raises(ValueError):
        valuation.dcf_per_share(100, 0.1, 5, 0.02, 0.10, shares=0.0)
    assert valuation.dcf_per_share(100, 0.1, 1, 0.02, 0.10, shares=0.5) > 0          # one year and a fractional share are valid
    assert valuation.dcf_per_share(100, 0.0, 1, 0.0, 0.10) == pytest.approx(1000.0)  # defaults: no net debt, one share


def test_reverse_dcf_bounds_and_boundary_prices():
    fcf, yrs, tg, w = 100.0, 5, 0.03, 0.10
    for g in (-0.45, 0.9):
        assert valuation.reverse_dcf_growth(valuation.dcf_per_share(fcf, g, yrs, tg, w), fcf, yrs, tg, w) == pytest.approx(g, abs=1e-6)
    for g in (-0.55, 1.1):                                                            # outside the default [-50%, +100%]
        with pytest.raises(ValueError, match="no growth"):
            valuation.reverse_dcf_growth(valuation.dcf_per_share(fcf, g, yrs, tg, w), fcf, yrs, tg, w)
    at_lo = valuation.dcf_per_share(fcf, -0.5, yrs, tg, w)
    at_hi = valuation.dcf_per_share(fcf, 1.0, yrs, tg, w)
    assert valuation.reverse_dcf_growth(at_lo, fcf, yrs, tg, w) == pytest.approx(-0.5, abs=1e-6)   # a price exactly at a bound is solvable
    assert valuation.reverse_dcf_growth(at_hi, fcf, yrs, tg, w) == pytest.approx(1.0, abs=1e-6)
    assert valuation.reverse_dcf_growth(150.0, 100.0, 5, 0.03, 0.10, net_debt=0.0, shares=1.0) == \
        valuation.reverse_dcf_growth(150.0, 100.0, 5, 0.03, 0.10)


def test_margin_graham_and_sensitivity_edges():
    assert valuation.margin_of_safety(0.25, 0.5) == pytest.approx(0.5)                # a small positive intrinsic value is valid
    with pytest.raises(ValueError):
        valuation.margin_of_safety(1.0, 0.0)
    assert valuation.graham_number(-1.0, -100.0) == 0.0 and valuation.graham_number(10.0, 0.0) == 0.0
    assert valuation.graham_number(4.0, 100.0) == pytest.approx((22.5 * 400.0) ** 0.5)
    assert valuation.sensitivity(100, 0.05, 3, [0.05], [0.05]) == {}                  # wacc == growth cells are omitted, not raised
    assert set(valuation.sensitivity(100, 0.05, 3, [0.08, 0.10], [0.02])) == {(0.08, 0.02), (0.10, 0.02)}


def test_valuation_view_numbers_and_note_branches():
    v = valuation.valuation_view(price=100.0, fcf0=100.0, growth=0.0, years=1, terminal_growth=0.0, wacc=0.10)
    assert v.intrinsic_base == pytest.approx(1000.0) and v.margin_of_safety_pct == pytest.approx(90.0)
    g = 0.12
    price = valuation.dcf_per_share(100.0, g, 5, 0.03, 0.10)
    more = valuation.valuation_view(price, 100.0, 0.1195, 5, 0.03, 0.10)               # price assumes 12.0% vs base 11.95%
    assert more.implied_growth_pct == pytest.approx(12.0, abs=1e-4) and "more growth" in more.note
    less = valuation.valuation_view(price, 100.0, 0.50, 5, 0.03, 0.10)                 # base case far above what is priced in
    assert "less growth" in less.note
    unsolvable = valuation.valuation_view(1e12, 100.0, 0.05, 5, 0.03, 0.10)
    assert unsolvable.implied_growth_pct is None and "not solvable" in unsolvable.note
    broke = valuation.valuation_view(10.0, 100.0, 0.0, 1, 0.0, 0.10, net_debt=1e9)     # intrinsic value below zero
    assert broke.margin_of_safety_pct != broke.margin_of_safety_pct                    # NaN


# ================================ corporate actions ================================
def test_unadjusted_jump_default_threshold_and_actions_guard():
    raw = lambda close: [bar(close, 100.0, basis="raw_previous_close")]                # noqa: E731
    assert corporate.unadjusted_jumps(raw(124.0)) == [] and len(corporate.unadjusted_jumps(raw(125.5))) == 1
    fixed = corporate.apply_actions([bar(50.0, 100.0, basis="raw_previous_close", sym="S")], [corporate.Action("S", T, 0.5)])
    assert fixed[0].prev_close == pytest.approx(200.0)                                  # a 2:1 reverse split has factor 0.5 and is valid
    with pytest.raises(ValueError):
        corporate.apply_actions([], [corporate.Action("S", T, 0.0)])


# ================================ cross-check ================================
def test_crosscheck_default_tolerances_and_agreed_close():
    n, b = [bar(101.5, 100.0)], [bar(100.0, 100.0, ex="BSE")]                          # 1.5% apart
    assert crosscheck.cross_check(n, b).mismatches and not crosscheck.cross_check(n, b, close_tol_pct=2.0, return_tol_pp=2.0).mismatches
    assert crosscheck.agreed_close(bar(100.0, 100.0), bar(101.5, 100.0, ex="BSE")) is None
    assert crosscheck.agreed_close(bar(100.0, 100.0), bar(100.5, 100.0, ex="BSE")) == pytest.approx(100.25)


def test_crosscheck_return_difference_uses_percentage_points():
    ret_a = 0.02
    ret_b = ret_a - 0.00995                                                             # returns differ by 0.995 percentage points
    a = bar(102.0, 102.0 / (1 + ret_a))
    b = bar(102.0, 102.0 / (1 + ret_b), ex="BSE")                                       # same close, so only the return check can fire
    assert crosscheck.cross_check([a], [b]).mismatches == ()
    assert crosscheck.cross_check([a], [dataclasses.replace(b, prev_close=102.0 / (1 + ret_a - 0.0101))]).mismatches


def test_crosscheck_single_match_is_clean_and_mismatches_are_ordered():
    assert crosscheck.cross_check([bar(100.0, 99.0)], [bar(100.0, 99.0, ex="BSE")]).clean is True
    n = [bar(150.0, 100.0, isin="B", day=T), bar(150.0, 100.0, isin="A", day=T), bar(150.0, 100.0, isin="A", day=T - timedelta(days=1))]
    b = [dataclasses.replace(x, close=100.0, exchange="BSE") for x in n]
    got = [(m.day, m.isin) for m in crosscheck.cross_check(n, b).mismatches]
    assert got == sorted(got) and len(got) == 3


# ================================ events ================================
def test_event_window_includes_today_and_day_three_but_not_past_or_day_four():
    mk = lambda days: events.Event(T + timedelta(days=days), "results", "A")            # noqa: E731
    got = [e.day - T for e in events.upcoming("A", T, [mk(-1), mk(0), mk(3), mk(4)])]
    assert got == [timedelta(0), timedelta(days=3)]


# ================================ fundamentals ================================
def stmt(**kw):
    base = {"sales": 1000, "cogs": 600, "sga": 100, "depreciation": 50, "ebit": 150, "net_income": 100, "cfo": 120, "receivables": 100,
                "current_assets": 400, "current_liabilities": 200, "ppe": 500, "total_assets": 1000, "total_liabilities": 500,
                "long_term_debt": 200, "retained_earnings": 300, "equity": 500, "shares_outstanding": 100}
    base.update(kw)
    return fundamentals.Statements(**base)


def test_piotroski_signals_are_strict_at_equality():
    s = stmt()
    sig = fundamentals.piotroski(s, s).signals
    assert sig == {"positive ROA": True, "positive operating cash flow": True, "ROA improved": False,
                   "cash flow exceeds net income": True, "leverage fell": False, "current ratio improved": False,
                   "no new shares issued": True, "gross margin improved": False, "asset turnover improved": False}
    zero = stmt(net_income=0, cfo=0)
    z = fundamentals.piotroski(zero, zero).signals
    assert z["positive ROA"] is False and z["positive operating cash flow"] is False and z["cash flow exceeds net income"] is False


def test_altman_zone_thresholds_with_equity_to_liabilities_as_the_only_driver():
    def z_for(target):  # WC, RE and EBIT are zero, so Z'' = 1.05 * equity / liabilities
        return stmt(current_assets=100, current_liabilities=100, retained_earnings=0, ebit=0, total_liabilities=1000,
                    equity=target * 1000 / 1.05)
    cases = [(2.61, "safe"), (2.59, "grey"), (1.11, "grey"), (1.09, "distress")]
    for target, zone in cases:
        a = fundamentals.altman_z_double_prime(z_for(target))
        assert a.z == pytest.approx(target) and a.zone == zone


def test_beneish_exact_m_score_with_every_index_different():
    prev = stmt(sales=1000, cogs=600, sga=100, depreciation=50, receivables=100, current_assets=400, ppe=500,
                total_assets=1000, current_liabilities=200, long_term_debt=200, net_income=100, cfo=100)
    cur = stmt(sales=1200, cogs=700, sga=150, depreciation=80, receivables=180, current_assets=440, ppe=600,
               total_assets=1200, current_liabilities=300, long_term_debt=330, net_income=150, cfo=90)
    b = fundamentals.beneish_m(cur, prev)
    want = {"DSRI": 1.5, "GMI": 0.96, "AQI": 4 / 3, "SGI": 1.2, "DEPI": (50 / 550) / (80 / 680), "SGAI": 1.25, "LVGI": 1.3125, "TATA": 0.05}
    for k, v in want.items():
        assert b.indices[k] == pytest.approx(v), k
    assert b.m == pytest.approx(-1.665427196969697, abs=1e-12)   # derived independently from the published coefficients
    assert b.flagged is True                                       # above the -1.78 threshold


def test_quality_report_piotroski_flag_boundary_is_three_of_nine():
    s = stmt()
    same = fundamentals.quality_report(s, s)                       # identical years score 4 (profit, cash flow, accruals, no issuance)
    assert same.piotroski.score == 4 and not any("Piotroski" in f for f in same.red_flags)
    diluted = fundamentals.quality_report(stmt(shares_outstanding=110), s)   # new shares lose that point: exactly 3 is flagged
    assert diluted.piotroski.score == 3 and any("weak Piotroski F-Score 3/9" in f for f in diluted.red_flags)


# ================================ round 2: remaining killable survivors ================================
def test_risk_small_numbers_and_unheld_tickers_at_exact_caps():
    # a ticker not held yet may reach exactly the position cap, not one rupee less
    s = risk.PortfolioState(cash=10_000.0, positions={}, peak_value=10_000.0)
    pol = risk.RiskPolicy(max_order_pct=100.0)                                  # position cap 15% = 1,500
    assert risk.size_position("NEW", s, pol) == pytest.approx(1500.0)
    assert risk.check_order("NEW", 1500.0, s, pol) == []
    # a zero or tiny positive amount: only <= 0 is "non-positive"
    assert "non-positive amount" not in risk.check_order("NEW", 0.5, s, pol)
    # drawdown of a book worth less than one rupee must still be computed
    assert risk.PortfolioState(cash=0.5, peak_value=0.6).drawdown_pct == pytest.approx((1 - 0.5 / 0.6) * 100)
    # cash reserve binds: 1,100 cash, 10% of 10,000 reserved -> 100 available
    tight = risk.PortfolioState(cash=1100.0, positions={"A": 8900.0}, peak_value=10_000.0)
    assert risk.size_position("B", tight, risk.RiskPolicy()) == pytest.approx(100.0)
    # the volatility cap scales with the policy's own budget, and applies for volatilities below 1%
    p = risk.RiskPolicy(target_daily_vol_pct=2.0)
    assert risk.size_position("A", risk.PortfolioState(cash=10_000.0, peak_value=10_000.0), p, daily_vol_pct=50.0) == pytest.approx(400.0)
    p2 = risk.RiskPolicy(target_daily_vol_pct=0.01)
    assert risk.size_position("A", risk.PortfolioState(cash=10_000.0, peak_value=10_000.0), p2, daily_vol_pct=0.5) == pytest.approx(200.0)


def test_risk_based_qty_accepts_a_stop_below_one_rupee():
    assert risk.risk_based_qty(100_000.0, 1.0, 100.0, 0.5) == 10                 # 1,000 / 99.5 = 10 shares


def test_quality_accepts_sub_rupee_prices():
    c = quality.check_price({"a": (0.5, T), "b": (0.5, T)}, T)
    assert c.ok and c.divergence_pct == pytest.approx(0.0) and not any("non-positive" in r for r in c.reasons)
    assert quality.implied_upside_ok(0.6, 0.5)                                    # +20% on a 50-paise price


def test_ladder_boundaries_on_every_guard():
    assert ladder.entry_ladder(1000, 100, tranches=1) == [(100.0, 10)]            # a single rung is a valid ladder
    assert ladder.entry_ladder(1000, 0.5, tranches=1) == [(0.5, 2000)]            # a sub-rupee price is valid
    with pytest.raises(ValueError):
        ladder.entry_ladder(1000, 0.02, tranches=1)                               # rounds to a zero limit price
    for kw in ({"price": 0.0}, {"total_amount": 0.0}, {"tranches": 0}, {"step_pct": 0.0}, {"step_pct": 50.0, "tranches": 2}):
        with pytest.raises(ValueError):
            ladder.entry_ladder(**{**{"total_amount": 1000, "price": 100, "tranches": 2, "step_pct": 4}, **kw})
    assert len(ladder.entry_ladder(10_000, 100, tranches=2, step_pct=49.9)) == 2   # just inside 100/tranches
    assert len(ladder.entry_ladder(10_000, 100, tranches=1, step_pct=99.0)) == 1   # one rung allows a step up to 100%
    assert ladder.entry_ladder(10_000, 100, tranches=2, step_pct=0.5)[1][0] == 99.5
    assert ladder.average_cost([(100.0, 1)]) == 100.0                              # one share is enough
    with pytest.raises(ValueError):
        ladder.average_cost([(100.0, 0)])


def test_discipline_zero_pnl_is_not_a_loss_and_small_books_are_handled():
    assert discipline.pre_trade_gate("A", good_thesis(), losses((0, 0.0)), BIG, T).allowed     # break-even is not a loss
    assert discipline.pre_trade_gate("A", good_thesis(), losses((0, 0.5)), BIG, T).allowed     # a small gain is not a loss
    assert discipline.pre_trade_gate("A", good_thesis(), [discipline.Closed(T + timedelta(days=1), -1.0)], BIG, T).allowed
    assert not discipline.circuit_breaker(losses((2, -0.4)), 0.5, T).allowed                   # 80% of a 50-paise book
    assert discipline.circuit_breaker(losses((2, -0.4)), 10.0, T).allowed                      # 4% of a Rs 10 book is under 5%
    assert "weekly net loss 6.0%" in discipline.circuit_breaker(losses((2, -0.6)), 10.0, T).reasons[0]


def test_valuation_zero_and_small_intrinsic_values_and_solver_range_edges():
    zero = valuation.valuation_view(10.0, fcf0=0.0, growth=0.0, years=1, terminal_growth=0.0, wacc=0.10)
    assert zero.intrinsic_base == 0.0 and zero.margin_of_safety_pct != zero.margin_of_safety_pct    # NaN, not an exception
    tiny = valuation.valuation_view(0.25, fcf0=0.05, growth=0.0, years=1, terminal_growth=0.0, wacc=0.10)
    assert tiny.intrinsic_base == pytest.approx(0.5) and tiny.margin_of_safety_pct == pytest.approx(50.0)
    fcf, yrs, tg, w = 100.0, 5, 0.03, 0.10
    at_lo, at_hi = (valuation.dcf_per_share(fcf, g, yrs, tg, w) for g in (-0.5, 1.0))
    with pytest.raises(ValueError):
        valuation.reverse_dcf_growth(at_lo - 0.5, fcf, yrs, tg, w)                 # just below the value at the lowest growth
    assert valuation.reverse_dcf_growth(at_hi - 0.5, fcf, yrs, tg, w) == pytest.approx(1.0, abs=1e-3)  # just inside the top of the range
    assert valuation.graham_number(0.5, 0.5) == pytest.approx((22.5 * 0.25) ** 0.5)


def test_liquidity_amihud_is_in_rupee_crore_and_counts_sub_unit_volumes():
    exact = (101.0 / 100.0 - 1.0) / (101.0 * 1_000_000.0 / 1e7)
    assert liquidity.amihud_per_crore(liq_bars(25)) == pytest.approx(exact, rel=1e-12)      # the 1e7 crore factor, exactly
    assert liquidity.amihud_per_crore(liq_bars(20, vol=0.5)) is not None                    # volume 0.5 > 0 counts


def test_crosscheck_agreed_close_scales_percent_exactly_and_orders_mismatches_by_day_then_isin():
    bse_close = 100.0 / (1 + 0.00995)                                                       # NSE is 0.995% above BSE
    assert crosscheck.agreed_close(bar(100.0, 100.0), bar(bse_close, 100.0, ex="BSE")) is not None
    isins = [f"I{i:02d}" for i in range(12)]
    n = [bar(150.0, 100.0, isin=i) for i in isins]
    b = [dataclasses.replace(x, close=100.0, exchange="BSE") for x in n]
    assert [m.isin for m in crosscheck.cross_check(n, b).mismatches] == isins                # sorted, not in set order


def test_piotroski_counts_tiny_positive_cash_flow_as_positive():
    s = stmt(cfo=0.5)
    assert fundamentals.piotroski(s, s).signals["positive operating cash flow"] is True


def test_ladder_single_rung_needs_a_positive_step_and_a_tiny_budget_buys_zero_shares():
    with pytest.raises(ValueError):
        ladder.entry_ladder(1000, 100, tranches=1, step_pct=0.0)      # a zero step is invalid even when no rung collapses
    assert ladder.entry_ladder(0.5, 100, tranches=1) == [(100.0, 0)]   # a budget below one share is valid: zero shares, no crash


def test_ladder_zero_price_is_reported_as_invalid_input_not_as_collapsing_rungs():
    with pytest.raises(ValueError, match="invalid ladder inputs"):
        ladder.entry_ladder(1000, 0.0)
