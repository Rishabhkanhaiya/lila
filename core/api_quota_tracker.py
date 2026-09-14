"""
api_quota_tracker.py — JARVIS API Quota & Usage Tracker
========================================================
Tracks per-provider API call counts, token estimates, failures, and
latency in SQLite (uses centralized DB from config.py).

Features:
  • Counts per-provider requests today / this week / all-time
  • Estimates tokens sent (chars ÷ 4 heuristic)
  • Tracks failures + 429 rate-limit hits
  • Records average latency per provider
  • Provides JSON snapshot for the live dashboard

Schema:
  api_calls(date, provider, model, calls, tokens_est, failures, rate_limits, total_latency_ms)
"""

import sqlite3
import time
import threading
from datetime import date, timedelta
from core.config import DB_PATH
from core.jarvis_logger import log_error

_lock = threading.Lock()

# ── Tier metadata (daily request limits from AI Studio / provider docs) ────────
PROVIDER_LIMITS = {
    # Gemini Flash Lite — 500 RPD per account
    "Gemini_FlashLite_Acc1":  {"rpm": 15,  "rpd": 500,  "tier": "Gemini"},
    "Gemini_FlashLite_Acc2":  {"rpm": 15,  "rpd": 500,  "tier": "Gemini"},
    # Gemma 4 — 1500 RPD per account
    "Gemma4_31B_Acc1":        {"rpm": 15,  "rpd": 1500, "tier": "Gemini"},
    "Gemma4_26B_Acc2":        {"rpm": 15,  "rpd": 1500, "tier": "Gemini"},
    # Gemini 2.5 Flash — 20 RPD (premium quality)
    "Gemini_Flash25_Acc1":    {"rpm": 5,   "rpd": 20,   "tier": "Gemini"},
    "Gemini_Flash25_Acc2":    {"rpm": 5,   "rpd": 20,   "tier": "Gemini"},
    # Gemini 2.0 Flash — 200 RPD
    "Gemini_Flash20":         {"rpm": 15,  "rpd": 200,  "tier": "Gemini"},
    # Groq — ~14,400 RPD free tier
    "Groq_Account1":          {"rpm": 30,  "rpd": 14400,"tier": "Groq"},
    "Groq_Account2":          {"rpm": 30,  "rpd": 14400,"tier": "Groq"},
    # External fallbacks — conservative estimates
    "OpenRouter":             {"rpm": 20,  "rpd": 1000, "tier": "External"},
    "Together":               {"rpm": 60,  "rpd": 5000, "tier": "External"},
    "SambaNova":              {"rpm": 30,  "rpd": 3000, "tier": "External"},
    "Cerebras":               {"rpm": 30,  "rpd": 3000, "tier": "External"},
}


