# JARVIS Background Task Queue Engine
# Priority: HIGH -> MEDIUM -> LOW
# Status:   PENDING -> RUNNING -> DONE | FAILED

import sqlite3
import threading
from core.config import DB_PATH
from core.jarvis_logger import log_error, log_info, log_warn

_lock = threading.Lock()

_INIT_SQL = """
    CREATE TABLE IF NOT EXISTS task_queue (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        task            TEXT NOT NULL,
        priority        TEXT DEFAULT 'MEDIUM',
        status          TEXT DEFAULT 'PENDING',
        source          TEXT DEFAULT 'VOICE',
        added_at        TEXT DEFAULT (datetime('now','localtime')),
        started_at      TEXT,
        completed_at    TEXT,
        result          TEXT,
        error_message   TEXT
    );
    CREATE TABLE IF NOT EXISTS queue_results (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id         INTEGER NOT NULL,
        task_text       TEXT,
        result_markdown TEXT,
        generated_at    TEXT DEFAULT (datetime('now','localtime')),
        exported        INTEGER DEFAULT 0,
        FOREIGN KEY(task_id) REFERENCES task_queue(id)
    );
"""


def _init_queue_tables():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.executescript(_INIT_SQL)
        conn.commit()
        conn.close()
        log_info("queue_db", "Queue tables initialized")
    except Exception as e:
        log_error("queue_db", "_init_queue_tables", e)

_init_queue_tables()


def add_task(task: str, priority: str = "MEDIUM", source: str = "VOICE") -> int:
    """Add a new task. Returns task ID. Priority: HIGH|MEDIUM|LOW. Source: VOICE|UI."""
    priority = priority.upper()
    if priority not in ("HIGH", "MEDIUM", "LOW"):
        priority = "MEDIUM"
    try:
        with _lock:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO task_queue (task, priority, source) VALUES (?, ?, ?)",
                (task.strip(), priority, source)
            )
            task_id = cur.lastrowid
            conn.commit()
            conn.close()
        log_info("queue_db", f"Task added [ID:{task_id}] [{priority}]: {task[:60]}")
        return task_id
    except Exception as e:
        log_error("queue_db", "add_task", e)
        return -1


def get_next_task():
    """Pop the highest-priority PENDING task. Returns dict or None."""
    try:
        with _lock:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM task_queue WHERE status = 'PENDING'
                ORDER BY
                    CASE priority WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 WHEN 'LOW' THEN 2 ELSE 3 END,
                    added_at ASC
                LIMIT 1
            """)
            row = cur.fetchone()
            if row:
                task = dict(row)
                cur.execute(
                    "UPDATE task_queue SET status='RUNNING', started_at=datetime('now','localtime') WHERE id=?",
                    (task["id"],)
                )
                conn.commit()
            conn.close()
            return task if row else None
    except Exception as e:
        log_error("queue_db", "get_next_task", e)
        return None


def complete_task(task_id: int, result_markdown: str):
    """Mark task DONE and store the formatted result."""
    try:
        with _lock:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "UPDATE task_queue SET status='DONE', completed_at=datetime('now','localtime'), result=? WHERE id=?",
                (result_markdown, task_id)
            )
            cur.execute(
                "INSERT INTO queue_results (task_id, task_text, result_markdown) SELECT id, task, ? FROM task_queue WHERE id=?",
                (result_markdown, task_id)
            )
            conn.commit()
            conn.close()
        log_info("queue_db", f"Task [ID:{task_id}] completed")
    except Exception as e:
        log_error("queue_db", "complete_task", e)


def fail_task(task_id: int, error: str):
    """Mark task FAILED."""
    try:
        with _lock:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "UPDATE task_queue SET status='FAILED', completed_at=datetime('now','localtime'), error_message=? WHERE id=?",
                (error[:500], task_id)
            )
            conn.commit()
            conn.close()
        log_warn("queue_db", f"Task [ID:{task_id}] failed: {error[:80]}")
    except Exception as e:
        log_error("queue_db", "fail_task", e)


def get_all_tasks():
    """Return all tasks for the Queue Manager UI."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM task_queue
            ORDER BY
                CASE status WHEN 'RUNNING' THEN 0 WHEN 'PENDING' THEN 1 WHEN 'DONE' THEN 2 WHEN 'FAILED' THEN 3 ELSE 4 END,
                CASE priority WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 WHEN 'LOW' THEN 2 ELSE 3 END,
                added_at DESC
        """)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        log_error("queue_db", "get_all_tasks", e)
        return []


def get_pending_count():
    """Return count of PENDING tasks (for UI badge)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM task_queue WHERE status='PENDING'")
        count = cur.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


def delete_task(task_id: int):
    """Delete a task and its results."""
    try:
        with _lock:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("DELETE FROM task_queue WHERE id=?", (task_id,))
            cur.execute("DELETE FROM queue_results WHERE task_id=?", (task_id,))
            conn.commit()
            conn.close()
    except Exception as e:
        log_error("queue_db", "delete_task", e)


def update_priority(task_id: int, priority: str):
    """Change priority of a PENDING task."""
    priority = priority.upper()
    if priority not in ("HIGH", "MEDIUM", "LOW"):
        return
    try:
        with _lock:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "UPDATE task_queue SET priority=? WHERE id=? AND status='PENDING'",
                (priority, task_id)
            )
            conn.commit()
            conn.close()
    except Exception as e:
        log_error("queue_db", "update_priority", e)


def get_results_for_export():
    """Return all DONE results for the results viewer panel."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT qr.id, qr.task_id, qr.task_text, qr.result_markdown,
                   qr.generated_at, qr.exported, tq.priority, tq.added_at as queued_at
            FROM queue_results qr
            JOIN task_queue tq ON tq.id = qr.task_id
            ORDER BY qr.generated_at DESC
        """)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        log_error("queue_db", "get_results_for_export", e)
        return []
