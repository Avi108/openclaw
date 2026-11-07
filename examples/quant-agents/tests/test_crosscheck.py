from datetime import date

import pytest

from quant_agents.crosscheck import agreed_close, cross_check
from quant_agents.nse import Bar, bars_from_csv

D = date(2026, 10, 9)


def bar(sym, isin, close, prev=100.0, ex="NSE", day=D):
    return Bar(sym, day, close, close, close, close, prev, 1000.0, isin=isin, exchange=ex)


def test_legacy_bse_file_uses_default_day_and_accepts_groups():
    text = "SC_CODE,SC_NAME,SC_GROUP,OPEN,HIGH,LOW,CLOSE,PREVCLOSE,NO_OF_SHRS\n500325,RELIANCE,A,10,11,9,10.5,10,1000\n500002,ABB,B,10,11,9,10.5,10,1000"
    bars, problems = bars_from_csv(text, series=None, exchange="BSE", default_day=D)
    assert len(bars) == 2 and bars[0].day == D and bars[0].exchange == "BSE" and bars[0].isin is None and not problems
    missing, problems = bars_from_csv(text, series=None, exchange="BSE")
    assert missing == [] and "no date column" in problems[0]


def test_udiff_row_keeps_isin():
    text = "TradDt,TckrSymb,SctySrs,ISIN,OpnPric,HghPric,LwPric,ClsPric,PrvsClsgPric,TtlTradgVol\n2026-10-09,abc,A,ine000a01011,1,2,1,1.5,1.2,50"
    bars, _ = bars_from_csv(text, series=None, exchange="BSE")
    assert bars[0].isin == "INE000A01011" and bars[0].exchange == "BSE"


def test_agreeing_exchanges_are_clean():
    nse = [bar("ABC", "INE1", 101.0), bar("XYZ", "INE2", 50.0, prev=49.0)]
    bse = [bar("500001", "INE1", 101.2, ex="BSE"), bar("500002", "INE2", 49.9, prev=49.0, ex="BSE")]
    r = cross_check(nse, bse)
    assert r.matched == 2 and r.clean and r.median_close_diff_pct < 0.5


def test_disagreement_is_flagged_with_both_symbols():
    nse = [bar("ABC", "INE1", 101.0)]
    bse = [bar("500001", "INE1", 115.0, ex="BSE")]  # ~14% apart
    r = cross_check(nse, bse)
    assert not r.clean and r.mismatches[0].nse_symbol == "ABC" and r.mismatches[0].bse_symbol == "500001"
    assert r.mismatches[0].close_diff_pct > 10


def test_corporate_action_mismatch_caught_by_return_check():
    # Same close, but one exchange failed to adjust prev_close for a 1:2 split -> returns disagree.
    nse = [bar("ABC", "INE1", 50.0, prev=50.0)]
    bse = [bar("500001", "INE1", 50.0, prev=100.0, ex="BSE")]
    r = cross_check(nse, bse)
    assert r.mismatches and r.mismatches[0].return_diff_pp > 40 and r.mismatches[0].close_diff_pct == 0


def test_unmatched_and_missing_isin_are_reported_not_joined_by_name():
    nse = [bar("ABC", "INE1", 100.0), bar("NOISIN", None, 100.0)]
    bse = [bar("ABC", None, 100.0, ex="BSE"), bar("ONLYBSE", "INE9", 100.0, ex="BSE")]
    r = cross_check(nse, bse)
    assert r.matched == 0 and not r.clean  # same name, but no ISIN: never matched
    assert r.no_isin == 2 and r.unmatched_nse == 1 and r.unmatched_bse == 1


def test_agreed_close_refuses_to_pick_a_side():
    assert agreed_close(bar("A", "I", 100.0), bar("B", "I", 100.4, ex="BSE")) == pytest.approx(100.2)
    assert agreed_close(bar("A", "I", 100.0), bar("B", "I", 110.0, ex="BSE")) is None


def test_cli_crosscheck(tmp_path, capsys):
    from quant_agents.cli import main

    n, b = tmp_path / "nse", tmp_path / "bse"
    n.mkdir(); b.mkdir()
    hdr = "TradDt,TckrSymb,SctySrs,ISIN,OpnPric,HghPric,LwPric,ClsPric,PrvsClsgPric,TtlTradgVol\n"
    (n / "a.csv").write_text(hdr + "2026-10-09,ABC,EQ,INE1,100,101,99,100.5,100,10\n2026-10-09,XYZ,EQ,INE2,50,51,49,60,50,10")
    (b / "a.csv").write_text(hdr + "2026-10-09,500001,A,INE1,100,101,99,100.6,100,10\n2026-10-09,500002,A,INE2,50,51,49,50.2,50,10")
    assert main(["crosscheck", str(n), str(b)]) == 1  # XYZ differs by ~20%
    out = capsys.readouterr().out
    assert "matched=2" in out and "mismatches beyond tolerance: 1" in out and "INE2" in out
