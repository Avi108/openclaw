"""NSE data layer: bhavcopy parsing and a minimal MCP client (streamable HTTP).

NSE publishes official MCP servers (https://www.nseindia.com/nse-mcp): a bhavcopy
server for end-of-day history and a CM market server for near-live snapshots.
The exact tool names and schemas are discovered at run time with `list_tools`,
because they are not hard-coded here. Check NSE's terms before using the data:
the NSE page says MCP data confers no right to train or fine-tune AI models.

The client has only been exercised against a local fake server in tests, not
against NSE's live endpoints.
"""
from __future__ import annotations

import csv
import io
import json
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from collections.abc import Iterable, Mapping

# Legacy bhavcopy and the newer "UDiff" layout use different column names.
_COLS = {
    "symbol": ("SYMBOL", "TckrSymb", "SC_CODE"),
    "isin": ("ISIN",),
    "series": ("SERIES", "SctySrs"),
    "open": ("OPEN", "OPEN_PRICE", "OpnPric"),
    "high": ("HIGH", "HIGH_PRICE", "HghPric"),
    "low": ("LOW", "LOW_PRICE", "LwPric"),
    "close": ("CLOSE", "CLOSE_PRICE", "ClsPric"),
    "prev_close": ("PREVCLOSE", "PREV_CLOSE", "PrvsClsgPric"),
    "volume": ("TOTTRDQTY", "TTL_TRD_QNTY", "TtlTradgVol", "NO_OF_SHRS"),
    "date": ("TIMESTAMP", "DATE1", "TradDt"),
}
_DATE_FORMATS = ("%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y")


@dataclass(frozen=True)
class Bar:
    symbol: str
    day: date
    open: float
    high: float
    low: float
    close: float
    prev_close: float  # NSE adjusts this for corporate actions on the ex-date
    volume: float
    isin: str | None = None  # the join key across exchanges (present in UDiFF files)
    exchange: str = "NSE"
    currency: str = "INR"
    # "exchange": prev_close comes from the exchange and is adjusted for corporate actions (NSE);
    # "derived_from_change": close minus the published change; "raw_previous_close": the prior session's
    # raw close, which a split turns into a fake crash; "adjusted_by_actions": fixed with supplied actions.
    prev_close_basis: str = "exchange"


def _pick(row: Mapping[str, Any], key: str) -> Any:
    for name in _COLS[key]:
        for k in row:
            if k.strip().lower() == name.lower():
                return row[k]
    return None


def _parse_date(raw: Any) -> date:
    s = str(raw).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date {raw!r}")


def bars_from_records(
    records: Iterable[Mapping[str, Any]],
    series: str | None = "EQ",
    exchange: str = "NSE",
    default_day: date | None = None,
) -> tuple[list[Bar], list[str]]:
    """Convert bhavcopy-like dict rows into Bars; return (bars, problems). Never guesses missing numbers.

    `series=None` accepts every series/group (BSE uses groups such as A and B, not EQ).
    `default_day` is used only when the file has no date column (legacy BSE files carry the
    date in the file name).
    """
    bars: list[Bar] = []
    problems: list[str] = []
    for i, row in enumerate(records):
        sr = _pick(row, "series")
        if series is not None and sr is not None and str(sr).strip().upper() != series:
            continue
        try:
            nums = {k: float(str(_pick(row, k)).replace(",", "")) for k in ("open", "high", "low", "close", "prev_close", "volume")}
            if min(nums["close"], nums["prev_close"], nums["open"], nums["high"], nums["low"]) <= 0:
                raise ValueError("non-positive price")
            raw_day = _pick(row, "date")
            if raw_day is not None:
                day = _parse_date(raw_day)
            elif default_day is not None:
                day = default_day
            else:
                raise ValueError("no date column and no default_day")
            raw_isin = _pick(row, "isin")
            isin = str(raw_isin).strip().upper() if raw_isin else None
            bars.append(Bar(
                symbol=str(_pick(row, "symbol")).strip().upper(), day=day,
                open=nums["open"], high=nums["high"], low=nums["low"], close=nums["close"],
                prev_close=nums["prev_close"], volume=nums["volume"], isin=isin, exchange=exchange))
        except (TypeError, ValueError) as e:
            problems.append(f"row {i}: {e}")
    return bars, problems


def bars_from_csv(
    text: str, series: str | None = "EQ", exchange: str = "NSE", default_day: date | None = None
) -> tuple[list[Bar], list[str]]:
    return bars_from_records(csv.DictReader(io.StringIO(text)), series, exchange, default_day)


# ------------------------------- MCP client -------------------------------

class McpError(RuntimeError):
    pass


class McpClient:
    """Minimal MCP client for streamable-HTTP servers (JSON or SSE responses)."""

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        self.url, self.timeout = url, timeout
        self._id = 0
        self._session: str | None = None
        self._ready = False

    def _post(self, payload: dict, expect_response: bool = True) -> dict | None:
        headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
        if self._session:
            headers["mcp-session-id"] = self._session
        req = urllib.request.Request(self.url, json.dumps(payload).encode(), headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            self._session = resp.headers.get("mcp-session-id") or self._session
            body = resp.read().decode()
            ctype = resp.headers.get("content-type", "")
        if not expect_response or not body.strip():
            return None
        if "text/event-stream" in ctype:
            events = [ln[5:].strip() for ln in body.splitlines() if ln.startswith("data:")]
            body = next((e for e in reversed(events) if e), "")
        msg = json.loads(body)
        if "error" in msg:
            raise McpError(f"{msg['error'].get('code')}: {msg['error'].get('message')}")
        return msg.get("result")

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        res = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        return res or {}

    def initialize(self) -> None:
        if self._ready:
            return
        self._rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                 "clientInfo": {"name": "quant-agents", "version": "0.1"}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_response=False)
        self._ready = True

    def list_tools(self) -> list[dict]:
        self.initialize()
        return self._rpc("tools/list").get("tools", [])

    def call_tool(self, name: str, arguments: dict | None = None) -> Any:
        """Return structuredContent if present, else parsed JSON text, else raw text."""
        self.initialize()
        res = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if res.get("isError"):
            raise McpError(f"tool {name} failed: {res.get('content')}")
        if res.get("structuredContent") is not None:
            return res["structuredContent"]
        text = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
