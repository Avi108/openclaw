import pytest

from quant_agents.fundamentals import (Statements, altman_z_double_prime, beneish_m, piotroski, quality_report)


def stmt(**kw):
    base = dict(sales=1000, cogs=600, sga=100, depreciation=50, ebit=150, net_income=100, cfo=120, receivables=100,
                current_assets=400, current_liabilities=200, ppe=500, total_assets=1000, total_liabilities=500,
                long_term_debt=200, retained_earnings=300, equity=500, shares_outstanding=100)
    base.update(kw)
    return Statements(**base)


def test_altman_hand_computed_value_and_zones():
    # direct textbook check: WC=20, TA=100, RE=30, EBIT=10, equity=50, liabilities=50
    s = stmt(current_assets=120, current_liabilities=100, total_assets=100, retained_earnings=30, ebit=10, equity=50, total_liabilities=50)
    a = altman_z_double_prime(s)
    assert a.z == pytest.approx(6.56 * 0.2 + 3.26 * 0.3 + 6.72 * 0.1 + 1.05 * 1.0) and a.zone == "safe"
    weak = stmt(current_assets=50, current_liabilities=100, total_assets=100, retained_earnings=-20, ebit=-5, equity=10, total_liabilities=90)
    assert altman_z_double_prime(weak).zone == "distress"


def test_beneish_neutral_firm_matches_published_constant():
    # All indices 1 and TATA 0 gives exactly -2.48 in the 8-variable model.
    s = stmt(net_income=100, cfo=100)
    b = beneish_m(s, s)
    assert b.m == pytest.approx(-2.48, abs=1e-9) and not b.flagged
    assert all(b.indices[k] == pytest.approx(1.0) for k in ("DSRI", "GMI", "AQI", "SGI", "DEPI", "SGAI", "LVGI"))


def test_beneish_flags_aggressive_accruals_and_receivables():
    prev = stmt()
    cur = stmt(sales=1400, receivables=350, net_income=200, cfo=20, cogs=900)  # sales up, receivables balloon, profit not cash
    b = beneish_m(cur, prev)
    assert b.flagged and b.indices["DSRI"] > 2 and b.indices["TATA"] > 0.1


def test_piotroski_perfect_and_worst():
    prev = stmt(net_income=50, cogs=650, current_assets=300, long_term_debt=300, shares_outstanding=100, sales=900)
    best = stmt(net_income=100, cfo=150, cogs=600, current_assets=400, long_term_debt=200, shares_outstanding=100, sales=1000)
    p = piotroski(best, prev)
    assert p.score == 9 and all(p.signals.values())
    worst = piotroski(prev, best)
    # still profitable with positive, higher-than-profit cash flow and no new shares: those 4 hold
    assert worst.score == 4 and not worst.signals["ROA improved"]
    assert worst.signals["no new shares issued"]  # equal share count is not an issuance


def test_quality_report_red_flags_and_financials_refused():
    prev = stmt()
    bad = stmt(net_income=-50, cfo=-20, current_assets=100, current_liabilities=300, retained_earnings=-100, ebit=-30, equity=50,
               total_liabilities=950, long_term_debt=400, shares_outstanding=150, sales=800, cogs=700)
    q = quality_report(bad, prev)
    assert any("Piotroski" in f for f in q.red_flags) and any("Altman" in f for f in q.red_flags)
    with pytest.raises(ValueError, match="banks, NBFCs"):
        quality_report(bad, prev, financial_company=True)
    with pytest.raises(ValueError, match="division by zero"):
        piotroski(stmt(total_assets=0), prev)
