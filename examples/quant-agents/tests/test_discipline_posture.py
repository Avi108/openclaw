import math
from datetime import date, timedelta

import pytest

from quant_agents.discipline import Closed, DisciplinePolicy, Journal, circuit_breaker, pre_trade_gate
from quant_agents.ladder import average_cost, entry_ladder
from quant_agents.liquidity import amihud_per_crore, sqrt_impact_pct
from quant_agents.nse import Bar
from quant_agents.papertrade import CostModel, PaperLedger, post_mortem, round_trips
from quant_agents.posture import exposure_ceiling
from quant_agents.scenarios import correlation_spike_vol, replay_window
from quant_agents.thesis import Invalidation, Thesis

T = date(2026, 10, 16)


def thesis(**kw):
    base = dict(ticker="A", opened=T, statement="s", entry_price=100, target_price=150, review_by=T + timedelta(days=30),
                invalidations=(Invalidation("m", "<", 1),), bear_case="b")
    base.update(kw)
    return Thesis(**base)


def closed(*pairs):
    return [Closed(T - timedelta(days=d), p) for d, p in pairs]


# ---------------- gate ----------------
def test_gate_allows_a_complete_plan_with_clean_record():
    j = Journal()
    d = pre_trade_gate("A", thesis(), closed((10, 500), (9, -200)), 100_000, T, journal=j)
    assert d.allowed and j.entries[0]["allowed"] is True


def test_gate_blocks_planless_in_every_form():
    for th, frag in ((None, "no written thesis"), (thesis(statement=" "), "no written thesis"),
                     (thesis(bear_case=""), "no bear case"), (thesis(invalidations=()), "no invalidation")):
        d = pre_trade_gate("A", th, [], 100_000, T)
        assert not d.allowed and frag in d.reasons[0]


def test_gate_blocks_revenge_trading_and_regime_and_journals_the_block():
    j = Journal()
    d = pre_trade_gate("A", thesis(), closed((0, -1000)), 100_000, T, regime_blocked=True, journal=j)
    assert not d.allowed and "cooling off" in " ".join(d.reasons) and "regime" in " ".join(d.reasons)
    assert j.entries[0]["allowed"] is False and j.entries[0]["ticker"] == "A"
    assert pre_trade_gate("A", thesis(), closed((2, -1000)), 100_000, T).allowed  # two days later is fine


def test_breaker_losing_streak_then_cooldown_ends():
    streak = closed((4, -100), (3, -100), (1, -100))
    assert not circuit_breaker(streak, 100_000, T).allowed
    assert circuit_breaker(streak, 100_000, T + timedelta(days=2)).allowed  # cooldown over, week/month not breached
    assert circuit_breaker(closed((4, -100), (3, 50), (1, -100)), 100_000, T).allowed  # streak broken by a win


def test_breaker_weekly_and_monthly_limits_use_net_loss():
    assert not circuit_breaker(closed((2, -6000)), 100_000, T).allowed                 # 6% in a week
    assert circuit_breaker(closed((2, -6000), (3, 5000)), 100_000, T).allowed          # net 1%
    r = circuit_breaker(closed((20, -11_000)), 100_000, T)
    assert not r.allowed and "monthly" in r.reasons[0]
    assert circuit_breaker(closed((40, -50_000)), 100_000, T).allowed                  # outside both windows


# ---------------- posture ----------------
def test_posture_ceiling_adjustments_clamp_and_missing_data_noted():
    assert exposure_ceiling(0.5, 70).ceiling_pct == 90.0  # 60 + 20 + 10
    p = exposure_ceiling(-0.6, 20)
    assert p.ceiling_pct == 10.0 and not p.entries_allowed  # 60 - 30 - 20 = 10, floor
    n = exposure_ceiling(None, None)
    assert n.ceiling_pct == 60.0 and n.entries_allowed and sum("unavailable" in r for r in n.reasons) == 2


def test_segmented_respects_exposure_ceiling():
    from quant_agents.risk import PortfolioState, RiskPolicy
    from quant_agents.segmented import run_segmented
    from quant_agents.universe import Instrument, Segment

    u = {f"L{i}": Instrument(f"L{i}", Segment.LARGE, f"s{i}") for i in range(5)}
    calls = "\n".join(f"2026-10-14 | L{i} | B{b} | buy | target 150 | cmp 100" for i in range(5) for b in range(2))
    st = PortfolioState(cash=100_000, positions={}, peak_value=100_000)
    r = run_segmented(calls, st, RiskPolicy(max_orders_per_day=20, max_orders_per_week=50), u, T, max_invested_pct=12.0)
    assert sum(c.amount for c in r.proposals) <= 12_000 + 1e-6 and any("exposure cap" in x.reasons[0] for x in r.research)


