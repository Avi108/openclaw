"""Event playbook: catalog integrity, judgment edge cases, measurement edge cases and the CLI."""
import math
from datetime import date, timedelta

import pytest
from hypothesis import given, settings, strategies as st

from quant_agents import playbook as pb
from quant_agents.cli import main

EP = {e.id: e for e in pb.CATALOG}


# ---- catalog integrity: a typo here would silently corrupt every judgment ----

def test_catalog_ids_are_unique_and_dates_ordered():
    assert len({e.id for e in pb.CATALOG}) == len(pb.CATALOG)
    assert all(e.start <= e.end for e in pb.CATALOG)


def test_catalog_values_are_from_closed_sets():
    assert all(e.confidence in pb.CONF_WEIGHT for e in pb.CATALOG)
    assert all(v in pb.SCORE for e in pb.CATALOG for v in e.effects.values())
    assert all(e.region in {"india", "asia", "global"} for e in pb.CATALOG)
    assert all(e.effects and e.aftermath and e.name for e in pb.CATALOG)


def test_catalog_sector_names_are_canonical_not_aliases():
    for e in pb.CATALOG:
        for s in e.effects:
            assert pb.sector_key(s) == s, f"{e.id}: {s} is an alias or not normalized"


def test_every_requested_scenario_family_is_covered():
    cats = set(pb.categories())
    assert {"war", "pandemic", "drought_weak_monsoon", "flood_cyclone", "earthquake_tsunami", "heat_cold_weather",
            "financial_crisis", "oil_shock", "policy_shock", "election", "trade_war"} <= cats


def test_covid_episode_has_the_well_known_dates():
    assert EP["covid-2020"].start == date(2020, 2, 20)
    assert EP["gfc-2008"].start == date(2008, 9, 15)
    assert EP["ukraine-2022"].start == date(2022, 2, 24)


# ---- sector names ----

@pytest.mark.parametrize("raw,want", [("IT", "it"), (" Banking ", "banks"), ("oil & gas", "oil_gas"), ("Real-Estate", "real_estate"),
                                      ("Defence", "defense"), ("FMCG", "fmcg"), ("weird sector", "weird_sector"), ("", "")])
def test_sector_key_normalizes(raw, want):
    assert pb.sector_key(raw) == want


# ---- judgment ----

def test_unknown_sector_is_not_inferred():
    j = pb.judge(["quantum_widgets"], ["war"])[0]
    assert j.stance == "no precedent in catalog" and j.n_episodes == 0 and j.score == 0.0


def test_unknown_category_is_rejected_with_choices():
    with pytest.raises(ValueError, match="choose from"):
        pb.judge(["it"], ["alien_invasion"])


def test_no_category_is_rejected():
    with pytest.raises(ValueError, match="at least one"):
        pb.judge(["it"], [])


def test_empty_sector_list_gives_empty_result():
    assert pb.judge([], ["war"]) == []


def test_category_spelling_variants_are_accepted():
    a = pb.judge(["banks"], ["Drought Weak-Monsoon"])[0]
    b = pb.judge(["banks"], ["drought_weak_monsoon"])[0]
    assert a == b and a.n_episodes > 0


def test_duplicate_categories_do_not_double_count():
    assert pb.judge(["defense"], ["war", "war"])[0].n_episodes == pb.judge(["defense"], ["war"])[0].n_episodes


def test_aliases_hit_the_same_judgment():
    assert pb.judge(["Defence"], ["war"])[0] == pb.judge(["defense"], ["war"])[0]


def test_thin_samples_are_flagged_as_anecdotes():
    j = pb.judge(["hotels_leisure"], ["pandemic"])[0]
    assert j.n_episodes < 3 and "anecdote" in j.caveat


