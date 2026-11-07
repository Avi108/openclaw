import math
from datetime import date, timedelta

import pytest

from quant_agents import breadth as br
from quant_agents.events import Event, describe, load_events, upcoming
from quant_agents.nse import Bar
from quant_agents.risk import kelly_fraction, risk_based_qty
from quant_agents.tax import TaxRules, estimate_tax
from quant_agents.thesis import Invalidation, Thesis, evaluate, load, save
from quant_agents.universe import Instrument, Segment
from quant_agents.valuation import (dcf_per_share, graham_number, margin_of_safety, reverse_dcf_growth, sensitivity,
                                    valuation_view)

D = date(2026, 10, 16)


# ---------------- valuation ----------------
def test_dcf_zero_growth_equals_perpetuity():
    assert dcf_per_share(100, 0.0, 5, 0.0, 0.10) == pytest.approx(1000.0)
    assert dcf_per_share(100, 0.0, 5, 0.0, 0.10, net_debt=200, shares=10) == pytest.approx(80.0)


def test_dcf_validation_and_sensitivity_grid():
    with pytest.raises(ValueError):
        dcf_per_share(100, 0.1, 5, 0.12, 0.10)
    grid = sensitivity(100, 0.08, 5, [0.10, 0.12], [0.02, 0.11])
    assert (0.10, 0.11) not in grid and grid[(0.10, 0.02)] > grid[(0.12, 0.02)]


def test_reverse_dcf_recovers_growth_and_flags_impossible_price():
    price = dcf_per_share(100, 0.12, 8, 0.04, 0.11, 50, 10)
    assert reverse_dcf_growth(price, 100, 8, 0.04, 0.11, 50, 10) == pytest.approx(0.12, abs=1e-8)
    with pytest.raises(ValueError, match="no growth"):
        reverse_dcf_growth(1e9, 100, 8, 0.04, 0.11)


def test_graham_and_margin_of_safety():
    assert graham_number(10, 100) == pytest.approx(math.sqrt(22.5 * 1000))
    assert graham_number(-1, 100) == 0.0
    assert margin_of_safety(60, 100) == pytest.approx(0.4) and margin_of_safety(120, 100) == pytest.approx(-0.2)
    with pytest.raises(ValueError):
        margin_of_safety(1, 0)


def test_valuation_view_says_what_price_assumes():
    v = valuation_view(price=2000, fcf0=100, growth=0.08, years=5, terminal_growth=0.04, wacc=0.11, shares=1.0)
    assert v.implied_growth_pct > 8 and "more growth" in v.note and v.margin_of_safety_pct < 0


# ---------------- thesis ----------------
def thesis(**kw):
    base = {"ticker": "ABC", "opened": D - timedelta(days=60), "statement": "margin recovery", "entry_price": 100, "target_price": 150,
                "review_by": D + timedelta(days=30), "invalidations": (Invalidation("promoter_pledge_pct", ">", 30, "pledge spike"),),
                "bear_case": "demand stays weak"}
    base.update(kw)
    return Thesis(**base)


def test_thesis_intact_review_broken():
    t = thesis()
    assert evaluate(t, {"promoter_pledge_pct": 5}, 110, D).status == "intact"
    s = evaluate(t, {"promoter_pledge_pct": 45}, 110, D)
    assert s.status == "broken" and "pledge spike" in s.reasons[0]
    assert evaluate(t, {}, 110, D).status == "review"  # untestable invalidation is not silently ok
    assert "price 25% below entry" in evaluate(t, {"promoter_pledge_pct": 5}, 75, D).reasons[0]
    assert evaluate(t, {"promoter_pledge_pct": 5}, 110, D + timedelta(days=31)).status == "review"
    assert "no bear case written" in evaluate(thesis(bear_case=""), {"promoter_pledge_pct": 5}, 110, D).reasons
    assert "target reached" in evaluate(t, {"promoter_pledge_pct": 5}, 150, D).reasons[0]


def test_thesis_rejects_bad_operator_and_roundtrips(tmp_path):
    with pytest.raises(ValueError):
        evaluate(thesis(invalidations=(Invalidation("x", "!=", 1),)), {"x": 1}, 100, D)
    f = tmp_path / "t.json"
    save([thesis()], f)
    assert load(f) == [thesis()]


# ---------------- breadth ----------------
def walk(sym, n, step, start=100.0, d0=date(2025, 1, 1)):
    out, px = [], start
    for i in range(n):
        new = px * (1 + step)
        out.append(Bar(sym, d0 + timedelta(days=i), new, new, new, new, px, 1000))
        px = new
    return out


def test_breadth_counts_and_moving_averages():
    bars = []
    for i in range(12):
        bars += walk(f"UP{i}", 260, 0.001)
    for i in range(8):
        bars += walk(f"DN{i}", 260, -0.001)
    day = bars[0].day + timedelta(days=259)
    b = br.breadth(bars, day)
    assert (b.advancing, b.declining, b.n_symbols) == (12, 8, 20)
    assert b.pct_above_50dma == pytest.approx(60.0) and b.pct_above_200dma == pytest.approx(60.0)
    assert b.new_highs == 12 and b.new_lows == 8 and b.advance_decline_ratio == pytest.approx(1.5)


