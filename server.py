#!/usr/bin/env python3
"""
Chartink MCP Server
Uses browser session cookies to run screeners on Chartink.
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

BASE_URL = "https://chartink.com"
SCREENER_PROCESS = f"{BASE_URL}/screener/process"
BACKTEST_PROCESS = f"{BASE_URL}/backtest/process"

CATEGORY_ID_TO_NAME = {
    1: "Bearish scan",
    2: "Bullish scan",
    3: "Range Breakouts scan",
    4: "Candlestick Patterns scan",
    5: "Other Scans",
    6: "Intraday Bullish scan",
    7: "Intraday Bearish scan",
    10: "Crossover",
    11: "Fundamental Scans",
}

# (data-page props key, human label) in the /screeners/ catalog page.
CATALOG_SOURCES: list[tuple[str, str]] = [
    ("topLovedScans", "Most Loved"),
    ("fundamentalScans", "Fundamental Scans"),
    ("candleStickPatternsScans", "Candlestick Patterns scan"),
    ("rangeBreakoutsScans", "Range Breakouts scan"),
    ("bullishScans", "Bullish scan"),
    ("bearishScans", "Bearish scan"),
    ("intradayBullishScans", "Intraday Bullish scan"),
    ("intradayBearishScans", "Intraday Bearish scan"),
    ("crossoverScans", "Crossover"),
]

_CATALOG_TTL_SECONDS = 3600
_catalog_cache: dict[str, Any] = {"fetched_at": 0.0, "scans": []}

# ---------------------------------------------------------------------------
# Phase 1 — clause intelligence (T-02 static reference, R-02 identifier check)
# Seeded from verified scans (roadmap §9) + skills/chartink-query/reference.md.
# All tokens lowercase; the checker normalizes input before matching.
# ---------------------------------------------------------------------------

INDICATORS_REFERENCE: dict[str, Any] = {
    "segments": [
        {"token": "{cash}", "meaning": "All NSE cash stocks (default universe)"},
        {"token": "{futures}", "meaning": "F&O / futures stocks (cash prices)"},
        {"token": "{<numeric id>}", "meaning": "Index/watchlist group, e.g. Nifty 50 constituents. Capture the exact id from the Chartink UI; do not guess."},
    ],
    "timeframes": ["daily", "weekly", "monthly", "latest"],
    "offsets": [
        "latest", "1 day ago", "N days ago", "1 week ago", "N weeks ago",
        "1 month ago", "N months ago", "1 year ago", "N years ago",
        "1 quarter ago", "N quarters ago",
    ],
    "operations": [">", "<", ">=", "<=", "=", "+", "-", "*", "/", "and", "or"],
    "measures": [
        {"name": "open", "kind": "stock_attribute", "params": [], "example": "latest open"},
        {"name": "high", "kind": "stock_attribute", "params": [], "example": "latest high"},
        {"name": "low", "kind": "stock_attribute", "params": [], "example": "latest low"},
        {"name": "close", "kind": "stock_attribute", "params": [], "example": "latest close"},
        {"name": "volume", "kind": "stock_attribute", "params": [], "example": "latest volume"},
        {"name": "yearly high", "kind": "stock_attribute", "params": [], "example": "latest close >= yearly high * 0.97"},
        {"name": "sma", "kind": "indicator", "params": ["source series", "period"], "example": "latest sma( latest close , 50 )"},
        {"name": "ema", "kind": "indicator", "params": ["source series", "period"], "example": "latest ema( latest close , 21 )"},
        {"name": "wma", "kind": "indicator", "params": ["source series", "period"], "example": "latest wma( latest close , 20 )"},
        {"name": "rsi", "kind": "indicator", "params": ["period"], "example": "latest rsi( 14 )"},
        {"name": "macd line", "kind": "indicator", "params": ["fast", "slow", "signal"], "example": "latest macd line( 26 , 12 , 9 )"},
        {"name": "macd signal", "kind": "indicator", "params": ["fast", "slow", "signal"], "example": "latest macd signal( 26 , 12 , 9 )"},
        {"name": "macd histogram", "kind": "indicator", "params": ["fast", "slow", "signal"], "example": "latest macd histogram( 26 , 12 , 9 )"},
        {"name": "adx", "kind": "indicator", "params": ["period"], "example": "latest adx( 14 )"},
        {"name": "adx di positive", "kind": "indicator", "params": ["period"], "example": "latest adx di positive( 14 )"},
        {"name": "adx di negative", "kind": "indicator", "params": ["period"], "example": "latest adx di negative( 14 )"},
        {"name": "plus di", "kind": "indicator", "params": ["period"], "example": "latest plus di( 14 )"},
        {"name": "minus di", "kind": "indicator", "params": ["period"], "example": "latest minus di( 14 )"},
        {"name": "stochastic %k", "kind": "indicator", "params": ["k", "slowing"], "example": "latest stochastic %k( 14 , 3 )"},
        {"name": "slow stochastic %k", "kind": "indicator", "params": ["k", "slowing"], "example": "latest slow stochastic %k( 10 , 3 )"},
        {"name": "slow stochastic %d", "kind": "indicator", "params": ["k", "slowing", "d"], "example": "latest slow stochastic %d( 10 , 3 )"},
        {"name": "fast stochastic %k", "kind": "indicator", "params": ["k", "slowing"], "example": "latest fast stochastic %k( 5 , 3 )"},
        {"name": "fast stochastic %d", "kind": "indicator", "params": ["k", "slowing", "d"], "example": "latest fast stochastic %d( 5 , 3 )"},
        {"name": "stochrsi", "kind": "indicator", "params": ["period"], "example": "latest stochrsi( 14 )"},
        {"name": "ichimoku conversion line", "kind": "indicator", "params": ["fast", "mid", "slow"], "example": "latest ichimoku conversion line( 9 , 26 , 52 )"},
        {"name": "ichimoku base line", "kind": "indicator", "params": ["fast", "mid", "slow"], "example": "latest ichimoku base line( 9 , 26 , 52 )"},
        {"name": "ichimoku span a", "kind": "indicator", "params": ["fast", "mid", "slow"], "example": "latest ichimoku span a( 9 , 26 , 52 )"},
        {"name": "ichimoku span b", "kind": "indicator", "params": ["fast", "mid", "slow"], "example": "latest ichimoku span b( 9 , 26 , 52 )"},
        {"name": "ichimoku cloud top", "kind": "indicator", "params": ["fast", "mid", "slow"], "example": "latest close > latest ichimoku cloud top( 9 , 26 , 52 )"},
        {"name": "ichimoku cloud bottom", "kind": "indicator", "params": ["fast", "mid", "slow"], "example": "latest close < latest ichimoku cloud bottom( 9 , 26 , 52 )"},
        {"name": "stochastic %d", "kind": "indicator", "params": ["k", "slowing", "d"], "example": "latest stochastic %d( 14 , 3 , 3 )"},
        {"name": "cci", "kind": "indicator", "params": ["period"], "example": "latest cci( 20 )"},
        {"name": "mfi", "kind": "indicator", "params": ["period"], "example": "latest mfi( 14 )"},
        {"name": "williams %r", "kind": "indicator", "params": ["period"], "example": "latest williams %r( 14 )"},
        {"name": "atr", "kind": "indicator", "params": ["period"], "example": "latest atr( 14 )"},
        {"name": "supertrend", "kind": "indicator", "params": ["period", "multiplier"], "example": "latest supertrend( 10 , 3 )"},
        {"name": "parabolic sar", "kind": "indicator", "params": ["start", "max"], "example": "latest parabolic sar( 0.02 , 0.2 )"},
        {"name": "aroon up", "kind": "indicator", "params": ["period"], "example": "latest aroon up( 14 )"},
        {"name": "aroon down", "kind": "indicator", "params": ["period"], "example": "latest aroon down( 14 )"},
        {"name": "mom", "kind": "indicator", "params": ["source series", "period"], "example": "latest mom( latest close , 14 )"},
        {"name": "roc", "kind": "indicator", "params": ["source series", "period"], "example": "latest roc( latest close , 12 )"},
        {"name": "obv", "kind": "indicator", "params": [], "example": "latest obv"},
        {"name": "vwap", "kind": "indicator", "params": [], "example": "latest vwap"},
        {"name": "bollinger band upper", "kind": "indicator", "params": ["source", "period", "stddev"], "example": "latest bollinger band upper( latest close , 20 , 2 )"},
        {"name": "bollinger band middle", "kind": "indicator", "params": ["source", "period", "stddev"], "example": "latest bollinger band middle( latest close , 20 , 2 )"},
        {"name": "bollinger band lower", "kind": "indicator", "params": ["source", "period", "stddev"], "example": "latest bollinger band lower( latest close , 20 , 2 )"},
        {"name": "lower bollinger band", "kind": "indicator", "params": ["period", "stddev"], "example": "[0] 15 minute lower bollinger band( 20 , 2 )"},
        {"name": "upper bollinger band", "kind": "indicator", "params": ["period", "stddev"], "example": "[0] 15 minute upper bollinger band( 20 , 2 )"},
        {"name": "donchian channel upper", "kind": "indicator", "params": ["period"], "example": "latest donchian channel upper( 20 )"},
        {"name": "donchian channel middle", "kind": "indicator", "params": ["period"], "example": "latest donchian channel middle( 20 )"},
        {"name": "donchian channel lower", "kind": "indicator", "params": ["period"], "example": "latest donchian channel lower( 20 )"},
        {"name": "pivot point", "kind": "indicator", "params": [], "example": "latest close > latest pivot point"},
        {"name": "resistance 1", "kind": "indicator", "params": [], "example": "latest close > latest resistance 1"},
        {"name": "resistance 2", "kind": "indicator", "params": [], "example": "latest close > latest resistance 2"},
        {"name": "resistance 3", "kind": "indicator", "params": [], "example": "latest close > latest resistance 3"},
        {"name": "support 1", "kind": "indicator", "params": [], "example": "latest close < latest support 1"},
        {"name": "support 2", "kind": "indicator", "params": [], "example": "latest close < latest support 2"},
        {"name": "support 3", "kind": "indicator", "params": [], "example": "latest close < latest support 3"},
        {"name": "max", "kind": "function", "params": ["lookback N", "series"], "example": "latest max( 252 , latest high )"},
        {"name": "greatest", "kind": "function", "params": ["series A", "series B", "..."], "example": "greatest( close , open )"},
        {"name": "least", "kind": "function", "params": ["series A", "series B", "..."], "example": "least( low , close )"},
        {"name": "min", "kind": "function", "params": ["lookback N", "series"], "example": "latest min( 20 , latest low )"},
        {"name": "count", "kind": "function", "params": ["period", "<value> where <condition>"], "example": "count( 5 , 1 where daily close > daily open ) >= 3"},
        {"name": "abs", "kind": "function", "params": ["expression"], "example": "abs( latest close - 1 day ago close )"},
        {"name": "round", "kind": "function", "params": ["expression", "digits"], "example": "round( latest close , 0 )"},
        {"name": "ceil", "kind": "function", "params": ["expression"], "example": "ceil( latest close )"},
        {"name": "floor", "kind": "function", "params": ["expression"], "example": "floor( latest close )"},
        {"name": "market cap", "kind": "fundamental", "params": [], "example": "market cap > 5000  (₹ crore)"},
        {"name": "yearly pe ratio", "kind": "fundamental", "params": [], "example": "yearly pe ratio < 25"},
        {"name": "yearly pc ratio", "kind": "fundamental", "params": [], "example": "yearly pc ratio < 20"},
        {"name": "price to book value", "kind": "fundamental", "params": [], "example": "price to book value < 3"},
        {"name": "book value", "kind": "fundamental", "params": [], "example": "book value > 100"},
        {"name": "eps after extraordinary items diluted", "kind": "fundamental", "params": [], "example": "yearly eps after extraordinary items diluted > 1 year ago eps after extraordinary items diluted"},
        {"name": "eps after extraordinary items basic", "kind": "fundamental", "params": [], "example": "yearly eps after extraordinary items basic > 0"},
        {"name": "net profit after minority interest & pnl assoco", "kind": "fundamental", "params": [], "example": "market cap / yearly net profit after minority interest & pnl assoco < 5"},
        {"name": "advance given by bank", "kind": "fundamental", "params": [], "example": "yearly advance given by bank > 0"},
        {"name": "net non performing assets", "kind": "fundamental", "params": [], "example": "yearly net non performing assets < 100"},
        {"name": "gross block", "kind": "fundamental", "params": [], "example": "yearly gross block > 1000"},
        {"name": "total loans", "kind": "fundamental", "params": [], "example": "yearly total loans < 5000"},
        {"name": "equity", "kind": "fundamental", "params": [], "example": "latest equity > 1000"},
        {"name": "face value", "kind": "fundamental", "params": [], "example": "latest face value = 10"},
        {"name": "net profit/reported profit after tax", "kind": "fundamental", "params": [], "example": "yearly net profit/reported profit after tax > 0"},
        {"name": "foreign institutional investors percentage", "kind": "fundamental", "params": [], "example": "quarterly foreign institutional investors percentage >= 1 quarter ago foreign institutional investors percentage"},
        {"name": "sales turnover", "kind": "fundamental", "params": [], "example": "yearly sales turnover > 1000  (₹ crore)"},
        {"name": "net sales", "kind": "fundamental", "params": [], "example": "quarterly net sales > 500"},
        {"name": "networth", "kind": "fundamental", "params": [], "example": "networth > 1000"},
        {"name": "reserves", "kind": "fundamental", "params": [], "example": "reserves > 500"},
        {"name": "secured loans", "kind": "fundamental", "params": [], "example": "yearly secured loans + yearly unsecured loans < 1 year ago secured loans + 1 year ago unsecured loans"},
        {"name": "unsecured loans", "kind": "fundamental", "params": [], "example": "see secured loans"},
        {"name": "total number", "kind": "fundamental", "params": [], "example": "quarterly total number"},
        {"name": "% change", "kind": "stock_attribute", "params": [], "example": "latest % change > 2"},
    ],
    "notes": [
        "'52 week high' is NOT valid — use 'yearly high' or 'weekly max( 52 , weekly high )' / 'max( 252 , latest high )'.",
        "No literal 'crossed above/below' token: express crossovers as two-part 'and' conditions (see skill).",
        "Fundamental values are consolidated ₹ crore; ratios/per-share are absolute numbers.",
        "Timeframe word goes in front: 'daily close', '1 day ago rsi( 14 )', 'weekly max( 52 , weekly high )'.",
        "Backtests cover only the last ~150 candles of the lowest timeframe.",
    ],
}

# Phrases the R-02 checker strips before looking for unknown residue.
_KNOWN_PHRASES = sorted(
    {
        m["name"]
        for m in INDICATORS_REFERENCE["measures"]
        if " " in m["name"] or "%" in m["name"]
    }
    | {
        "days ago", "day ago", "weeks ago", "week ago", "months ago", "month ago",
        "years ago", "year ago", "quarters ago", "quarter ago",
        "cash", "futures", "where",
        # '&' is stripped to a space before matching, so keep a spaced variant.
        "net profit after minority interest pnl assoco",
    },
    key=len,
    reverse=True,
)
_KNOWN_WORDS = {
    m["name"] for m in INDICATORS_REFERENCE["measures"] if " " not in m["name"] and "%" not in m["name"]
} | {
    "latest", "daily", "weekly", "monthly", "day", "days", "week", "weeks",
    "month", "months", "year", "years", "quarter", "quarters", "ago",
    "minute", "minutes", "hour", "hours",
    "and", "or", "where", "cash", "futures", "number",
    "max", "min", "count", "abs", "round", "ceil", "floor",
    "sma", "ema", "wma", "rsi", "adx", "cci", "mfi", "atr", "mom", "roc", "obv", "vwap",
    "macd", "line", "signal", "histogram", "plus", "di", "minus", "stochastic",
    "williams", "supertrend", "parabolic", "sar", "aroon", "up", "down",
    "bollinger", "band", "upper", "middle", "lower", "envelope", "donchian", "channel",
    "pivot", "point", "resistance", "support",
    "open", "high", "low", "close", "volume", "yearly", "change",
    "market", "cap", "pe", "pc", "ratio", "price", "to", "book", "value", "earning",
    "per", "share", "eps", "prev", "cash", "net", "profit", "reported", "after", "tax",
    "diluted", "extraordinary", "items", "operating", "gross", "margin", "sales",
    "turnover", "networth", "reserves", "face", "dividend", "loans", "secured",
    "unsecured", "total", "foreign", "institutional", "investors", "percentage",
    "bank", "advance", "given", "by", "non", "performing", "assets", "bse", "nse",
    "trailing", "twelve", "ttm", "quarterly",
    "cps", "depreciation", "pb", "yield", "debt", "split", "splits", "bonus",
}
_SUGGESTION_POOL = sorted({m["name"] for m in INDICATORS_REFERENCE["measures"]})


def _check_identifiers(scan_clause: str) -> list[dict]:
    """R-02: find unknown identifiers via allow-list subtraction + suggestions."""
    import difflib

    text = (scan_clause or "").lower()
    text = re.sub(r"\{[^{}]*\}", " ", text)  # {cash} / {33489} segments
    text = re.sub(r"\{?custom_indicator_\d+[a-z_]*\}?", " ", text)  # custom indicador markers
    text = re.sub(r'"[^"]*"', " ", text)  # quoted custom-indicator labels
    text = re.sub(r"\d+(\.\d+)?", " ", text)  # numbers
    text = re.sub(r"[^a-z% ]", " ", text)  # keep letters, % and spaces
    text = re.sub(r"\s+", " ", text)  # collapse so multi-space gaps match phrases
    for phrase in _KNOWN_PHRASES:
        if phrase in text:
            text = text.replace(phrase, " ")
    unknown: list[dict] = []
    for word in sorted(set(text.split())):
        if not word or word in _KNOWN_WORDS or len(word) == 1:
            continue
        suggestions = difflib.get_close_matches(word, _SUGGESTION_POOL, n=3, cutoff=0.6)
        # Also try matching against single words for short typos.
        if not suggestions:
            suggestions = difflib.get_close_matches(word, sorted(_KNOWN_WORDS), n=3, cutoff=0.75)
        unknown.append({"unknown": word, "suggestions": suggestions})
    return unknown


def _static_clause_checks(scan_clause: str) -> tuple[list[str], list[str]]:
    """Returns (errors, warnings) from offline checks (T-01 static part)."""
    errors: list[str] = []
    warnings: list[str] = []
    clause = (scan_clause or "").strip()
    if not clause:
        errors.append("Empty scan clause. Wrap conditions as '( {cash} ( <condition> ) )'.")
        return errors, warnings
    # Known trap: '52 week high' is not a field (silent zero rows). Catch the
    # pattern even though 'week'/'high' are individually valid words.
    if re.search(r"\b\d+\s*weeks?\s+(high|low)\b", clause, flags=re.I):
        errors.append(
            "'N week high/low' (e.g. '52 week high') is NOT a valid field and returns "
            "zero rows silently — use 'Yearly high' or 'weekly max( 52 , weekly high )' instead."
        )
    if clause.count("(") != clause.count(")"):
        errors.append(
            f"Unbalanced parentheses: {clause.count('(')} '(' vs {clause.count(')')} ')'. "
            "Every condition group needs matching parens."
        )
    if "{" not in clause or "}" not in clause:
        warnings.append("No {segment} token found — Chartink needs e.g. '( {cash} ( ... ) )'. Add {cash} for all NSE stocks.")
    # Each and/or-separated filter should contain exactly one comparison.
    parts = re.split(r"\b(?:and|or)\b", clause, flags=re.I)
    for part in parts:
        p = part.strip(" ()")
        if not p or re.search(r"\{[^}]*\}", p):
            continue
        ops = re.findall(r">=|<=|>|<|=", p)
        if len(ops) == 0 and re.search(r"[a-z]", p, re.I):
            warnings.append(f"Filter has no comparison operator: '{p[:80]}'. Did you forget '> / < / ='?")
        elif len(ops) > 1:
            warnings.append(f"Filter has {len(ops)} comparisons (expected 1): '{p[:80]}'. Split with 'and'.")
    return errors, warnings

# R-01: on-disk session so an MCP restart doesn't wipe cookies.
_SESSION_FILE = Path(os.environ.get("CHARTINK_SESSION_FILE", Path.home() / ".chartink-mcp" / "session.json"))
_SESSION_MAX_AGE_SECONDS = 2 * 3600  # ci_session Max-Age=7200

# R-04: timeouts + retry/backoff for all HTTP.
_CONNECT_TIMEOUT = 10
_READ_TIMEOUT = 30
_MAX_RETRIES = 1  # one retry on network errors
_RETRY_BACKOFF_BASE = 1.0


def _safe_err(e: Exception) -> str:
    """R-05: format exceptions without leaking secrets (cookies, tokens)."""
    msg = f"{type(e).__name__}: {e}"
    # Redact anything that looks like a credential value.
    msg = re.sub(r"(XSRF-TOKEN\s*=\s*)[^;\s]+", r"\1[redacted]", msg, flags=re.I)
    msg = re.sub(r"(ci_session\s*=\s*)[^;\s]+", r"\1[redacted]", msg, flags=re.I)
    msg = re.sub(r"(x-xsrf-token['\"]?\s*:\s*\S+)", "x-xsrf-token: [redacted]", msg, flags=re.I)
    return msg[:1000]


def _is_rate_limited(resp: requests.Response) -> bool:
    return resp.status_code == 429


def _http_get(url: str, **kwargs) -> requests.Response:
    """GET with timeout, one retry on network errors, 429 surfaced clearly."""
    kwargs.setdefault("timeout", (_CONNECT_TIMEOUT, _READ_TIMEOUT))
    last: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = _session.get(url, **kwargs)
            if _is_rate_limited(resp):
                retry_after = resp.headers.get("Retry-After")
                hint = f" (retry after {retry_after}s)" if retry_after else ""
                raise RuntimeError(f"rate_limited: Chartink returned 429{hint}")
            return resp
        except RuntimeError:
            raise
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF_BASE * (2 ** attempt))
    raise RuntimeError(f"Network error GET {url}: {_safe_err(last)}")  # type: ignore[arg-type]


def _http_post(url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", (_CONNECT_TIMEOUT, _READ_TIMEOUT))
    last: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = _session.post(url, **kwargs)
            if _is_rate_limited(resp):
                retry_after = resp.headers.get("Retry-After")
                hint = f" (retry after {retry_after}s)" if retry_after else ""
                raise RuntimeError(f"rate_limited: Chartink returned 429{hint}")
            return resp
        except RuntimeError:
            raise
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF_BASE * (2 ** attempt))
    raise RuntimeError(f"Network error POST {url}: {_safe_err(last)}")  # type: ignore[arg-type]


def _cookie_age_seconds() -> float | None:
    try:
        saved_at = float(_session.cookies.get("chartink_saved_at", domain="chartink.com") or 0)
        return time.time() - saved_at if saved_at else None
    except Exception:
        return None


def _save_cookies_to_disk() -> None:
    """Persist XSRF + ci_session (R-01). Values never logged."""
    try:
        _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "xsrf_token": _session.cookies.get("XSRF-TOKEN", domain="chartink.com"),
            "ci_session": _session.cookies.get("ci_session", domain="chartink.com"),
            "saved_at": time.time(),
        }
        _SESSION_FILE.write_text(json.dumps(payload))
        os.chmod(_SESSION_FILE, 0o600)
    except Exception:
        pass  # persistence is best-effort; in-memory session still works


def _load_cookies_from_disk() -> bool:
    """Load persisted cookies if fresh. Returns True when loaded."""
    try:
        if not _SESSION_FILE.exists():
            return False
        payload = json.loads(_SESSION_FILE.read_text())
        saved_at = float(payload.get("saved_at") or 0)
        if time.time() - saved_at > _SESSION_MAX_AGE_SECONDS:
            return False
        if payload.get("xsrf_token"):
            _session.cookies.set("XSRF-TOKEN", payload["xsrf_token"], domain="chartink.com")
        if payload.get("ci_session"):
            _session.cookies.set("ci_session", payload["ci_session"], domain="chartink.com")
        if payload.get("xsrf_token") or payload.get("ci_session"):
            # Track age for server_status via a timestamp cookie:
            _session.cookies.set("chartink_saved_at", str(int(saved_at)), domain="chartink.com")
            return True
        return False
    except Exception:
        return False


_session = requests.Session()
_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
})

_cookies_from_disk = _load_cookies_from_disk()


def _get_xsrf_token() -> str:
    """Return the XSRF-TOKEN cookie value (URL-decoded). Refreshes from the
    screener page if not present in the session."""
    token = _session.cookies.get("XSRF-TOKEN", domain="chartink.com")
    if token:
        return unquote(token)
    # Refresh by hitting any chartink page
    resp = _http_get(f"{BASE_URL}/screeners/")
    resp.raise_for_status()
    token = _session.cookies.get("XSRF-TOKEN", domain="chartink.com")
    if not token:
        raise RuntimeError("XSRF-TOKEN not found — are you logged in?")
    return unquote(token)


def _post_screener(scan_clause: str, max_rows: int = 160, referer: str = f"{BASE_URL}/screeners/") -> dict:
    xsrf = _get_xsrf_token()
    headers = {
        "content-type": "application/x-www-form-urlencoded",
        "x-xsrf-token": xsrf,
        "referer": referer,
        "origin": BASE_URL,
    }
    resp = _http_post(
        SCREENER_PROCESS,
        data={"max_rows": max_rows, "scan_clause": scan_clause},
        headers=headers,
    )
    resp.raise_for_status()
    return resp.json()


def _post_backtest(scan_clause: str, max_rows: int = 160, referer: str = f"{BASE_URL}/screeners/") -> dict:
    xsrf = _get_xsrf_token()
    headers = {
        "content-type": "application/x-www-form-urlencoded",
        "x-xsrf-token": xsrf,
        "referer": referer,
        "origin": BASE_URL,
    }
    resp = _http_post(
        BACKTEST_PROCESS,
        data={"max_rows": max_rows, "scan_clause": scan_clause},
        headers=headers,
    )
    resp.raise_for_status()
    return resp.json()


def _normalize_slug(slug: str) -> str:
    """Accept a bare slug, full URL, or leading-slash path; return bare slug."""
    s = (slug or "").strip()
    if not s:
        return s
    # Full URL like https://chartink.com/screener/abc
    m = re.search(r"/screener/([A-Za-z0-9\-_]+)", s)
    if m:
        return m.group(1)
    s = s.strip("/")
    # Last path segment if user pasted something else
    if "/" in s:
        s = s.split("/")[-1]
    return s


def _normalize_username(username: str) -> str:
    """Accept '@admin', 'admin', or full profile URL; return bare username."""
    u = (username or "").strip()
    if not u:
        return u
    m = re.search(r"/@([A-Za-z][A-Za-z0-9_\-]{0,63})", u)
    if m:
        return m.group(1)
    u = u.lstrip("@").strip().strip("/")
    if "/" in u:
        u = u.split("/")[-1].lstrip("@")
    return u


def _get_data_page_payload(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    div = soup.find("div", {"id": "app"})
    if not div or not div.get("data-page"):
        raise RuntimeError("Could not find embedded page data (site layout changed?)")
    return json.loads(div.get("data-page"))


def _server_status() -> dict:
    """R-03: cheap auth/site probe — no scan consumed, no side effects."""
    has_xsrf = bool(_session.cookies.get("XSRF-TOKEN", domain="chartink.com"))
    has_session = bool(_session.cookies.get("ci_session", domain="chartink.com"))
    age = _cookie_age_seconds()
    try:
        start = time.time()
        resp = _http_get(f"{BASE_URL}/screeners/")
        latency_ms = int((time.time() - start) * 1000)
        reachable = resp.ok
        # Premium users get extra payloads; the logged-out page has "user":null.
        premium = None
        try:
            if resp.ok:
                payload = _get_data_page_payload(resp.text)
                user = payload.get("props", {}).get("user")
                premium = bool(user and (user.get("is_premium") or user.get("isPremium")))
                if user is None:
                    premium = False
        except Exception:
            premium = None
        return {
            "site_reachable": reachable,
            "site_latency_ms": latency_ms,
            "cookies_present": has_xsrf or has_session,
            "xsrf_present": has_xsrf,
            "ci_session_present": has_session,
            "cookie_age_seconds": int(age) if age is not None else None,
            "cookies_loaded_from_disk": _cookies_from_disk,
            "premium_detected": premium,
        }
    except Exception as e:
        return {
            "site_reachable": False,
            "site_latency_ms": None,
            "cookies_present": has_xsrf or has_session,
            "xsrf_present": has_xsrf,
            "ci_session_present": has_session,
            "cookie_age_seconds": int(age) if age is not None else None,
            "cookies_loaded_from_disk": _cookies_from_disk,
            "premium_detected": None,
            "error": _safe_err(e),
        }


def _r02_precheck(scan_clause: str) -> dict | None:
    """R-02: block runs with unknown identifiers (silent-zero-rows bug).

    Returns an error payload dict when the clause must NOT be run, else None.
    """
    unknown = _check_identifiers(scan_clause or "")
    problems: list[str] = []
    if re.search(r"\b\d+\s*weeks?\s+(high|low)\b", scan_clause or "", flags=re.I):
        problems.append(
            "invalid trap 'N week high/low' (e.g. '52 week high') — use 'Yearly high' "
            "or 'weekly max( 52 , weekly high )'"
        )
    if not unknown and not problems:
        return None
    bits = []
    for u in unknown:
        sug = f" — did you mean: {', '.join(u['suggestions'])}?" if u["suggestions"] else ""
        bits.append(f"unknown identifier '{u['unknown']}'{sug}")
    bits.extend(problems)
    return {
        "warning": "NOT_RUN_UNKNOWN_IDENTIFIERS: " + "; ".join(bits) + ". "
        "Chartink returns zero rows (HTTP 200) for bad field names, so the scan was NOT run. "
        "Fix the clause (tip: '52 week high' → 'Yearly high'; confirm tokens via list_indicators) "
        "or verify with validate_scan.",
        "unknown_identifiers": unknown,
    }


def _parse_fundamentals(html: str) -> dict:
    """Parse a /fundamentals/<symbol>.html page (no auth) into tables."""
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")

    def _cells(tr):
        return [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]

    snapshot: dict[str, str] = {}
    if tables:
        # Table 1 (index 1): label | value | label | value ... pairs.
        rows = tables[1].find_all("tr") if len(tables) > 1 else []
        for tr in rows:
            cells = _cells(tr)
            for i in range(0, len(cells) - 1, 2):
                k, v = cells[i].strip(), cells[i + 1].strip()
                if k and v:
                    snapshot[k] = v
    series: dict[str, Any] = {}
    labels = ["quarterly", "yearly", "balance_sheet", "cash_flow"]
    for idx, label in zip([3, 4, 5, 6], labels):
        if idx >= len(tables):
            continue
        data_rows = [_cells(tr) for tr in tables[idx].find_all("tr")]
        data_rows = [r for r in data_rows if any(c for c in r)]
        if not data_rows:
            continue
        series[label] = {"headers": data_rows[0], "rows": data_rows[1:40]}
    return {"snapshot": snapshot, "tables": series}


_HISTORY_FILE = Path(os.environ.get("CHARTINK_HISTORY_FILE", Path.home() / ".chartink-mcp" / "history.jsonl"))


def _log_history(tool: str, scan_clause: str, count: int | None, label: str | None = None) -> None:
    """T-10: append-only local run log. Best-effort, never raises."""
    try:
        import hashlib
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": int(time.time()),
            "tool": tool,
            "label": label,
            "clause_fingerprint": hashlib.sha1((scan_clause or "").encode()).hexdigest()[:12],
            "scan_clause": (scan_clause or "")[:2000],
            "count": count,
        }
        with open(_HISTORY_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


# T-06: segment ids. {cash} is literal; numeric ids resolve via the segment
# list Chartink embeds on every scan page (:watchlist-json). Cached 24h.
_SEGMENTS_TTL = 24 * 3600
_segments_cache: dict[str, Any] = {"fetched_at": 0.0, "by_name": {}, "raw": []}
_STATIC_SEGMENTS = {"cash": "cash", "futures": "33489"}


def _get_segments() -> dict[str, str]:
    now = time.time()
    if _segments_cache["by_name"] and (now - _segments_cache["fetched_at"]) < _SEGMENTS_TTL:
        return _segments_cache["by_name"]
    by_name = dict(_STATIC_SEGMENTS)
    raw: list[dict] = []
    try:
        resp = _http_get(_screener_url("short-term-breakouts"))
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        scanner = soup.find("scanner")
        wl = scanner.get(":watchlist-json") if scanner is not None else None
        if wl:
            for w in json.loads(wl):
                raw.append({"id": w.get("id"), "name": w.get("name")})
                if w.get("name") is not None and w.get("id") is not None:
                    by_name[str(w["name"]).strip().lower()] = str(w["id"])
                    by_name[str(w["name"]).strip().lower().replace(" ", "")] = str(w["id"])
    except Exception:
        pass
    _segments_cache["by_name"] = by_name
    _segments_cache["fetched_at"] = now
    _segments_cache["raw"] = raw
    return by_name


def _apply_segment(scan_clause: str, segment: str | None) -> tuple[str, str | None]:
    """Wrap a bare clause in a {segment} token. Returns (clause, applied_id).

    Leaves the clause untouched when it already contains a {…} token or when
    no segment was requested. Raises RuntimeError for unknown segment names.
    """
    seg = (segment or "").strip()
    if not seg:
        return scan_clause, None
    if re.search(r"\{[^{}]+\}", scan_clause or ""):
        return scan_clause, None  # already segmented — don't double-wrap
    key = seg.lower().strip("{} ")
    if key.isdigit():
        token = key
    else:
        by_name = _get_segments()
        norm = key.replace(" ", "")
        token = by_name.get(key) or by_name.get(norm)
        if token is None:
            known = sorted(set(_STATIC_SEGMENTS) | {w.get("name", "") for w in _segments_cache["raw"]})
            raise RuntimeError(
                f"Unknown segment '{segment}'. Use 'cash', 'futures', a numeric group id, "
                f"or one of: {', '.join(known) or 'cash, futures'}."
            )
    return f"( {{{token}}} ( {scan_clause.strip()} ) )", token


def _shape_results(stocks: list[dict], sort_by: str | None = None,
                   min_price: float | None = None) -> list[dict]:
    """T-07 (local part): client-side price filter + sorting."""
    rows = list(stocks)
    if min_price is not None:
        rows = [r for r in rows if isinstance(r.get("close"), (int, float)) and r["close"] >= min_price]
    if sort_by:
        key = {"volume": "volume", "per_chg": "per_chg", "close": "close"}.get(sort_by.strip().lower())
        if key:
            rows.sort(key=lambda r: (r.get(key) is None, r.get(key)), reverse=True)
    return rows


def _to_csv(stocks: list[dict]) -> str:
    import csv
    import io
    if not stocks:
        return ""
    cols = list(stocks[0].keys())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(stocks)
    return buf.getvalue()


def _fetch_dashboard_props(path: str) -> dict:
    """GET a dashboard page (login required). Returns data-page props.

    Raises RuntimeError with a login-required message when logged out.
    """
    resp = _http_get(f"{BASE_URL}/{path}")
    if resp.status_code in (401, 419):
        raise RuntimeError(
            f"Login required for /{path} (HTTP {resp.status_code}). "
            "Re-run set_cookies with a fresh logged-in session."
        )
    try:
        payload = _get_data_page_payload(resp.text)
    except RuntimeError:
        raise RuntimeError(
            f"Login required for /{path} (got the login page, not a dashboard). "
            "Re-run set_cookies with a fresh logged-in session."
        )
    return payload.get("props", {})


def _extract_named_lists(props: dict) -> dict:
    """Heuristically pull list-of-dict (name/id) payloads out of dashboard props."""
    found: dict[str, Any] = {}
    for key, val in props.items():
        if isinstance(val, list) and val and isinstance(val[0], dict) \
                and ("name" in val[0] or "title" in val[0]):
            rows = []
            for item in val[:100]:
                row = {k: item.get(k) for k in
                       ("id", "name", "title", "slug", "url", "status", "schedule",
                        "channels", "created_at", "updated_at", "is_private", "symbols",
                        "stocks", "count") if k in item}
                rows.append(row)
            found[key] = rows
    return found


def _parse_backtest_payload(data: dict) -> dict:
    """Parse /backtest/process payload (NOT screener-shaped: no 'data' key).

    aggregatedStockList = one flat [symbol, cap, sector, …] snapshot per candle,
    aligned with metaData.tradeTimes; the LAST entry is the current matches
    (verified: 145/146 overlap with a live screener run of the same clause).
    """
    asl = data.get("aggregatedStockList") or []
    md = (data.get("metaData") or [{}])[0]
    trade_times = md.get("tradeTimes") or []
    stocks: list[dict] = []
    if asl:
        snap = asl[-1]
        for i in range(0, len(snap) - 2, 3):
            sym, cap, sector = snap[i], snap[i + 1], snap[i + 2]
            if sym:
                stocks.append({"nsecode": sym, "marketcap": cap, "sector": sector})
    as_of = None
    if trade_times:
        try:
            as_of = datetime.fromtimestamp(trade_times[-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            as_of = None
    return {
        "stocks": stocks,
        "as_of": as_of,
        "candles": data.get("time"),
        "groups": len(data.get("groupData") or []),
    }


def _parse_searched_screeners(html: str) -> dict:
    """Parse a /screeners/search or group page (searchedScreeners component)."""
    payload = _get_data_page_payload(html)
    props = payload.get("props", {})
    pg = props.get("paginatedScans") or {}
    rows = []
    for item in pg.get("data") or []:
        slug = item.get("slug") or ""
        rows.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "slug": slug,
            "url": _screener_url(slug) if slug else None,
            "description": item.get("description"),
            "user_id": item.get("user_id"),
            "likes": item.get("like_count"),
            "backtest_hits": item.get("hit_count"),
            "category_id": item.get("scan_category_id"),
            "category": CATEGORY_ID_TO_NAME.get(item.get("scan_category_id")),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "scan_clause": item.get("atlas_query"),
            "is_private": item.get("is_private"),
        })
    return {
        "rows": rows,
        "page": pg.get("current_page"),
        "per_page": pg.get("per_page"),
        "total": pg.get("total"),
        "last_page": pg.get("last_page"),
        "search_term": props.get("searchTerm"),
        "scan_category": props.get("scanCategory"),
    }


def _screener_url(slug: str) -> str:
    return f"{BASE_URL}/screener/{slug}"


def _profile_url(username: str) -> str:
    return f"{BASE_URL}/@{username}"


def _get_catalog(force_refresh: bool = False) -> list[dict]:
    """Fetch and cache the curated /screeners/ catalog (no auth needed).

    Returns deduped list of scans with url/category/scan_clause attached.
    """
    now = time.time()
    if (
        not force_refresh
        and _catalog_cache["scans"]
        and (now - _catalog_cache["fetched_at"]) < _CATALOG_TTL_SECONDS
    ):
        return _catalog_cache["scans"]

    resp = _http_get(f"{BASE_URL}/screeners/")
    resp.raise_for_status()
    payload = _get_data_page_payload(resp.text)
    props = payload.get("props", {})

    by_id: dict[Any, dict] = {}
    for props_key, label in CATALOG_SOURCES:
        for item in props.get(props_key) or []:
            sid = item.get("id")
            if sid is None:
                continue
            slug = item.get("slug") or ""
            cat_id = item.get("scan_category_id")
            entry = by_id.get(sid)
            if entry is None:
                entry = {
                    "id": sid,
                    "name": item.get("name"),
                    "slug": slug,
                    "url": _screener_url(slug) if slug else None,
                    "description": item.get("description"),
                    "category_id": cat_id,
                    "category": CATEGORY_ID_TO_NAME.get(cat_id),
                    "created_at": item.get("created_at"),
                    "scan_clause": item.get("atlas_query"),
                    "source_groups": [],
                }
                by_id[sid] = entry
            if label not in entry["source_groups"]:
                entry["source_groups"].append(label)

    scans = sorted(by_id.values(), key=lambda s: (str(s.get("name") or "").lower()))
    _catalog_cache["scans"] = scans
    _catalog_cache["fetched_at"] = now
    return scans


def _matches_category(scan: dict, category_filter: str) -> bool:
    if not category_filter:
        return True
    cf = category_filter.strip().lower()
    if not cf:
        return True
    candidates = [
        str(scan.get("category") or "").lower(),
        *[g.lower() for g in (scan.get("source_groups") or [])],
    ]
    # Also allow filtering by numeric category id ("11", "2", ...)
    if cf.isdigit() and str(scan.get("category_id")) == cf:
        return True
    return any(cf in c for c in candidates if c)


def _score_scan(scan: dict, query: str, tokens: list[str]) -> float:
    name = str(scan.get("name") or "").lower()
    desc = str(scan.get("description") or "").lower()
    clause = str(scan.get("scan_clause") or "").lower()
    score = 0.0
    if query and query in name:
        score += 3.0
    if tokens and all(t in name for t in tokens):
        score += 2.0
    elif tokens and any(t in name for t in tokens):
        score += 1.0
    if query and query in desc:
        score += 1.5
    elif tokens and any(t in desc for t in tokens):
        score += 0.75
    if query and query in clause:
        score += 0.5
    elif tokens and any(t in clause for t in tokens):
        score += 0.25
    return score


def _fetch_screener_details(slug: str) -> dict:
    """Fetch a public screener page and extract full metadata + scan clause.

    No login cookies required for public scans. Raises RuntimeError with a
    user-friendly message for 404/private/layout changes.
    """
    clean = _normalize_slug(slug)
    if not clean:
        raise RuntimeError("Empty screener slug. Pass the part after /screener/ in the URL.")
    url = _screener_url(clean)
    resp = _http_get(url)
    if resp.status_code == 404:
        raise RuntimeError(
            f"Screener '{clean}' not found (404). Use search_screeners to find the "
            f"exact slug, or check the URL: {url}"
        )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    scanner = soup.find("scanner")
    if scanner is None:
        raise RuntimeError(
            f"Could not read screener '{clean}' — page layout changed or login "
            f"required. Open {url} in a browser to verify it is public."
        )
    raw_scan = scanner.get(":scan-json")
    if not raw_scan:
        raise RuntimeError(
            f"Screener '{clean}' has no readable condition (it may be private). "
            f"Ask the owner to share the scan clause, then use run_screener. URL: {url}"
        )
    try:
        scan = json.loads(raw_scan)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse screener '{clean}' data: {e}")

    likes_total = None
    try:
        raw_likes = scanner.get(":scan-likes")
        if raw_likes:
            likes_total = json.loads(raw_likes).get("totalLikes")
    except Exception:
        likes_total = None

    author: dict[str, Any] = {}
    try:
        raw_owner = scanner.get(":public-owner-profile")
        if raw_owner:
            owner = json.loads(raw_owner)
            username = owner.get("username") or ""
            author = {
                "id": owner.get("id"),
                "name": owner.get("name"),
                "username": username,
                "profile_url": _profile_url(username) if username else None,
                "avatar_url": owner.get("avatar_url"),
                "is_premium": owner.get("is_premium"),
            }
    except Exception:
        author = {}

    cat_id = scan.get("scan_category_id")
    # Resolve category name from the page's own category list when possible.
    category_name = CATEGORY_ID_TO_NAME.get(cat_id)
    try:
        raw_cats = scanner.get(":scan-categories-json")
        if raw_cats:
            for c in json.loads(raw_cats):
                if c.get("id") == cat_id:
                    category_name = c.get("name") or category_name
                    break
    except Exception:
        pass

    return {
        "id": scan.get("id"),
        "name": scan.get("name"),
        "slug": scan.get("slug") or clean,
        "url": _screener_url(scan.get("slug") or clean),
        "description": scan.get("description"),
        "author": author,
        "likes_total": likes_total,
        "created_at": scan.get("created_at"),
        "category_id": cat_id,
        "category": category_name,
        "scan_clause": scan.get("atlas_query"),
        "is_private": scan.get("is_private"),
    }


def _fetch_user_scans(username: str) -> dict:
    """Fetch a public Chartink profile's latest + popular scans (no auth)."""
    clean = _normalize_username(username)
    if not clean:
        raise RuntimeError("Empty username. Pass e.g. 'admin' or 'https://chartink.com/@admin'.")
    url = _profile_url(clean)
    resp = _http_get(url)
    if resp.status_code == 404:
        raise RuntimeError(f"Chartink user '{clean}' not found (404). URL tried: {url}")
    resp.raise_for_status()
    payload = _get_data_page_payload(resp.text)
    props = payload.get("props", {})
    profile = props.get("profile") or {}
    stats = props.get("stats") or {}

    def _norm_scan(s: dict, found_in: str) -> dict:
        slug = s.get("slug") or ""
        rel = s.get("url") or (f"/screener/{slug}" if slug else None)
        full = f"{BASE_URL}{rel}" if rel and rel.startswith("/") else rel
        return {
            "id": s.get("id"),
            "name": s.get("name"),
            "slug": slug,
            "url": full,
            "description": s.get("description"),
            "views": s.get("views"),
            "likes": s.get("likes"),
            "created_at": s.get("created_at"),
            "updated_at": s.get("updated_at"),
            "found_in": found_in,
        }

    combined: dict[Any, dict] = {}
    for s in props.get("popularScans") or []:
        if s.get("id") is None:
            continue
        combined[s["id"]] = _norm_scan(s, "popular")
    for s in props.get("latestScans") or []:
        if s.get("id") is None:
            continue
        if s["id"] in combined:
            existing = combined[s["id"]]
            if "latest" not in existing["found_in"]:
                existing["found_in"] = existing["found_in"] + "+latest"
        else:
            combined[s["id"]] = _norm_scan(s, "latest")

    scans = sorted(
        combined.values(),
        key=lambda s: ((s.get("likes") or 0), (s.get("views") or 0)),
        reverse=True,
    )
    prof_username = profile.get("username") or clean
    return {
        "profile": {
            "id": profile.get("id"),
            "name": profile.get("name"),
            "username": prof_username,
            "profile_url": _profile_url(prof_username),
            "avatar_url": profile.get("avatar_url"),
            "is_premium": profile.get("is_premium"),
            "bio": profile.get("bio"),
            "joined_at": profile.get("joined_at"),
        },
        "stats": stats,
        "scans": scans,
    }


