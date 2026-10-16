"""Property-based tests (hypothesis): laws that must hold for ANY input, not just hand-picked examples."""
import os
from datetime import date, timedelta

import pytest

pytest.importorskip("hypothesis", reason="install the dev extras: pip install -e '.[dev]'")

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from quant_agents.backtest import equity_to_metrics
from quant_agents.consensus import build_consensus
from quant_agents.crosscheck import cross_check
from quant_agents.ingest import parse_calls
from quant_agents.ladder import entry_ladder
from quant_agents.markets import KRX, decode_bytes, normalize_symbol, parse_number
from quant_agents.models import BrokerCall, Rating
from quant_agents.nse import Bar
from quant_agents.pdf_extract import extract_call
from quant_agents.risk import PortfolioState, RiskPolicy, check_order, kelly_fraction, risk_based_qty, size_position
from quant_agents.tax import estimate_tax
from quant_agents.valuation import dcf_per_share, reverse_dcf_growth
from quant_agents.validation import permutation_p_value, spearman
import itertools

# PROP_EXAMPLES=2000 pytest tests/test_properties.py   -> a deep search (slower)
S = settings(max_examples=int(os.environ.get("PROP_EXAMPLES", "150")), deadline=None, suppress_health_check=[HealthCheck.too_slow])
money = st.floats(min_value=1.0, max_value=1e7, allow_nan=False, allow_infinity=False)
D = date(2026, 10, 16)


# ---------------------------------- parsing never crashes ----------------------------------
@S
@given(st.text(max_size=400))
def test_parse_calls_survives_arbitrary_text(text):
    res = parse_calls(text)
    assert all(c.target > 0 and (c.price is None or c.price > 0) for c in res.calls)
    assert len(res.calls) + len(res.rejected) <= len(text.splitlines())


@S
@given(st.text(max_size=800), st.sampled_from(["ABC", "x1"]))
def test_extract_call_survives_arbitrary_text_and_never_invents_numbers(text, ticker):
    ex = extract_call(text, ticker, "B", D)
    if ex.call is not None:
        assert ex.call.target > 0
        assert ex.call.price is None or ex.call.price > 0
    else:
        assert ex.reasons  # a refusal always says why


@S
@given(st.floats(min_value=0, max_value=1e9, allow_nan=False, allow_infinity=False))
def test_parse_number_round_trips_formatted_numbers(x):
    assert parse_number(f"{x:,.2f}") == pytest.approx(round(x, 2), abs=1e-6)


@S
@given(st.text(alphabet="0123456789abcXYZ", min_size=1, max_size=8))
def test_normalize_symbol_is_idempotent_and_pads_only_digits(raw):
    once = normalize_symbol(raw, KRX)
    assert normalize_symbol(once, KRX) == once
    assert (len(once) >= 6) == (raw.isdigit() or len(raw) >= 6)


@S
@given(st.text(alphabet=st.characters(min_codepoint=0xAC00, max_codepoint=0xD7A3), min_size=6, max_size=30))
def test_cp949_roundtrip_for_realistic_hangul_headers(text):
    """cp949 bytes could in theory also be valid UTF-8 (then decoded wrongly). Realistic header-length Hangul text
    makes that vanishingly unlikely; very short strings are not guaranteed, which is why markdown docs say to verify."""
    assert decode_bytes(text.encode("cp949"), KRX) == text


# ---------------------------------- sizing and limits ----------------------------------
@S
@given(cash=money, held=st.lists(st.floats(min_value=0, max_value=5e6, allow_nan=False), max_size=4),
       order_pct=st.floats(1, 20), pos_pct=st.floats(1, 40), reserve=st.floats(0, 50), vol=st.one_of(st.none(), st.floats(0.1, 50)))
