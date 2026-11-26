import json
from datetime import date, timedelta

import pytest

from quant_agents.corporate import Action, apply_actions, unadjusted_jumps
from quant_agents.markets import (HKEX, KRX, bars_from_rows, day_from_filename, decode_bytes, load_dir, load_text, normalize_symbol, parse_number, records_from_json, resolve_columns,
                                  verify_columns)

D = date(2026, 10, 15)


# ---------------- primitives ----------------
def test_parse_number_handles_commas_and_missing_markers():
    assert parse_number("1,234.50") == 1234.5 and parse_number(77000) == 77000.0
    assert all(parse_number(x) is None for x in ("", "-", "N/A", "n.a.", None, "abc"))
    assert parse_number("0") == 0.0  # a real zero stays zero; missing is None


def test_symbol_padding_per_market():
    assert normalize_symbol("700", HKEX) == "00700" and normalize_symbol("0005", HKEX) == "00005"
    assert normalize_symbol("5930", KRX) == "005930" and normalize_symbol("0001a0", KRX) == "0001A0"  # alphanumeric kept
    assert normalize_symbol("RELIANCE", HKEX) == "RELIANCE"


def test_day_from_filename():
    assert day_from_filename("data_20261015.csv") == D and day_from_filename("krx-2026-10-15.csv") == D
    assert day_from_filename("nodate.csv") is None and day_from_filename("x_20261345.csv") is None


def test_cp949_decoding_and_refusal(tmp_path):
    korean = "종목코드,종목명,종가,대비,시가,고가,저가,거래량,거래대금\n5930,삼성전자,77000,-500,77500,78000,76800,12000000,925000000000\n"
    assert "삼성전자" in decode_bytes(korean.encode("cp949"), KRX)
    assert "삼성전자" in decode_bytes(korean.encode("utf-8-sig"), KRX)
    with pytest.raises(ValueError, match="could not decode"):
        decode_bytes("종가".encode("utf-16"), HKEX)  # HKEX spec only allows utf-8


# ---------------- column mapping ----------------
def test_resolve_aliases_overrides_and_bad_override():
    cols = ["Stock Code", "Closing Price", "Prev. Close", "Weird Vol"]
    m = resolve_columns(cols, HKEX)
    assert m == {"symbol": "Stock Code", "close": "Closing Price", "prev_close": "Prev. Close"}
    assert resolve_columns(cols, HKEX, {"volume": "weird vol"})["volume"] == "Weird Vol"
    with pytest.raises(ValueError, match="not a column"):
        resolve_columns(cols, HKEX, {"volume": "nope"})


def test_verify_reports_missing_unmapped_and_prev_close_source():
    r = verify_columns(["종목코드", "종가", "대비", "시장구분"], KRX)
    assert r.usable and r.prev_close_source == "close minus published change" and "시장구분" in r.unmapped_columns
    r2 = verify_columns(["Code", "Price"], HKEX)
    assert not r2.usable and r2.missing_required == ("close",)
    assert "UNADJUSTED" in verify_columns(["Code", "Close"], HKEX).prev_close_source


def test_unmappable_file_refused_with_helpful_message():
    with pytest.raises(ValueError, match="cannot map required fields.*Pass overrides"):
        load_text("A,B\n1,2\n", HKEX)


# ---------------- Korea ----------------
def test_krx_file_derives_prev_close_from_change_and_uses_filename_day(tmp_path):
    csv = ("종목코드,종목명,종가,대비,등락률,시가,고가,저가,거래량,거래대금\n"
           "5930,삼성전자,\"77,000\",\"-500\",-0.65,\"77,500\",\"78,000\",\"76,800\",12000000,925000000000\n"
           "660,SK하이닉스,-,0,0,0,0,0,0,0\n")
    (tmp_path / "krx_20261015.csv").write_bytes(csv.encode("cp949"))
    bars, problems = load_dir(tmp_path, KRX)
    assert len(bars) == 1 and bars[0].symbol == "005930" and bars[0].day == D
    b = bars[0]
    assert (b.close, b.prev_close, b.currency, b.exchange) == (77000.0, 77500.0, "KRW", "KRX")
    assert b.prev_close_basis == "derived_from_change" and any("no usable close" in p for p in problems)


def test_krx_open_api_style_json_records():
    payload = {"OutBlock_1": [{"BAS_DD": "20261015", "ISU_CD": "005930", "ISU_NM": "삼성전자", "TDD_CLSPRC": "77000",
                              "CMPPREVDD_PRC": "-500", "TDD_OPNPRC": "77500", "TDD_HGPRC": "78000", "TDD_LWPRC": "76800",
                              "ACC_TRDVOL": "12000000", "ACC_TRDVAL": "925000000000"}]}
    recs = records_from_json(json.dumps(payload))
    bars, problems, rep = bars_from_rows(recs, KRX)
    assert bars[0].day == D and bars[0].prev_close == 77500.0 and not problems
    with pytest.raises(ValueError):
        records_from_json({"x": 1})