def test_breadth_hides_percentages_when_history_or_sample_is_too_small():
    bars = [x for i in range(3) for x in walk(f"S{i}", 260, 0.001)]
    b = br.breadth(bars, bars[0].day + timedelta(days=259))
    assert b.pct_above_50dma is None  # only 3 symbols: refuse a percentage
    short = [x for i in range(12) for x in walk(f"S{i}", 40, 0.001)]
    assert br.breadth(short, short[0].day + timedelta(days=39)).pct_above_50dma is None  # <50 sessions


def test_split_not_counted_as_decline():
    rows = walk("SPL", 5, 0.0)
    d = rows[-1].day + timedelta(days=1)
    rows.append(Bar("SPL", d, 20, 20, 20, 20, 20, 1000))  # 1:5 split: adjusted prev close is 20, raw would be 100
    assert br.breadth(rows, d, min_symbols=1).declining == 0


def test_sector_strength_equal_weighted():
    bars = walk("A1", 80, 0.002) + walk("A2", 80, 0.002) + walk("B1", 80, -0.001)
    uni = {"A1": Instrument("A1", Segment.LARGE, "autos"), "A2": Instrument("A2", Segment.LARGE, "autos"),
           "B1": Instrument("B1", Segment.LARGE, "metals")}
    s = br.sector_strength(bars, uni, bars[0].day + timedelta(days=79))
    assert s["autos"][20] > 0 > s["metals"][20] and s["autos"][60] > s["autos"][20]


# ---------------- sizing ----------------
def test_risk_based_qty_and_kelly():
    assert risk_based_qty(100_000, 1.0, 100, 90) == 100  # lose Rs 1,000 if stopped at 90
    assert risk_based_qty(100_000, 1.0, 100, 95) == 200  # tighter stop, larger size
    with pytest.raises(ValueError):
        risk_based_qty(100_000, 1.0, 100, 110)
    assert kelly_fraction(0.6, 1.0, fraction=1.0, cap=1.0) == pytest.approx(0.2)
    assert kelly_fraction(0.4, 1.0) == 0.0 and kelly_fraction(0.9, 5.0) == 0.10  # no edge -> 0; big edge -> capped
    with pytest.raises(ValueError):
        kelly_fraction(1.0, 1.0)


# ---------------- tax ----------------
def test_tax_short_long_exemption_and_cess():
    t = estimate_tax([(date(2026, 1, 1), date(2026, 6, 1), 100_000), (date(2024, 1, 1), date(2026, 6, 1), 225_000)])
    assert t.stcg == 100_000 and t.taxable_ltcg == 100_000
    assert t.tax == pytest.approx((100_000 * 0.20 + 100_000 * 0.125) * 1.04)


def test_tax_setoff_rules_and_configurable_rates():
    # short-term loss offsets long-term gain
    t = estimate_tax([(date(2026, 1, 1), date(2026, 3, 1), -50_000), (date(2024, 1, 1), date(2026, 3, 1), 300_000)])
    assert t.stcg == 0 and t.ltcg == 250_000 and t.taxable_ltcg == 125_000
    # long-term loss does NOT offset short-term gain
    t2 = estimate_tax([(date(2026, 1, 1), date(2026, 3, 1), 80_000), (date(2024, 1, 1), date(2026, 3, 1), -30_000)])
    assert t2.stcg == 80_000 and t2.tax == pytest.approx(80_000 * 0.20 * 1.04)
    assert estimate_tax([(date(2026, 1, 1), date(2026, 3, 1), 1000)], TaxRules(stcg_rate=0.15, cess=0)).tax == pytest.approx(150)
    with pytest.raises(ValueError):
        estimate_tax([(date(2026, 3, 1), date(2026, 1, 1), 1)])
    assert estimate_tax([(date(2025, 1, 1), date(2025, 1, 1) + timedelta(days=365), 10_000)]).stcg == 10_000  # exactly 365d is short


# ---------------- events ----------------
def test_events_window_market_wide_and_load(tmp_path):
    ev = [Event(D + timedelta(days=2), "results", "ABC"), Event(D + timedelta(days=1), "rbi_mpc", None),
          Event(D + timedelta(days=9), "results", "ABC"), Event(D + timedelta(days=1), "results", "XYZ")]
    u = upcoming("abc", D, ev)
    assert [e.kind for e in u] == ["rbi_mpc", "results"] and "rbi_mpc in 1d; results ABC in 2d" == describe(u, D)
    f = tmp_path / "e.csv"
    f.write_text("# c\ndate,kind,ticker\n2026-10-20,results,abc\n2026-10-22,rbi_mpc,\n")
    loaded = load_events(f)
    assert loaded[0].ticker == "ABC" and loaded[1].ticker is None