async def _get_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="set_cookies",
            description=(
                "Set your Chartink browser session cookies so the server can make "
                "authenticated requests. Paste the raw Cookie header string from "
                "Chrome DevTools → Network → any chartink.com request → Headers → Cookie."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "cookie_string": {
                        "type": "string",
                        "description": "Raw Cookie header, e.g. 'XSRF-TOKEN=abc; ci_session=xyz; ...'",
                    }
                },
                "required": ["cookie_string"],
            },
        ),
        types.Tool(
            name="server_status",
            description=(
                "Check MCP server health without running a scan: site reachability "
                "+ latency, whether login cookies are present and their age, whether "
                "cookies were restored from disk, and premium detection. No login needed."
            ),
            input_schema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="list_indicators",
            description=(
                "Cheat-sheet of valid Chartink scan-clause vocabulary: indicators "
                "(sma, rsi, macd, ichimoku, supertrend…), functions (max/min/count/abs), "
                "segments ({cash}/{futures}), timeframes/offsets, and fundamental fields — "
                "each with params and an example. Call this BEFORE writing a clause so you "
                "don't guess field names ('52 week high' is invalid — see notes). No login needed."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Optional filter: 'indicator', 'function', 'fundamental', 'stock_attribute'. Omit for all.",
                    }
                },
                "required": [],
            },
        ),
        types.Tool(
            name="validate_scan",
            description=(
                "Validate a scan clause WITHOUT wasting runs: checks parens balance, "
                "{segment} wrapper, one-comparison-per-filter, unknown identifiers "
                "(with suggestions), and the 'N week high' trap. Optionally does a live "
                "1-row dry run when login cookies exist. Always call before run_screener "
                "on a hand-written clause."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "scan_clause": {"type": "string", "description": "Chartink scan condition string"},
                    "dry_run": {
                        "type": "boolean",
                        "description": "Live 1-row run to confirm the endpoint accepts it (needs cookies; default true)",
                        "default": True,
                    },
                },
                "required": ["scan_clause"],
            },
        ),
        types.Tool(
            name="get_fundamentals",
            description=(
                "Snapshot + quarterly/yearly/balance-sheet/cash-flow fundamentals for one "
                "NSE/BSE stock from Chartink's fundamentals page (figures in ₹ crore unless "
                "noted). No login needed. Symbol like 'RELIANCE' or 'reliance'."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE/BSE symbol, e.g. RELIANCE"},
                },
                "required": ["symbol"],
            },
        ),
        types.Tool(
            name="compare_screeners",
            description=(
                "Run 2+ scan clauses and join the hits: per-screener counts, overlap ranked "
                "by appearance count (which stocks pass multiple strategies), and uniques "
                "per screener. Joins on nsecode (fallback bsecode). Needs cookies."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "screeners": {
                        "type": "array",
                        "description": "2+ screeners to compare",
                        "minItems": 2,
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "scan_clause": {"type": "string"},
                                "max_rows": {"type": "integer", "default": 160},
                            },
                            "required": ["name", "scan_clause"],
                        },
                    }
                },
                "required": ["screeners"],
            },
        ),
        types.Tool(
            name="scan_history",
            description=(
                "Recall past runs from the local append-only log (tool, label, clause "
                "fingerprint, hit count, timestamp). Filter by keyword across label/clause. "
                "Use it to re-find 'that 52-week-high screen from last week'. Local only, no login."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max entries, newest first (default 20, max 100)", "default": 20},
                    "query": {"type": "string", "description": "Optional keyword filter"},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="list_segments",
            description=(
                "List runnable universes for the 'segment' param (cash, futures, index "
                "and group names with their numeric ids). No login needed."
            ),
            input_schema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="backtest_summary",
            description=(
                "Run a backtest and summarize it: matches over time (latest count, peak, "
                "trend), per-sector totals ranked, and the sector time series. NOTE: "
                "Chartink backtests cover only the last ~150 candles of the lowest "
                "timeframe, and the endpoint returns sector match counts — not per-trade "
                "win rates. Works with a fresh XSRF session; set_cookies if it fails."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "scan_clause": {"type": "string", "description": "Chartink scan condition string"},
                    "max_rows": {"type": "integer", "description": "Max rows (default 160)", "default": 160},
                    "segment": {
                        "type": "string",
                        "description": "Universe: 'cash', 'futures', index/watchlist name, or numeric id.",
                    },
                    "name": {"type": "string", "description": "Optional label"},
                },
                "required": ["scan_clause"],
            },
        ),
        types.Tool(
            name="list_my_scans",
            description=(
                "List YOUR scans from the scan dashboard (login required via set_cookies). "
                "Returns your scan lists with slugs/URLs. Experimental: dashboard payload "
                "shape is confirmed after first logged-in use — raw keys included."
            ),
            input_schema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="list_alerts",
            description=(
                "List YOUR alerts from the alert dashboard (login + premium via set_cookies). "
                "Experimental: payload shape confirmed after first logged-in use."
            ),
            input_schema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="list_watchlists",
            description=(
                "List YOUR watchlists (login required via set_cookies). Names/ids can feed "
                "the 'segment' param of run tools. Experimental: shape confirmed after "
                "first logged-in use."
            ),
            input_schema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="search_all_screeners",
            description=(
                "Full-text search across ALL Chartink public scans (not just the curated "
                "catalog) — same search the website uses. Returns id/slug/exact URL, "
                "description, likes, backtest hit counts, category, and the runnable "
                "scan_clause. Paginated: pass page=2+ to see more. No login needed. "
                "Prefer this over search_screeners when the scan isn't famous."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search text, e.g. '52 week high', 'supertrend'"},
                    "limit": {"type": "integer", "description": "Max results (default 10, max 50 = 5 pages)", "default": 10},
                    "page": {"type": "integer", "description": "Start page (default 1)", "default": 1},
                    "sort": {
                        "type": "string",
                        "description": "Result order: 'relevance' (default), 'likes', or 'newest' (client-side re-sort of fetched rows)",
                        "default": "relevance",
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="browse_catalog",
            description=(
                "Browse Chartink's merchandised scan groups: 'top_loved' (paginated, "
                "with like counts) or any curated group from the main page "
                "('most_loved', 'fundamental', 'bullish', 'bearish', 'intraday_bullish', "
                "'intraday_bearish', 'breakouts', 'candlestick', 'crossover'). "
                "No login needed."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "group": {"type": "string", "description": "Group to browse (default 'top_loved')", "default": "top_loved"},
                    "limit": {"type": "integer", "description": "Max scans (default 10, max 50)", "default": 10},
                    "page": {"type": "integer", "description": "Page for top_loved (default 1)", "default": 1},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="search_screeners",
            description=(
                "Search Chartink's curated public screener catalog by keyword "
                "(matches name, description, and scan condition). Returns exact "
                "slug + URL + id + category + description + scan_clause for each "
                "match so you can distinguish same-name scans. Use the slug with "
                "get_screener_details or run_saved_screener. No login needed."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword to search, e.g. 'rsi breakout', 'golden crossover'. Empty returns category overview.",
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional category filter, e.g. 'Bullish scan', 'Intraday Bearish scan', 'Fundamental Scans', 'Crossover' (substring ok).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max matches to return (default 10, max 50)",
                        "default": 10,
                    },
                    "sort": {
                        "type": "string",
                        "description": "Order: 'relevance' (default) or 'newest'.",
                        "default": "relevance",
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="get_screener_details",
            description=(
                "Get full metadata for one public screener WITHOUT running it: "
                "exact URL, id, slug, description, author (name/username/profile URL), "
                "likes, created date, category, and the full scan_clause so you can "
                "understand it first. Accepts a bare slug or full /screener/ URL. "
                "No login needed for public scans."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Screener slug or full URL, e.g. 'short-term-breakouts' or 'https://chartink.com/screener/short-term-breakouts'",
                    }
                },
                "required": ["slug"],
            },
        ),
        types.Tool(
            name="list_user_scans",
            description=(
                "List public scans created by one Chartink user. Returns the author's "
                "profile (with exact profile URL), stats, and each scan's exact URL, "
                "slug, id, description, views, likes, and dates — so same-name scans "
                "from different authors stay distinguishable. No login needed."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "username": {
                        "type": "string",
                        "description": "Chartink username, e.g. 'admin' or 'https://chartink.com/@admin'",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max scans to return, sorted by likes (default 20, max 50)",
                        "default": 20,
                    },
                    "include_clause": {
                        "type": "boolean",
                        "description": "Attach each scan's runnable scan_clause (costs one page fetch per scan; default false)",
                        "default": False,
                    },
                },
                "required": ["username"],
            },
        ),
        types.Tool(
            name="run_screener",
            description=(
                "Run a Chartink screener scan clause and return matching stocks. "
                "Uses the /screener/process endpoint. "
                "Requires cookies to be set first via set_cookies."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "scan_clause": {
                        "type": "string",
                        "description": "Chartink scan condition string",
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "Max results to return (default 160)",
                        "default": 160,
                    },
                    "name": {
                        "type": "string",
                        "description": "Optional label for this screener",
                    },
                    "segment": {
                        "type": "string",
                        "description": "Universe to run in: 'cash' (default), 'futures', an index/watchlist name (e.g. 'nifty 50'), or numeric group id. Ignored if the clause already has a {…} token. Watchlist segments need premium.",
                    },
                    "sort_by": {
                        "type": "string",
                        "description": "Sort hits: 'volume', 'per_chg', or 'close' (default: endpoint order).",
                    },
                    "min_price": {
                        "type": "number",
                        "description": "Drop hits with close below this price.",
                    },
                    "format": {
                        "type": "string",
                        "description": "'json' (default) or 'csv' for spreadsheets.",
                        "default": "json",
                    },
                },
                "required": ["scan_clause"],
            },
        ),
        types.Tool(
            name="run_multiple_screeners",
            description=(
                "Run multiple Chartink scan clauses in one call and return combined results. "
                "Each screener runs independently. Useful for comparing multiple strategies."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "screeners": {
                        "type": "array",
                        "description": "List of screeners to run",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Label for this screener"},
                                "scan_clause": {"type": "string", "description": "Chartink scan condition"},
                                "max_rows": {"type": "integer", "default": 160},
                            },
                            "required": ["name", "scan_clause"],
                        },
                    }
                },
                "required": ["screeners"],
            },
        ),
        types.Tool(
            name="run_backtest",
            description=(
                "Run a Chartink backtest clause. Returns the CURRENT matches (latest "
                "candle snapshot with symbol + market-cap + sector) plus the as-of date. "
                "For matches-over-time per sector and trend, use backtest_summary. "
                "Requires cookies to be set first via set_cookies."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "scan_clause": {
                        "type": "string",
                        "description": "Chartink scan condition string",
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "Max results to return (default 160)",
                        "default": 160,
                    },
                    "name": {
                        "type": "string",
                        "description": "Optional label for this backtest",
                    },
                    "segment": {
                        "type": "string",
                        "description": "Universe to run in: 'cash' (default), 'futures', an index/watchlist name (e.g. 'nifty 50'), or numeric group id. Ignored if the clause already has a {…} token. Watchlist segments need premium.",
                    },
                    "sort_by": {
                        "type": "string",
                        "description": "Sort hits: 'volume', 'per_chg', or 'close' (default: endpoint order).",
                    },
                    "min_price": {
                        "type": "number",
                        "description": "Drop hits with close below this price.",
                    },
                    "format": {
                        "type": "string",
                        "description": "'json' (default) or 'csv' for spreadsheets.",
                        "default": "json",
                    },
                },
                "required": ["scan_clause"],
            },
        ),
        types.Tool(
            name="run_saved_screener",
            description=(
                "Fetch a public saved screener by slug/URL and RUN it. Returns full "
                "metadata (exact URL, id, author, likes, description, scan_clause) "
                "plus matching stocks, so you know exactly which same-name scan ran. "
                "Requires cookies (set_cookies) for the run; the metadata fetch itself "
                "needs no login. Prefer search_screeners/get_screener_details first "
                "to confirm you have the right scan."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Screener slug or full URL from chartink.com/screener/<slug>",
                    },
                    "max_rows": {
                        "type": "integer",
                        "default": 160,
                    },
                    "sort_by": {
                        "type": "string",
                        "description": "Sort hits: 'volume', 'per_chg', or 'close'.",
                    },
                    "min_price": {
                        "type": "number",
                        "description": "Drop hits with close below this price.",
                    },
                    "format": {
                        "type": "string",
                        "description": "'json' (default) or 'csv'.",
                        "default": "json",
                    },
                },
                "required": ["slug"],
            },
        ),
    ]