# ---------------- Hong Kong ----------------
def test_hk_rows_with_na_and_raw_prev_close_across_days(tmp_path):
    for i, (d, px) in enumerate([("20261014", "300.00"), ("20261015", "310.00")]):
        (tmp_path / f"hk_{d}.csv").write_text(
            "Stock Code,Name of Securities,Closing Price,Shares Traded\n"
            f"700,TENCENT,{px},\"20,000,000\"\n5,HSBC,N/A,0\n", encoding="utf-8")
    bars, problems = load_dir(tmp_path, HKEX)
    assert [(b.symbol, b.day, b.close) for b in bars] == [("00700", D, 310.0)]  # first day has no prior close
    assert bars[0].prev_close == 300.0 and bars[0].prev_close_basis == "raw_previous_close" and bars[0].currency == "HKD"
    assert any("first observation" in p for p in problems) and any("no usable close" in p for p in problems)


def test_row_order_does_not_matter_for_raw_prev_close():
    rows = [{"Code": "700", "Close": "310", "Date": "2026-10-15"}, {"Code": "700", "Close": "300", "Date": "2026-10-14"}]
    bars, _, _ = bars_from_rows(rows, HKEX)
    assert len(bars) == 1 and bars[0].prev_close == 300.0


def test_missing_date_without_default_is_a_problem_not_a_guess():
    bars, problems, _ = bars_from_rows([{"Code": "1", "Close": "10", "Prev. Close": "9"}], HKEX)
    assert bars == [] and "no date column" in problems[0]
    ok, _, _ = bars_from_rows([{"Code": "1", "Close": "10", "Prev. Close": "9"}], HKEX, default_day=D)
    assert ok[0].day == D and ok[0].prev_close_basis == "exchange"


# ---------------- corporate actions ----------------
def split_bars():
    rows = [{"Code": "100", "Close": str(c), "Date": f"2026-10-{10 + i}"} for i, c in enumerate([100, 101, 20.4, 20.8])]
    return bars_from_rows(rows, HKEX)[0]  # a 1:5 split on the third day: 101 -> 20.4 looks like -80%


def test_split_shows_as_jump_then_actions_fix_returns():
    bars = split_bars()
    jumps = unadjusted_jumps(bars)
    assert len(jumps) == 1 and jumps[0].change_pct < -75
    fixed = apply_actions(bars, [Action("00100", date(2026, 10, 12), 5.0)])
    ex = next(b for b in fixed if b.day == date(2026, 10, 12))
    assert ex.prev_close == pytest.approx(20.2) and abs(ex.close / ex.prev_close - 1) < 0.02
    assert ex.prev_close_basis == "adjusted_by_actions" and unadjusted_jumps(fixed) == []
    with pytest.raises(ValueError):
        apply_actions(bars, [Action("00100", date(2026, 10, 12), 0)])


def test_krx_price_limit_tightens_jump_threshold():
    rows = [{"종목코드": "1", "종가": str(c), "일자": f"2026-10-{10 + i}"} for i, c in enumerate([1000, 1300, 1700])]
    bars = bars_from_rows(rows, KRX)[0]
    assert [round(j.change_pct) for j in unadjusted_jumps(bars, threshold_pct=50, price_limit_pct=30)] == [31]


def test_exchange_adjusted_bars_are_never_flagged():
    from quant_agents.nse import Bar

    b = Bar("X", D, 1, 1, 1, 20, 20, 1)  # NSE-style: prev close already adjusted
    assert unadjusted_jumps([b]) == []


# ---------------- CLI and scenario compatibility ----------------
def test_cli_markets_verify_and_scenarios_on_korean_folder(tmp_path, capsys):
    from quant_agents.cli import main

    base = date(2026, 1, 1)
    for i in range(80):
        d = base + timedelta(days=i)
        rows = "\n".join(f"{t},{px:.0f},{ch:.0f},{px},{px},{px},1000000,{px * 1000000:.0f}"
                         for t, px, ch in (("5930", 1000 + i, 1), ("660", 2000 + 2 * i, 2), ("35420", 500 + i, 1)))
        (tmp_path / f"krx_{d:%Y%m%d}.csv").write_bytes(
            ("종목코드,종가,대비,시가,고가,저가,거래량,거래대금\n" + rows).encode("cp949"))
    first = sorted(tmp_path.glob("*.csv"))[0]
    assert main(["markets-verify", str(first), "--market", "krx", "--day", "2026-01-01"]) == 0
    out = capsys.readouterr().out
    assert "3 rows loaded" in out and "close minus published change" in out
    assert main(["scenarios", str(tmp_path), "--market", "krx", "--weights", "005930=0.5,000660=0.3,035420=0.2",
                 "--horizon", "20"]) == 0
    assert "VaR95" in capsys.readouterr().out


def test_avg_traded_value_is_currency_neutral_and_adv_crore_is_inr_only():
    from quant_agents.universe import adv_from_bars, avg_traded_value

    rows = [{"종목코드": "5930", "종가": "1000", "일자": f"2026-10-{i + 1:02d}", "대비": "0", "거래량": "2000000"} for i in range(5)]
    bars = bars_from_rows(rows, KRX)[0]
    assert avg_traded_value(bars)["005930"] == pytest.approx(2_000_000_000)  # KRW 2bn a day, no crore scaling
    assert adv_from_bars(bars)["005930"] == pytest.approx(200.0)             # the INR-crore helper would mislabel this
