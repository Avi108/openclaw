from datetime import date, timedelta

import pytest

from quant_agents.models import BrokerCall, Rating
from quant_agents.validation import (
    evaluate,
    evaluate_call,
    permutation_p_value,
    simulate_world,
    spearman,
    summarize,
)

D0 = date(2026, 1, 1)


def path(prices, start=D0):
    return [(start + timedelta(days=i), p) for i, p in enumerate(prices)]


def call(published, target=150.0, price=100.0):
    return BrokerCall("X", "B", Rating.BUY, target, price, published)


def test_entry_is_strictly_after_publication_no_lookahead():
    # Price spikes on publication day itself; a leaky harness would enter at 200.
    p = path([100, 200, 100, 100, 100, 100])
    o = evaluate_call(call(D0 + timedelta(days=1)), p, horizon_days=3)
    assert o.realized_return_pct == pytest.approx(0.0)  # entered at day 2 (100) and exited at 100


def test_partial_window_is_not_scored():
    p = path([100] * 10)
    assert evaluate_call(call(D0), p, horizon_days=30) is None


def test_target_hit_and_returns():
    p = path([100, 100, 110, 130, 160, 120, 120, 120])
    o = evaluate_call(call(D0, target=150), p, horizon_days=5)
    assert o.target_hit and o.realized_return_pct == pytest.approx(20.0)  # entry day1=100 -> day6=120


def test_call_without_price_is_skipped():
    c = BrokerCall("X", "B", Rating.BUY, 150.0, None, D0)
    assert evaluate_call(c, path([100] * 10), 3) is None


def test_spearman_known_values():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert spearman([1, 2, 3, 4], [1, 1, 1, 1]) == 0.0
    assert spearman([1, 2, 2, 4], [1, 2, 3, 4]) == pytest.approx(0.9486833, rel=1e-5)


def test_permutation_p_value_extremes():
    x = list(range(30))
    assert permutation_p_value(x, x, 500) < 0.01
    assert permutation_p_value(x, x[::-1], 500) > 0.95


def test_harness_finds_planted_edge():
    calls, paths = simulate_world(edge=1.0, seed=3)
    s = summarize(evaluate(calls, paths, 180))
    assert s.rank_ic > 0.2 and s.p_value < 0.01
    low, _, high = s.bucket_realized_pct
    assert high > low


def test_harness_stays_quiet_without_edge():
    # Across many seeds, "edge=0" must rarely look significant (nominal 5% level).
    false_positives = 0
    runs = 30
    for seed in range(runs):
        calls, paths = simulate_world(edge=0.0, seed=100 + seed)
        s = summarize(evaluate(calls, paths, 180), n_perm=300, seed=seed)
        false_positives += s.p_value < 0.05
    assert false_positives <= 6  # allow slack over the expected ~1.5


def test_overstatement_when_targets_are_pure_noise():
    calls, paths = simulate_world(edge=0.0, seed=5)
    s = summarize(evaluate(calls, paths, 180))
    assert s.overstatement_pct > 20  # mean implied ~42% vs ~0% realized
    assert s.hit_rate < 0.5


def test_too_few_calls_refuses_to_summarize():
    with pytest.raises(ValueError):
        summarize([])


def test_cli_extract_and_validate_end_to_end(tmp_path, capsys):
    from quant_agents.cli import main

    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "ABC__Broker_X__2026-01-01.txt").write_text("Rating: BUY\nCMP: 100\nTarget Price: 150")
    (reports / "BAD__Broker_X__2026-01-01.txt").write_text("nothing useful")
    (reports / "oops.txt").write_text("x")
    assert main(["extract", str(reports)]) == 0
    out = capsys.readouterr()
    assert "| ABC | Broker X | buy | target 150 | cmp 100 |" in out.out
    assert "REFUSED BAD__Broker_X__2026-01-01.txt" in out.err and "SKIP oops.txt" in out.err

    calls, paths = simulate_world(n_stocks=30, edge=1.0, seed=2)
    cf, pf = tmp_path / "calls.txt", tmp_path / "px.csv"
    cf.write_text("\n".join(
        f"{c.published} | {c.ticker} | {c.broker} | buy | target {c.target:.2f} | cmp {c.price:.2f}" for c in calls))
    pf.write_text("ticker,date,close\n" + "\n".join(
        f"{t},{d},{px}" for t, pth in paths.items() for d, px in pth))
    assert main(["validate", str(cf), str(pf), "--horizon", "180"]) == 0
    text = capsys.readouterr().out
    assert "scored=30" in text and "verdict:" in text
