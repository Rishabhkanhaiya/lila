"""
core/trajectory_memory.py — JARVIS Trajectory Memory Engine (Modern 2026 Engine)
=================================================================================
Persistent logging of agentic action sequences, tools invoked, and execution outcomes.
Stores trajectories in centralized SQLite WAL storage for contextual warm-starts.
"""

import time
import json
import sqlite3
from typing import Optional, Dict, Any, List
from core.jarvis_logger import log_info, log_warn

try:
    from core.config import DB_PATH
except Exception:
    import os
    DB_PATH = os.path.join(os.path.dirname(__file__), "..", "jarvis_memory.db")

def _init_table():
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trajectories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    action TEXT,
                    outcome TEXT,
                    details TEXT
                )
            """)
            # Self-Healing Action Cache (Stagehand Architecture: Cache the Fix)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS action_cache (
                    domain TEXT,
                    intent_key TEXT,
                    selector TEXT,
                    action_type TEXT,
                    fallback_strategy TEXT,
                    success_count INTEGER DEFAULT 1,
                    last_used REAL,
                    PRIMARY KEY (domain, intent_key)
                )
            """)
            # Per-Site Execution Memory (Domain Immunization)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS site_execution_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT,
                    failure_signature TEXT,
                    fix_applied TEXT,
                    timestamp REAL
                )
            """)
            conn.commit()
    except Exception as e:
        log_warn("trajectory_memory", f"Init table failed: {e}")

_init_table()


def log_trajectory(action: str, outcome: str, details: Optional[Dict[str, Any]] = None):
    """Logs an agentic action-outcome pair to persistent memory."""
    try:
        det_str = json.dumps(details or {})
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.execute(
                "INSERT INTO trajectories (timestamp, action, outcome, details) VALUES (?, ?, ?, ?)",
                (time.time(), str(action)[:200], str(outcome)[:500], det_str)
            )
            conn.commit()
    except Exception as e:
        log_warn("trajectory_memory", f"Log trajectory error: {e}")

def get_recent_trajectories(limit: int = 10) -> list:
    """Fetches recent agentic executions for contextual recall."""
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            cur = conn.cursor()
            cur.execute("SELECT action, outcome, details, timestamp FROM trajectories ORDER BY id DESC LIMIT ?", (limit,))
            return [{"action": r[0], "outcome": r[1], "details": json.loads(r[2]), "timestamp": r[3]} for r in cur.fetchall()]
    except Exception as e:
        log_warn("trajectory_memory", f"Fetch trajectories error: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# 1. Self-Healing Action Cache (Stagehand Ladder Tier 1)
# ─────────────────────────────────────────────────────────────────────────────

class ActionCache:
    """
    Stores and retrieves verified, resilient DOM selectors keyed by (domain, intent_key).
    Promotes Tier 2/3 resolutions to Tier 1 hits for 0-token, <50ms execution.
    """

    def get(self, domain: str, intent_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached selector if available for this domain and action intent."""
        if not domain or not intent_key:
            return None
        dom_clean = domain.lower().replace("https://", "").replace("http://", "").split("/")[0]
        try:
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT selector, action_type, fallback_strategy, success_count FROM action_cache WHERE domain = ? AND intent_key = ?",
                    (dom_clean, intent_key.lower().strip())
                )
                row = cur.fetchone()
                if row:
                    return {
                        "selector": row[0],
                        "action_type": row[1],
                        "strategy": row[2],
                        "success_count": row[3]
                    }
        except Exception as e:
            log_warn("trajectory_memory", f"ActionCache get error: {e}")
        return None

    def save_fix(self, domain: str, intent_key: str, selector: str, action_type: str = "click", strategy: str = "healed"):
        """Save or update a healed selector for this domain and action intent."""
        if not domain or not intent_key or not selector:
            return
        dom_clean = domain.lower().replace("https://", "").replace("http://", "").split("/")[0]
        try:
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                conn.execute("""
                    INSERT INTO action_cache (domain, intent_key, selector, action_type, fallback_strategy, success_count, last_used)
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(domain, intent_key) DO UPDATE SET
                        selector = excluded.selector,
                        action_type = excluded.action_type,
                        fallback_strategy = excluded.fallback_strategy,
                        success_count = success_count + 1,
                        last_used = excluded.last_used
                """, (dom_clean, intent_key.lower().strip(), selector.strip(), action_type.lower().strip(), strategy, time.time()))
                conn.commit()
                log_info(f"[ACTION CACHE]: Saved healed fix for {dom_clean} -> {intent_key} ('{selector}')")
        except Exception as e:
            log_warn("trajectory_memory", f"ActionCache save_fix error: {e}")

    def invalidate(self, domain: str, intent_key: str):
        """Invalidate a broken/stale selector so Tier 2 can re-heal it."""
        if not domain or not intent_key:
            return
        dom_clean = domain.lower().replace("https://", "").replace("http://", "").split("/")[0]
        try:
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                conn.execute(
                    "DELETE FROM action_cache WHERE domain = ? AND intent_key = ?",
                    (dom_clean, intent_key.lower().strip())
                )
                conn.commit()
                log_info(f"[ACTION CACHE]: Invalidated stale selector for {dom_clean} -> {intent_key}")
        except Exception as e:
            log_warn("trajectory_memory", f"ActionCache invalidate error: {e}")

    def get_domain_cache(self, domain: str) -> Dict[str, Dict[str, Any]]:
        """Fetch all cached action selectors for a given domain."""
        dom_clean = domain.lower().replace("https://", "").replace("http://", "").split("/")[0]
        out = {}
        try:
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT intent_key, selector, action_type, success_count FROM action_cache WHERE domain = ?",
                    (dom_clean,)
                )
                for r in cur.fetchall():
                    out[r[0]] = {"selector": r[1], "action_type": r[2], "success_count": r[3]}
        except Exception as e:
            log_warn("trajectory_memory", f"ActionCache get_domain_cache error: {e}")
        return out


