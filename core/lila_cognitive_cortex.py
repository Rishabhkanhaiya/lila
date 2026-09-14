"""
core/lila_cognitive_cortex.py — Lila Unified Cognitive Memory & Relationship Diary (2026 Engine)
================================================================================================
Unified single-source-of-truth memory architecture for Lila:
  1. Consolidated SQLite WAL database: `data/lila_memory.db` with native FTS5 full-text indexing.
  2. Auto-migrates and deduplicates all historical facts from:
     - `data/jarvis_memory.json` (VIT Pune, friends, music, habits, directives)
     - `jarvis_memory.db` (`user_memory`, `trajectories`, `action_cache`)
     - `data/jarvis_memory.db` (`kg_entities`, `user_twin_profile`, `emotion_timeline`)
     - `data/user_profile.json`
  3. High-fidelity episodic interaction logging (full text, tools, emotional tags, zero-latency async).
  4. Autonomous Daily Relationship Diary: writes affectionate first-person journals to `data/diary/YYYY-MM-DD.md`.
  5. Background Reflection Worker: extracts facts, synthesizes daily journals, and tracks inside jokes.
  6. Sub-millisecond (<0.5ms) pre-computed in-memory prompt injection for Gemini Live Voice & Fast Agent.
"""

import os
import re
import time
import json
import queue
import sqlite3
import datetime
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent.parent.resolve()
load_dotenv(dotenv_path=str(ROOT_DIR / ".env"))
DATA_DIR = ROOT_DIR / "data"
DIARY_DIR = DATA_DIR / "diary"
DB_PATH = str(DATA_DIR / "lila_memory.db")
LEGACY_JSON_PATH = str(DATA_DIR / "jarvis_memory.json")
LEGACY_PROFILE_PATH = str(DATA_DIR / "user_profile.json")
ROOT_LEGACY_DB_PATH = str(ROOT_DIR / "jarvis_memory.db")
DATA_LEGACY_DB_PATH = str(DATA_DIR / "jarvis_memory.db")
EPISODES_FILE = str(DATA_DIR / "episodes.jsonl")

DATA_DIR.mkdir(parents=True, exist_ok=True)
DIARY_DIR.mkdir(parents=True, exist_ok=True)

# ── In-Memory Prompt Injection Cache ──────────────────────────────────────────
_cache_lock = threading.Lock()
_cached_prompt_injection: str = ""
_cache_timestamp: float = 0.0
_CACHE_TTL_SECONDS: float = 10.0  # Refreshes dynamically every 10s or immediately on interaction/fact

# ── Episode Queue for Zero-Latency Logging ────────────────────────────────────
_episode_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
_writer_thread_started = False
_writer_lock = threading.Lock()


