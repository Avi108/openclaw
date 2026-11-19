from datetime import date

from quant_agents.consensus import build_consensus
from quant_agents.ingest import drop_stale, parse_calls
from quant_agents.models import Rating

SAMPLE = """\
# comment
2026-10-12 | ABC | Broker A | buy | target Rs 1,200 | cmp Rs 800 | src
2026-10-11 | ABC | Broker B | Outperform | target ₹1,100 | cmp ₹800
2026-10-10 | ABC | Broker C | reduce | target Rs 700 | cmp Rs 800
2026-10-10 | XYZ | Broker A | buy | target Rs 500
not a call
2026-13-40 | BAD | Broker A | buy | target Rs 5
2026-10-10 | NEG | Broker A | buy | target Rs 0
"""


def test_parse_extracts_and_rejects():
    res = parse_calls(SAMPLE)
    assert [c.ticker for c in res.calls] == ["ABC", "ABC", "ABC", "XYZ"]
    assert res.calls[1].rating is Rating.BUY and res.calls[2].rating is Rating.SELL
    assert res.calls[0].target == 1200.0 and res.calls[3].price is None
    reasons = {r[2] for r in res.rejected}
    assert reasons == {"unparseable", "bad date", "non-positive number"}


def test_upside_pct():
    c = parse_calls("2026-10-12 | ABC | B | buy | target Rs 150 | cmp Rs 100").calls[0]
    assert round(c.upside_pct, 6) == 50.0


def test_drop_stale():
    calls = parse_calls(
        "2026-10-12 | A | B | buy | target 10 | cmp 5\n2026-08-01 | A | C | buy | target 10 | cmp 5"
    ).calls
    kept = drop_stale(calls, today=date(2026, 10, 14), max_age_days=30)
    assert len(kept) == 1 and kept[0].broker == "B"


def test_latest_call_per_broker_wins():
    calls = parse_calls(
        "2026-10-01 | A | B1 | buy | target 200 | cmp 100\n2026-10-10 | A | B1 | buy | target 120 | cmp 100"
    ).calls
    row = build_consensus(calls)[0]
    assert row.n_brokers == 1 and row.median_target == 120


def test_flags_and_scoring():
    calls = parse_calls(
        "\n".join(
            [
                "2026-10-12 | GOOD | B1 | buy | target 150 | cmp 100",
                "2026-10-12 | GOOD | B2 | buy | target 140 | cmp 100",
                "2026-10-12 | GOOD | B3 | buy | target 160 | cmp 100",
                "2026-10-12 | SUSP | B1 | buy | target 400 | cmp 100",
                "2026-10-12 | NOPX | B1 | buy | target 400",
                "2026-10-12 | MIX | B1 | buy | target 150 | cmp 100",
                "2026-10-12 | MIX | B2 | sell | target 60 | cmp 100",
            ]
        )
    ).calls
    rows = {r.ticker: r for r in build_consensus(calls)}
    assert rows["SUSP"].flags.count("implausible-upside") == 1 and rows["SUSP"].score == 0
    assert "no-price" in rows["NOPX"].flags and rows["NOPX"].score == 0
    assert "has-sell" in rows["MIX"].flags and "high-dispersion" in rows["MIX"].flags
    assert rows["GOOD"].score > rows["MIX"].score
    assert build_consensus(calls)[0].ticker == "GOOD"


def test_regression_buy_with_target_below_price_is_flagged_not_scored_negative():
    """Found by property-based testing: a Buy whose target sat below the price produced a negative score."""
    rows = {r.ticker: r for r in build_consensus(parse_calls(
        "2026-10-12 | BADBUY | B1 | buy | target 80 | cmp 100\n2026-10-12 | BADBUY | B2 | buy | target 90 | cmp 100\n"
        "2026-10-12 | BADSELL | B1 | sell | target 120 | cmp 100\n2026-10-12 | OK | B1 | sell | target 70 | cmp 100\n").calls)}
    assert "rating-target-mismatch" in rows["BADBUY"].flags and rows["BADBUY"].score == 0.0
    assert "rating-target-mismatch" in rows["BADSELL"].flags and rows["BADSELL"].score == 0.0
    assert "rating-target-mismatch" not in rows["OK"].flags  # a Sell with a lower target is consistent