def test_disagreeing_episodes_say_the_setup_mattered():
    eps = [pb.Episode(f"x{i}", "x", "war", date(2000, 1, 1), date(2000, 2, 1), "global", "high", {"it": v}, "x")
           for i, v in enumerate(("win", "win", "flop", "flop"))]
    j = pb.judge_sector("it", eps)
    assert j.stance == "inconsistent" and j.score == 0.0 and "disagree" in j.caveat


def test_low_confidence_counts_less_than_high():
    hi = pb.Episode("h", "h", "war", date(2000, 1, 1), date(2000, 2, 1), "global", "high", {"it": "win"}, "x")
    lo = pb.Episode("l", "l", "war", date(2000, 1, 1), date(2000, 2, 1), "global", "low", {"it": "flop"}, "x")
    assert pb.judge_sector("it", [hi, lo, hi]).score == pytest.approx((2 - 0.3) / 2.3)
    assert pb.judge_sector("it", [lo, hi, lo]).score == pytest.approx((1 - 0.6) / 1.6)   # two low-confidence flops < one high win


def test_threshold_boundary_is_exact():
    eps = [pb.Episode(f"x{i}", "x", "war", date(2000, 1, 1), date(2000, 2, 1), "global", "high", {"it": v}, "x")
           for i, v in enumerate(("win", "mixed", "mixed"))]   # score = 1/3 > 0.3
    assert pb.judge_sector("it", eps).stance == "tended to hold up"
    assert pb.judge_sector("it", eps, threshold=1 / 3 + 1e-9).stance == "inconsistent"


def test_region_filter_keeps_global_episodes_and_drops_other_regions():
    india = {e.id for e in pb.episodes("war", "india")}
    assert "kargil-1999" in india and "ukraine-2022" in india and not any(EP[i].region == "asia" for i in india)


def test_pooling_two_simultaneous_events_uses_both():
    war = pb.judge(["agri_rural"], ["war"])[0]
    both = pb.judge(["agri_rural"], ["war", "drought_weak_monsoon"])[0]
    assert war.n_episodes == 0 and both.n_episodes > 0


def test_covid_style_pandemic_hits_travel_and_helps_pharma():
    j = {x.sector: x for x in pb.judge(["airlines_travel", "pharma"], ["pandemic"])}
    assert j["airlines_travel"].score < 0 < j["pharma"].score


# ---- measuring real data ----

def series(start, rets):
    return {start + timedelta(days=i): r for i, r in enumerate(rets)}


def test_measure_compounds_inside_window_and_after():
    ep = EP["covid-2020"]
    s = {**series(ep.start, [0.1, -0.1]), **series(ep.end + timedelta(days=1), [0.05])}
    m = pb.measure_episode({"IT": s}, ep, after_days=60)["it"]
    assert m["during_pct"] == pytest.approx((1.1 * 0.9 - 1) * 100) and m["after_pct"] == pytest.approx(5.0)


def test_measure_window_edges_are_inclusive_and_after_starts_next_day():
    ep = EP["covid-2020"]
    s = {ep.start - timedelta(days=1): 0.5, ep.start: 0.01, ep.end: 0.02, ep.end + timedelta(days=1): 0.03,
         ep.end + timedelta(days=61): 0.9}
    m = pb.measure_episode({"x": s}, ep, after_days=60)["x"]
    assert m["during_pct"] == pytest.approx((1.01 * 1.02 - 1) * 100) and m["after_pct"] == pytest.approx(3.0)


def test_measure_reports_gaps_as_none_not_zero():
    ep = EP["covid-2020"]
    m = pb.measure_episode({"a": {date(1999, 1, 1): 0.1}, "b": {}}, ep)
    assert m["a"] == {"during_pct": None, "after_pct": None} and m["b"]["during_pct"] is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, -2.0])
def test_measure_rejects_impossible_returns(bad):
    ep = EP["covid-2020"]
    with pytest.raises(ValueError, match="finite"):
        pb.measure_episode({"x": {ep.start: bad}}, ep)


