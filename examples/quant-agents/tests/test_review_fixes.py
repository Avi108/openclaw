"""Regression tests for defects reported by code review (symbol, headline flags, timestamps, skill coverage, env, orders, CLI flags, precision)."""
import json
from datetime import date, datetime, timezone

import pytest

from quant_agents import skillwatch
from quant_agents.cli import main
from quant_agents.cues import build_briefing, load_cues
from quant_agents.nse import bars_from_records
from quant_agents.risk import PortfolioState, RiskPolicy
from quant_agents.workflow import run

ROW = {"OPEN": 1, "HIGH": 2, "LOW": 1, "CLOSE": 2, "PREVCLOSE": 1, "TOTTRDQTY": 5, "TIMESTAMP": "2026-01-01"}


@pytest.mark.parametrize("extra", [{}, {"SYMBOL": ""}, {"SYMBOL": "   "}])
def test_row_without_a_symbol_is_a_problem_not_a_bar_named_none(extra):
    bars, problems = bars_from_records([{**ROW, **extra}])
    assert bars == [] and problems == ["row 0: missing symbol"]


def test_row_with_a_symbol_still_loads():
    bars, problems = bars_from_records([{**ROW, "SYMBOL": " abc "}])
    assert [b.symbol for b in bars] == ["ABC"] and not problems


@pytest.mark.parametrize("headline,flags", [
    ("Infosys wins software award", ()), ("Federal feed mill expands", ()), ("Briefing: crypto", ()),
    ("RBI holds rates", ("rbi",)), ("Fed, tariff and war worries", ("fed", "tariff", "war")),
])
def test_headline_flags_match_whole_words_only(headline, flags):
    assert build_briefing({}, datetime(2026, 10, 16, tzinfo=timezone.utc), [headline]).event_flags == flags


def test_timestamp_without_offset_is_a_clean_error(tmp_path, capsys):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"cues": {"nikkei": {"value": 1, "prev": 1, "as_of": "2026-10-16T07:30:00"}}}))
    with pytest.raises(ValueError, match="UTC offset"):
        load_cues(f)
    assert main(["briefing", str(f)]) == 2 and "UTC offset" in capsys.readouterr().err


@pytest.mark.parametrize("name,covered", [("license-checker", False), ("knowledge-graph", False), ("database-migration", False),
                                          ("recommendation-engine", False), ("nse-bhavcopy-reader", True), ("tax-lens", True)])
def test_short_keywords_must_match_whole_words(name, covered):
    assert skillwatch.is_covered(name) is covered


def test_git_keeps_the_proxy_environment(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw["env"])
        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    monkeypatch.setattr(skillwatch.subprocess, "run", fake_run)
    skillwatch._git("ls-remote", "x")
    assert seen["HTTPS_PROXY"] == "http://proxy:3128" and seen["GIT_TERMINAL_PROMPT"] == "0"


class FlakyBroker:
    def __init__(self):
        self.orders = []

    def place_limit_buy(self, ticker, amount):
        if self.orders:
            raise TimeoutError("broker timed out")
        self.orders.append(ticker)
        return f"id-{ticker}"


def test_failed_order_reports_the_ones_already_placed():
    calls = """\
2026-10-12 | AAA | B1 | buy | target 150 | cmp 100
2026-10-12 | AAA | B2 | buy | target 145 | cmp 100
2026-10-12 | BBB | B1 | buy | target 140 | cmp 100
2026-10-12 | BBB | B3 | buy | target 135 | cmp 100
"""
    broker = FlakyBroker()
    with pytest.raises(RuntimeError, match=r"(?s)order for \w+ failed .*already placed: \[\('AAA', 'id-AAA'\)\].*order_failed"):
        run(calls, PortfolioState(cash=100_000.0, positions={}, peak_value=100_000.0), RiskPolicy(), date(2026, 10, 14),
            approve=lambda p: True, broker=broker)


@pytest.mark.parametrize("flag,value", [("--max-invested-pct", "40"), ("--events", "e.csv"), ("--bhavcopy", "b")])
def test_propose_refuses_flags_it_would_ignore(tmp_path, capsys, flag, value):
    f = tmp_path / "c.txt"
    f.write_text("2026-10-12 | AAA | B1 | buy | target 150 | cmp 100\n")
    assert main(["propose", str(f), flag, value]) == 2 and "only work with --universe" in capsys.readouterr().err


def test_extract_keeps_large_numbers_parseable(tmp_path, capsys):
    from quant_agents.ingest import parse_calls
    d = tmp_path / "r"
    d.mkdir()
    (d / "MRF__B1__2026-10-12.txt").write_text("MRF\nRating: Buy\nTarget Price: Rs 1234567\nCMP: 12345.67\n")
    assert main(["extract", str(d)]) == 0
    line = capsys.readouterr().out.splitlines()[0].rsplit(" | ", 1)[0]
    assert "e+" not in line and "1234567" in line and "12345.67" in line
    res = parse_calls(line)
    assert res.calls and res.calls[0].target == 1234567 and not res.rejected