# ---------------- stress and liquidity ----------------
def test_replay_window_and_gap_is_not_calm():
    s = {T + timedelta(days=i): r for i, r in enumerate([0.01, -0.05, -0.05, 0.02])}
    ret, n = replay_window(s, T + timedelta(days=1), T + timedelta(days=2))
    assert n == 2 and ret == pytest.approx((0.95 * 0.95 - 1) * 100)
    assert replay_window(s, T + timedelta(days=100), T + timedelta(days=110)) == (0.0, 0)


def test_correlation_spike_known_values():
    w, v = {"A": 0.5, "B": 0.5}, {"A": 0.02, "B": 0.02}
    assert correlation_spike_vol(w, v, 1.0) == pytest.approx(2.0)   # no diversification
    assert correlation_spike_vol(w, v, 0.0) == pytest.approx(math.sqrt(0.5 * 0.0004) * 100)
    assert correlation_spike_vol(w, v, 0.8) > correlation_spike_vol(w, v, 0.2)
    with pytest.raises(ValueError):
        correlation_spike_vol(w, v, 1.5)


def test_sqrt_impact_and_amihud():
    assert sqrt_impact_pct(2.0, 100, 10_000) == pytest.approx(0.2)
    assert sqrt_impact_pct(2.0, 400, 10_000) == pytest.approx(0.4)  # 4x size, 2x impact
    with pytest.raises(ValueError):
        sqrt_impact_pct(2.0, 1, 0)
    d0 = date(2026, 1, 1)
    def mk(vol): return [Bar("X", d0 + timedelta(days=i), 100, 100, 100, 101 if i % 2 else 100, 100 if i % 2 else 101, vol) for i in range(40)]
    assert amihud_per_crore(mk(10_000)) > amihud_per_crore(mk(1_000_000))
    assert amihud_per_crore(mk(1000)[:10]) is None


# ---------------- ladder ----------------
def test_ladder_prices_amounts_and_guards():
    lad = entry_ladder(30_000, 100, tranches=3, step_pct=5)
    assert lad == [(100.0, 100), (95.0, 105), (90.0, 111)]
    assert average_cost(lad) == pytest.approx((100 * 100 + 95 * 105 + 90 * 111) / 316)
    assert all(q * p <= 10_000 for p, q in lad)  # never rounds a rung up past its share of the money
    for bad in (dict(total_amount=0), dict(step_pct=40), dict(tranches=0)):
        with pytest.raises(ValueError):
            entry_ladder(**{**dict(total_amount=1000, price=100, tranches=3, step_pct=4), **bad})


# ---------------- post-mortem ----------------
def path(prices, d0=date(2026, 1, 1)):
    return [(d0 + timedelta(days=i), p) for i, p in enumerate(prices)]


def test_round_trips_and_post_mortem_stats():
    L = PaperLedger(100_000, CostModel(0, 0, 0, 0, 0, 0))
    win, lose = path([100, 100, 120, 120, 120]), path([100, 100, 80, 80, 80])
    L.enter("W", 10_000, date(2026, 1, 1), win)
    L.exit("W", date(2026, 1, 2), win)
    L.enter("L", 10_000, date(2026, 1, 1), lose)
    L.exit("L", date(2026, 1, 2), lose)
    L.enter("OPEN", 5_000, date(2026, 1, 1), win)
    trips = round_trips(L)
    assert [t.ticker for t in trips] == ["W", "L"]  # open position excluded
    pm = post_mortem(trips)
    assert pm.n == 2 and pm.win_rate == 0.5 and pm.avg_win_pct == pytest.approx(20.0) and pm.avg_loss_pct == pytest.approx(-20.0)
    assert pm.expectancy_pct == pytest.approx(0.0) and pm.profit_factor == pytest.approx(1.0) and pm.worst.ticker == "L"
    with pytest.raises(ValueError):
        post_mortem([])
    assert post_mortem([trips[0]]).profit_factor is None  # no losses


def test_cli_paper_review(tmp_path, capsys):
    from quant_agents.cli import main

    L = PaperLedger(100_000, CostModel(0, 0, 0, 0, 0, 0))
    p = path([100, 100, 120, 120])
    L.enter("W", 10_000, date(2026, 1, 1), p)
    L.exit("W", date(2026, 1, 2), p)
    f = tmp_path / "l.json"
    L.save(f)
    assert main(["paper-review", str(f)]) == 0
    out = capsys.readouterr().out
    assert "closed trades=1" in out and "win rate 100%" in out and "profit factor n/a" in out
