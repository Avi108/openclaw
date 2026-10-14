import json
import random
from datetime import datetime, timedelta, timezone

import pytest

from quant_agents.cues import Cue, build_briefing, load_cues, render, validate_tone

NOW = datetime(2026, 10, 15, 3, 30, tzinfo=timezone.utc)


def mk(**kv):
    return {k: Cue(k, v[0], v[1], NOW - timedelta(hours=1)) for k, v in kv.items()}


FULL = dict(gift_nifty=(100, 100), nikkei=(100, 100), hang_seng=(100, 100), kospi=(100, 100), sti=(100, 100),
            us_futures=(100, 100), usd_inr=(100, 100), brent=(100, 100), india_vix=(100, 100), us10y=(4.0, 4.0))


def test_flat_inputs_give_neutral_with_full_coverage():
    b = build_briefing(mk(**FULL, fii_net_cr=(0, None)), NOW)
    assert b.label == "neutral" and b.tone == pytest.approx(0.0) and b.coverage == pytest.approx(1.0)


def test_risk_on_and_risk_off_directions_respect_signs():
    up = dict(FULL, gift_nifty=(101.5, 100), nikkei=(102, 100), hang_seng=(102, 100), kospi=(102, 100), sti=(101.5, 100),
              us_futures=(101.5, 100), brent=(94, 100), usd_inr=(99.5, 100), india_vix=(90, 100), us10y=(3.9, 4.0))
    assert build_briefing(mk(**up, fii_net_cr=(3000, None)), NOW).label == "risk-on"
    down = dict(FULL, gift_nifty=(98.5, 100), nikkei=(98, 100), hang_seng=(98, 100), kospi=(98, 100), sti=(98.5, 100),
                us_futures=(98.5, 100), brent=(106, 100), usd_inr=(100.6, 100), india_vix=(112, 100), us10y=(4.15, 4.0))
    b = build_briefing(mk(**down, fii_net_cr=(-3000, None)), NOW)
    assert b.label == "risk-off" and b.tone < -0.8


def test_oil_up_hurts_and_contribution_is_clipped():
    b = build_briefing(mk(**dict(FULL, brent=(150, 100))), NOW)  # +50% oil: clipped at one "scale"
    oil = next(c for c in b.contributions if c.name == "brent")
    assert oil.score == pytest.approx(-1.5)


def test_missing_and_stale_inputs_are_excluded_not_imputed():
    cues = mk(**FULL)
    cues["nikkei"] = Cue("nikkei", 90, 100, NOW - timedelta(hours=40))
    del cues["kospi"]
    b = build_briefing(cues, NOW)
    assert any("Nikkei" in e and "stale" in e for e in b.excluded)
    assert any("KOSPI" in e and "missing" in e for e in b.excluded)
    assert all(c.name not in ("nikkei", "kospi") for c in b.contributions)


def test_low_coverage_refuses_a_label():
    b = build_briefing(mk(nikkei=(101, 100)), NOW)
    assert b.tone is None and b.label == "insufficient data" and b.coverage < 0.5


def test_event_keywords_flagged_but_not_scored():
    b = build_briefing(mk(**FULL), NOW, headlines=["RBI holds repo rate", "Tariff talks stall", "Cricket final today"])
    assert b.event_flags == ("rbi", "tariff") and b.tone == pytest.approx(0.0, abs=1e-9)


def test_load_cues_and_render(tmp_path):
    f = tmp_path / "cues.json"
    f.write_text(json.dumps({"now": NOW.isoformat(), "headlines": ["Fed minutes out"],
                             "cues": {k: {"value": v[0], "prev": v[1], "as_of": (NOW - timedelta(hours=2)).isoformat()} for k, v in FULL.items()}}))
    cues, heads, now = load_cues(f)
    text = render(build_briefing(cues, now, heads))
    assert "tone: neutral" in text and "unvalidated" in text and "fed" in text


def test_validate_tone_finds_planted_signal_and_stays_quiet_on_noise():
    rng = random.Random(4)
    tones = [rng.uniform(-1, 1) for _ in range(250)]
    real = [(t, 0.6 * t + rng.gauss(0, 0.5)) for t in tones]
    v = validate_tone(real)
    assert v.direction_hit_rate > 0.65 and v.p_value < 0.01
    noise = [(t, rng.gauss(0, 1)) for t in tones]
    assert validate_tone(noise, seed=1).p_value > 0.01
    with pytest.raises(ValueError):
        validate_tone(real[:10])


def test_cli_briefing_paper_and_validate_tone(tmp_path, capsys):
    from quant_agents.cli import main

    from pathlib import Path

    sample = Path(__file__).resolve().parent.parent / "data" / "sample_cues.json"
    assert main(["briefing", str(sample)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("tone:") and "contribution" in out and "unvalidated" in out

    prices = tmp_path / "px.csv"
    rows = ["ticker,date,close"]
    for i in range(10):
        d = f"2026-01-{i + 1:02d}"
        rows += [f"AAA,{d},{100 + i * 2}", f"IDX,{d},{1000 + i}"]
    prices.write_text("\n".join(rows))
    ledger = tmp_path / "l.json"
    assert main(["paper-enter", str(ledger), "--ticker", "AAA", "--amount", "5000", "--day", "2026-01-01",
                 "--prices", str(prices), "--cash", "10000"]) == 0
    assert main(["paper-report", str(ledger), "--prices", str(prices), "--benchmark", "IDX"]) == 0
    assert "too early to judge" in capsys.readouterr().out
    assert main(["paper-exit", str(ledger), "--ticker", "AAA", "--day", "2026-01-08", "--prices", str(prices)]) == 0

    rng = random.Random(2)
    hist = tmp_path / "h.csv"
    hist.write_text("tone,nifty_return\n" + "\n".join(f"{t:.3f},{0.5 * t + rng.gauss(0, 0.4):.3f}"
                    for t in (rng.uniform(-1, 1) for _ in range(120))))
    assert main(["validate-tone", str(hist)]) == 0
    assert "permutation p=" in capsys.readouterr().out
