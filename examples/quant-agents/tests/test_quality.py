from datetime import date

from quant_agents.quality import audit_sources, check_price, implied_upside_ok

TODAY = date(2026, 10, 14)


def test_price_needs_two_sources():
    r = check_price({"a": (100.0, TODAY)}, TODAY)
    assert not r.ok and "two independent" in r.reasons[0]


def test_agreeing_fresh_sources_pass():
    r = check_price({"a": (869.05, date(2026, 10, 13)), "b": (870.0, TODAY)}, TODAY)
    assert r.ok and r.divergence_pct < 0.2


def test_divergent_sources_fail_like_a_bad_quote_feed():
    # A feed ~15% off the exchange close must never be accepted silently.
    r = check_price({"exchange": (869.05, TODAY), "delayed_feed": (1001.6, TODAY)}, TODAY)
    assert not r.ok and r.divergence_pct > 15 and any("diverge" in x for x in r.reasons)


def test_stale_and_future_quotes_rejected():
    r = check_price({"a": (100.0, date(2026, 10, 1)), "b": (100.0, date(2026, 10, 20))}, TODAY)
    assert any("stale by 13" in x for x in r.reasons) and any("future" in x for x in r.reasons)


def test_audit_flags_unsourced_numbers():
    report = "\n".join(
        [
            "# Valuation 25x heading is ignored",
            "CMP is ₹869.05 [Source: Exchange close, 13 Oct 2026]",
            "P/E is 17.5x and target is Rs 1,154",
            "No numbers in this sentence.",
            "ROE 13.9% [Source: Q2 filing]",
        ]
    )
    a = audit_sources(report)
    assert a.claims == 4 and a.sourced == 2
    assert a.unsourced == ("P/E is 17.5x and target is Rs 1,154",)
    assert a.coverage == 0.5


def test_empty_report_has_full_coverage():
    assert audit_sources("nothing numeric here").coverage == 1.0


def test_upside_bound():
    assert implied_upside_ok(150, 100)
    assert not implied_upside_ok(377.7, 138)  # ~174%
    assert not implied_upside_ok(10, 0)