# ─────────────────────────────────────────────────────────────────────────────
# 2. Per-Site Execution Memory (Domain Immunization)
# ─────────────────────────────────────────────────────────────────────────────

class SiteExecutionMemory:
    """
    Maintains per-domain execution rules, known obstacles, popups, and the fixes
    that worked. Injected pre-flight so the browser agent never repeats a mistake.
    """

    def record_quirk(self, domain: str, failure_signature: str, fix_applied: str):
        """Log what failed on this site and what fixed it."""
        if not domain or not failure_signature:
            return
        dom_clean = domain.lower().replace("https://", "").replace("http://", "").split("/")[0]
        try:
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                conn.execute(
                    "INSERT INTO site_execution_memory (domain, failure_signature, fix_applied, timestamp) VALUES (?, ?, ?, ?)",
                    (dom_clean, failure_signature[:250], fix_applied[:250], time.time())
                )
                conn.commit()
                log_info(f"[SITE MEMORY]: Recorded quirk for {dom_clean}: '{failure_signature}' -> fix: '{fix_applied}'")
        except Exception as e:
            log_warn("trajectory_memory", f"SiteExecutionMemory record_quirk error: {e}")

    def get_quirks(self, domain: str) -> List[Dict[str, Any]]:
        """Retrieve past failure signatures and fixes for a target site."""
        dom_clean = domain.lower().replace("https://", "").replace("http://", "").split("/")[0]
        try:
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT failure_signature, fix_applied, timestamp FROM site_execution_memory WHERE domain = ? ORDER BY id DESC LIMIT 5",
                    (dom_clean,)
                )
                return [{"failure": r[0], "fix": r[1], "timestamp": r[2]} for r in cur.fetchall()]
        except Exception as e:
            log_warn("trajectory_memory", f"SiteExecutionMemory get_quirks error: {e}")
            return []

    def format_heuristics(self, domain: str) -> str:
        """Format domain-specific heuristics as guidance for the planner."""
        quirks = self.get_quirks(domain)
        if not quirks:
            return ""
        lines = [f"- Known quirk: When encountering '{q['failure']}', fix by: {q['fix']}" for q in quirks]
        return "\n\n## DOMAIN EXECUTION MEMORY (LEARNED LESSONS FOR THIS SITE):\n" + "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 3. TrajectoryMemory (Backward Compatibility & Global Accessors)
# ─────────────────────────────────────────────────────────────────────────────

class TrajectoryMemory:
    """In-memory and SQLite-backed trajectory memory for WebAgent."""
    def recall(self, goal: str, domain: str = "") -> list:
        try:
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                cur = conn.cursor()
                query = "%" + (domain or goal[:20]) + "%"
                cur.execute(
                    "SELECT action, outcome, details FROM trajectories WHERE action LIKE ? OR details LIKE ? ORDER BY id DESC LIMIT 5",
                    (query, query)
                )
                return [{"action": r[0], "outcome": r[1], "details": json.loads(r[2] or "{}")} for r in cur.fetchall()]
        except Exception:
            return []

    def format_hint(self, recalled: list) -> str:
        if not recalled:
            return ""
        hints = []
        for item in recalled:
            act = item.get("action", "")
            out = item.get("outcome", "")
            if act and out:
                hints.append(f"- Previous attempt: '{act}' -> {out}")
        if hints:
            return "\n\n## PAST SUCCESSFUL TACTICS & TRAJECTORY HINTS\n" + "\n".join(hints)
        return ""


# Singletons
_trajectory_memory_instance = None
_action_cache_instance = None
_site_memory_instance = None

def get_trajectory_memory() -> TrajectoryMemory:
    global _trajectory_memory_instance
    if _trajectory_memory_instance is None:
        _trajectory_memory_instance = TrajectoryMemory()
    return _trajectory_memory_instance

def get_action_cache() -> ActionCache:
    global _action_cache_instance
    if _action_cache_instance is None:
        _action_cache_instance = ActionCache()
    return _action_cache_instance

def get_site_memory() -> SiteExecutionMemory:
    global _site_memory_instance
    if _site_memory_instance is None:
        _site_memory_instance = SiteExecutionMemory()
    return _site_memory_instance

