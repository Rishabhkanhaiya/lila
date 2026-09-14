"""
finance_engine.py — JARVIS Finance Engine v2.0
===============================================
Full-featured financial intelligence:
  • Stock price + 1-year history (yfinance)
  • Crypto prices (CoinGecko public API — zero API key needed)
  • Portfolio tracker (SQLite-backed)
  • News sentiment per ticker
  • Voice query handler for natural language finance questions
  • Ticker extraction from natural language (brain.py fallback if no SambaNova/Cerebras)
  • All data cached to avoid redundant API hits

Voice queries handled:
  "what's Apple stock"         → live price + change
  "how's Bitcoin"              → crypto price
  "my portfolio"               → portfolio summary
  "add AAPL 10 shares"         → portfolio add
  "remove TSLA"                → portfolio remove

Public API:
  handle_finance_command(text) → (handled: bool, response: str)
  fetch_stock_graph_data(prompt) → JSON string for UI chart
"""

import json
import re
import os
import sqlite3
import time
import httpx
from datetime import datetime
from pathlib import Path

from core.jarvis_logger import log_error, log_info, log_warn

# ── Optional deps ─────────────────────────────────────────────────────────────
try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False
    log_warn("finance", "yfinance not installed")

# ── DB ────────────────────────────────────────────────────────────────────────
_DB = str(Path(__file__).parent.parent / "jarvis_memory.db")

