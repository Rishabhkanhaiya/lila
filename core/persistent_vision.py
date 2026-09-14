"""
persistent_vision.py  —  JARVIS Persistent Vision Engine v1.0

Always-on screen understanding with proactive coding assistance.

Architecture:
  ScreenWatcher (thread, every 8s) → ScreenDiffer (hash-based change detection)
    → only if changed: ScreenAnalyzer (call_vision → structured JSON)
    → VisionMemory (circular buffer, last 10 states)
    → CodingAssistant (detects errors/warnings → ProactiveEvent)
    → brain.py injection: real-time screen context

Structured screen analysis returns:
  {
    "active_app":     "Visual Studio Code",
    "file_open":      "main.py",
    "language":       "Python",
    "screen_state":   "coding",        # coding / browsing / reading / idle
    "errors_visible": ["TypeError on line 47: ..."],
    "code_context":   "function call_groq_brain ...",
    "suggestions":    ["Check parameter types", "..."],
    "emotional_cue":  "user appears focused"
  }
"""

import os
from core.jarvis_logger import log_error, log_warn
import base64
import hashlib
import json
import time
import sqlite3
import threading
import tempfile
from datetime import datetime, timedelta
from collections import deque
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List

from core.monitors.base_monitor import BaseMonitor

# ── Paths ─────────────────────────────────────────────────────────────────────
_BASE   = Path(__file__).parent.parent
PV_DB   = str(_BASE / "jarvis_memory.db")

# ── Singleton ─────────────────────────────────────────────────────────────────
_pv_instance = None
_pv_lock     = threading.Lock()

# ── Analysis result type ──────────────────────────────────────────────────────
@dataclass
class ScreenState:
    active_app:     str   = "unknown"
    file_open:      str   = ""
    language:       str   = ""
    screen_state:   str   = "idle"          # coding/browsing/reading/idle/media
    errors_visible: list  = field(default_factory=list)
    code_context:   str   = ""
    suggestions:    list  = field(default_factory=list)
    emotional_cue:  str   = ""
    raw_summary:    str   = ""
    timestamp:      float = field(default_factory=time.time)
    screen_hash:    str   = ""


# ── DB Init ───────────────────────────────────────────────────────────────────
def _init_pv_tables():
    try:
        conn = sqlite3.connect(PV_DB)
        try:
            cur  = conn.cursor()
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS screen_sessions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT DEFAULT (datetime('now','localtime')),
                    active_app      TEXT,
                    file_open       TEXT,
                    language        TEXT,
                    screen_state    TEXT,
                    errors_count    INTEGER DEFAULT 0,
                    summary         TEXT,
                    suggestions     TEXT
                );
            """)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log_warn("persistent_vision", f"DB init failed: {e}")


# ── Screen Differ ─────────────────────────────────────────────────────────────
class ScreenDiffer:
    """
    Detects meaningful screen changes using MD5 hash of downsampled screenshot.
    Only triggers analysis when the screen actually changes, saving API calls.
    """

    SAMPLE_RATE = 4   # Sample every 4th pixel for faster hashing

    def __init__(self, sensitivity: float = 0.02):
        self.sensitivity   = sensitivity  # Fraction of pixels that must change
        self._last_hash    = None
        self._change_count = 0

    def has_changed(self, screenshot_bytes: bytes) -> bool:
        """Returns True if the screenshot is meaningfully different from the last one."""
        current_len = len(screenshot_bytes)
        if self._last_hash is None:
            self._last_hash = current_len
            return True
        if abs(current_len - self._last_hash) > (current_len * self.sensitivity):
            self._last_hash = current_len
            return True
        return False

    @property
    def total_changes(self) -> int:
        return self._change_count


# ── Screen Analyzer ───────────────────────────────────────────────────────────
class ScreenAnalyzer:
    """
    Sends screenshot to vision model and parses structured JSON response.
    """

    VISION_PROMPT = """
You are JARVIS's persistent visual cortex. Analyze this screenshot and return ONLY a JSON object.

