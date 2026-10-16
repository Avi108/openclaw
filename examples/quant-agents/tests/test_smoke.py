"""Smoke tests: run every CLI command in a real subprocess on realistic (synthetic) inputs.

These catch what unit tests miss: wiring, imports, argument names, exit codes, and tracebacks reaching the user.
"""
import os
import subprocess
import sys

import pytest

from conftest import DATA, ROOT
from quant_agents.cli import build_parser
from test_nse_scenarios import FakeMcp, serve


def run(*args, cwd=ROOT):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-m", "quant_agents.cli", *map(str, args)], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=120)


def commands():
    sub = next(a for a in build_parser()._actions if a.dest == "cmd")
    return set(sub.choices)


@pytest.fixture(scope="module")
def mcp_url():
    FakeMcp.sse, FakeMcp.seen = False, []
    srv, url = serve(FakeMcp)
    yield url
    srv.shutdown()


@pytest.fixture(scope="module")
def tone_csv(tmp_path_factory):
    import random
    rng = random.Random(5)
    f = tmp_path_factory.mktemp("tone") / "tone.csv"
    f.write_text("tone,nifty_return\n" + "\n".join(f"{t:.3f},{0.5 * t + rng.gauss(0, 0.4):.3f}" for t in (rng.uniform(-1, 1) for _ in range(80))))
    return f


def smoke_table(world, tmp_path, mcp_url, tone_csv):
    ledger = tmp_path / "ledger.json"
    prices = world / "prices.csv"
    return {
        "consensus": ([DATA / "sample_broker_calls.txt", "--today", "2026-10-14"] and
                      ["consensus", DATA / "sample_broker_calls.txt", "--today", "2026-10-14"], 0, "ALPHAPWR"),
        "propose": (["propose", DATA / "sample_broker_calls.txt", "--today", "2026-10-14"], 0, "nothing is placed"),
        "backtest": (["backtest", "--days", "200"], 0, "SYNTHETIC"),
        "extract": (["extract", world / "reports"], 0, "| ABC |"),
        "validate": (["validate", world / "calls.txt", prices, "--horizon", "30"], 0, "verdict:"),
        "nse-tools": (["nse-tools", mcp_url], 0, "get_bhavcopy"),
        "scenarios": (["scenarios", world / "nse", "--weights", "AAA=0.4,SPL=0.3,BBB=0.3", "--horizon", "20"], 0, "VaR95"),
        "markets-verify": (["markets-verify", world / "krx_20261015.csv", "--market", "krx"], 0, "1 rows loaded"),
        "crosscheck": (["crosscheck", world / "nse", world / "bse"], 0, "mismatches beyond tolerance: 0"),
        "paper-enter": (["paper-enter", ledger, "--ticker", "AAA", "--day", "2026-01-02", "--prices", prices,
                         "--amount", "5000", "--cash", "20000"], 0, "paper BUY"),
        "paper-report": (["paper-report", ledger, "--prices", prices, "--benchmark", "IDX"], 0, "verdict:"),
        "paper-exit": (["paper-exit", ledger, "--ticker", "AAA", "--day", "2026-02-01", "--prices", prices], 0, "paper SELL"),
        "paper-review": (["paper-review", ledger], 0, "closed trades=1"),
        "briefing": (["briefing", DATA / "sample_cues.json"], 0, "tone:"),
        "validate-tone": (["validate-tone", tone_csv], 0, "permutation p="),
        "playbook": (["playbook", "--events", "war", "pandemic", "--sectors", "banks", "it", "defense"], 0, "Not a forecast"),
        "skillwatch": (["skillwatch", "--watchlist", world / "watchlist.json", "--state", tmp_path / "st.json",
                        "--digest-dir", tmp_path / "digests"], 0, "No watched repo changed"),
    }


def test_every_command_has_a_smoke_scenario(world, tmp_path, mcp_url, tone_csv):
    assert set(smoke_table(world, tmp_path, mcp_url, tone_csv)) == commands(), "add a smoke scenario for the new command"


def test_all_commands_run_end_to_end(world, tmp_path, mcp_url, tone_csv):
    # paper-* commands share one ledger and must run in this order
    order = ["consensus", "propose", "backtest", "extract", "validate", "nse-tools", "scenarios", "markets-verify",
             "crosscheck", "paper-enter", "paper-report", "paper-exit", "paper-review", "briefing", "validate-tone", "playbook", "skillwatch"]
    table = smoke_table(world, tmp_path, mcp_url, tone_csv)
    failures = []
    for name in order:
        args, want_rc, marker = table[name]
        r = run(*args)
        out = r.stdout + r.stderr
        if r.returncode != want_rc or marker not in out or "Traceback" in out:
            failures.append(f"{name}: rc={r.returncode} marker={marker!r}\n{out[-600:]}")
    assert not failures, "\n\n".join(failures)


@pytest.mark.parametrize("cmd", sorted(commands()))
def test_help_works_and_documents_every_argument(cmd):
    r = run(cmd, "--help")
    assert r.returncode == 0 and r.stdout.startswith("usage:") and "Traceback" not in r.stderr
    sub = next(a for a in build_parser()._actions if a.dest == "cmd").choices[cmd]
    undocumented = [a.option_strings or a.dest for a in sub._actions if a.help in (None, "") and a.dest != "help"]
    assert not undocumented, f"{cmd}: arguments without help text: {undocumented}"


def test_bad_input_gives_a_clean_error_not_a_traceback(world, tmp_path):
    cases = {
        "missing file": ["consensus", tmp_path / "nope.txt"],
        "bad date": ["consensus", DATA / "sample_broker_calls.txt", "--today", "not-a-date"],
        "bad weights": ["scenarios", world / "nse", "--weights", "AAA0.5"],
        "weights not summing to 1": ["scenarios", world / "nse", "--weights", "AAA=0.5"],
        "unmappable columns": ["markets-verify", DATA / "sample_cues.json", "--market", "krx"],
        "unknown ledger": ["paper-review", tmp_path / "nope.json"],
        "unknown command": ["frobnicate"],
    }
    bad = []
    for label, args in cases.items():
        r = run(*args)
        if r.returncode == 0 or "Traceback" in r.stderr or not r.stderr.strip():
            bad.append(f"{label}: rc={r.returncode}\n{r.stderr[-400:]}")
    assert not bad, "\n\n".join(bad)
