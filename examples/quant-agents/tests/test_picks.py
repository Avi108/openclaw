from datetime import date

from quant_agents.cli import main
from quant_agents.consensus import build_consensus, tier_picks
from quant_agents.ingest import parse_calls

TODAY = date(2026, 10, 14)


def rows(text):
    return build_consensus(parse_calls(text).calls)


def test_tiers_separate_clean_soft_and_blocked():
    r = rows("""\
2026-10-12 | GOOD | B1 | buy | target 150 | cmp 100
2026-10-12 | GOOD | B2 | buy | target 145 | cmp 100
2026-10-12 | LONE | B1 | buy | target 160 | cmp 100
2026-10-12 | HYPE | B1 | buy | target 900 | cmp 100
2026-10-12 | HYPE | B2 | buy | target 880 | cmp 100
2026-10-12 | NOPX | B1 | buy | target 160
2026-10-12 | NOPX | B2 | buy | target 150
2026-10-12 | DOWN | B1 | sell | target 60 | cmp 100
2026-10-12 | DOWN | B2 | sell | target 55 | cmp 100
""")
    short, watch, excluded = tier_picks(r)
    assert [x.ticker for x in short] == ["GOOD"] and [x.ticker for x in watch] == ["LONE"] and excluded == len(r) - 2


def test_top_limits_each_list_and_empty_input_is_safe():
    assert tier_picks([]) == ([], [], 0)
    text = "\n".join(f"2026-10-12 | T{i} | {b} | buy | target {150 + i} | cmp 100" for i in range(6) for b in ("B1", "B2"))
    assert len(tier_picks(rows(text), top=3)[0]) == 3


def write(tmp_path, text):
    f = tmp_path / "c.txt"
    f.write_text(text)
    return str(f)


def test_cli_prints_lists(tmp_path, capsys):
    f = write(tmp_path, "2026-10-12 | GOOD | B1 | buy | target 150 | cmp 100\n2026-10-12 | GOOD | B2 | buy | target 145 | cmp 100\n")
    assert main(["picks", f, "--today", "2026-10-14"]) == 0
    out = capsys.readouterr().out
    assert "GOOD" in out and "WATCH" in out and "none" in out and "often wrong" in out


def test_stale_or_empty_input_never_invents_names(tmp_path, capsys):
    f = write(tmp_path, "2026-01-12 | OLD | B1 | buy | target 150 | cmp 100\n2026-01-12 | OLD | B2 | buy | target 150 | cmp 100\n")
    assert main(["picks", f, "--today", "2026-10-14"]) == 3
    out = capsys.readouterr().out
    assert "NO PICKS" in out and "OLD" not in out
    assert main(["picks", write(tmp_path, ""), "--today", "2026-10-14"]) == 3
    assert main(["picks", str(tmp_path / "missing.txt")]) == 2