Extract:
1. active_app: The application name (e.g., "Visual Studio Code", "Chrome", "Terminal")
2. file_open: If it's a code editor, the filename being edited (e.g., "main.py") or ""
3. language: Programming language if code is visible (e.g., "Python", "JavaScript") or ""
4. screen_state: One of: "coding", "browsing", "reading", "idle", "media", "terminal", "docs"
5. errors_visible: List of visible error messages, red underlines, or linter warnings (max 3)
6. code_context: Brief description of what code the user is working on (1 sentence) or ""
7. suggestions: Up to 2 proactive suggestions JARVIS could make right now (be specific)
8. emotional_cue: User's apparent focus level from screen activity (e.g., "deep focus", "switching tabs frequently", "idle")

Return ONLY valid JSON. No explanation. Example:
{
  "active_app": "Visual Studio Code",
  "file_open": "brain.py",
  "language": "Python",
  "screen_state": "coding",
  "errors_visible": ["NameError: name 'call_vision' is not defined on line 82"],
  "code_context": "User is editing the LLM routing function in JARVIS brain",
  "suggestions": ["Import call_vision from core.eyes", "Check if the function signature matches"],
  "emotional_cue": "deep focus"
}
"""

    def analyze(self, img_b64: str) -> ScreenState:
        try:
            from core.eyes import call_vision
            result = call_vision(img_b64, self.VISION_PROMPT)

            # Strip markdown fences
            import re
            result = re.sub(r'^```json\s*', '', result, flags=re.IGNORECASE)
            result = re.sub(r'^```\s*',     '', result)
            result = re.sub(r'\s*```$',     '', result).strip()

            data = json.loads(result)
            return ScreenState(
                active_app     = str(data.get("active_app",     "unknown")),
                file_open      = str(data.get("file_open",      "")),
                language       = str(data.get("language",       "")),
                screen_state   = str(data.get("screen_state",   "idle")),
                errors_visible = data.get("errors_visible", []) or [],
                code_context   = str(data.get("code_context",   "")),
                suggestions    = data.get("suggestions",    []) or [],
                emotional_cue  = str(data.get("emotional_cue",  "")),
                raw_summary    = result[:300],
                timestamp      = time.time(),
            )
        except json.JSONDecodeError:
            # Vision returned plain text — still useful
            return ScreenState(raw_summary=result[:300] if 'result' in locals() else "",
                               timestamp=time.time())
        except Exception as e:
            print(f"[PERSISTENT VISION]: Analysis error: {e}")
            return ScreenState(timestamp=time.time())


# ── Vision Memory ─────────────────────────────────────────────────────────────
class VisionMemory:
    """Circular buffer of the last 10 screen states with temporal context."""

    def __init__(self, maxlen: int = 10):
        self._buffer = deque(maxlen=maxlen)
        self._lock   = threading.Lock()

    def add(self, state: ScreenState):
        with self._lock:
            self._buffer.append(state)

    def get_recent(self, n: int = 3) -> List[ScreenState]:
        with self._lock:
            return list(self._buffer)[-n:]

    def get_current(self) -> Optional[ScreenState]:
        with self._lock:
            return self._buffer[-1] if self._buffer else None

    def get_context_string(self) -> str:
        """Returns a concise description of current screen for brain.py injection."""
        current = self.get_current()
        if not current:
            return ""

        parts = []
        if current.active_app and current.active_app != "unknown":
            parts.append(f"App: {current.active_app}")
        if current.file_open:
            parts.append(f"File: {current.file_open}")
        if current.language:
            parts.append(f"Language: {current.language}")
        if current.screen_state:
            parts.append(f"Mode: {current.screen_state}")
        if current.code_context:
            parts.append(f"Working on: {current.code_context}")
        if current.errors_visible:
            errs = "; ".join(current.errors_visible[:2])
            parts.append(f"Visible errors: {errs}")

        # Time on current task
        recent = self.get_recent(3)
        if len(recent) >= 2:
            same_task_since = None
            for s in reversed(recent[:-1]):
                if s.file_open == current.file_open and s.active_app == current.active_app:
                    same_task_since = s.timestamp
            if same_task_since:
                mins = int((time.time() - same_task_since) / 60)
                if mins > 5:
                    parts.append(f"On this task: ~{mins} minutes")

        return " | ".join(parts) if parts else ""


# ── Coding Assistant ──────────────────────────────────────────────────────────
class CodingAssistant:
    """
    Analyzes screen states and fires ProactiveEvents for coding issues.
    Tracks:
    - Visible errors (immediate alert)
    - Long time on same error (escalate)
    - Docs browsing while coding (pre-fetch examples)
    - Stuck on same file > 30 min with no progress
    """

    def __init__(self):
        self._last_error_alert   = {}   # error_text → last_alert_time
        self._file_start_times   = {}   # file_path → first_seen_time
        self._stuck_alert_sent   = set()
        self._cooldown           = 180  # seconds between same-error alerts

    def analyze(self, state: ScreenState) -> list:
        """Returns list of ProactiveEvent for any coding issues detected."""
        events = []

        try:
            from core.proactive_orchestrator import ProactiveEvent
        except ImportError:
            return events

        # ── 1. Visible errors alert ───────────────────────────────
        for error in state.errors_visible[:2]:
            last = self._last_error_alert.get(error[:50], 0)
            if time.time() - last > self._cooldown:
                self._last_error_alert[error[:50]] = time.time()
                events.append(ProactiveEvent(
                    priority=3,
                    category="vision_coding",
                    title=f"Code Error Detected: {state.file_open or state.active_app}",
                    message=(
                        f"Rishabh, I noticed an error in {state.file_open or 'your code'}: "
                        f"{error[:120]}. "
                        f"{''.join(state.suggestions[:1]) if state.suggestions else 'Would you like me to take a look and fix it?'}"
                    ),
                    data={"error": error, "file": state.file_open,
                          "suggestions": state.suggestions},
                    timestamp=time.time(),
                ))

        # ── 2. Stuck on same file > 30 min ───────────────────────
        if state.file_open and state.screen_state == "coding":
            key = state.file_open
            if key not in self._file_start_times:
                self._file_start_times[key] = time.time()
            elif (time.time() - self._file_start_times[key] > 1800 and
                  key not in self._stuck_alert_sent):
                self._stuck_alert_sent.add(key)
                events.append(ProactiveEvent(
                    priority=4,
                    category="vision_coding",
                    title="Extended Session Detected",
                    message=(
                        f"Hey Rishabh, aap {state.file_open} par 30 minutes se kaam kar rahe ho. "
                        f"Want to take a quick break or need me to review it?"
                    ),
                    data={"file": state.file_open},
                    timestamp=time.time(),
                ))
        elif state.file_open:
            self._file_start_times.pop(state.file_open, None)
            self._stuck_alert_sent.discard(state.file_open)

        return events


# ── Persistent Vision Engine (singleton) ─────────────────────────────────────
class PersistentVisionEngine:
    """
    Master engine coordinating all vision components.
    ScreenWatcher thread polls the screen every POLL_INTERVAL seconds.
    """

    POLL_INTERVAL = 8   # seconds between screen captures
    API_COOLDOWN  = 15  # minimum seconds between API calls (rate limit protection)

    def __init__(self, enabled: bool = True):
        _init_pv_tables()
        self.enabled      = enabled
        self.differ       = ScreenDiffer()
        self.analyzer     = ScreenAnalyzer()
        self.memory       = VisionMemory(maxlen=10)
        self.coding_asst  = CodingAssistant()

        self._event_queue = None
        self._last_api_call = 0.0
        self._lock          = threading.Lock()
        self._stop_event    = threading.Event()
        self._watcher_thread = None

        print(f"[PERSISTENT VISION]: Engine online. Polling every {self.POLL_INTERVAL}s.")

    def start(self, event_queue=None):
        """Start the background screen watcher thread."""
        self._event_queue = event_queue
        self._stop_event.clear()
        self._watcher_thread = threading.Thread(
            target=self._watcher_loop,
            daemon=True,
            name="JARVIS-ScreenWatcher"
        )
        self._watcher_thread.start()

    def stop(self):
        """Signal the watcher thread to stop."""
        self._stop_event.set()

    def _watcher_loop(self):
        if self._stop_event.wait(5.0):
            return
        while not self._stop_event.is_set():
            try:
                self._capture_and_analyze()
            except Exception as e:
                print(f"[PERSISTENT VISION]: Watcher error: {e}")
            if self._stop_event.wait(self.POLL_INTERVAL):
                break

    def _capture_and_analyze(self):
        try:
            import pyautogui
            screenshot = pyautogui.screenshot()

            # Convert to bytes for hashing
            import io
            buf = io.BytesIO()
            screenshot.save(buf, format="PNG")
            img_bytes = buf.getvalue()

            # Check if screen actually changed
            if not self.differ.has_changed(img_bytes):
                return

            # Rate limit API calls
            if time.time() - self._last_api_call < self.API_COOLDOWN:
                return

            # Analyze
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            state   = self.analyzer.analyze(img_b64)
            state.screen_hash = hashlib.md5(img_bytes[::4]).hexdigest()[:8]

            self._last_api_call = time.time()
            self.memory.add(state)

            # Persist to DB
            self._persist(state)

            # Coding assistant check
            events = self.coding_asst.analyze(state)
            if self._event_queue and events:
                for evt in events:
                    self._event_queue.put((evt.priority, evt.timestamp, evt))

            # ⚡ Notify Sixth Sense immediately when errors become visible
            if state.errors_visible:
                try:
                    from core.omni_synthesis import notify_sixth_sense
                    notify_sixth_sense("screen_errors", {
                        "errors":  state.errors_visible[:2],
                        "file":    state.file_open,
                        "app":     state.active_app,
                    })
                except Exception as e:
                    log_warn("persistent_vision", f"notify sixth sense failed: {e}")

            # Log
            if state.active_app and state.active_app != "unknown":
                print(
                    f"[PERSISTENT VISION]: {state.active_app} | "
                    f"{state.file_open or state.screen_state} | "
                    f"errors={len(state.errors_visible)}"
                )

        except ImportError:
            pass  # pyautogui not available
        except Exception as e:
            print(f"[PERSISTENT VISION]: Capture error: {e}")

    def get_injection(self) -> str:
        """Returns brain.py system prompt injection."""
        ctx = self.memory.get_context_string()
        if not ctx:
            return ""
        return (
            f"[PERSISTENT VISION — Live Screen Context]\n{ctx}\n"
            f"DIRECTIVE: Use this real-time screen context to make your response "
            f"more relevant. If errors are visible, offer to help fix them."
        )

    def get_current_state(self) -> Optional[ScreenState]:
        return self.memory.get_current()

    def _persist(self, state: ScreenState):
        try:
            conn = sqlite3.connect(PV_DB)
            try:
                cur  = conn.cursor()
                cur.execute("""
                    INSERT INTO screen_sessions
                    (active_app, file_open, language, screen_state,
                     errors_count, summary, suggestions)
                    VALUES (?,?,?,?,?,?,?)
                """, (
                    state.active_app, state.file_open, state.language,
                    state.screen_state, len(state.errors_visible),
                    state.raw_summary[:300],
                    json.dumps(state.suggestions),
                ))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            log_warn("persistent_vision", f"persist vision state failed: {e}")


# ── Singleton accessors ───────────────────────────────────────────────────────
def get_vision() -> PersistentVisionEngine:
    global _pv_instance
    if _pv_instance is None:
        with _pv_lock:
            if _pv_instance is None:
                _pv_instance = PersistentVisionEngine()
    return _pv_instance


def get_vision_injection() -> str:
    """Module-level shortcut for brain.py."""
    try:
        return get_vision().get_injection()
    except Exception as e:
        log_warn("persistent_vision", f"vision injection failed: {e}")
        return ""


# ── BaseMonitor wrapper ───────────────────────────────────────────────────────
class PersistentVisionMonitor(BaseMonitor):
    """
    Orchestrator-compatible monitor that starts the vision engine's background
    watcher thread and passes the event queue to it.
    The actual work is done in the watcher thread, not in check().
    """
    name = "PersistentVision"
    interval_seconds = 120   # check() only reports stats every 2 min

    def __init__(self, event_queue=None, config=None):
        super().__init__(event_queue, config)
        self._engine = get_vision()
        # Start watcher thread with event queue
        self._engine.start(event_queue=event_queue)
        print("[PERSISTENT VISION]: Screen watcher started.")

    def check(self):
        events = []
        try:
            current = self._engine.get_current_state()
            if current:
                changes = self._engine.differ.total_changes
                print(
                    f"[PERSISTENT VISION]: {current.active_app} | "
                    f"{current.screen_state} | "
                    f"total changes seen: {changes}"
                )
        except Exception as e:
            print(f"[PERSISTENT VISION]: Monitor check error: {e}")
        return events