async def _dispatch_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:

    if name == "set_cookies":
        cookie_string = arguments["cookie_string"]
        _session.cookies.clear()
        for part in cookie_string.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                _session.cookies.set(k.strip(), v.strip(), domain="chartink.com")
        _session.cookies.set("chartink_saved_at", str(int(time.time())), domain="chartink.com")
        _save_cookies_to_disk()  # R-01: survive MCP restarts (0600 file)
        token = _session.cookies.get("XSRF-TOKEN", domain="chartink.com")
        status = "XSRF-TOKEN found ✓" if token else "Warning: XSRF-TOKEN not in cookies"
        return [types.TextContent(type="text", text=f"Cookies set and saved to disk. {status}")]

    elif name == "server_status":
        try:
            return [types.TextContent(type="text", text=json.dumps(_server_status(), indent=2))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]

    elif name == "list_indicators":
        category = str(arguments.get("category") or "").strip().lower()
        measures = INDICATORS_REFERENCE["measures"]
        if category:
            measures = [m for m in measures if m.get("kind") == category]
            if not measures:
                return [types.TextContent(
                    type="text",
                    text=f"Error: unknown category '{category}'. Use indicator|function|fundamental|stock_attribute.",
                )]
        payload = {**INDICATORS_REFERENCE, "measures": measures,
                   "measure_count": len(measures)}
        return [types.TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))]

    elif name == "validate_scan":
        scan_clause = arguments.get("scan_clause", "")
        dry_run = arguments.get("dry_run", True)
        errors, warnings = _static_clause_checks(scan_clause)
        unknown = _check_identifiers(scan_clause)
        if unknown:
            errors.append(
                "Unknown identifiers: "
                + "; ".join(
                    f"'{u['unknown']}'"
                    + (f" (did you mean: {', '.join(u['suggestions'])})" if u["suggestions"] else "")
                    for u in unknown
                )
                + ". Chartink HTTP-200s with zero rows for bad field names."
            )
        dry: dict[str, Any] = {"attempted": False}
        if dry_run and not errors:
            has_auth = bool(_session.cookies.get("ci_session", domain="chartink.com"))
            if not has_auth:
                dry = {"attempted": False, "status": "skipped_no_auth",
                       "hint": "set_cookies first for a live 1-row dry run; static checks below still apply."}
            else:
                try:
                    data = _post_screener(scan_clause, 1)
                    dry = {"attempted": True, "status": "ok",
                           "dry_run_count": len(data.get("data", []))}
                except Exception as e:
                    dry = {"attempted": True, "status": "endpoint_rejected",
                           "detail": _safe_err(e)}
                    errors.append(f"Endpoint rejected the clause: {_safe_err(e)}")
        elif dry_run and errors:
            dry = {"attempted": False, "status": "skipped_static_errors"}
        result = {"valid": not errors, "errors": errors, "warnings": warnings,
                  "unknown_identifiers": unknown, "dry_run": dry}
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "get_fundamentals":
        symbol = str(arguments.get("symbol") or "").strip().strip(".html")
        if not symbol:
            return [types.TextContent(type="text", text="Error: 'symbol' is required, e.g. RELIANCE.")]
        try:
            url = f"{BASE_URL}/fundamentals/{symbol.lower()}.html"
            resp = _http_get(url)
            if resp.status_code == 404:
                return [types.TextContent(type="text", text=f"Error: no fundamentals page for '{symbol}' (404). Check the NSE/BSE symbol.")]
            resp.raise_for_status()
            parsed = _parse_fundamentals(resp.text)
            if not parsed["snapshot"] and not parsed["tables"]:
                return [types.TextContent(type="text", text=f"Error: no fundamentals data found for '{symbol}'. Check the NSE/BSE symbol spelling.")]
            payload = {"symbol": symbol.upper(), "url": url, **parsed,
                       "units_note": "Money figures in ₹ crore unless the label says otherwise."}
            return [types.TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]

    elif name == "compare_screeners":
        screeners = arguments.get("screeners") or []
        if len(screeners) < 2:
            return [types.TextContent(type="text", text="Error: compare needs 2+ screeners.")]

        def _key(row: dict) -> str:
            return str(row.get("nsecode") or row.get("bsecode") or row.get("name") or "").upper()

        try:
            per: list[dict] = []
            by_key: dict[str, dict] = {}
            for i, s in enumerate(screeners):
                if i > 0:
                    time.sleep(0.5)
                mr = s.get("max_rows", 160)
                blocked = _r02_precheck(s.get("scan_clause", ""))
                if blocked:
                    per.append({"screener": s["name"], "error": blocked["warning"]})
                    continue
                try:
                    data = _post_screener(s["scan_clause"], mr)
                    stocks = data.get("data", [])
                    _log_history("compare_screeners", s["scan_clause"], len(stocks), s["name"])
                    per.append({"screener": s["name"], "count": len(stocks)})
                    for row in stocks:
                        k = _key(row)
                        if not k:
                            continue
                        slot = by_key.setdefault(k, {"symbol": k, "name": row.get("name"),
                                                     "close": row.get("close"), "appears_in": []})
                        if s["name"] not in slot["appears_in"]:
                            slot["appears_in"].append(s["name"])
                except Exception as e:
                    per.append({"screener": s["name"], "error": _safe_err(e)})
            overlap = sorted(
                (v for v in by_key.values() if len(v["appears_in"]) >= 2),
                key=lambda v: (-len(v["appears_in"]), v["symbol"]),
            )
            names = [s["name"] for s in screeners]
            unique_to = {
                n: sorted(k for k, v in by_key.items() if v["appears_in"] == [n])[:100]
                for n in names
            }
            payload = {"per_screener": per, "overlap_count": len(overlap),
                       "overlap": overlap[:200], "unique_to": unique_to}
            return [types.TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]

    elif name == "scan_history":
        try:
            limit = max(1, min(int(arguments.get("limit", 20)), 100))
        except (TypeError, ValueError):
            limit = 20
        query = str(arguments.get("query") or "").strip().lower()
        try:
            entries: list[dict] = []
            if _HISTORY_FILE.exists():
                for line in _HISTORY_FILE.read_text().splitlines():
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        continue
            if query:
                entries = [e for e in entries
                           if query in str(e.get("label") or "").lower()
                           or query in str(e.get("scan_clause") or "").lower()]
            entries.sort(key=lambda e: e.get("ts", 0), reverse=True)
            payload = {"total": len(entries), "returned": len(entries[:limit]),
                       "entries": entries[:limit]}
            return [types.TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]

    elif name == "list_segments":
        try:
            by_name = _get_segments()
            inv: dict[str, list[str]] = {}
            for name, sid in by_name.items():
                inv.setdefault(sid, []).append(name)
            payload = {
                "segments": [{"id": sid, "names": sorted(names)} for sid, names in sorted(inv.items())],
                "usage": 'run_screener({scan_clause: "Latest close > 100", segment: "futures"})',
            }
            return [types.TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]

    elif name == "backtest_summary":
        scan_clause = arguments.get("scan_clause", "")
        max_rows = arguments.get("max_rows", 160)
        label = arguments.get("name", "Backtest")
        try:
            scan_clause, applied_segment = _apply_segment(scan_clause, arguments.get("segment"))
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]
        blocked = _r02_precheck(scan_clause)
        if blocked:
            blocked["backtest"] = label
            return [types.TextContent(type="text", text=json.dumps(blocked, indent=2, ensure_ascii=False))]
        try:
            data = _post_backtest(scan_clause, max_rows)
            groups = data.get("groupData") or []
            per_sector: list[dict] = []
            overall: list[int] = []
            for g in groups:
                try:
                    series = list(g.get("results", [])[0].values())[0]
                except Exception:
                    continue
                series = [int(x) for x in series]
                if not overall:
                    overall = [0] * len(series)
                for i, v in enumerate(series):
                    if i < len(overall):
                        overall[i] += v
                per_sector.append({"sector": g.get("name"), "total": sum(series),
                                   "latest": series[-1] if series else 0,
                                   "peak": max(series) if series else 0})
            per_sector.sort(key=lambda s: s["total"], reverse=True)
            n = len(overall)
            trend = None
            if n >= 20:
                first = sum(overall[: n // 2]) / max(1, n // 2)
                second = sum(overall[n // 2:]) / max(1, n - n // 2)
                trend = "rising" if second > first * 1.1 else ("falling" if first > second * 1.1 else "flat")
            _log_history("backtest_summary", scan_clause, sum(overall), label)
            payload = {
                "backtest": label, "segment_applied": applied_segment,
                "candles": data.get("time"), "latest_matches": overall[-1] if overall else 0,
                "peak_matches": max(overall) if overall else 0,
                "trend_first_half_vs_second_half": trend,
                "coverage_note": "Last ~150 candles of the lowest timeframe only.",
                "sectors_ranked": per_sector[:20],
                "overall_series_tail": overall[-20:],
            }
            return [types.TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]

    elif name == "list_my_scans":
        try:
            props = _fetch_dashboard_props("scan_dashboard")
            payload = {"lists": _extract_named_lists(props), "prop_keys": sorted(props.keys()),
                       "note": "Use a scan's slug/URL with get_screener_details / run_saved_screener."}
            return [types.TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]

    elif name == "list_alerts":
        try:
            props = _fetch_dashboard_props("alert_dashboard")
            payload = {"lists": _extract_named_lists(props), "prop_keys": sorted(props.keys())}
            return [types.TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]

    elif name == "list_watchlists":
        try:
            props = _fetch_dashboard_props("watchlist_dashboard")
            payload = {"lists": _extract_named_lists(props), "prop_keys": sorted(props.keys()),
                       "note": "A watchlist name/id can be passed as 'segment' to run tools (premium)."}
            return [types.TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]

    elif name == "search_all_screeners":
        from urllib.parse import quote as _quote
        query = str(arguments.get("query") or "").strip()
        if not query:
            return [types.TextContent(type="text", text="Error: 'query' is required, e.g. '52 week high'.")]
        try:
            limit = max(1, min(int(arguments.get("limit", 10)), 50))
        except (TypeError, ValueError):
            limit = 10
        try:
            start_page = max(1, int(arguments.get("page", 1)))
        except (TypeError, ValueError):
            start_page = 1
        sort = str(arguments.get("sort") or "relevance").strip().lower()
        if sort not in ("relevance", "likes", "newest"):
            return [types.TextContent(type="text", text="Error: sort must be relevance|likes|newest.")]
        try:
            rows: list[dict] = []
            total = None
            page = start_page
            while len(rows) < limit and page < start_page + 5:
                url = f"{BASE_URL}/screeners/search?searchTerm={_quote(query)}&page={page}"
                resp = _http_get(url)
                resp.raise_for_status()
                parsed = _parse_searched_screeners(resp.text)
                total = parsed["total"]
                if not parsed["rows"]:
                    break
                rows.extend(parsed["rows"])
                if page >= (parsed["last_page"] or page):
                    break
                page += 1
            rows = rows[:limit]
            if sort == "likes":
                rows.sort(key=lambda r: (r.get("likes") or 0), reverse=True)
            elif sort == "newest":
                rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
            payload = {
                "query": query, "sort": sort, "total_matches": total,
                "returned": len(rows), "start_page": start_page,
                "note": "Slugs/URLs are unique — use them with get_screener_details / run_saved_screener. "
                        "Author names need get_screener_details per scan (rows carry user_id only).",
                "results": rows,
            }
            return [types.TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]

    elif name == "browse_catalog":
        from urllib.parse import quote as _quote2
        group = str(arguments.get("group") or "top_loved").strip().lower()
        try:
            limit = max(1, min(int(arguments.get("limit", 10)), 50))
        except (TypeError, ValueError):
            limit = 10
        try:
            page = max(1, int(arguments.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            if group in ("top_loved", "top-loved", "most_loved", "most-loved", "loved"):
                rows: list[dict] = []
                total = None
                p = page
                while len(rows) < limit and p < page + 5:
                    url = f"{BASE_URL}/screeners/top-loved-screeners?page={p}"
                    resp = _http_get(url)
                    resp.raise_for_status()
                    parsed = _parse_searched_screeners(resp.text)
                    total = parsed["total"]
                    if not parsed["rows"]:
                        break
                    rows.extend(parsed["rows"])
                    if p >= (parsed["last_page"] or p):
                        break
                    p += 1
                payload = {"group": "top_loved", "total": total, "returned": len(rows[:limit]),
                           "results": rows[:limit]}
                return [types.TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))]
            # Curated groups from the main /screeners/ page.
            scans = _get_catalog()
            alias = {"fundamental": "fundamental scans", "bullish": "bullish scan",
                     "bearish": "bearish scan", "intraday_bullish": "intraday bullish",
                     "intraday_bearish": "intraday bearish", "breakouts": "breakouts",
                     "candlestick": "candlestick", "crossover": "crossover",
                     "most_loved": "most loved"}
            key = alias.get(group, group)
            matched = [s for s in scans
                       if any(key in (g or "").lower() for g in (s.get("source_groups") or []))
                       or key in str(s.get("category") or "").lower()]
            if not matched and group not in ("most_loved", "most-loved"):
                return [types.TextContent(type="text", text=(
                    f"Error: unknown group '{group}'. Use top_loved, most_loved, fundamental, "
                    "bullish, bearish, intraday_bullish, intraday_bearish, breakouts, candlestick, crossover."
                ))]
            if not matched:  # most_loved == top_loved curated slice
                matched = [s for s in scans if "most loved" in [g.lower() for g in (s.get("source_groups") or [])]]
            payload = {"group": group, "total": len(matched), "returned": len(matched[:limit]),
                       "results": matched[:limit]}
            return [types.TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]

    elif name == "search_screeners":
        query = str(arguments.get("query") or "").strip()
        category = str(arguments.get("category") or "").strip()
        try:
            limit = int(arguments.get("limit", 10))
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 50))
        try:
            scans = _get_catalog()
            if not query:
                groups: dict[str, int] = {}
                for s in scans:
                    for g in s.get("source_groups") or []:
                        groups[g] = groups.get(g, 0) + 1
                overview = {
                    "message": "Pass a 'query' to search. Catalog overview below.",
                    "total_scans": len(scans),
                    "groups": groups,
                    "categories": sorted(set(CATEGORY_ID_TO_NAME.values())),
                    "sample": [
                        {"name": s["name"], "slug": s["slug"], "url": s["url"]}
                        for s in scans[:5]
                    ],
                }
                return [types.TextContent(type="text", text=json.dumps(overview, indent=2, ensure_ascii=False))]
            q = query.lower()
            tokens = [t for t in re.split(r"\s+", q) if t]
            scored: list[tuple[float, dict]] = []
            for s in scans:
                if not _matches_category(s, category):
                    continue
                score = _score_scan(s, q, tokens)
                if score > 0:
                    scored.append((score, s))
            scored.sort(key=lambda kv: (-kv[0], str(kv[1].get("name") or "").lower()))
            total = len(scored)
            results = [s for _, s in scored[:limit]]
            sort = str(arguments.get("sort") or "relevance").strip().lower()
            if sort == "likes":
                # No like counts in curated payload — keep relevance order, say so.
                pass
            elif sort == "newest":
                results.sort(key=lambda s: str(s.get("created_at") or ""), reverse=True)
            payload = {
                "query": query,
                "category_filter": category or None,
                "total_matches": total,
                "returned": len(results),
                "note": "Slugs/URLs are unique — use them (not names) with get_screener_details / run_saved_screener.",
                "results": results,
            }
            if total == 0:
                payload["suggestion"] = (
                    "No curated hits — try search_all_screeners (full-text over every public scan)."
                )
            return [types.TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]

    elif name == "get_screener_details":
        slug = arguments.get("slug", "")
        try:
            details = _fetch_screener_details(slug)
            details["note"] = "Use the exact 'slug'/'url' (unique) to run it via run_saved_screener."
            return [types.TextContent(type="text", text=json.dumps(details, indent=2, ensure_ascii=False))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]

    elif name == "list_user_scans":
        username = arguments.get("username", "")
        try:
            limit = int(arguments.get("limit", 20))
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 50))
        try:
            data = _fetch_user_scans(username)
            total = len(data["scans"])
            data["scans"] = data["scans"][:limit]
            if arguments.get("include_clause"):
                for s in data["scans"]:
                    try:
                        details = _fetch_screener_details(s["slug"])
                        s["scan_clause"] = details.get("scan_clause")
                        time.sleep(0.3)
                    except Exception as e:
                        s["scan_clause"] = None
                        s["clause_error"] = _safe_err(e)
            data["total_scans_found"] = total
            data["returned"] = len(data["scans"])
            data["note"] = "Each scan's slug/URL is unique — use it with get_screener_details / run_saved_screener."
            return [types.TextContent(type="text", text=json.dumps(data, indent=2, ensure_ascii=False))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]

    elif name == "run_screener":
        scan_clause = arguments["scan_clause"]
        max_rows = arguments.get("max_rows", 160)
        label = arguments.get("name", "Screener")
        fmt = str(arguments.get("format") or "json").strip().lower()
        if fmt not in ("json", "csv"):
            return [types.TextContent(type="text", text="Error: format must be json|csv.")]
        try:
            scan_clause, applied_segment = _apply_segment(scan_clause, arguments.get("segment"))
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]
        blocked = _r02_precheck(scan_clause)
        if blocked:
            blocked["screener"] = label
            return [types.TextContent(type="text", text=json.dumps(blocked, indent=2, ensure_ascii=False))]
        try:
            data = _post_screener(scan_clause, max_rows)
            stocks = data.get("data", [])
            _log_history("run_screener", scan_clause, len(stocks), label)
            try:
                min_price = arguments.get("min_price")
                min_price = float(min_price) if min_price is not None else None
            except (TypeError, ValueError):
                min_price = None
            stocks = _shape_results(stocks, arguments.get("sort_by"), min_price)[:max_rows]
            if fmt == "csv":
                return [types.TextContent(type="text", text=(
                    f"# {label} | segment={applied_segment or 'as-written'} | count={len(stocks)}\n"
                    + _to_csv(stocks)))]
            result = {"screener": label, "segment_applied": applied_segment,
                      "scan_clause_run": scan_clause, "count": len(stocks),
                      "returned": min(len(stocks), max_rows), "stocks": stocks[:max_rows]}
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]

    elif name == "run_multiple_screeners":
        screeners = arguments["screeners"]
        results = []
        for i, s in enumerate(screeners):
            if i > 0:
                time.sleep(0.5)  # R-04: be gentle on the session-sensitive endpoint
            mr = s.get("max_rows", 160)
            blocked = _r02_precheck(s.get("scan_clause", ""))
            if blocked:
                blocked["screener"] = s["name"]
                results.append(blocked)
                continue
            try:
                data = _post_screener(s["scan_clause"], mr)
                stocks = data.get("data", [])
                _log_history("run_multiple_screeners", s["scan_clause"], len(stocks), s["name"])
                results.append({"screener": s["name"], "count": len(stocks),
                                "returned": min(len(stocks), mr), "stocks": stocks[:mr]})
            except Exception as e:
                results.append({"screener": s["name"], "error": _safe_err(e)})
        return [types.TextContent(type="text", text=json.dumps(results, indent=2))]

    elif name == "run_backtest":
        scan_clause = arguments["scan_clause"]
        max_rows = arguments.get("max_rows", 160)
        label = arguments.get("name", "Backtest")
        try:
            scan_clause, _ = _apply_segment(scan_clause, arguments.get("segment"))
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]
        blocked = _r02_precheck(scan_clause)
        if blocked:
            blocked["backtest"] = label
            return [types.TextContent(type="text", text=json.dumps(blocked, indent=2, ensure_ascii=False))]
        try:
            data = _post_backtest(scan_clause, max_rows)
            parsed = _parse_backtest_payload(data)
            stocks = parsed["stocks"]
            _log_history("run_backtest", scan_clause, len(stocks), label)
            result = {"backtest": label, "as_of": parsed["as_of"],
                      "candles": parsed["candles"],
                      "count": len(stocks),
                      "returned": min(len(stocks), max_rows), "stocks": stocks[:max_rows],
                      "note": "Stocks = latest-candle snapshot. Full per-sector history: backtest_summary."}
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]

    elif name == "run_saved_screener":
        slug = arguments.get("slug", "")
        max_rows = arguments.get("max_rows", 160)
        try:
            details = _fetch_screener_details(slug)
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]
        scan_clause = (details.get("scan_clause") or "").strip()
        if not scan_clause:
            details["error"] = (
                "No runnable scan condition found (scan may be private). "
                "Ask the owner for the clause and use run_screener."
            )
            return [types.TextContent(type="text", text=json.dumps(details, indent=2, ensure_ascii=False))]
        blocked = _r02_precheck(scan_clause)
        if blocked:
            # Saved scans are site-generated (known-good), so warn but still run —
            # unlike hand-written clauses, blocking here has no upside.
            details["identifier_warning"] = blocked["warning"]
        url = details.get("url") or _screener_url(_normalize_slug(slug))
        try:
            data = _post_screener(scan_clause, max_rows, referer=url)
            stocks = data.get("data", [])
            _log_history("run_saved_screener", scan_clause, len(stocks), details.get("slug"))
            try:
                min_price = arguments.get("min_price")
                min_price = float(min_price) if min_price is not None else None
            except (TypeError, ValueError):
                min_price = None
            stocks = _shape_results(stocks, arguments.get("sort_by"), min_price)[:max_rows]
            fmt = str(arguments.get("format") or "json").strip().lower()
            if fmt == "csv":
                return [types.TextContent(type="text", text=(
                    f"# {details.get('name')} ({details.get('url')}) | count={len(stocks)}\n"
                    + _to_csv(stocks)))]
            result = {
                **details,
                "count": len(stocks),
                "returned": min(len(stocks), max_rows),
                "stocks": stocks[:max_rows],
            }
            return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]
        except Exception as e:
            details["run_error"] = (
                f"{e}. Metadata above was fetched OK; set fresh cookies via "
                "set_cookies and retry the run."
            )
            return [types.TextContent(type="text", text=json.dumps(details, indent=2, ensure_ascii=False))]

    return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


async def handle_list_tools(ctx, params) -> types.ListToolsResult:
    """V2 lowlevel handler: list all tools."""
    return types.ListToolsResult(tools=await _get_tools())


async def handle_call_tool(ctx, params) -> types.CallToolResult:
    """V2 lowlevel handler: dispatch to _dispatch_tool and wrap result."""
    tool_name = params.name
    tool_args = params.arguments or {}
    try:
        items = await _dispatch_tool(tool_name, tool_args)
    except Exception as e:
        items = [types.TextContent(type="text", text=f"Error: {_safe_err(e)}")]
    return types.CallToolResult(content=items)


app = Server(
    "chartink",
    version="2.0.0",
    on_list_tools=handle_list_tools,
    on_call_tool=handle_call_tool,
)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