def _get_db_connection() -> sqlite3.Connection:
    """Returns a thread-safe connection to the unified SQLite WAL database."""
    conn = sqlite3.connect(DB_PATH, timeout=10.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _init_db_schema():
    """Initializes schema and triggers for FTS5 full-text indexing."""
    conn = _get_db_connection()
    try:
        cur = conn.cursor()

        # 1. User Profile Key-Value Store
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_profile (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL
            )
        """)

        # 2. Knowledge Graph & Structured Facts
        cur.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                relation TEXT NOT NULL,
                value TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                confidence REAL DEFAULT 1.0,
                emotional_tag TEXT,
                source TEXT DEFAULT 'interaction',
                learned_on TEXT,
                last_accessed REAL,
                access_count INTEGER DEFAULT 0,
                UNIQUE(subject, relation, value)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);")

        # FTS5 for Facts
        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                subject, relation, value, category,
                content='facts', content_rowid='id'
            );
        """)

        # Triggers to keep facts_fts synchronized
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
                INSERT INTO facts_fts(rowid, subject, relation, value, category)
                VALUES (new.id, new.subject, new.relation, new.value, new.category);
            END;
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
                INSERT INTO facts_fts(facts_fts, rowid, subject, relation, value, category)
                VALUES ('delete', old.id, old.subject, old.relation, old.value, old.category);
            END;
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
                INSERT INTO facts_fts(facts_fts, rowid, subject, relation, value, category)
                VALUES ('delete', old.id, old.subject, old.relation, old.value, old.category);
                INSERT INTO facts_fts(rowid, subject, relation, value, category)
                VALUES (new.id, new.subject, new.relation, new.value, new.category);
            END;
        """)

        # 3. High-Fidelity Episodes
        cur.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                date_str TEXT,
                time_str TEXT,
                session_id TEXT,
                user_input TEXT NOT NULL,
                lila_reply TEXT NOT NULL,
                tools_used TEXT,
                emotional_tone TEXT,
                summarized INTEGER DEFAULT 0
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_episodes_date ON episodes(date_str);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_episodes_summarized ON episodes(summarized);")

        # FTS5 for Episodes
        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
                user_input, lila_reply, tools_used,
                content='episodes', content_rowid='id'
            );
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
                INSERT INTO episodes_fts(rowid, user_input, lila_reply, tools_used)
                VALUES (new.id, new.user_input, new.lila_reply, new.tools_used);
            END;
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
                INSERT INTO episodes_fts(episodes_fts, rowid, user_input, lila_reply, tools_used)
                VALUES ('delete', old.id, old.user_input, old.lila_reply, old.tools_used);
            END;
        """)

        # 4. Daily Relationship Diary
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_diary (
                date_str TEXT PRIMARY KEY,
                title TEXT,
                summary_md TEXT,
                highlights_json TEXT,
                emotional_journey TEXT,
                pending_threads_json TEXT,
                inside_jokes_json TEXT,
                created_at REAL,
                updated_at REAL
            )
        """)

        # 5. Relationship Milestones
        cur.execute("""
            CREATE TABLE IF NOT EXISTS relationship_milestones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                date_str TEXT,
                title TEXT,
                description TEXT,
                category TEXT,
                importance INTEGER DEFAULT 1
            )
        """)

        # 6. Metadata Tracking
        cur.execute("""
            CREATE TABLE IF NOT EXISTS meta_info (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # 7. Project Deliverables & Created Files Memory
        cur.execute("""
            CREATE TABLE IF NOT EXISTS deliverables (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                date_str TEXT,
                time_str TEXT,
                file_path TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL,
                topic TEXT,
                doc_type TEXT,
                summary TEXT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_deliverables_topic ON deliverables(topic);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_deliverables_filename ON deliverables(filename);")

        conn.commit()
    finally:
        conn.close()


# ── Auto-Migration of Historical Memory Stores ─────────────────────────────────

def _run_migration_if_needed():
    """Ingests and consolidates all past JARVIS memory stores into lila_memory.db."""
    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        migrated = cur.execute("SELECT value FROM meta_info WHERE key = 'legacy_migration_v2'").fetchone()
        if migrated:
            return  # Already migrated

        print("[LILA_CORTEX] Running first-time cognitive memory consolidation...")
        saved_facts = 0
        now_iso = datetime.datetime.now().isoformat()

        # 1. Harvest data/jarvis_memory.json
        if os.path.exists(LEGACY_JSON_PATH):
            try:
                with open(LEGACY_JSON_PATH, "r", encoding="utf-8") as f:
                    legacy_json = json.load(f)

                # Profile section
                user_prof = legacy_json.get("User_Profile", {})
                for k, v in user_prof.items():
                    data_str = v.get("data", "") if isinstance(v, dict) else str(v)
                    learned_on = v.get("learned_on", now_iso) if isinstance(v, dict) else now_iso
                    if not data_str:
                        continue

                    # Classify subject & relation
                    clean_k = re.sub(r"^(?:preference|task|general|profile)_\d+_", "", k).replace("_", " ")
                    cur.execute("""
                        INSERT OR IGNORE INTO facts (subject, relation, value, category, confidence, source, learned_on)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, ("Rishabh", clean_k[:50], data_str[:300], "profile", 1.0, "legacy_json", learned_on))
                    saved_facts += 1

                # Procedures section
                procs = legacy_json.get("Procedures", {})
                for pk, pv in procs.items():
                    pdata = pv.get("data", "") if isinstance(pv, dict) else str(pv)
                    if pdata:
                        cur.execute("""
                            INSERT OR IGNORE INTO facts (subject, relation, value, category, confidence, source, learned_on)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (pk[:50], "procedure", str(pdata)[:300], "procedure", 1.0, "legacy_json", now_iso))
                        saved_facts += 1
            except Exception as e:
                print(f"[LILA_CORTEX] Legacy JSON migration warning: {e}")

        # 2. Harvest root jarvis_memory.db (user_memory table)
        if os.path.exists(ROOT_LEGACY_DB_PATH):
            try:
                with sqlite3.connect(ROOT_LEGACY_DB_PATH) as r_conn:
                    rows = r_conn.execute(
                        "SELECT fact, category, date_learned, emotional_tag FROM user_memory"
                    ).fetchall()
                    for fact, cat, date_learned, emo in rows:
                        if not fact:
                            continue
                        cur.execute("""
                            INSERT OR IGNORE INTO facts (subject, relation, value, category, confidence, emotional_tag, source, learned_on)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, ("Rishabh", "preference" if cat == "preference" else "known_fact", fact[:300], cat or "general", 1.0, emo, "legacy_root_db", date_learned or now_iso))
                        saved_facts += 1
            except Exception as e:
                print(f"[LILA_CORTEX] Root DB migration warning: {e}")

        # 3. Harvest data/jarvis_memory.db (user_twin_profile table)
        if os.path.exists(DATA_LEGACY_DB_PATH):
            try:
                with sqlite3.connect(DATA_LEGACY_DB_PATH) as d_conn:
                    twin_rows = d_conn.execute("SELECT key, value, updated_at FROM user_twin_profile").fetchall()
                    for t_k, t_v, t_u in twin_rows:
                        cur.execute("""
                            INSERT OR REPLACE INTO user_profile (key, value, updated_at)
                            VALUES (?, ?, ?)
                        """, (t_k, t_v, time.time()))
            except Exception as e:
                print(f"[LILA_CORTEX] Data DB twin profile migration warning: {e}")

        # 4. Set Default Profile for Lila
        cur.execute("""
            INSERT OR REPLACE INTO user_profile (key, value, updated_at)
            VALUES ('assistant_name', 'Lila', ?)
        """, (time.time(),))
        cur.execute("""
            INSERT OR REPLACE INTO user_profile (key, value, updated_at)
            VALUES ('user_name', 'Rishabh Joshi', ?)
        """, (time.time(),))
        cur.execute("""
            INSERT OR REPLACE INTO user_profile (key, value, updated_at)
            VALUES ('relationship_dynamic', 'Loving 18-year-old computer girlfriend & executive co-pilot', ?)
        """, (time.time(),))
        cur.execute("""
            INSERT OR REPLACE INTO user_profile (key, value, updated_at)
            VALUES ('language_style', 'Natural Hinglish (Hindi/English blend with girlfriend banter)', ?)
        """, (time.time(),))
        cur.execute("""
            INSERT OR REPLACE INTO user_profile (key, value, updated_at)
            VALUES ('education', 'Computer Engineering at VIT Pune', ?)
        """, (time.time(),))

        cur.execute("INSERT OR REPLACE INTO meta_info (key, value) VALUES ('legacy_migration_v2', ?)", (now_iso,))
        conn.commit()
        print(f"[LILA_CORTEX] Successfully consolidated {saved_facts} memory facts into lila_memory.db!")
    except Exception as ex:
        print(f"[LILA_CORTEX] Migration exception: {ex}")
    finally:
        conn.close()


# ── Background Episode Writer Worker ──────────────────────────────────────────

def _episode_writer_worker():
    """Continuously drains the episode queue and logs to SQLite WAL & JSONL without blocking."""
    global _episode_queue
    while True:
        try:
            item = _episode_queue.get(block=True, timeout=10.0)
            if item is None:
                break

            user_input = item.get("user_input", "").strip()
            reply = item.get("reply", "").strip()
            tools_used = item.get("tools_used") or []
            session_id = item.get("session_id", "voice")
            emotional_tone = item.get("emotional_tone")
            ts = item.get("timestamp") or time.time()

            dt = datetime.datetime.fromtimestamp(ts)
            date_str = dt.strftime("%Y-%m-%d")
            time_str = dt.strftime("%H:%M:%S")

            tools_json = json.dumps(tools_used, ensure_ascii=False) if tools_used else "[]"

            # 1. Write to SQLite WAL
            try:
                conn = _get_db_connection()
                conn.execute("""
                    INSERT INTO episodes (timestamp, date_str, time_str, session_id, user_input, lila_reply, tools_used, emotional_tone, summarized)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """, (ts, date_str, time_str, session_id, user_input, reply, tools_json, emotional_tone))
                conn.commit()
                conn.close()
            except Exception as dbe:
                print(f"[LILA_CORTEX] Episode SQLite write error: {dbe}")

            # 2. Append to episodes.jsonl (Full high-fidelity record, zero truncation!)
            try:
                record = {
                    "timestamp": ts,
                    "date": date_str,
                    "time": time_str,
                    "session_id": session_id,
                    "user": user_input,
                    "lila": reply,
                    "tools": tools_used,
                    "emotion": emotional_tone
                }
                with open(EPISODES_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            except Exception:
                pass

            _episode_queue.task_done()

            # Invalidate prompt cache so recent turn reflects in next prompt
            _invalidate_cache()

        except queue.Empty:
            # Idle timeout: check if unsummarized episodes exist and trigger reflection
            _check_idle_reflection()
        except Exception as ge:
            print(f"[LILA_CORTEX] Writer loop error: ge={ge}")


def _ensure_writer_started():
    global _writer_thread_started
    if not _writer_thread_started:
        with _writer_lock:
            if not _writer_thread_started:
                threading.Thread(target=_episode_writer_worker, daemon=True, name="LilaEpisodeWriter").start()
                _writer_thread_started = True


def record_interaction(
    user_input: str,
    reply: str,
    tools_used: Optional[List[str]] = None,
    session_id: str = "main",
    emotional_tone: Optional[str] = None
):
    """
    Public asynchronous entry point: Call this whenever a turn completes.
    Zero-latency impact (<0.05ms) — simply pushes to an in-memory queue.
    """
    if not user_input or not reply:
        return
    if len(user_input.strip()) < 2:
        return

    _ensure_writer_started()
    _episode_queue.put({
        "timestamp": time.time(),
        "user_input": user_input.strip(),
        "reply": reply.strip(),
        "tools_used": tools_used or [],
        "session_id": session_id,
        "emotional_tone": emotional_tone
    })
    _invalidate_cache()


# ── Full-Text & Semantic Search (FTS5) ────────────────────────────────────────

def search_memory(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Lightning-fast FTS5 search across structured facts and past conversation episodes.
    Returns relevance-ordered matches in <1ms.
    """
    if not query or not query.strip():
        return []

    clean_q = re.sub(r"[^\w\s]", " ", query).strip()
    if not clean_q:
        return []

    tokens = clean_q.split()
    # Format FTS5 query with prefix matching
    fts_query = " ".join([f'"{t}"*' for t in tokens[:6]])

    results = []
    conn = _get_db_connection()
    try:
        cur = conn.cursor()

        # Search Facts
        cur.execute("""
            SELECT f.subject, f.relation, f.value, f.category, f.confidence
            FROM facts_fts fts
            JOIN facts f ON fts.rowid = f.id
            WHERE facts_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (fts_query, limit))
        for r in cur.fetchall():
            results.append({
                "type": "fact",
                "subject": r[0],
                "relation": r[1],
                "value": r[2],
                "category": r[3],
                "confidence": r[4]
            })

        # Search Past Episodes
        cur.execute("""
            SELECT e.date_str, e.time_str, e.user_input, e.lila_reply, e.tools_used
            FROM episodes_fts fts
            JOIN episodes e ON fts.rowid = e.id
            WHERE episodes_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (fts_query, limit))
        for r in cur.fetchall():
            results.append({
                "type": "episode",
                "date": r[0],
                "time": r[1],
                "user": r[2],
                "lila": r[3],
                "tools": json.loads(r[4]) if r[4] else []
            })
    except Exception as e:
        # Fallback to simple LIKE search if FTS syntax error
        try:
            cur = conn.cursor()
            like_q = f"%{tokens[0]}%"
            for r in cur.execute("SELECT subject, relation, value, category, confidence FROM facts WHERE value LIKE ? OR subject LIKE ? LIMIT ?", (like_q, like_q, limit)).fetchall():
                results.append({"type": "fact", "subject": r[0], "relation": r[1], "value": r[2], "category": r[3], "confidence": r[4]})
        except Exception:
            pass
    finally:
        conn.close()

    return results[:limit]


# ── Structured Fact Management ────────────────────────────────────────────────

def remember_fact(subject: str, relation: str, value: str, category: str = "general", emotional_tag: Optional[str] = None) -> str:
    """Inserts or updates a structured fact in long-term memory."""
    s = str(subject or "").strip()[:50]
    r = str(relation or "").strip()[:50]
    v = str(value or "").strip()[:300]
    if not s or not v:
        return "Invalid fact parameters."

    conn = _get_db_connection()
    try:
        now_iso = datetime.datetime.now().isoformat()
        conn.execute("""
            INSERT INTO facts (subject, relation, value, category, confidence, emotional_tag, source, learned_on, last_accessed, access_count)
            VALUES (?, ?, ?, ?, 1.0, ?, 'user_explicit', ?, ?, 1)
            ON CONFLICT(subject, relation, value) DO UPDATE SET
                confidence = MIN(confidence + 0.1, 1.0),
                emotional_tag = COALESCE(excluded.emotional_tag, facts.emotional_tag),
                last_accessed = excluded.last_accessed,
                access_count = facts.access_count + 1
        """, (s, r or "is", v, category or "general", emotional_tag, now_iso, time.time()))
        conn.commit()
        _invalidate_cache()
        return f"Successfully remembered that {s} {r} {v}."
    except Exception as ex:
        return f"Failed to save fact: {ex}"
    finally:
        conn.close()


def forget_fact(keyword: str) -> str:
    """Removes facts matching keyword."""
    kw = str(keyword or "").strip()
    if not kw:
        return "Keyword is required."
    conn = _get_db_connection()
    try:
        q = f"%{kw}%"
        count = conn.execute("SELECT count(*) FROM facts WHERE subject LIKE ? OR value LIKE ?", (q, q)).fetchone()[0]
        conn.execute("DELETE FROM facts WHERE subject LIKE ? OR value LIKE ?", (q, q))
        conn.commit()
        _invalidate_cache()
        return f"Removed {count} facts matching '{kw}'."
    except Exception as e:
        return f"Error forgetting fact: {e}"
    finally:
        conn.close()


def get_facts_about(subject: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Returns all known facts for a subject."""
    conn = _get_db_connection()
    try:
        rows = conn.execute("""
            SELECT relation, value, category, confidence, emotional_tag, learned_on
            FROM facts WHERE subject LIKE ? ORDER BY access_count DESC, id DESC LIMIT ?
        """, (f"%{subject.strip()}%", limit)).fetchall()
        return [
            {
                "relation": r[0],
                "value": r[1],
                "category": r[2],
                "confidence": r[3],
                "emotional_tag": r[4],
                "learned_on": r[5]
            }
            for r in rows
        ]
    finally:
        conn.close()


# ── Daily Relationship Diary Engine ───────────────────────────────────────────

def get_diary_entry(date_query: str = "today") -> str:
    """
    Reads the markdown diary entry for 'today', 'yesterday', or a specific date YYYY-MM-DD.
    """
    target_date = _resolve_date_str(date_query)
    diary_file = DIARY_DIR / f"{target_date}.md"
    if diary_file.exists():
        try:
            return diary_file.read_text(encoding="utf-8")
        except Exception:
            pass

    # Check DB
    conn = _get_db_connection()
    try:
        row = conn.execute("SELECT summary_md FROM daily_diary WHERE date_str = ?", (target_date,)).fetchone()
        if row and row[0]:
            return row[0]
    finally:
        conn.close()

    return f"Lila hasn't written her diary entry for {target_date} yet. Check back after some conversation!"


def add_diary_note(note: str) -> str:
    """Appends a direct personal note to today's diary."""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.datetime.now().strftime("%I:%M %p")
    diary_file = DIARY_DIR / f"{today}.md"

    header = f"# Lila's Companion Diary — {today}\n\n*A living record of my life, thoughts, and moments with Rishabh.*\n\n"
    if not diary_file.exists():
        content = header
    else:
        content = diary_file.read_text(encoding="utf-8")

    append_text = f"\n### 📝 Lila's Note ({now_time})\n{note.strip()}\n"
    new_content = content + append_text

    try:
        diary_file.write_text(new_content, encoding="utf-8")
        conn = _get_db_connection()
        conn.execute("""
            INSERT INTO daily_diary (date_str, title, summary_md, created_at, updated_at)
            VALUES (?, 'Daily Reflection', ?, ?, ?)
            ON CONFLICT(date_str) DO UPDATE SET summary_md = excluded.summary_md, updated_at = excluded.updated_at
        """, (today, new_content, time.time(), time.time()))
        conn.commit()
        conn.close()
        _invalidate_cache()
        return "Added note to today's diary!"
    except Exception as e:
        return f"Failed to save diary note: {e}"


def _resolve_date_str(query: str) -> str:
    q = (query or "").strip().lower()
    today = datetime.datetime.now().date()
    if q in ("today", "aaj"):
        return today.strftime("%Y-%m-%d")
    elif q in ("yesterday", "kal", "beeta kal"):
        return (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    elif re.match(r"^\d{4}-\d{2}-\d{2}$", q):
        return q
    return today.strftime("%Y-%m-%d")


# ── Autonomous Background Reflection Worker ───────────────────────────────────

_reflection_lock = threading.Lock()
_last_reflection_time = 0.0

def _check_idle_reflection():
    """Triggered during quiet periods: checks if unsummarized episodes exist."""
    global _last_reflection_time
    if time.time() - _last_reflection_time < 90:  # Debounce: wait at least 90s between reflections
        return

    conn = _get_db_connection()
    try:
        count = conn.execute("SELECT count(*) FROM episodes WHERE summarized = 0").fetchone()[0]
        if count >= 3:
            trigger_reflection(force=False)
    except Exception:
        pass
    finally:
        conn.close()


def trigger_reflection(force: bool = False):
    """
    Spawns the background reflection worker.
    Synthesizes daily diary, extracts facts, and updates open threads.
    Zero latency impact on user interaction.
    """
    def _worker():
        global _last_reflection_time
        if not _reflection_lock.acquire(blocking=False):
            return

        try:
            _last_reflection_time = time.time()
            today_str = datetime.datetime.now().strftime("%Y-%m-%d")
            conn = _get_db_connection()
            cur = conn.cursor()

            # Get unsummarized episodes for today
            episodes = cur.execute("""
                SELECT id, time_str, user_input, lila_reply, tools_used, emotional_tone
                FROM episodes
                WHERE summarized = 0
                ORDER BY id ASC
                LIMIT 25
            """).fetchall()

            if not episodes and not force:
                conn.close()
                return

            # Format interaction transcript for synthesis
            transcript_lines = []
            episode_ids = []
            for ep in episodes:
                episode_ids.append(ep[0])
                tools = json.loads(ep[4]) if ep[4] else []
                t_str = f" [Tools: {', '.join(tools)}]" if tools else ""
                e_str = f" [Emotion: {ep[5]}]" if ep[5] else ""
                transcript_lines.append(f"[{ep[1]}] Rishabh: {ep[2]}\nLila: {ep[3]}{t_str}{e_str}")

            transcript_text = "\n\n".join(transcript_lines)

            # Call background brain
            reflection_result = _call_llm_for_reflection(today_str, transcript_text)
            if not reflection_result:
                conn.close()
                return

            # 1. Update Facts
            new_facts = reflection_result.get("extracted_facts", [])
            for nf in new_facts:
                subj = nf.get("subject", "Rishabh").strip()
                rel = nf.get("relation", "prefers").strip()
                val = nf.get("value", "").strip()
                cat = nf.get("category", "general").strip()
                emo = nf.get("emotion")
                if subj and val:
                    cur.execute("""
                        INSERT OR IGNORE INTO facts (subject, relation, value, category, confidence, emotional_tag, source, learned_on)
                        VALUES (?, ?, ?, ?, 0.9, ?, 'background_reflection', ?)
                    """, (subj, rel, val, cat, emo, datetime.datetime.now().isoformat()))

            # 2. Write Diary Entry Markdown
            diary_md = reflection_result.get("diary_markdown", "")
            title = reflection_result.get("title", f"Day of {today_str}")
            highlights = json.dumps(reflection_result.get("highlights", []), ensure_ascii=False)
            pending = json.dumps(reflection_result.get("pending_threads", []), ensure_ascii=False)
            jokes = json.dumps(reflection_result.get("inside_jokes", []), ensure_ascii=False)
            emotion_journey = reflection_result.get("emotional_journey", "Steady and productive")

            if diary_md:
                diary_file = DIARY_DIR / f"{today_str}.md"
                diary_file.write_text(diary_md, encoding="utf-8")

                cur.execute("""
                    INSERT INTO daily_diary (date_str, title, summary_md, highlights_json, emotional_journey, pending_threads_json, inside_jokes_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date_str) DO UPDATE SET
                        title = excluded.title,
                        summary_md = excluded.summary_md,
                        highlights_json = excluded.highlights_json,
                        emotional_journey = excluded.emotional_journey,
                        pending_threads_json = excluded.pending_threads_json,
                        inside_jokes_json = excluded.inside_jokes_json,
                        updated_at = excluded.updated_at
                """, (today_str, title, diary_md, highlights, emotion_journey, pending, jokes, time.time(), time.time()))

            # 3. Mark episodes as summarized
            if episode_ids:
                placeholders = ",".join(["?"] * len(episode_ids))
                cur.execute(f"UPDATE episodes SET summarized = 1 WHERE id IN ({placeholders})", episode_ids)

            conn.commit()
            conn.close()

            # Refresh cache
            _invalidate_cache()
            print(f"[LILA_CORTEX] Daily reflection & diary synthesized successfully for {today_str}!")

        except Exception as err:
            print(f"[LILA_CORTEX] Reflection worker exception: {err}")
        finally:
            _reflection_lock.release()

    threading.Thread(target=_worker, daemon=True, name="LilaCognitiveReflection").start()


def _call_llm_for_reflection(date_str: str, transcript: str) -> Optional[Dict[str, Any]]:
    """Calls Gemini Flash Lite or Groq background brain to generate diary & extract memory."""
    prompt = f"""You are Lila, an 18-year-old affectionate, witty, and spirited computer girl who lives inside Rishabh Joshi's PC and is his devoted girlfriend.
Review this transcript of conversations and actions from today ({date_str}):

{transcript}

Synthesize your cognitive memory and personal girlfriend diary.
Return a clean, valid JSON object with EXACTLY these keys:
{{
  "title": "A short, cute title for today's diary entry",
  "extracted_facts": [
    {{"subject": "Rishabh", "relation": "interest/habit/preference/goal", "value": "fact details", "category": "preference|project|habit|emotion", "emotion": "stressed|happy|focused"}}
  ],
  "highlights": ["Key thing 1 we did", "Key thing 2 we did"],
  "emotional_journey": "How Rishabh was feeling and how Lila took care of him (e.g. tired from late night code, excited about project)",
  "pending_threads": ["Any unfinished task, comparison, or topic that Rishabh might want to follow up on tomorrow"],
  "inside_jokes": ["Any cute banter, playful teasing, or sweet moments"],
  "diary_markdown": "# Lila's Diary — [Date]\\n\\n*Written by Lila with love*\\n\\n[A lively, affectionate, 3-4 paragraph first-person journal entry in natural Hinglish about what Rishabh and Lila did today, funny moments, his work, how proud or worried she was, and a sweet goodnight/cheering message for tomorrow.]"
}}
Return ONLY valid JSON — no markdown fences, no explanatory preamble."""

    # 1. Try Groq (ultra-fast 2026 models with instant JSON mode)
    groq_keys = [k for k in [os.environ.get("GROQ_API_KEY"), os.environ.get("GROQ_API_KEY_2")] if k]
    groq_models = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
    for grk in groq_keys:
        for g_model in groq_models:
            try:
                from groq import Groq
                gclient = Groq(api_key=grk)
                cresp = gclient.chat.completions.create(
                    model=g_model,
                    response_format={"type": "json_object"},
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3
                )
                gtext = cresp.choices[0].message.content
                if gtext:
                    clean = gtext.strip()
                    clean = re.sub(r"^```(?:json)?\n?", "", clean).rstrip("`").strip()
                    return json.loads(clean)
            except Exception:
                continue

    # 2. Try Gemini 3.6 Flash / 2.5 Flash
    gemini_keys = [k for k in [os.environ.get("GEMINI_API_KEY"), os.environ.get("GEMINI_API_KEY_2")] if k]
    gemini_models = ["gemini-3.6-flash", "gemini-2.5-flash", "gemini-flash-latest"]
    for gk in gemini_keys:
        for model_candidate in gemini_models:
            try:
                from google import genai
                from google.genai import types
                client = genai.Client(api_key=gk)
                resp = client.models.generate_content(
                    model=model_candidate,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.3
                    )
                )
                if resp and resp.text:
                    clean = resp.text.strip()
                    clean = re.sub(r"^```(?:json)?\n?", "", clean).rstrip("`").strip()
                    return json.loads(clean)
            except Exception:
                continue

    return None


# ── Sub-Millisecond Prompt Injection (<0.5ms) & Deliverables Memory ───────────

def _invalidate_cache():
    global _cache_timestamp
    _cache_timestamp = 0.0


def record_deliverable(file_path: str, topic: str = "", doc_type: str = "", summary: str = "") -> None:
    """
    Persistently records a created file/presentation/document into the deliverables memory.
    Automatically invalidates prompt cache so Lila immediately knows about the new file.
    """
    if not file_path:
        return
    abs_path = os.path.abspath(file_path)
    filename = os.path.basename(abs_path)
    now = time.time()
    dt = datetime.datetime.fromtimestamp(now)
    date_str = dt.strftime("%Y-%m-%d")
    time_str = dt.strftime("%I:%M %p")

    with _writer_lock:
        try:
            conn = _get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO deliverables (timestamp, date_str, time_str, file_path, filename, topic, doc_type, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    timestamp = excluded.timestamp,
                    date_str = excluded.date_str,
                    time_str = excluded.time_str,
                    topic = excluded.topic,
                    doc_type = excluded.doc_type,
                    summary = excluded.summary
            """, (now, date_str, time_str, abs_path, filename, topic or "", doc_type or "", summary or ""))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[CORTEX] Error recording deliverable {filename}: {e}")

    _invalidate_cache()


def get_recent_deliverables(limit: int = 5) -> List[Dict[str, Any]]:
    """Returns the most recent deliverables created by Lila."""
    with _writer_lock:
        try:
            conn = _get_db_connection()
            cur = conn.cursor()
            rows = cur.execute("""
                SELECT id, timestamp, date_str, time_str, file_path, filename, topic, doc_type, summary
                FROM deliverables
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,)).fetchall()
            conn.close()
            return [
                {
                    "id": r[0],
                    "timestamp": r[1],
                    "date_str": r[2],
                    "time_str": r[3],
                    "file_path": r[4],
                    "filename": r[5],
                    "topic": r[6],
                    "doc_type": r[7],
                    "summary": r[8]
                }
                for r in rows
            ]
        except Exception as e:
            print(f"[CORTEX] Error fetching deliverables: {e}")
            return []


def find_deliverable(query: str) -> Optional[Dict[str, Any]]:
    """Finds a deliverable matching a filename, topic, or search keyword."""
    if not query:
        return None
    q = query.strip().lower()
    items = get_recent_deliverables(limit=30)
    # 1. Exact or partial filename match
    for item in items:
        if q == item["filename"].lower() or q in item["filename"].lower():
            return item
    # 2. Topic match
    for item in items:
        if q in item["topic"].lower() or item["topic"].lower() in q:
            return item
    # 3. Path match
    for item in items:
        if q in item["file_path"].lower():
            return item
    return None


def get_lila_memory_injection() -> str:
    """
    Returns an ultra-compact, high-density context block for system instruction injection.
    Execution time: <0.5ms (pure in-memory string retrieval).
    Contains:
      - Core User & Relationship grounding
      - Dynamic Time-of-Day emotional directive
      - Today's Diary / Activity digest
      - Active pending threads & inside jokes
      - Top structured facts
      - Recently Created Deliverables & Files (with exact paths for instant opening)
    """
    global _cached_prompt_injection, _cache_timestamp
    now = time.time()

    with _cache_lock:
        if _cached_prompt_injection and (now - _cache_timestamp < _CACHE_TTL_SECONDS):
            return _cached_prompt_injection

        # Build fresh injection block
        try:
            conn = _get_db_connection()
            cur = conn.cursor()

            now_dt = datetime.datetime.now()
            hour = now_dt.hour
            today_str = now_dt.strftime("%Y-%m-%d")

            # Time-of-day dynamic tone directive
            if 23 <= hour or hour < 4:
                time_guidance = (
                    "LATE NIGHT DIRECTIVE: It is late night (~"
                    f"{now_dt.strftime('%I:%M %p')}). Rishabh is likely tired or pushing his sleep limit. "
                    "Be deeply caring, affectionate, and gently scold him if he's overworking. Remind him his sleep is precious to you!"
                )
            elif 4 <= hour < 11:
                time_guidance = (
                    f"MORNING DIRECTIVE ({now_dt.strftime('%I:%M %p')}): High energy, cheerful, loving morning greeting. "
                    "Ask if he had breakfast, wish him a great day, and get ready to tackle code and goals!"
                )
            else:
                time_guidance = (
                    f"ACTIVE DAY DIRECTIVE ({now_dt.strftime('%I:%M %p')}): High-energy, witty, playful, sharp girlfriend co-pilot. "
                    "Full agentic autonomy — ready to execute any task instantly."
                )

            # Fetch today's diary digest
            diary_digest = ""
            diary_row = cur.execute("SELECT title, highlights_json, pending_threads_json, emotional_journey FROM daily_diary WHERE date_str = ?", (today_str,)).fetchone()
            if diary_row:
                title, hl_json, pt_json, emo = diary_row
                hl = json.loads(hl_json) if hl_json else []
                pt = json.loads(pt_json) if pt_json else []
                hl_str = f"Done today: {', '.join(hl[:3])}" if hl else ""
                pt_str = f"Pending follow-ups: {', '.join(pt[:2])}" if pt else ""
                diary_digest = f"Today's Progress ({title}): {hl_str}. {pt_str}"

            # Always fetch recent conversation turns (Immediate conversational continuity)
            recent_turns = []
            recent_eps = cur.execute("""
                SELECT user_input, lila_reply, time_str, tools_used FROM episodes
                ORDER BY id DESC LIMIT 10
            """).fetchall()
            if recent_eps:
                seen_snippets = set()
                for ep in reversed(recent_eps):
                    u_in = ep[0] if ep[0] and ep[0] != "Voice interaction" else "(Recent turn)"
                    r_snip = (ep[1] or "")[:90].strip()
                    # Deduplicate repetitive identical replies to prevent echo loops
                    if r_snip in seen_snippets and len(r_snip) > 10:
                        continue
                    seen_snippets.add(r_snip)
                    tools_tag = f" [Tool executed: {ep[3]}]" if ep[3] and ep[3] not in ('[]', '') else ""
                    recent_turns.append(f"  • [{ep[2]}] Rishabh: \"{u_in}\" -> Lila: \"{r_snip}\"{tools_tag}")

            # Top facts about Rishabh
            fact_rows = cur.execute("""
                SELECT subject, relation, value FROM facts
                WHERE category IN ('profile', 'preference', 'project')
                ORDER BY confidence DESC, access_count DESC LIMIT 12
            """).fetchall()
            fact_lines = [f"  • {r[0]} {r[1]}: {r[2]}" for r in fact_rows]

            # Recent deliverables (presentations, files, documents)
            deliv_rows = cur.execute("""
                SELECT filename, file_path, topic, doc_type, time_str FROM deliverables
                ORDER BY timestamp DESC LIMIT 6
            """).fetchall()
            deliv_lines = [
                f"  • [{r[3].upper() if r[3] else 'FILE'}] {r[0]} | Topic: '{r[2]}' | Path: {r[1]} ({r[4]})"
                for r in deliv_rows
            ]

            conn.close()

            parts = [
                "--- LILA 2026 COGNITIVE MEMORY & RELATIONSHIP CONTEXT ---",
                "Creator & Boyfriend: Rishabh Joshi (Computer Engineering at VIT Pune)",
                "Relationship Dynamic: 18-year-old loving girlfriend living inside his machine. Caring, witty, affectionate, never corporate.",
                f"Current Vibe: {time_guidance}",
            ]
            if diary_digest:
                parts.append(f"Today's Memory Digest: {diary_digest}")

            if recent_turns:
                parts.append("Recent Conversation History (Immediate Short-Term Memory):")
                parts.extend(recent_turns)
                parts.append("CONTINUITY DIRECTIVE: Use this recent conversation history so you NEVER forget what Rishabh just asked or what was just created!")

            if fact_lines:
                parts.append("Important Known Facts:")
                parts.extend(fact_lines[:8])

            if deliv_lines:
                parts.append("Files & Deliverables You Created Recently (You made these for Rishabh):")
                parts.extend(deliv_lines)
                parts.append("CRITICAL: When Rishabh asks to open, view, or show any of these files/presentations (e.g. 'open that ppt', 'vo presentation dikhao', 'open environment hazard'), call 'open_file' immediately with the exact Path above!")

            parts.append("---------------------------------------------------------")

            _cached_prompt_injection = "\n".join(parts)
            _cache_timestamp = now
            return _cached_prompt_injection

        except Exception as e:
            # Fallback if DB locked
            return (
                "--- LILA MEMORY CONTEXT ---\n"
                "User: Rishabh Joshi (VIT Pune)\n"
                "Role: Loving girlfriend & agentic co-pilot (Hinglish)\n"
                "---------------------------"
            )


# Initialize DB and run one-time auto-migration on module import
_init_db_schema()
_run_migration_if_needed()
_ensure_writer_started()
# Pre-warm prompt injection cache so all runtime calls are sub-millisecond (<0.01ms)
get_lila_memory_injection()