def test_size_position_never_exceeds_any_limit(cash, held, order_pct, pos_pct, reserve, vol):
    pol = RiskPolicy(max_order_pct=order_pct, max_position_pct=pos_pct, min_cash_pct=reserve)
    positions = {f"T{i}": v for i, v in enumerate(held)}
    st_ = PortfolioState(cash=cash, positions=positions)
    st_.peak_value = st_.value
    amt = size_position("T0", st_, pol, vol)
    value = st_.value
    assert amt >= 0
    assert amt <= value * order_pct / 100 + 1e-6
    assert amt <= max(0.0, value * pos_pct / 100 - positions.get("T0", 0.0)) + 1e-6
    assert amt <= max(0.0, cash - value * reserve / 100) + 1e-6
    if amt > 0:
        assert check_order("T0", amt, st_, pol) == []  # whatever sizing returns must pass the policy check


@S
@given(value=money, risk=st.floats(0.1, 5), entry=st.floats(10, 5000), gap=st.floats(0.01, 0.9))
def test_risk_based_qty_loses_at_most_the_budget_and_is_maximal(value, risk, entry, gap):
    stop = entry * (1 - gap)
    q = risk_based_qty(value, risk, entry, stop)
    budget = value * risk / 100
    assert q >= 0 and q * (entry - stop) <= budget + 1e-6
    assert (q + 1) * (entry - stop) > budget - 1e-6  # one more share would exceed the budget


@S
@given(p=st.floats(0.01, 0.99), b=st.floats(0.05, 20), frac=st.floats(0.05, 1), cap=st.floats(0.01, 1))
def test_kelly_is_between_zero_and_cap(p, b, frac, cap):
    assert 0.0 <= kelly_fraction(p, b, frac, cap) <= cap + 1e-12


@S
@given(total=st.floats(100, 1e6), price=st.floats(1, 5000), n=st.integers(1, 6), step=st.floats(0.5, 15))
def test_ladder_never_spends_more_than_total_and_prices_fall(total, price, n, step):
    assume(step < 100 / n)
    try:
        lad = entry_ladder(total, price, n, step)
    except ValueError:
        return  # a step too small for the tick is refused, which is the correct outcome
    assert sum(p * q for p, q in lad) <= total + 1e-6
    assert all(lad[i][0] > lad[i + 1][0] for i in range(len(lad) - 1))  # rungs are distinct and falling
    assert all(abs(p / 0.05 - round(p / 0.05)) < 1e-6 for p, _ in lad)  # every limit sits on the tick


# ---------------------------------- statistics ----------------------------------
@S
@given(st.lists(st.floats(0.5, 1e4, allow_nan=False), min_size=3, max_size=60))
def test_metrics_invariants(eq):
    m = equity_to_metrics(eq)
    assert 0.0 <= m.max_drawdown_pct <= 100.0
    assert m.total_return_pct == pytest.approx((eq[-1] / eq[0] - 1) * 100)
    if all(b >= a for a, b in itertools.pairwise(eq)):
        assert m.max_drawdown_pct == pytest.approx(0.0)


pairs = st.lists(st.tuples(st.floats(-1e3, 1e3, allow_nan=False), st.floats(-1e3, 1e3, allow_nan=False)), min_size=3, max_size=40)


@S
@given(pairs)
def test_spearman_is_bounded_symmetric_and_rank_invariant(ps):
    x, y = [a for a, _ in ps], [b for _, b in ps]
    r = spearman(x, y)
    assert -1.0 - 1e-9 <= r <= 1.0 + 1e-9
    assert spearman(y, x) == pytest.approx(r, abs=1e-9)
    assert spearman([3.0 * v for v in x], y) == pytest.approx(r, abs=1e-9)  # an exact increasing transform (no float collapse)


@S
@given(pairs, st.integers(0, 5))
def test_permutation_p_value_is_a_probability(ps, seed):
    x, y = [a for a, _ in ps], [b for _, b in ps]
    assert 0.0 < permutation_p_value(x, y, n_perm=50, seed=seed) <= 1.0


# ---------------------------------- valuation ----------------------------------
@S
@given(fcf=st.floats(1, 1e5), g=st.floats(-0.1, 0.4), dg=st.floats(0.001, 0.2), w=st.floats(0.06, 0.25), tg=st.floats(0.0, 0.05),
       years=st.integers(1, 15))
