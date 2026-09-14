"""
graph_memory.py — NEXUS-2 Structured Facts Memory (GraphDB layer)
================================================================
Stores entity-relationship facts in SQLite as a lightweight graph.
Replaces scattered JSON facts with queryable entity-relationship store.

Schema: entity | relation | value | confidence | source | timestamp

Examples stored:
  ("Rishabh", "owns", "ASUS ROG laptop", 1.0, "user", ...)
  ("JARVIS", "runs_on", "Python 3.11", 1.0, "system", ...)
  ("project", "name", "jarvis_project", 1.0, "user", ...)
"""

import sqlite3
import os
import datetime
from typing import Optional
from core.jarvis_logger import log_error, log_info

# DB path — sits next to the main memory vault
_GRAPH_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "graph_memory.db")

_schema_initialized = False

def _get_conn():
    global _schema_initialized
    os.makedirs(os.path.dirname(_GRAPH_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_GRAPH_DB_PATH, check_same_thread=False)
    if not _schema_initialized:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                entity    TEXT NOT NULL,
                relation  TEXT NOT NULL,
                value     TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                source    TEXT DEFAULT 'user',
                ts        TEXT NOT NULL,
                UNIQUE(entity, relation)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entity ON facts(entity)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_relation ON facts(relation)")
        conn.commit()
        _schema_initialized = True
    return conn


# ── Write ──────────────────────────────────────────────────────────────────────

def store_fact(entity: str, relation: str, value: str,
               confidence: float = 1.0, source: str = "user") -> bool:
    """Upsert a fact. Returns True on success."""
    try:
        conn = _get_conn()
        ts = datetime.datetime.now().isoformat()
        conn.execute("""
            INSERT INTO facts (entity, relation, value, confidence, source, ts)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity, relation) DO UPDATE SET
                value=excluded.value,
                confidence=excluded.confidence,
                ts=excluded.ts
        """, (entity.strip(), relation.strip(), value.strip(), confidence, source, ts))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        log_error("graph_memory", "store_fact", e)
        return False


# ── Read ───────────────────────────────────────────────────────────────────────

def get_facts_about(entity: str) -> list[dict]:
    """Return all known facts about an entity."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT relation, value, confidence, source, ts FROM facts WHERE entity = ? ORDER BY confidence DESC",
            (entity.strip(),)
        ).fetchall()
        conn.close()
        return [{"relation": r, "value": v, "confidence": c, "source": s, "ts": t}
                for r, v, c, s, t in rows]
    except Exception as e:
        log_error("graph_memory", "get_facts_about", e)
        return []


def find_by_relation(relation: str) -> list[dict]:
    """Find all entities with a specific relation (e.g. all 'owns' facts)."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT entity, value, confidence FROM facts WHERE relation = ? ORDER BY confidence DESC",
            (relation.strip(),)
        ).fetchall()
        conn.close()
        return [{"entity": e, "value": v, "confidence": c} for e, v, c in rows]
    except Exception as e:
        log_error("graph_memory", "find_by_relation", e)
        return []


def search_facts(query: str, limit: int = 5) -> str:
    """Full-text search across all facts. Returns formatted string for LLM injection."""
    try:
        conn = _get_conn()
        q = f"%{query.strip()}%"
        rows = conn.execute(
            """SELECT entity, relation, value FROM facts
               WHERE entity LIKE ? OR value LIKE ? OR relation LIKE ?
               ORDER BY confidence DESC LIMIT ?""",
            (q, q, q, limit)
        ).fetchall()
        conn.close()
        if not rows:
            return ""
        lines = [f"{e} → {r}: {v}" for e, r, v in rows]
        return "STRUCTURED FACTS:\n" + "\n".join(lines)
    except Exception as e:
        log_error("graph_memory", "search_facts", e)
        return ""


def get_all_facts_summary(limit: int = 20) -> str:
    """Return top facts formatted for LLM context injection."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT entity, relation, value FROM facts ORDER BY confidence DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        if not rows:
            return ""
        lines = [f"{e} → {r}: {v}" for e, r, v in rows]
        return "KNOWN FACTS:\n" + "\n".join(lines)
    except Exception as e:
        log_error("graph_memory", "get_all_facts_summary", e)
        return ""


# ── Auto-extract from memory corrections ──────────────────────────────────────

def extract_facts_from_correction(correction_text: str) -> None:
    """
    Parse a user correction like "my laptop is ASUS ROG, not Dell"
    and store the corrected fact automatically.
    """
    try:
        # Simple pattern: "X is Y", "my X is Y", "X = Y"
        import re
        patterns = [
            r"(?:my\s+)?(.+?)\s+is\s+(.+)",
            r"(.+?)\s*=\s*(.+)",
            r"(?:the\s+)?(.+?)\s+(?:was|are|were)\s+(.+)",
        ]
        for pat in patterns:
            m = re.search(pat, correction_text.lower(), re.IGNORECASE)
            if m:
                entity = m.group(1).strip()[:50]
                value  = m.group(2).strip()[:100]
                if len(entity) > 2 and len(value) > 1:
                    store_fact(entity, "is", value, confidence=0.9, source="correction")
                    log_info("graph_memory", f"Auto-extracted fact: {entity} → is: {value}")
                    break
    except Exception as e:
        log_error("graph_memory", "extract_facts_from_correction", e)
