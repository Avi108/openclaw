from datetime import date, timedelta

import pytest

from quant_agents.papertrade import CostModel, PaperLedger, equity_curve, report

D0 = date(2026, 1, 1)
NOCOST = CostModel(0, 0, 0, 0, 0, 0)


def path(prices, start=D0):
    return [(start + timedelta(days=i), p) for i, p in enumerate(prices)]


def test_entry_uses_next_close_not_decision_day_price():
    L = PaperLedger(10_000, NOCOST)
    t = L.enter("A", 1000, D0, path([100, 50, 50]))  # decided on day 0 (100), executes day 1 (50)
    assert t.price == 50 and t.day == D0 + timedelta(days=1) and t.qty == 20


def test_costs_and_slippage_reduce_quantity_and_cash_never_negative():
    L = PaperLedger(1000, CostModel())
    t = L.enter("A", 1000, D0, path([100] * 5))
    assert t.qty == 9 and t.price > 100  # slippage worsens the price
    assert L.cash() >= 0


def test_insufficient_cash_and_unaffordable_share_rejected():
    L = PaperLedger(500, NOCOST)
    with pytest.raises(ValueError, match="insufficient"):
        L.enter("A", 1000, D0, path([100] * 3))
    with pytest.raises(ValueError, match="cannot buy one share"):
        PaperLedger(5000, NOCOST).enter("A", 50, D0, path([100] * 3))
    with pytest.raises(ValueError, match="no price after"):
        PaperLedger(5000, NOCOST).enter("A", 500, D0, path([100]))


def test_exit_sells_whole_position_and_requires_one():
    L = PaperLedger(10_000, NOCOST)
    p = path([100, 100, 120, 120])
    L.enter("A", 1000, D0, p)
    assert L.positions() == {"A": 10}
    t = L.exit("A", D0 + timedelta(days=1), p)
    assert t.price == 120 and L.positions() == {} and L.cash() == pytest.approx(10_200)
    with pytest.raises(ValueError):
        L.exit("A", D0, p)


def test_round_trip_costs_make_a_flat_trade_a_loss():
    L = PaperLedger(10_000, CostModel())
    p = path([100] * 6)
    L.enter("A", 5000, D0, p)
    L.exit("A", D0 + timedelta(days=2), p)
    assert L.cash() < 10_000  # friction alone loses money


def test_save_load_roundtrip(tmp_path):
    L = PaperLedger(10_000, CostModel(slippage_bps=5))
    L.enter("A", 1000, D0, path([100] * 4))
    f = tmp_path / "ledger.json"
    L.save(f)
    M = PaperLedger.load(f)
    assert M.trades == L.trades and M.cost.slippage_bps == 5 and M.cash() == pytest.approx(L.cash())


def test_equity_curve_forward_fills_missing_days():
    L = PaperLedger(10_000, NOCOST)
    L.enter("A", 1000, D0, path([100, 100, 100, 100]))
    prices = {"A": [(D0, 100), (D0 + timedelta(days=1), 100), (D0 + timedelta(days=3), 110)],
              "B": path([5, 5, 5, 5])}
    curve = dict(equity_curve(L, prices, D0 + timedelta(days=1), D0 + timedelta(days=3)))
    assert curve[D0 + timedelta(days=2)] == pytest.approx(10_000)  # A missing on day 2: carry 100, not zero
    assert curve[D0 + timedelta(days=3)] == pytest.approx(10_100)


def test_report_beats_flat_benchmark_but_says_too_early():
    L = PaperLedger(10_000, NOCOST)
    stock = path([100] + [100 * (1 + 0.01 * i) for i in range(1, 30)])
    L.enter("A", 10_000, D0, stock)
    prices = {"A": stock, "IDX": path([1000] * 30)}
    r = report(L, prices, "IDX")
    assert r.excess_return_pct > 20 and "too early to judge" in r.verdict
    assert r.benchmark.total_return_pct == pytest.approx(0.0, abs=1e-9)


def test_report_underperformance_verdict_with_enough_sample():
    L = PaperLedger(100_000, NOCOST)
    days = 90
    flat = path([100] * days)
    stocks = {}
    for i in range(25):  # 25 buys, all go nowhere while the index rises
        s = f"S{i}"
        stocks[s] = flat
        L.enter(s, 3000, D0, flat)
    prices = {**stocks, "IDX": path([1000 + 5 * i for i in range(days)])}
    r = report(L, prices, "IDX")
    assert r.n_trades == 25 and r.excess_return_pct < 0 and "behind or level" in r.verdict


def test_report_requires_trades_and_benchmark():
    with pytest.raises(ValueError):
        report(PaperLedger(100, NOCOST), {"X": path([1, 2, 3])}, "X")
    L = PaperLedger(1000, NOCOST)
    L.enter("A", 100, D0, path([10] * 5))
    with pytest.raises(ValueError, match="benchmark"):
        report(L, {"A": path([10] * 5)}, "IDX")
