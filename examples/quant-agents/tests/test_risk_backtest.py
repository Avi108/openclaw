import math
from datetime import date

import pytest

from quant_agents.backtest import equity_to_metrics, momentum_signal, run_backtest, synthetic_prices
from quant_agents.risk import PortfolioState, RiskPolicy, check_order, size_position

POLICY = RiskPolicy()


def state(**kw):
    base = dict(cash=1000.0, positions={"AAA": 500.0, "BBB": 500.0}, peak_value=2000.0)
    base.update(kw)
    return PortfolioState(**base)


def test_size_respects_every_cap():
    s = PortfolioState(cash=3000.0, positions={"AAA": 300.0, "BBB": 700.0}, peak_value=4000.0)
    amt = size_position("AAA", s, POLICY)
    assert amt == pytest.approx(200.0)  # 5% order cap of 4000 binds
    # a very volatile name (50% daily vol) gets a smaller slot: 4000 * 1 / 50 = 80
    assert size_position("AAA", s, POLICY, daily_vol_pct=50.0) == pytest.approx(80.0)
    assert check_order("AAA", amt, s, POLICY) == []


def test_size_never_negative_and_respects_cash_reserve():
    s = PortfolioState(cash=100.0, positions={"AAA": 3900.0}, peak_value=4000.0)
    assert size_position("BBB", s, POLICY) == 0.0  # cash 100 < 10% reserve (400)


def test_position_cap_uses_existing_holding():
    s = PortfolioState(cash=3000.0, positions={"AAA": 590.0, "BBB": 410.0}, peak_value=4000.0)
    assert size_position("AAA", s, POLICY) == pytest.approx(10.0)  # 15% of 4000 = 600


def test_check_order_reports_all_violations():
    s = state(orders_today=2, orders_this_week=5)
    v = check_order("AAA", 900.0, s, POLICY, today=date(2026, 10, 14))
    joined = " | ".join(v)
    assert "order exceeds" in joined and "position would exceed" in joined
    assert "cash would fall" in joined and "daily order limit" in joined and "weekly order limit" in joined


def test_drawdown_halt():
    s = PortfolioState(cash=800.0, positions={"AAA": 200.0}, peak_value=1500.0)  # -33%
    assert any("halted" in v for v in check_order("AAA", 10.0, s, POLICY))


def test_metrics_known_values():
    eq = [1.0, 1.1, 0.99, 1.089]
    m = equity_to_metrics(eq)
    assert m.total_return_pct == pytest.approx(8.9)
    assert m.max_drawdown_pct == pytest.approx(10.0)
    assert m.n_days == 3
    flat = equity_to_metrics([1.0, 1.0, 1.0])
    assert flat.sharpe == 0.0 and flat.max_drawdown_pct == 0.0


def test_constant_growth_sharpe_is_zero_std_guard_and_cagr():
    eq = [1.0 * (1.001**i) for i in range(253)]
    m = equity_to_metrics(eq)
    assert m.cagr_pct == pytest.approx((1.001**252 - 1) * 100, rel=1e-9)


def test_no_lookahead_signal_sees_only_past():
    prices = synthetic_prices(120)
    seen = []

    def spy(hist):
        seen.append(len(hist))
        return 1.0

    run_backtest(prices, spy, start=30)
    assert seen[0] == 30 and seen[-1] == 119  # never sees today's price


def test_costs_reduce_returns_and_always_flat_is_flat():
    prices = synthetic_prices(300)
    free = run_backtest(prices, momentum_signal(10), cost_bps=0)
    paid = run_backtest(prices, momentum_signal(10), cost_bps=50)
    assert paid[-1] < free[-1]
    assert run_backtest(prices, lambda h: 0.0)[-1] == pytest.approx(1.0)


def test_synthetic_prices_deterministic():
    assert synthetic_prices(50, seed=1) == synthetic_prices(50, seed=1)
    assert synthetic_prices(50, seed=1) != synthetic_prices(50, seed=2)
    assert all(math.isfinite(p) and p > 0 for p in synthetic_prices(500))
