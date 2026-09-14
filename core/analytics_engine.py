"""
analytics_engine.py — JARVIS Analytics Engine v2.0
====================================================
Tracks system vitals, session activity, usage patterns, and market data.
All data persisted to SQLite and broadcast to UI via WebSocket every 15s.

Insights generated:
  • Live: CPU, RAM, disk, top processes, network I/O
  • Session: commands count, avg response time, peak hour, feature usage
  • Market: stock tickers via yfinance (fallback to mock)
  • News: Yahoo Finance RSS headlines
  • Pattern: most used feature, busiest hour, session streaks

Voice query support:
  get_analytics_summary() → spoken text JARVIS can read aloud
"""

import time
import threading
import json
import sqlite3
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime, date
from pathlib import Path
from collections import Counter, defaultdict

from core.jarvis_logger import log_error, log_info, log_warn

_stop_event = threading.Event()

# ── Optional deps ─────────────────────────────────────────────────────────────
try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    log_warn("analytics", "psutil not installed — system metrics disabled")

# ── Config ────────────────────────────────────────────────────────────────────
WATCHED_TICKERS  = ["AAPL", "NVDA", "TSLA", "MSFT"]
REFRESH_INTERVAL = 15          # seconds between full refreshes
_DB = str(Path(__file__).parent.parent / "jarvis_memory.db")

# ── In-memory session state ───────────────────────────────────────────────────
_session_lock        = threading.Lock()
_session_start       = time.time()
_command_count       = 0
_response_times: list = []          # seconds
_feature_counter     = Counter()    # feature_name → usage count
_hourly_counter      = Counter()    # hour (0-23) → command count
_last_payload: dict  = {}


