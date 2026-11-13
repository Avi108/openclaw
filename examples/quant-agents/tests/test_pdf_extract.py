from datetime import date

from quant_agents.models import Rating
from quant_agents.pdf_extract import extract_call

D = date(2026, 10, 12)


def run(text):
    return extract_call(text, "abc", "Broker X", D)


def test_clean_report_front_page():
    e = run("ABC Ltd | Rating: BUY | CMP: Rs 1,183 | Target Price: Rs 1,855 (12 months)\nSector: Realty")
    assert e.call and e.call.ticker == "ABC" and e.call.rating is Rating.BUY
    assert e.call.target == 1855 and e.call.price == 1183
    assert round(e.call.upside_pct) == 57


def test_indian_wording_accumulate_and_reduce():
    assert run("Recommendation: Accumulate\nTP: ₹ 640\nCMP ₹ 500").call.rating is Rating.BUY
    assert run("Rating - Reduce  Target 90  CMP 100").call.rating is Rating.SELL
    assert run("Rating: Neutral, Target price 100, CMP 98").call.rating is Rating.HOLD


def test_refuses_conflicting_or_missing_fields():
    assert run("Rating: BUY and also SELL view\nTarget Price: 100").call is None
    assert "no target price found" in run("Rating: BUY\nCMP 100").reasons
    assert "no rating found" in run("Target price: 100 CMP 90").reasons
    e = run("Rating: BUY\nTarget Price: 100\nTarget Price: 130")
    assert e.call is None and "multiple target prices" in e.reasons[0]


def test_ambiguous_price_is_unknown_not_guessed():
    e = run("Rating: BUY\nTarget Price: 200\nCMP: 100\nCurrent market price: 105")
    assert e.call and e.call.price is None and "price left unknown" in e.reasons[0]
    assert e.call.upside_pct is None


def test_only_front_page_is_read():
    text = "Rating: BUY\nTarget Price: 200\nCMP: 100\n" + ("x" * 7000) + "\nTarget Price: 999"
    assert run(text).call.target == 200
