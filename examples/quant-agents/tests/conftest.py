"""Shared fixtures: synthetic market files used by the smoke and golden tests."""
import json
import os
import random
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
GOLDEN = Path(__file__).resolve().parent / "golden"


def golden(name: str, text: str) -> None:
    """Compare text with tests/golden/<name>.txt. Set UPDATE_GOLDEN=1 to rewrite after a reviewed, intended change."""
    f = GOLDEN / f"{name}.txt"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        f.write_text(text)
        return
    assert f.exists(), f"missing golden file {f}; run once with UPDATE_GOLDEN=1 and review it"
    assert text == f.read_text(), f"output changed vs {f.name}; if intended, re-run with UPDATE_GOLDEN=1 and review the diff"


def _walk(rng, n, start, vol=0.015, drift=0.0004):
    px, out = start, []
    for _ in range(n):
        new = px * (1 + rng.gauss(drift, vol))
        out.append((px, new))
        px = new
    return out


@pytest.fixture(scope="session")
def world(tmp_path_factory):
    """One directory tree with every input the CLI commands need. Fully synthetic and seeded."""
    root = tmp_path_factory.mktemp("world")
    d0 = date(2026, 1, 1)
    rng = random.Random(11)

    # NSE bhavcopy folder (4 symbols, one with a planted 1:5 split) and a matching BSE UDiFF folder
    nse, bse = root / "nse", root / "bse"
    nse.mkdir(); bse.mkdir()
    syms = {"AAA": "INE001A01011", "BBB": "INE002A01012", "SPL": "INE003A01013", "CCC": "INE004A01014"}
    series = {s: _walk(rng, 120, 100.0 + 10 * i) for i, s in enumerate(syms)}
    nse_hdr = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,PREVCLOSE,TOTTRDQTY,TIMESTAMP,ISIN"
    udiff_hdr = "TradDt,TckrSymb,SctySrs,ISIN,OpnPric,HghPric,LwPric,ClsPric,PrvsClsgPric,TtlTradgVol"
    for i in range(120):
        day = d0 + timedelta(days=i)
        nrows, brows = [], []
        for s, isin in syms.items():
            prev, close = series[s][i]
            if s == "SPL" and i >= 60:
                # 1:5 split on day 60: every price from then on is a fifth of the pre-split path, and the exchange's
                # adjusted previous close on the ex-date is the old close divided by 5
                prev, close = prev / 5, close / 5
            nrows.append(f"{s},EQ,{close:.2f},{close:.2f},{close:.2f},{close:.2f},{prev:.2f},1000000,{day},{isin}")
            brows.append(f"{day},{s},A,{isin},{close:.2f},{close:.2f},{close:.2f},{close * 1.001:.2f},{prev:.2f},900000")
        (nse / f"bhav_{day}.csv").write_text(nse_hdr + "\n" + "\n".join(nrows))
        (bse / f"bse_{day}.csv").write_text(udiff_hdr + "\n" + "\n".join(brows))

    # price CSV (ticker,date,close) including an index fund, used by validate and the paper ledger
    px_lines = ["ticker,date,close"]
    for s in ("AAA", "IDX"):
        p = 100.0
        for i in range(120):
            p *= 1 + rng.gauss(0.0005, 0.01)
            px_lines.append(f"{s},{d0 + timedelta(days=i)},{p:.4f}")
    (root / "prices.csv").write_text("\n".join(px_lines))

    # calls for validate: published early so the 30-day window has finished by the end of the series
    (root / "calls.txt").write_text("\n".join(
        f"{d0 + timedelta(days=k)} | AAA | Broker{k} | buy | target {120 + k} | cmp 100" for k in range(1, 13)) + "\n")

    # broker reports for extract
    rep = root / "reports"
    rep.mkdir()
    (rep / "ABC__Broker_X__2026-10-01.txt").write_text("Rating: BUY\nCMP: 100\nTarget Price: 150")
    (rep / "BAD__Broker_X__2026-10-01.txt").write_text("no useful content")

    # a Korean file (cp949) and a Hong Kong file
    krx = root / "krx_20261015.csv"
    krx.write_bytes("종목코드,종목명,종가,대비,시가,고가,저가,거래량,거래대금\n5930,삼성전자,77000,-500,77500,78000,76800,12000000,925000000000\n".encode("cp949"))

    # empty watchlist so the skillwatch smoke test needs no network
    (root / "watchlist.json").write_text(json.dumps({"repos": []}))
    return root