# ── DB setup ──────────────────────────────────────────────────────────────────
def _init_db():
    conn = sqlite3.connect(_DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS analytics_sessions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_date TEXT DEFAULT (date('now','localtime')),
            commands     INTEGER DEFAULT 0,
            avg_resp_ms  INTEGER DEFAULT 0,
            peak_hour    INTEGER DEFAULT 0,
            top_feature  TEXT    DEFAULT '',
            notes        TEXT    DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS analytics_hourly (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            hour      INTEGER,
            count     INTEGER,
            logged_at TEXT DEFAULT (datetime('now','localtime'))
        );
    """)
    conn.commit()
    conn.close()


# ── Public tracking API ───────────────────────────────────────────────────────
def track_command(feature: str = "general", response_time_s: float = 0.0):
    """
    Call this after each JARVIS command is processed.
    feature: e.g. 'voice', 'web_search', 'vision', 'smart_home', 'self_evolution'
    """
    with _session_lock:
        global _command_count
        _command_count += 1
        if response_time_s > 0:
            _response_times.append(response_time_s)
        hour = datetime.now().hour
        _feature_counter[feature] += 1
        _hourly_counter[hour] += 1


def get_analytics_summary() -> str:
    """Returns a spoken summary of session analytics for voice queries."""
    with _session_lock:
        uptime_min  = int((time.time() - _session_start) / 60)
        cmds        = _command_count
        avg_resp    = (sum(_response_times) / len(_response_times) * 1000) \
                      if _response_times else 0
        top_feature = _feature_counter.most_common(1)
        peak_hour   = _hourly_counter.most_common(1)

    lines = [f"You've been running for {uptime_min} minutes this session."]
    lines.append(f"I've processed {cmds} commands.")
    if avg_resp:
        lines.append(f"Average response time is {avg_resp:.0f} milliseconds.")
    if top_feature:
        lines.append(f"Your most used feature today is {top_feature[0][0]}, "
                     f"used {top_feature[0][1]} times.")
    if peak_hour:
        h = peak_hour[0][0]
        lines.append(f"Your peak usage hour is {h}:00.")

    # System snapshot
    if HAS_PSUTIL:
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory().percent
        lines.append(f"System: CPU at {cpu}%, RAM at {ram}% usage.")

    return " ".join(lines)


def get_last_payload() -> dict:
    """Return the most recently broadcast analytics payload."""
    return dict(_last_payload)


# ── System metrics ────────────────────────────────────────────────────────────
def _collect_system_metrics() -> dict:
    if not HAS_PSUTIL:
        return {}
    try:
        cpu   = psutil.cpu_percent(interval=0.5)
        ram   = psutil.virtual_memory()
        import os
        disk  = psutil.disk_usage(os.path.abspath(os.sep))
        net   = psutil.net_io_counters()

        # Top 5 processes by CPU
        procs = []
        for p in sorted(psutil.process_iter(["pid","name","cpu_percent","memory_percent"]),
                        key=lambda x: x.info["cpu_percent"] or 0, reverse=True)[:5]:
            procs.append({
                "name": p.info["name"][:20],
                "cpu":  round(p.info["cpu_percent"] or 0, 1),
                "mem":  round(p.info["memory_percent"] or 0, 1),
            })

        return {
            "cpu_percent":    cpu,
            "ram_percent":    round(ram.percent, 1),
            "ram_used_gb":    round(ram.used / 1e9, 2),
            "ram_total_gb":   round(ram.total / 1e9, 2),
            "disk_percent":   round(disk.percent, 1),
            "disk_free_gb":   round(disk.free / 1e9, 2),
            "net_sent_mb":    round(net.bytes_sent / 1e6, 2),
            "net_recv_mb":    round(net.bytes_recv / 1e6, 2),
            "top_processes":  procs,
        }
    except Exception as e:
        log_error("analytics", "system_metrics", e)
        return {}


# ── Session metrics ───────────────────────────────────────────────────────────
def _collect_session_metrics() -> dict:
    with _session_lock:
        uptime_s    = time.time() - _session_start
        avg_resp_ms = (sum(_response_times) / len(_response_times) * 1000) \
                      if _response_times else 0
        top_feature = _feature_counter.most_common(3)
        peak_hour   = _hourly_counter.most_common(1)
        hourly_data = dict(_hourly_counter)

    return {
        "uptime_minutes":  round(uptime_s / 60, 1),
        "command_count":   _command_count,
        "avg_response_ms": round(avg_resp_ms, 1),
        "top_features":    [{"name": k, "count": v} for k, v in top_feature],
        "peak_hour":       peak_hour[0][0] if peak_hour else None,
        "hourly_activity": hourly_data,
        "session_date":    date.today().isoformat(),
    }


# ── Market data ───────────────────────────────────────────────────────────────
def _collect_market_data() -> list:
    tickers_data = []
    try:
        from core.finance_engine import get_stock_price
        for symbol in WATCHED_TICKERS:
            try:
                st = get_stock_price(symbol)
                if st and st.get("price", 0) > 0:
                    tickers_data.append({
                        "symbol": symbol,
                        "price": st["price"],
                        "change": st.get("change", 0.0),
                        "pct_change": st.get("pct_change", 0.0),
                    })
            except Exception:
                pass
    except ImportError:
        pass

    if not tickers_data and HAS_YF:
        for symbol in WATCHED_TICKERS:
            try:
                t      = yf.Ticker(symbol)
                price  = round(getattr(t.fast_info, "last_price",      0), 2)
                prev   = round(getattr(t.fast_info, "previous_close", 1), 2)
                change = round(price - prev, 2)
                pct    = round((change / prev) * 100, 2) if prev else 0
                tickers_data.append({
                    "symbol": symbol, "price": price,
                    "change": change,  "pct_change": pct,
                })
            except Exception:
                pass

    if not tickers_data:
        import random
        bases = {"AAPL": 180.0, "NVDA": 850.0, "TSLA": 200.0, "MSFT": 410.0}
        for sym in WATCHED_TICKERS:
            base = bases[sym]
            f    = random.uniform(-2.0, 2.0)
            tickers_data.append({
                "symbol": sym, "price": round(base + f, 2),
                "change": round(f, 2),
                "pct_change": round((f / base) * 100, 2),
            })
    return tickers_data


# ── News ──────────────────────────────────────────────────────────────────────
def _fetch_news() -> list:
    try:
        url = ("https://feeds.finance.yahoo.com/rss/2.0/headline"
               "?s=aapl,nvda,tsla,msft,goog,amzn")
        resp = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5.0)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            return [
                {"title": item.find("title").text, "link": item.find("link").text}
                for item in root.findall("./channel/item")[:5]
            ]
    except Exception:
        pass
        return [
            {"title": "AI models reach human-level reasoning on new benchmarks", "link": "#"},
            {"title": "NVIDIA hits record revenue on data center GPU demand", "link": "#"},
            {"title": "Central banks signal rate cuts as inflation cools", "link": "#"},
            {"title": "SpaceX Starship completes first full orbital test flight", "link": "#"},
            {"title": "Clean energy investment surpasses fossil fuels globally", "link": "#"},
        ]


# ── Persist session summary ───────────────────────────────────────────────────
def _persist_session(session: dict):
    try:
        conn = sqlite3.connect(_DB)
        conn.execute("""
            INSERT INTO analytics_sessions (commands, avg_resp_ms, peak_hour, top_feature)
            VALUES (?, ?, ?, ?)
        """, (
            session["command_count"],
            int(session["avg_response_ms"]),
            session.get("peak_hour") or 0,
            session["top_features"][0]["name"] if session["top_features"] else "",
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        log_error("analytics", "persist_session", e)


# ── Main loop ─────────────────────────────────────────────────────────────────
def analytics_loop():
    from core.ws_server import manager
    global _last_payload

    log_info("system", "system", "[ANALYTICS ENGINE]: Background daemon started.")
    _init_db()
    _persist_tick = 0   # persist session every 10 ticks (150s)

    while not _stop_event.is_set():
        try:
            system  = _collect_system_metrics()
            session = _collect_session_metrics()
            market  = _collect_market_data()
            news    = _fetch_news()

            # ── Alert detection ───────────────────────────────────────────────
            alerts = []
            if system.get("cpu_percent", 0) > 90:
                alerts.append({"level": "warn", "msg": f"CPU critical: {system['cpu_percent']}%"})
            if system.get("ram_percent", 0) > 85:
                alerts.append({"level": "warn", "msg": f"RAM high: {system['ram_percent']}%"})
            if system.get("disk_percent", 0) > 90:
                alerts.append({"level": "critical", "msg": f"Disk almost full: {system['disk_percent']}%"})

            payload = {
                "type":    "dashboard_update",
                "tickers": market,
                "news":    news,
                "system":  system,
                "session": session,
                "alerts":  alerts,
                "ts":      datetime.now().isoformat(),
            }
            _last_payload = payload
            manager.broadcast(payload)

            # Persist every 150s
            _persist_tick += 1
            if _persist_tick >= 10:
                _persist_session(session)
                _persist_tick = 0

        except Exception as e:
            log_error("analytics", "analytics_loop", e)

        if _stop_event.wait(REFRESH_INTERVAL):
            break


def start_analytics_engine():
    _init_db()
    _stop_event.clear()
    t = threading.Thread(target=analytics_loop, daemon=True, name="AnalyticsEngine")
    t.start()
    log_info("system", "system", "[ANALYTICS ENGINE]: Started.")
    return t


def stop_analytics_engine():
    _stop_event.set()