def _init_db():
    conn = sqlite3.connect(_DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT UNIQUE,
            shares      REAL DEFAULT 0,
            avg_cost    REAL DEFAULT 0,
            added_at    TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS finance_cache (
            ticker      TEXT PRIMARY KEY,
            data_json   TEXT,
            cached_at   REAL
        );
    """)
    conn.commit()
    conn.close()

_init_db()

# ── Price cache (in-memory, 60s TTL) ─────────────────────────────────────────
_price_cache: dict = {}
_CACHE_TTL = 60  # seconds


# ── Ticker extraction ─────────────────────────────────────────────────────────
# Known aliases — no LLM needed for common queries
_ALIASES = {
    "apple": "AAPL", "nvidia": "NVDA", "google": "GOOGL", "alphabet": "GOOGL",
    "microsoft": "MSFT", "amazon": "AMZN", "tesla": "TSLA", "meta": "META",
    "facebook": "META", "netflix": "NFLX", "samsung": "005930.KS",
    "reliance": "RELIANCE.NS", "tcs": "TCS.NS", "infosys": "INFY.NS",
    "wipro": "WIPRO.NS", "hdfc": "HDFCBANK.NS", "icici": "ICICIBANK.NS",
}

def extract_ticker(user_prompt: str) -> str | None:
    """Extract ticker from natural language. Uses alias map first, then LLM if needed."""
    text = user_prompt.lower()

    # 1. Check alias map
    for alias, ticker in _ALIASES.items():
        if alias in text:
            return ticker

    # 2. Look for explicit uppercase ticker (e.g. "AAPL")
    m = re.search(r'\b([A-Z]{1,5})\b', user_prompt)
    if m:
        candidate = m.group(1)
        # Sanity check with yfinance
        if HAS_YF:
            try:
                t = yf.Ticker(candidate)
                if getattr(t.fast_info, "last_price", None):
                    return candidate
            except Exception:
                pass

    # 3. Try LLM extraction (SambaNova/Cerebras)
    providers = [
        {"name": "SambaNova", "base_url": "https://api.sambanova.ai/v1",
         "api_key": os.environ.get("SAMBANOVA_API_KEY"),
         "model": "Meta-Llama-3.3-70B-Instruct"},
        {"name": "Cerebras",  "base_url": "https://api.cerebras.ai/v1",
         "api_key": os.environ.get("CEREBRAS_API_KEY"),
         "model": "llama3.1-70b"},
    ]
    prompt = (
        f'Identify the publicly traded company mentioned: "{user_prompt}"\n'
        "Return ONLY the uppercase ticker symbol (e.g. AAPL, TSLA). Nothing else."
    )
    for p in providers:
        if not p["api_key"]:
            continue
        try:
            from openai import OpenAI
            client = OpenAI(base_url=p["base_url"], api_key=p["api_key"])
            r = client.chat.completions.create(
                model=p["model"],
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            raw = r.choices[0].message.content
            ticker = "".join(e for e in raw if e.isalpha()).upper()
            if 1 <= len(ticker) <= 5:
                return ticker
        except Exception as e:
            log_warn("finance", f"LLM ticker extraction ({p['name']}): {e}")

    # 4. Brain fallback
    try:
        from core.brain import call_groq_brain
        result = call_groq_brain(
            f'Return ONLY the stock ticker for: "{user_prompt}". No explanation.',
            phase="DIRECTIVE", is_logic_task=False
        )
        if isinstance(result, str):
            ticker = "".join(e for e in result.strip() if e.isalpha()).upper()
            if 1 <= len(ticker) <= 5:
                return ticker
    except Exception:
        pass

    return None


def _gemini_stock_fallback(ticker: str) -> dict | None:
    """Grounding fallback using Gemini Google Search Grounding for live financial data."""
    try:
        from core.brain import get_gemini_client
        client = get_gemini_client()
        if not client:
            return None
        from google.genai import types
        res = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"What is the current stock price of {ticker}? Return ONLY JSON with fields: price (float), prev_close (float), change (float), pct_change (float), day_high (float), day_low (float).",
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                response_mime_type="application/json"
            )
        )
        if res and res.text:
            parsed = json.loads(res.text)
            parsed["ticker"] = ticker
            return parsed
    except Exception as e:
        log_warn("finance", f"Gemini finance search fallback failed: {e}")
    return None

# ── Stock data ────────────────────────────────────────────────────────────────
def get_stock_price(ticker: str) -> dict | None:
    """Get live stock price with 60s cache and Gemini Search Grounding fallback."""
    now = time.time()
    if ticker in _price_cache:
        cached_at, data = _price_cache[ticker]
        if now - cached_at < _CACHE_TTL:
            return data

    if HAS_YF:
        try:
            t     = yf.Ticker(ticker)
            price = round(getattr(t.fast_info, "last_price",     0), 2)
            prev  = round(getattr(t.fast_info, "previous_close", 1), 2)
            high  = round(getattr(t.fast_info, "day_high",       0), 2)
            low   = round(getattr(t.fast_info, "day_low",        0), 2)
            vol   = getattr(t.fast_info, "three_month_average_volume", 0)
            change = round(price - prev, 2)
            pct    = round((change / prev) * 100, 2) if prev else 0
            if price > 0:
                data   = {
                    "ticker": ticker, "price": price, "prev_close": prev,
                    "change": change, "pct_change": pct,
                    "day_high": high, "day_low": low,
                    "volume": int(vol or 0),
                }
                _price_cache[ticker] = (now, data)
                return data
        except Exception as e:
            log_warn("finance", f"yfinance fetch failed for {ticker}: {e}. Trying Gemini grounding fallback.")

    # Fallback to Gemini 2026 live search grounding
    fb_data = _gemini_stock_fallback(ticker)
    if fb_data and fb_data.get("price", 0) > 0:
        _price_cache[ticker] = (now, fb_data)
        return fb_data

    return None


def fetch_stock_graph_data(user_prompt: str) -> str | None:
    """Pull 1-year price history for charting. Returns JSON string."""
    ticker = extract_ticker(user_prompt)
    if not ticker:
        log_warn("finance", f"Could not extract ticker from: {user_prompt}")
        return None
    if not HAS_YF:
        return None
    try:
        stock = yf.Ticker(ticker)
        hist  = stock.history(period="1y")
        if hist.empty:
            return None
        dates  = [d.strftime("%Y-%m-%d") for d in hist.index]
        prices = [round(p, 2) for p in hist["Close"].tolist()]
        return json.dumps({
            "type": "line",
            "title": f"{ticker} — 1-Year Price (USD)",
            "labels": dates,
            "datasets": [{"name": "Close Price", "values": prices}],
            "analysis": f"Live data for {ticker}.",
        })
    except Exception as e:
        log_error("finance", "fetch_stock_graph_data", e)
        return None


# ── Crypto prices (CoinGecko — no API key) ───────────────────────────────────
_CRYPTO_IDS = {
    "bitcoin": "bitcoin",   "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "solana": "solana",     "sol": "solana",
    "bnb": "binancecoin",   "xrp": "ripple",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "cardano": "cardano",   "ada": "cardano",
    "polygon": "matic-network", "matic": "matic-network",
}

_crypto_cache: dict = {}

def get_crypto_price(name: str) -> dict | None:
    """Fetch crypto price via CoinGecko public API (no key required)."""
    coin_id = _CRYPTO_IDS.get(name.lower())
    if not coin_id:
        return None

    now = time.time()
    if coin_id in _crypto_cache:
        cached_at, data = _crypto_cache[coin_id]
        if now - cached_at < _CACHE_TTL:
            return data

    try:
        url = (f"https://api.coingecko.com/api/v3/simple/price"
               f"?ids={coin_id}&vs_currencies=usd&include_24hr_change=true")
        resp = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5.0)
        raw = resp.json()
        info = raw.get(coin_id, {})
        price  = info.get("usd", 0)
        change = round(info.get("usd_24h_change", 0), 2)
        data = {"coin": name, "id": coin_id, "price": price, "change_24h": change}
        _crypto_cache[coin_id] = (now, data)
        return data
    except Exception as e:
        log_error("finance", f"crypto_{coin_id}", e)
        return None


# ── Portfolio ─────────────────────────────────────────────────────────────────
def portfolio_add(ticker: str, shares: float, avg_cost: float = 0.0) -> str:
    conn = sqlite3.connect(_DB)
    conn.execute("""
        INSERT INTO portfolio (ticker, shares, avg_cost) VALUES (?,?,?)
        ON CONFLICT(ticker) DO UPDATE SET
            shares   = shares + excluded.shares,
            avg_cost = CASE WHEN avg_cost=0 THEN excluded.avg_cost
                       ELSE (avg_cost + excluded.avg_cost) / 2 END
    """, (ticker.upper(), shares, avg_cost))
    conn.commit()
    conn.close()
    return f"Added {shares} shares of {ticker.upper()} to your portfolio."


def portfolio_remove(ticker: str) -> str:
    conn = sqlite3.connect(_DB)
    conn.execute("DELETE FROM portfolio WHERE ticker=?", (ticker.upper(),))
    conn.commit()
    conn.close()
    return f"Removed {ticker.upper()} from your portfolio."


def get_portfolio_summary() -> str:
    """Return spoken portfolio summary with live P&L."""
    try:
        conn = sqlite3.connect(_DB)
        rows = conn.execute("SELECT ticker, shares, avg_cost FROM portfolio").fetchall()
        conn.close()
    except Exception:
        return "Could not load portfolio."

    if not rows:
        return "Your portfolio is empty. Say 'add AAPL 10 shares' to start tracking."

    lines  = [f"Here is your portfolio ({len(rows)} positions):"]
    total_value = 0.0
    total_cost  = 0.0

    for ticker, shares, avg_cost in rows:
        data = get_stock_price(ticker)
        if data:
            current = data["price"]
            value   = round(current * shares, 2)
            cost    = round(avg_cost * shares, 2)
            pnl     = round(value - cost, 2)
            pnl_pct = round((pnl / cost * 100), 1) if cost else 0
            arrow   = "▲" if pnl >= 0 else "▼"
            lines.append(
                f"  {ticker}: {shares:.0f} shares @ ${current:.2f} "
                f"= ${value:,.2f} ({arrow}{abs(pnl_pct)}%)"
            )
            total_value += value
            total_cost  += cost
        else:
            lines.append(f"  {ticker}: {shares:.0f} shares (price unavailable)")

    if total_cost:
        total_pnl = round(total_value - total_cost, 2)
        lines.append(
            f"Total value: ${total_value:,.2f} | "
            f"P&L: {'▲' if total_pnl>=0 else '▼'}${abs(total_pnl):,.2f}"
        )
    return "\n".join(lines)


# ── Voice command router ───────────────────────────────────────────────────────
_STOCK_KEYWORDS = [
    "stock", "share", "price", "market", "invest",
    "ticker", "nasdaq", "nse", "bse", "sensex", "nifty",
]
_CRYPTO_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "crypto", "coin",
    "solana", "sol", "doge", "dogecoin", "bnb", "xrp",
]

def handle_finance_command(text: str) -> tuple[bool, str]:
    """
    Parse natural language finance queries.
    Returns (handled: bool, response: str).
    """
    t = text.lower().strip()

    # ── Portfolio operations ───────────────────────────────────────────────────
    if "my portfolio" in t or "portfolio summary" in t:
        return True, get_portfolio_summary()

    # "add AAPL 10 shares"
    m = re.search(r'\badd\s+([A-Z]{1,5})\s+(\d+(?:\.\d+)?)\s*shares?', text, re.IGNORECASE)
    if m:
        ticker, shares = m.group(1).upper(), float(m.group(2))
        return True, portfolio_add(ticker, shares)

    # "remove TSLA" / "delete AAPL from portfolio"
    m = re.search(r'\b(?:remove|delete)\s+([A-Z]{1,5})', text, re.IGNORECASE)
    if m and "portfolio" in t:
        return True, portfolio_remove(m.group(1))

    # ── Crypto query ──────────────────────────────────────────────────────────
    if any(k in t for k in _CRYPTO_KEYWORDS):
        for name, cid in _CRYPTO_IDS.items():
            if name in t:
                data = get_crypto_price(name)
                if data:
                    arrow = "▲" if data["change_24h"] >= 0 else "▼"
                    resp = (f"{data['coin'].title()} is currently ${data['price']:,.2f} USD, "
                            f"{arrow}{abs(data['change_24h']):.2f}% in the last 24 hours.")
                    return True, resp
        return True, "Could not fetch crypto data right now. CoinGecko may be rate limiting."

    # ── Stock query ───────────────────────────────────────────────────────────
    if any(k in t for k in _STOCK_KEYWORDS) or re.search(r'\b[A-Z]{2,5}\b', text):
        ticker = extract_ticker(text)
        if not ticker:
            return True, "Could not identify the stock ticker from your request."
        data = get_stock_price(ticker)
        if not data:
            return True, f"Could not fetch data for {ticker} right now."
        arrow  = "▲" if data["change"] >= 0 else "▼"
        resp = (
            f"{ticker} is trading at ${data['price']:.2f}, "
            f"{arrow}{abs(data['change']):.2f} ({abs(data['pct_change']):.2f}%) "
            f"from yesterday's close of ${data['prev_close']:.2f}. "
            f"Today's range: ${data['day_low']:.2f} – ${data['day_high']:.2f}."
        )
        return True, resp

    return False, ""