def _init_table():
    """Create the api_calls table if it doesn't exist."""
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_calls (
                    date         TEXT NOT NULL,
                    provider     TEXT NOT NULL,
                    model        TEXT NOT NULL DEFAULT '',
                    calls        INTEGER DEFAULT 0,
                    tokens_est   INTEGER DEFAULT 0,
                    failures     INTEGER DEFAULT 0,
                    rate_limits  INTEGER DEFAULT 0,
                    total_latency_ms INTEGER DEFAULT 0,
                    PRIMARY KEY (date, provider)
                )
            """)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log_error("api_quota_tracker", "_init_table", e)

_init_table()


def record_call(provider_name: str, model: str, prompt_chars: int,
                latency_ms: int, success: bool, rate_limited: bool = False):
    """
    Record one API call. Called from brain.py after every provider attempt.

    Args:
        provider_name: e.g. "Gemini_FlashLite_Acc1"
        model:         e.g. "gemini-3.1-flash-lite"
        prompt_chars:  approximate total chars in the request (used for token estimate)
        latency_ms:    round-trip time in milliseconds
        success:       True if provider returned a usable response
        rate_limited:  True if response was a 429
    """
    today = str(date.today())
    tokens_est = max(1, prompt_chars // 4)
    with _lock:
        try:
            conn = sqlite3.connect(DB_PATH)
            try:
                conn.execute("""
                    INSERT INTO api_calls (date, provider, model, calls, tokens_est,
                                           failures, rate_limits, total_latency_ms)
                    VALUES (?, ?, ?, 1, ?, ?, ?, ?)
                    ON CONFLICT(date, provider) DO UPDATE SET
                        model            = excluded.model,
                        calls            = calls + 1,
                        tokens_est       = tokens_est + excluded.tokens_est,
                        failures         = failures + excluded.failures,
                        rate_limits      = rate_limits + excluded.rate_limits,
                        total_latency_ms = total_latency_ms + excluded.total_latency_ms
                """, (
                    today, provider_name, model, tokens_est,
                    0 if success else 1,
                    1 if rate_limited else 0,
                    latency_ms
                ))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            log_error("api_quota_tracker", "record_call", e)


def get_dashboard_data() -> dict:
    """
    Returns a JSON-serialisable dict with all stats for the dashboard.
    Includes today's per-provider breakdown + 7-day totals.
    """
    today     = str(date.today())
    week_ago  = str(date.today() - timedelta(days=7))

    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.row_factory = sqlite3.Row

            # Today's stats
            today_rows = conn.execute("""
                SELECT provider, model, calls, tokens_est, failures,
                       rate_limits, total_latency_ms
                FROM api_calls WHERE date = ?
                ORDER BY calls DESC
            """, (today,)).fetchall()

            # 7-day per-provider totals
            week_rows = conn.execute("""
                SELECT provider,
                       SUM(calls) as calls,
                       SUM(tokens_est) as tokens_est,
                       SUM(failures) as failures,
                       SUM(rate_limits) as rate_limits,
                       SUM(total_latency_ms) as total_latency_ms
                FROM api_calls WHERE date >= ?
                GROUP BY provider ORDER BY calls DESC
            """, (week_ago,)).fetchall()

            # 7-day daily totals (for sparkline chart)
            daily_rows = conn.execute("""
                SELECT date, SUM(calls) as calls, SUM(tokens_est) as tokens_est
                FROM api_calls WHERE date >= ?
                GROUP BY date ORDER BY date ASC
            """, (week_ago,)).fetchall()
        finally:
            conn.close()

        def row_to_dict(r):
            d = dict(r)
            avg_lat = (d["total_latency_ms"] // d["calls"]) if d["calls"] > 0 else 0
            lim = PROVIDER_LIMITS.get(d["provider"], {"rpm": 0, "rpd": 0, "tier": "Unknown"})
            pct = round(100 * d["calls"] / lim["rpd"], 1) if lim["rpd"] > 0 else 0
            d.update({
                "avg_latency_ms": avg_lat,
                "rpd_limit": lim["rpd"],
                "rpm_limit": lim["rpm"],
                "tier": lim["tier"],
                "pct_used": min(pct, 100),
                "remaining": max(0, lim["rpd"] - d["calls"]),
            })
            return d

        today_data = [row_to_dict(r) for r in today_rows]
        week_data  = [row_to_dict(r) for r in week_rows]
        daily_data = [{"date": r["date"], "calls": r["calls"],
                       "tokens_est": r["tokens_est"]} for r in daily_rows]

        # Totals
        total_calls_today  = sum(r["calls"] for r in today_data)
        total_tokens_today = sum(r["tokens_est"] for r in today_data)
        total_failures     = sum(r["failures"] for r in today_data)
        total_rl           = sum(r["rate_limits"] for r in today_data)

        return {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "today": today_data,
            "week": week_data,
            "daily_chart": daily_data,
            "summary": {
                "total_calls_today":  total_calls_today,
                "total_tokens_today": total_tokens_today,
                "total_failures":     total_failures,
                "total_rate_limits":  total_rl,
                "providers_active":   len(today_data),
            }
        }

    except Exception as e:
        log_error("api_quota_tracker", "get_dashboard_data", e)
        return {"error": str(e), "today": [], "week": [], "daily_chart": [], "summary": {}}