def test_winners_and_flops_ordering_ties_and_missing():
    m = {"a": {"during_pct": 5.0}, "b": {"during_pct": -3.0}, "c": {"during_pct": 5.0}, "d": {"during_pct": None}}
    best, worst = pb.winners_and_flops(m)
    assert best == ["a", "c", "b"] and worst == ["b", "c", "a"] and "d" not in best
    assert pb.winners_and_flops({}) == ([], [])


# ---- properties ----

@settings(max_examples=100, deadline=None)
@given(st.lists(st.sampled_from(pb.categories()), min_size=1, max_size=4), st.lists(st.sampled_from(pb.known_sectors()), min_size=1, max_size=6))
def test_judgment_invariants(cats, secs):
    for j in pb.judge(secs, cats):
        assert -1.0 <= j.score <= 1.0 and j.wins + j.flops + j.mixed == j.n_episodes
        assert (j.n_episodes == 0) == (j.stance == "no precedent in catalog")


@settings(max_examples=100, deadline=None)
@given(st.lists(st.floats(-0.5, 0.5), min_size=1, max_size=40))
def test_measure_equals_manual_compounding(rets):
    ep = EP["gfc-2008"]
    m = pb.measure_episode({"s": series(ep.start, rets)}, ep, after_days=0)["s"]
    inside = rets[: (ep.end - ep.start).days + 1]
    assert m["during_pct"] == pytest.approx((math.prod(1 + r for r in inside) - 1) * 100)


# ---- CLI edge cases ----

def run(capsys, *argv):
    rc = main(["playbook", *map(str, argv)])
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_cli_judges_sectors(capsys):
    rc, out, _ = run(capsys, "--events", "pandemic", "--sectors", "pharma", "airlines")
    assert rc == 0 and "pharma" in out and "airlines_travel" in out and "Not a forecast" in out


def test_cli_list_and_default_list(capsys):
    rc, out, _ = run(capsys, "--list")
    assert rc == 0 and "covid-2020" in out and "sectors in catalog" in out
    rc2, out2, _ = run(capsys)
    assert out2 == out


def test_cli_events_without_sectors_is_a_clean_error(capsys):
    rc, _, err = run(capsys, "--events", "war")
    assert rc == 2 and "--sectors" in err


def test_cli_unknown_event_is_a_clean_error(capsys):
    rc, _, err = run(capsys, "--events", "zombies", "--sectors", "it")
    assert rc == 2 and "unknown event category" in err


def test_cli_measure_end_to_end(capsys, tmp_path):
    ep = EP["covid-2020"]
    f = tmp_path / "s.csv"
    rows = ["date,sector,return"]
    for i in range(10):
        d = (ep.start + timedelta(days=i)).isoformat()
        rows += [f"{d},pharma,0.01", f"{d},airlines,-0.02"]
    f.write_text("\n".join(rows))
    rc, out, _ = run(capsys, "--measure", f, "--episode", "covid-2020")
    assert rc == 0 and out.index("pharma") < out.index("airlines") and "winners: pharma" in out and "flops: airlines" in out


def test_cli_measure_bad_inputs(capsys, tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("date,sector,return\n2020-03-01,pharma,abc\n")
    assert run(capsys, "--measure", f)[0] == 2                                  # non-numeric return
    f.write_text("date,sector,return\nnot-a-date,pharma,0.1\n")
    assert run(capsys, "--measure", f)[0] == 2                                  # bad date
    f.write_text("date,sector\n2020-03-01,pharma\n")
    assert run(capsys, "--measure", f)[0] == 2                                  # missing column
    f.write_text("date,sector,return\n")
    rc, out, _ = run(capsys, "--measure", f)                                    # header only: no crash, nothing claimed
    assert rc == 0 and "winners: none" in out
    assert run(capsys, "--measure", tmp_path / "missing.csv")[0] == 2
    f.write_text("date,sector,return\n2020-03-01,pharma,0.1\n")
    assert run(capsys, "--measure", f, "--episode", "nope")[0] == 2
