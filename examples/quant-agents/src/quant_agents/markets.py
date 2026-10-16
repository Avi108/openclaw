"""Spec-driven loaders for other exchanges (Hong Kong, Korea).

HKEX and KRX publish in different layouts and conventions, and I could not verify
their current file schemas from this sandbox. So nothing is hard-coded as "the"
layout. Each market has a spec with candidate column aliases; `verify_columns`
reports exactly which required fields it could not map; `overrides` lets you
state the mapping yourself; and a row that cannot be read is reported, never guessed.

Alias lists include names I believe the exchanges use, marked UNVERIFIED in the specs.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .nse import Bar

REQUIRED = ("symbol", "close")  # a file with no symbol or close is unusable


@dataclass(frozen=True)
class MarketSpec:
    name: str
    exchange: str
    currency: str
    timezone: str                     # IANA name, for session logic elsewhere
    price_limit_pct: float | None     # daily price limit; None when the market has no fixed limit
    aliases: Mapping[str, tuple[str, ...]]
    date_formats: tuple[str, ...] = ("%Y-%m-%d", "%Y%m%d", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y")
    encodings: tuple[str, ...] = ("utf-8-sig", "utf-8")
    symbol_digits: int | None = None  # zero-pad all-digit codes to this width
    notes: str = ""


# UNVERIFIED: candidate names only; run `quant-agents markets-verify` on a real file.
HKEX = MarketSpec(
    name="hkex", exchange="HKEX", currency="HKD", timezone="Asia/Hong_Kong", price_limit_pct=None, symbol_digits=5,
    aliases={
        "symbol": ("Stock Code", "Code", "Symbol", "stock_code", "證券代號"),
        "name": ("Name of Securities", "Name", "Stock Name"),
        "date": ("Date", "Trade Date", "date"),
        "open": ("Open", "Opening Price", "Opening"),
        "high": ("High", "Day High", "Highest"),
        "low": ("Low", "Day Low", "Lowest"),
        "close": ("Close", "Closing Price", "Day Close", "Last", "Close/Last"),
        "prev_close": ("Previous Close", "Prev. Close", "Prev Close", "Previous Closing Price"),
        "change": ("Net Change", "Change"),
        "volume": ("Shares Traded", "Volume", "Turnover (Shares)", "No. of Shares Traded"),
        "value": ("Turnover (HK$)", "Turnover", "Turnover (HKD)", "Value"),
    },
    notes="Free daily quotations are published as web pages; historical CSV is a paid product. No fixed daily price limit.")

KRX = MarketSpec(
    name="krx", exchange="KRX", currency="KRW", timezone="Asia/Seoul", price_limit_pct=30.0, symbol_digits=6,
    encodings=("utf-8-sig", "utf-8", "cp949"),  # KRX Data Marketplace downloads are commonly cp949 (UNVERIFIED)
    aliases={
        "symbol": ("종목코드", "ISU_SRT_CD", "ISU_CD", "Code", "Symbol"),
        "name": ("종목명", "ISU_ABBRV", "ISU_NM", "Name"),
        "date": ("일자", "BAS_DD", "TRD_DD", "Date"),
        "open": ("시가", "TDD_OPNPRC", "Open"),
        "high": ("고가", "TDD_HGPRC", "High"),
        "low": ("저가", "TDD_LWPRC", "Low"),
        "close": ("종가", "TDD_CLSPRC", "Close"),
        "prev_close": ("전일종가", "기준가", "Previous Close"),
        "change": ("대비", "CMPPREVDD_PRC", "Change"),
        "volume": ("거래량", "ACC_TRDVOL", "Volume"),
        "value": ("거래대금", "ACC_TRDVAL", "Value"),
    },
    notes="Daily price limit of +/-30%. Open API needs an AUTH_KEY; field names above are unverified.")

SPECS: dict[str, MarketSpec] = {s.name: s for s in (HKEX, KRX)}


def decode_bytes(data: bytes, spec: MarketSpec) -> str:
    """Try each encoding in order; refuse rather than return garbage."""
    for enc in spec.encodings:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"could not decode with {spec.encodings}; convert the file to UTF-8 first")


def normalize_symbol(raw: Any, spec: MarketSpec) -> str:
    s = str(raw).strip().upper()
    return s.zfill(spec.symbol_digits) if spec.symbol_digits and s.isdigit() else s


def parse_number(raw: Any) -> float | None:
    """'1,234.50' -> 1234.5; '', '-', 'N/A', 'n.a.' -> None (suspended or missing, never zero)."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace(",", "")
    if s in ("", "-", "--", "N/A", "n/a", "NA", "n.a.", "N.A.", "null", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_day(raw: Any, spec: MarketSpec) -> date:
    s = str(raw).strip()
    for fmt in spec.date_formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date {raw!r}")


def resolve_columns(columns: Sequence[str], spec: MarketSpec, overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """field -> actual column name. Overrides win; otherwise the first alias present (case-insensitive)."""
    lower = {c.strip().lower(): c for c in columns}
    out: dict[str, str] = {}
    for field_name, names in spec.aliases.items():
        ov = (overrides or {}).get(field_name)
        if ov is not None:
            if ov.strip().lower() not in lower:
                raise ValueError(f"override {field_name}={ov!r} is not a column in the file: {list(columns)}")
            out[field_name] = lower[ov.strip().lower()]
            continue
        for n in names:
            if n.strip().lower() in lower:
                out[field_name] = lower[n.strip().lower()]
                break
    return out


@dataclass(frozen=True)
class ColumnReport:
    mapped: dict[str, str]
    missing_required: tuple[str, ...]
    missing_optional: tuple[str, ...]
    unmapped_columns: tuple[str, ...]
    prev_close_source: str  # how previous close will be obtained

    @property
    def usable(self) -> bool:
        return not self.missing_required


def verify_columns(columns: Sequence[str], spec: MarketSpec, overrides: Mapping[str, str] | None = None) -> ColumnReport:
    m = resolve_columns(columns, spec, overrides)
    used = set(m.values())
    prev = ("exchange-provided previous close" if "prev_close" in m else
            "close minus published change" if "change" in m else
            "prior session's raw close (UNADJUSTED: splits will look like crashes)")
    return ColumnReport(
        mapped=m,
        missing_required=tuple(f for f in REQUIRED if f not in m),
        missing_optional=tuple(f for f in spec.aliases if f not in m and f not in REQUIRED),
        unmapped_columns=tuple(c for c in columns if c not in used),
        prev_close_source=prev,
    )


def bars_from_rows(rows: Sequence[Mapping[str, Any]], spec: MarketSpec, default_day: date | None = None,
                   overrides: Mapping[str, str] | None = None) -> tuple[list[Bar], list[str], ColumnReport]:
    """Convert dict rows (CSV or JSON records) into Bars. Returns (bars, problems, column report).

    Two passes: parse every row, then fill a missing previous close from the same symbol's prior
    observation (sorted by date), so loading several days at once works regardless of row order.
    """
    if not rows:
        return [], [], ColumnReport({}, REQUIRED, (), (), "n/a")
    report = verify_columns(list(rows[0].keys()), spec, overrides)
    if not report.usable:
        raise ValueError(f"cannot map required fields {report.missing_required}; columns are {list(rows[0].keys())}. "
                         f"Pass overrides, e.g. close=<column>")
    m = report.mapped
    problems: list[str] = []
    parsed: list[dict] = []
    for i, row in enumerate(rows):
        get = lambda f: row.get(m[f]) if f in m else None
        try:
            sym = normalize_symbol(get("symbol"), spec)
            close = parse_number(get("close"))
            if close is None or close <= 0:
                problems.append(f"row {i} {sym}: no usable close (suspended or missing)")
                continue
            raw_day = get("date")
            if raw_day in (None, ""):
                if default_day is None:
                    raise ValueError("no date column and no default_day")
                day = default_day
            else:
                day = parse_day(raw_day, spec)
            prev, basis = parse_number(get("prev_close")), "exchange"
            if prev is None and (ch := parse_number(get("change"))) is not None:
                prev, basis = close - ch, "derived_from_change"
            px = lambda f: (parse_number(get(f)) or close)
            parsed.append(dict(sym=sym, day=day, open=px("open"), high=px("high"), low=px("low"), close=close,
                               prev=prev, basis=basis, vol=parse_number(get("volume")) or 0.0))
        except ValueError as e:
            problems.append(f"row {i}: {e}")
    parsed.sort(key=lambda r: (r["sym"], r["day"]))
    bars: list[Bar] = []
    last_close: dict[str, float] = {}
    for r in parsed:
        prev, basis = r["prev"], r["basis"]
        if prev is None and r["sym"] in last_close:
            prev, basis = last_close[r["sym"]], "raw_previous_close"
        last_close[r["sym"]] = r["close"]
        if prev is None or prev <= 0:
            problems.append(f"{r['sym']} {r['day']}: no previous close available (first observation); skipped")
            continue
        bars.append(Bar(r["sym"], r["day"], r["open"], r["high"], r["low"], r["close"], prev, r["vol"], None,
                        spec.exchange, spec.currency, basis))
    return bars, problems, report


def load_text(text: str, spec: MarketSpec, default_day: date | None = None,
              overrides: Mapping[str, str] | None = None) -> tuple[list[Bar], list[str], ColumnReport]:
    return bars_from_rows(list(csv.DictReader(io.StringIO(text))), spec, default_day, overrides)


def load_file(path: str | Path, spec: MarketSpec, default_day: date | None = None,
              overrides: Mapping[str, str] | None = None) -> tuple[list[Bar], list[str], ColumnReport]:
    return load_text(decode_bytes(Path(path).read_bytes(), spec), spec, default_day, overrides)


def records_from_json(payload: Any) -> list[dict]:
    """Find the list of record dicts in a JSON payload (the list's key name is not assumed)."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, list) and (not payload or isinstance(payload[0], dict)):
        return payload
    if isinstance(payload, dict):
        for v in payload.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    raise ValueError("no list of records found in JSON payload")


_DAY_IN_NAME = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})")


def day_from_filename(name: str) -> date | None:
    """Find a YYYYMMDD or YYYY-MM-DD in a file name (per-day downloads often carry no date column)."""
    m = _DAY_IN_NAME.search(name)
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def load_dir(folder: str | Path, spec: MarketSpec, overrides: Mapping[str, str] | None = None) -> tuple[list[Bar], list[str]]:
    """Load every CSV in a folder as one dataset. Rows are pooled before previous closes are derived."""
    rows: list[dict] = []
    problems: list[str] = []
    for f in sorted(Path(folder).glob("*.csv")):
        text = decode_bytes(f.read_bytes(), spec)
        day = day_from_filename(f.name)
        for r in csv.DictReader(io.StringIO(text)):
            if day is not None:
                r = {**r, "__file_day__": day.isoformat()}
            rows.append(r)
    if not rows:
        return [], [f"no CSV files in {folder}"]
    ov = dict(overrides or {})
    cols = list(rows[0].keys())
    if "date" not in resolve_columns(cols, spec, ov) and "__file_day__" in cols:
        ov["date"] = "__file_day__"  # the file name is the only date we have
    bars, probs, _ = bars_from_rows(rows, spec, None, ov)
    return bars, problems + probs