def test_dcf_rises_with_growth_and_falls_with_discount_rate(fcf, g, dg, w, tg, years):
    assume(w > tg + 0.01)
    base = dcf_per_share(fcf, g, years, tg, w)
    assert dcf_per_share(fcf, g + dg, years, tg, w) > base
    assert dcf_per_share(fcf, g, years, tg, w + 0.01) < base


@S
@given(fcf=st.floats(10, 1e4), g=st.floats(-0.2, 0.6), w=st.floats(0.08, 0.2), years=st.integers(2, 12))
def test_reverse_dcf_recovers_the_growth_that_made_the_price(fcf, g, w, years):
    price = dcf_per_share(fcf, g, years, 0.03, w)
    assume(price > 0)
    assert reverse_dcf_growth(price, fcf, years, 0.03, w) == pytest.approx(g, abs=1e-6)


# ---------------------------------- tax ----------------------------------
gain = st.floats(-5e6, 5e6, allow_nan=False)
lots = st.lists(st.tuples(st.integers(0, 800), st.integers(0, 800), gain), max_size=8)


def to_lots(raw):
    return [(D + timedelta(days=min(a, b)), D + timedelta(days=max(a, b)), g) for a, b, g in raw]


@S
@given(lots)
def test_tax_is_never_negative(raw):
    t = estimate_tax(to_lots(raw))
    assert t.tax >= 0 and t.taxable_ltcg >= 0 and t.stcg >= 0 and t.ltcg >= 0


@S
@given(lots, st.floats(0, 5e6), st.integers(0, 800))
def test_an_extra_gain_never_lowers_tax(raw, extra, hold):
    base = estimate_tax(to_lots(raw)).tax
    more = estimate_tax(to_lots(raw) + [(D, D + timedelta(days=hold), extra)]).tax
    assert more >= base - 1e-6


# ---------------------------------- consensus and cross-check ----------------------------------
calls_st = st.lists(
    st.builds(BrokerCall, ticker=st.sampled_from(["A", "B", "C"]), broker=st.sampled_from(["b1", "b2", "b3"]),
              rating=st.sampled_from(list(Rating)), target=st.floats(1, 1e4), price=st.one_of(st.none(), st.floats(1, 1e4)),
              published=st.dates(min_value=date(2026, 1, 1), max_value=date(2026, 10, 1))), max_size=25)


@S
@given(calls_st)
def test_consensus_invariants_and_idempotence_under_duplicates(calls):
    rows = build_consensus(calls)
    for r in rows:
        assert 1 <= r.n_brokers <= 3 and 0.0 <= r.buy_ratio <= 1.0
        if {"implausible-upside", "no-price", "rating-target-mismatch"} & set(r.flags):
            assert r.score == 0.0
        if r.median_upside_pct is not None and r.median_upside_pct <= 0:
            assert r.score <= 0.0  # a score never rewards a non-positive upside
    assert {(r.ticker, r.n_brokers, r.score) for r in rows} == {(r.ticker, r.n_brokers, r.score) for r in build_consensus(calls + calls)}


bars_st = st.lists(
    st.builds(Bar, symbol=st.sampled_from(["A", "B"]), day=st.dates(min_value=date(2026, 1, 1), max_value=date(2026, 1, 20)),
              open=st.just(1.0), high=st.just(1.0), low=st.just(1.0), close=st.floats(1, 1e3), prev_close=st.floats(1, 1e3),
              volume=st.just(1.0), isin=st.sampled_from(["I1", "I2", None])), max_size=20)


@S
@given(bars_st, bars_st)
def test_cross_check_is_symmetric_in_what_it_matches(n, b):
    r1, r2 = cross_check(n, b), cross_check(b, n)
    assert r1.matched == r2.matched and len(r1.mismatches) == len(r2.mismatches)
    assert r1.unmatched_nse == r2.unmatched_bse and r1.no_isin == r2.no_isin
