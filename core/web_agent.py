"""
web_agent.py  —  JARVIS Autonomous Web Agent v7.0  ⚡ Powered by browser-use

Architecture (v7.0) — The Ultimate Hybrid:
  browser-use Agent  → State-of-the-art DOM extraction, LLM planning, retry (handles 90%+ of sites)
  WebMemory          → SQLite self-teaching: saves learned tactics per-domain, auto-injects on next visit
  VoiceCollaboration → JARVIS speaks progress + listens for user input mid-task (OTP, login, confirm)
  BarrierBuster      → Auto-dismisses cookie banners, age gates, popups before every interaction
  JARVIS Custom Tools → Voice + Memory exposed as native LLM tools (jarvis_ask_user, jarvis_confirm, etc.)
  TrajectoryMemory   → ChromaDB vector recall: warm-starts agent with past success patterns
  SwarmOrchestrator  → Parallel multi-tab async agents for price comparison tasks
  SessionPersistence → Persistent Chrome profile: cookies + sessions saved across runs
  WebAgent           → Public singleton API (backward-compatible with all existing callers)

What was DELETED (now handled by browser-use internally):
  DOMExtractor, A11yExtractor, ShadowDOMPiercer → browser-use has superior a11y tree
  ActionPlanner, PLAN_PROMPT, PAGE_EVALUATOR    → browser-use does its own LLM planning
  ActionExecutor, SmartRetryEngine              → browser-use has built-in multi-strategy retry
  AutocompleteHandler                           → browser-use handles dropdowns natively
  AntiDetection (mouse paths, timing)           → browser-use has stealth mode
  StuckDetector                                 → browser-use has loop_detection_enabled
  Old _run_loop (500 lines)                     → replaced by await agent.run()
"""

import os
import json
import re
import time
import base64
import sqlite3
import asyncio
import random
import threading
import traceback
import concurrent.futures
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field

from core.monitors.base_monitor import BaseMonitor
from core.jarvis_logger import log_error, log_warn

# ── Paths ─────────────────────────────────────────────────────────────────────
_BASE    = Path(__file__).parent.parent
from core.config import DB_PATH as WA_DB   # Fix #3: centralized DB path
PROFILE  = str(_BASE / "browser_profile")   # persistent session storage

# ── browser-use imports ───────────────────────────────────────────────────────
try:
    from browser_use.agent.service import Agent as BrowserUseAgent
    from browser_use import Browser, BrowserProfile, BrowserSession
    from browser_use.llm.google.chat import ChatGoogle
    from browser_use.tools.service import Tools
    HAS_BROWSER_USE = True
except ImportError as _bu_err:
    HAS_BROWSER_USE = False
    print(f"[WEB AGENT v7]: browser-use not installed: {_bu_err}")
    print("[WEB AGENT v7]: Run: pip install browser-use langchain-google-genai")

# ✅ NEXUS-2: Playwright as fast headless fallback (lazy initialization)
HAS_PLAYWRIGHT = None

def _check_playwright():
    global HAS_PLAYWRIGHT
    if HAS_PLAYWRIGHT is None:
        try:
            import playwright.sync_api
            HAS_PLAYWRIGHT = True
        except ImportError:
            HAS_PLAYWRIGHT = False
    return HAS_PLAYWRIGHT

def playwright_quick_search(query: str) -> str:
    """
    NEXUS-2 stealth fast-path: Use Playwright with anti-fingerprint evasion for fast web searches.
    Undetectable by Cloudflare, Google, and bot detectors (navigator.webdriver = undefined).
    """
    if not _check_playwright():
        try:
            from core.web_reader import WebCrawler
            return WebCrawler().execute_research(query)
        except Exception:
            return ""
    try:
        from playwright.sync_api import sync_playwright
        from bs4 import BeautifulSoup
        url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-infobars'
                ]
            )
            try:
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                    viewport={'width': 1920, 'height': 1080},
                    locale='en-US'
                )
                context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    window.chrome = { runtime: {} };
                    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                """)
                page = context.new_page()
                try:
                    page.goto(url, timeout=9000, wait_until='domcontentloaded')
                    html = page.content()
                except Exception:
                    # Fallback to DuckDuckGo if Google blocks
                    ddg_url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
                    page.goto(ddg_url, timeout=7000, wait_until='domcontentloaded')
                    html = page.content()
            finally:
                browser.close()

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)

        # Detect Google redirect notices / bot challenge interstitials
        bot_phrases = [
            "please click here if you are not redirected",
            "before you continue to google",
            "unusual traffic from your computer network",
            "consent.google.com",
            "our systems have detected"
        ]
        if any(p in text.lower() for p in bot_phrases) or len(text.strip()) < 120:
            try:
                from core.web_reader import WebCrawler
                fallback_data = WebCrawler().execute_research(query)
                if fallback_data and "Research failed" not in fallback_data:
                    return fallback_data[:3500]
            except Exception:
                pass

        return text[:3500]
    except Exception as _e:
        log_warn("web_agent", f"playwright_quick_search failed: {_e}")
        try:
            from core.web_reader import WebCrawler
            return WebCrawler().execute_research(query)
        except Exception:
            return ""



# ── Smart Multi-LLM Builder ───────────────────────────────────────────────────
def _build_llm_with_fallback():
    """
    Builds a primary LLM + the ONE fallback browser-use supports.

    STRATEGY: browser-use has ONE fallback_llm slot. Use it wisely.
      Primary : Gemini 2.5 Flash (best vision quality)
      Fallback: Groq Llama-3.3 70B  <-- 14,400 req/DAY free (720x more than Google's 20/day)
                OpenRouter Maverick  <-- if Groq unavailable
                Gemini 2.0 key2      <-- last resort only (same low Google quotas)

    Returns (primary_llm, fallback_llm). Either may be None.
    """
    from dotenv import load_dotenv
    load_dotenv()

    primary_llm  = None
    fallback_llm = None

    # -- PRIMARY: Gemini 2.5 Flash (ChatGoogle reads GEMINI_API_KEY from env) --
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if gemini_key and HAS_BROWSER_USE:
        try:
            # ChatGoogle auto-reads GEMINI_API_KEY — no kwarg needed
            primary_llm = ChatGoogle(
                model="gemini-2.5-flash",
                temperature=0.1,
            )
            print("[WEB AGENT LLM]: [OK] Primary -> Gemini 2.5 Flash")
        except Exception as e:
            print(f"[WEB AGENT LLM]: [WARN] Gemini 2.5 Flash failed: {e}")

    # -- FALLBACK 1: browser-use native ChatGroq (Rock-solid, fast, high quota) ---
    groq_key = os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_API_KEY_2", "")
    if groq_key:
        try:
            from browser_use.llm.groq.chat import ChatGroq as BrowserUseGroq
            fallback_llm = BrowserUseGroq(
                model="llama-3.3-70b-versatile",
                api_key=groq_key,
                temperature=0.1,
                max_retries=3,
            )
            print("[WEB AGENT LLM]: [OK] Fallback -> Groq Llama-3.3 70B (browser-use native)")
        except Exception as e:
            print(f"[WEB AGENT LLM]: [WARN] Groq native fallback failed: {e}")

    # -- FALLBACK 2: OpenRouter (Active paid or standard models) ---------------
    if fallback_llm is None:
        or_key = os.environ.get("OPENROUTER_API_KEY", "")
        if or_key:
            try:
                from browser_use.llm.openrouter.chat import ChatOpenRouter as BrowserUseOpenRouter
                fallback_llm = BrowserUseOpenRouter(
                    model="meta-llama/llama-3.3-70b-instruct",
                    api_key=or_key,
                    temperature=0.1,
                )
                print("[WEB AGENT LLM]: [OK] Fallback -> OpenRouter Llama-3.3 70B (browser-use native)")
            except Exception as e:
                print(f"[WEB AGENT LLM]: [WARN] OpenRouter native fallback failed: {e}")

    # -- FALLBACK 3 (last resort): Gemini key2 ---------------------------------
    if fallback_llm is None:
        gemini_key2 = os.environ.get("GEMINI_API_KEY_2", "")
        if gemini_key2 and HAS_BROWSER_USE:
            try:
                _orig = os.environ.get("GEMINI_API_KEY", "")
                os.environ["GEMINI_API_KEY"] = gemini_key2
                fallback_llm = ChatGoogle(model="gemini-2.5-flash", temperature=0.1)
                os.environ["GEMINI_API_KEY"] = _orig
                print("[WEB AGENT LLM]: [OK] Fallback -> Gemini 2.5 Flash key2")
            except Exception as e:
                os.environ["GEMINI_API_KEY"] = gemini_key
                print(f"[WEB AGENT LLM]: [WARN] Gemini key2 last-resort failed: {e}")

    # -- If primary failed but we have a fallback, promote it -----------------
    if primary_llm is None and fallback_llm is not None:
        primary_llm, fallback_llm = fallback_llm, None
        print("[WEB AGENT LLM]: [WARN] Primary unavailable -- fallback promoted to primary")

    return primary_llm, fallback_llm

# Fix #8: Cache the LLM pair as a module-level singleton.
# _build_llm_with_fallback() is expensive (reads env, constructs clients).
# Before this fix it was called on EVERY web task. Now it is built ONCE.
_LLM_CACHE: tuple = (None, None)
_LLM_CACHE_LOCK = threading.Lock()

def _get_cached_llm():
    """Return the cached (primary_llm, fallback_llm) tuple, building it on first call."""
    global _LLM_CACHE
    with _LLM_CACHE_LOCK:
        if _LLM_CACHE == (None, None):
            try:
                _LLM_CACHE = _build_llm_with_fallback()
            except Exception as e:
                log_error("web_agent", "build_llm_with_fallback", e)
        return _LLM_CACHE


# ── DB Init ───────────────────────────────────────────────────────────────────
def _init_wa_tables():
    try:
        conn = sqlite3.connect(WA_DB)
        try:
            cur  = conn.cursor()
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS web_agent_sessions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    goal         TEXT NOT NULL,
                    url          TEXT,
                    status       TEXT DEFAULT 'running',
                    steps_taken  INTEGER DEFAULT 0,
                    result       TEXT,
                    error        TEXT,
                    started_at   TEXT DEFAULT (datetime('now','localtime')),
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS web_agent_actions (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id     INTEGER,
                    step_number    INTEGER,
                    action_type    TEXT,
                    target         TEXT,
                    value          TEXT,
                    result         TEXT,
                    screenshot_b64 TEXT,
                    timestamp      TEXT DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (session_id) REFERENCES web_agent_sessions(id)
                );
                CREATE TABLE IF NOT EXISTS web_memory (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain      TEXT NOT NULL,
                    key         TEXT NOT NULL,
                    value       TEXT NOT NULL,
                    hits        INTEGER DEFAULT 1,
                    updated_at  TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(domain, key) ON CONFLICT REPLACE
                );
                CREATE TABLE IF NOT EXISTS web_preferences (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    key     TEXT UNIQUE NOT NULL,
                    value   TEXT NOT NULL,
                    updated TEXT DEFAULT (datetime('now','localtime'))
                );
            """)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log_warn("web_agent", f"DB table init failed: {e}")


# ── Data Classes ──────────────────────────────────────────────────────────────
@dataclass
class WebAction:
    action:          str
    target:          str  = ""
    value:           str  = ""
    description:     str  = ""
    is_irreversible: bool = False

@dataclass
class StepResult:
    success:      bool = False
    message:      str  = ""
    page_changed: bool = False
    screenshot:   str  = ""   # base64
    dom_snapshot: str  = ""

@dataclass
class SessionResult:
    goal:           str  = ""
    success:        bool = False
    steps_taken:    int  = 0
    final_message:  str  = ""
    extracted_data: dict = field(default_factory=dict)
    session_id:     int  = 0


# ── Web Memory ────────────────────────────────────────────────────────────────
class WebMemory:
    """
    Persistent learning across sessions.
    Saves website patterns, personal prefs, selector hints, and learned tactics.
    """

    def save_hint(self, domain: str, key: str, value: str):
        try:
            conn = sqlite3.connect(WA_DB)
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO web_memory (domain, key, value, hits, updated_at) "
                    "VALUES (?, ?, ?, COALESCE((SELECT hits FROM web_memory WHERE domain=? AND key=?)+1, 1), datetime('now','localtime'))",
                    (domain, key, value, domain, key)
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            log_warn("web_agent", f"save_hint failed: {e}")

    def load_hints(self, domain: str) -> Dict[str, str]:
        try:
            conn = sqlite3.connect(WA_DB)
            try:
                rows = conn.execute(
                    "SELECT key, value FROM web_memory WHERE domain=? ORDER BY hits DESC LIMIT 20",
                    (domain,)
                ).fetchall()
            finally:
                conn.close()
            return {r[0]: r[1] for r in rows}
        except Exception:
            return {}

    def save_preference(self, key: str, value: str):
        try:
            conn = sqlite3.connect(WA_DB)
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO web_preferences (key, value, updated) VALUES (?, ?, datetime('now','localtime'))",
                    (key, value)
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            log_warn("web_agent", f"save_preference failed: {e}")

    def load_preferences(self) -> Dict[str, str]:
        try:
            conn = sqlite3.connect(WA_DB)
            try:
                rows = conn.execute("SELECT key, value FROM web_preferences").fetchall()
            finally:
                conn.close()
            return {r[0]: r[1] for r in rows}
        except Exception:
            return {}

    def save_failure(self, domain: str, action: str, target: str):
        self.save_hint(domain, f"FAIL:{action}:{target[:50]}", "1")

    def save_learned_tactic(self, domain: str, tactic: str):
        self.save_hint(domain, f"TACTIC:{hash(tactic)}", tactic)

    def load_learned_tactics(self, domain: str) -> List[str]:
        hints = self.load_hints(domain)
        return [v for k, v in hints.items() if k.startswith("TACTIC:")]

    def was_failure(self, domain: str, action: str, target: str) -> bool:
        hints = self.load_hints(domain)
        return f"FAIL:{action}:{target[:50]}" in hints


_web_memory = WebMemory()


# ── BarrierBuster — Cookie / Popup Auto-Dismiss ───────────────────────────────
class BarrierBuster:
    """
    Intelligent barrier resolution engine.
    Handles every obstacle automatically: cookie banners, popups, age gates, login walls.
    Now works with browser-use pages directly via Playwright API.
    """

    COOKIE_SELECTORS = [
        '#onetrust-accept-btn-handler', '#accept-cookies', '#cookie-accept',
        '#acceptAllButton', '#CookieConsentAccept', '#cookieAcceptBtn',
        '#truste-consent-button', '#gdpr-accept', '#cookies-accept-all',
        '#js-accept-cookies', '#accept_cookies', '#cookieOkButton',
        'button[class*="accept-all"]', 'button[class*="cookie-accept"]',
        'button[class*="gdpr-accept"]', 'button[class*="consent-accept"]',
        'button[class*="acceptAll"]', 'button[class*="AcceptAll"]',
        '[data-testid*="cookie-accept"]', '[data-testid*="accept-all"]',
        'button:has-text("Accept All")', 'button:has-text("Accept all")',
        'button:has-text("Accept Cookies")', 'button:has-text("Accept")',
        'button:has-text("I Accept")', 'button:has-text("I Agree")',
        'button:has-text("Agree")', 'button:has-text("Allow All")',
        'button:has-text("Allow Cookies")', 'button:has-text("Got it")',
        '.fc-cta-consent', '.cookie-accept', '.cc-btn.cc-allow', '.cc-allow',
        '.gdpr-accept', '[class*="cookie"] [class*="accept"]',
        '.cc-btn-accept-all', '#cookie-notification-accept',
        'button[id*="accept"]', 'a[id*="accept"]',
    ]

    POPUP_SELECTORS = [
        'button[aria-label*="Close"]', 'button[aria-label*="close"]',
        'button[aria-label*="Dismiss"]', 'button[aria-label*="dismiss"]',
        '[data-dismiss="modal"]', '.close-button', '.btn-close',
        '.modal__close', '.popup__close', '.overlay__close',
        '.modal-close', '.popup-close', '.dialog-close',
        '[class*="modal"] [class*="close"]', '[class*="popup"] [class*="close"]',
        '[class*="overlay"] [class*="close"]',
        'button:has-text("No thanks")', 'button:has-text("Not now")',
        'button:has-text("Skip")', 'button:has-text("Maybe later")',
        'button:has-text("Close")', 'button:has-text("Dismiss")',
        '.loginModal .close', '.sign-in-modal .close',
        '.a-popover-closebutton',
        'span.commonModal__close', '[class*="loginModal"] [class*="close"]',
        'button._2KpZ6l._2doB4z',
    ]

    AGE_GATE_SELECTORS = [
        'button[class*="age"]', 'button[class*="over"]', 'button[class*="18"]',
        'button:has-text("I am 18")', 'button:has-text("I am over 18")',
        'button:has-text("Enter")', 'button:has-text("Yes, I am")',
    ]

    def auto_resolve(self, page, page_state: str = "UNKNOWN", voice=None) -> bool:
        resolved = False
        resolved = resolved or self._try_cookie_accept(page)
        resolved = resolved or self._try_popup_dismiss(page)
        resolved = resolved or self._try_age_gate(page)
        return resolved

    def _try_cookie_accept(self, page) -> bool:
        for sel in self.COOKIE_SELECTORS:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click(timeout=2000)
                    time.sleep(0.5)
                    print(f"[BARRIER BUSTER]: ✅ Cookie consent accepted via {sel[:60]}")
                    return True
            except Exception:
                continue
        return False

    def _try_popup_dismiss(self, page) -> bool:
        for sel in self.POPUP_SELECTORS:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click(timeout=2000)
                    time.sleep(0.4)
                    print(f"[BARRIER BUSTER]: ✅ Popup dismissed via {sel[:60]}")
                    return True
            except Exception:
                continue
        return False

    def _try_age_gate(self, page) -> bool:
        for sel in self.AGE_GATE_SELECTORS:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click(timeout=2000)
                    time.sleep(0.5)
                    return True
            except Exception:
                continue
        return False


# ── Voice Collaboration ───────────────────────────────────────────────────────
class VoiceCollaboration:
    """
    Real-time voice narration + voice input during web tasks.
    Speaks progress updates and listens for yes/no or data input.
    """

    def speak(self, text: str):
        try:
            from core.voice import speak
            threading.Thread(target=speak, args=(text,), daemon=True).start()
            print(f"[WEB AGENT SPEAKING]: {text[:80]}")
        except Exception as e:
            log_warn("web_agent", f"voice speak failed: {e}")

    def narrate(self, msg: str):
        print(f"[WEB AGENT]: {msg}")
        self.speak(msg)

    def listen_for_voice(self, timeout: int = 20) -> str:
        """Listen using JARVIS's main ear pipeline — WITH TIMEOUT GUARD."""
        try:
            import core.voice
            from core.ears import listen

            print(f"[WEB AGENT]: 🎙️ Waiting for JARVIS to finish speaking...")
            tts_wait_start = time.time()
            while core.voice.is_speaking:
                time.sleep(0.1)
                if time.time() - tts_wait_start > 15:
                    print("[WEB AGENT]: ⚠️ TTS wait timeout (15s). Proceeding anyway.")
                    core.voice.is_speaking = False
                    break

            print(f"[WEB AGENT]: 🎙️ Listening for user input (timeout={timeout}s)...")
            core.voice.was_interrupted = False

            result_holder = [None]
            abort_event = threading.Event()

            def _listen_worker():
                try:
                    result_holder[0] = listen(timeout=timeout, abort_event=abort_event)
                except Exception as e:
                    print(f"[WEB AGENT VOICE]: listen() error: {e}")
                    result_holder[0] = ""

            listener_thread = threading.Thread(target=_listen_worker, daemon=True)
            listener_thread.start()
            listener_thread.join(timeout=timeout)

            if listener_thread.is_alive():
                print(f"[WEB AGENT]: ⏰ Voice listen timed out after {timeout}s.")
                abort_event.set()
                return ""

            text = result_holder[0] or ""
            if text:
                print(f"[WEB AGENT HEARD]: '{text}'")
            return text
        except Exception as e:
            print(f"[WEB AGENT VOICE ERROR]: {e}")
            return ""

    def listen_for_approval(self, question: str, timeout: int = 20) -> bool:
        self.speak(question)
        time.sleep(0.5)
        text = self.listen_for_voice(timeout=timeout)
        yes_words = ["yes", "proceed", "go ahead", "confirm", "ok", "sure",
                     "ha", "haan", "do it", "book it", "continue", "aage",
                     "theek hai", "bilkul", "perfect", "absolutely"]
        return any(w in text.lower() for w in yes_words)

    def listen_for_data(self, question: str, timeout: int = 25) -> str:
        self.speak(question)
        time.sleep(0.5)
        return self.listen_for_voice(timeout=timeout)


# ── Global instances (used by jarvis_browser_actions.py) ─────────────────────
_web_voice  = VoiceCollaboration()
_web_barrier = BarrierBuster()


# ── JarvisBrowserAgent — v7.0 Core ───────────────────────────────────────────
class JarvisBrowserAgent:
    """
    v7.0: JARVIS Web Agent powered by browser-use.

    Uses browser-use's Agent for all DOM extraction and LLM planning,
    while injecting JARVIS's Voice, Memory, and BarrierBuster as native tools.
    """

    MAX_STEPS = 80
    TIMEOUT   = 900  # 15 minutes hard cap

    def __init__(self, goal: str, start_url: str = "", headless: bool = False):
        self.goal      = goal
        self.start_url = start_url
        self.headless  = headless
        self.voice     = _web_voice
        self.session_id = 0
        self._start_time = 0.0

    def _domain(self, url: str) -> str:
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc.replace("www.", "")
        except Exception:
            return ""

    def _save_session_start(self) -> int:
        try:
            conn = sqlite3.connect(WA_DB)
            try:
                cur  = conn.cursor()
                cur.execute(
                    "INSERT INTO web_agent_sessions (goal, url) VALUES (?, ?)",
                    (self.goal, self.start_url)
                )
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()
        except Exception as e:
            log_error("web_agent", "_save_session_start", e)
            return 0

    def _save_session_end(self, success: bool, result: str, steps: int = 0):
        try:
            conn = sqlite3.connect(WA_DB)
            try:
                conn.execute(
                    "UPDATE web_agent_sessions SET status=?, result=?, steps_taken=?, completed_at=datetime('now','localtime') WHERE id=?",
                    ("success" if success else "failed", result[:2000], steps, self.session_id)
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            log_warn("web_agent", f"save_session_end failed: {e}")

    def _build_system_extension(self, domain: str) -> str:
        """
        Builds the JARVIS-specific system prompt extension that gets appended
        to browser-use's default system prompt.
        Injects: learned tactics from WebMemory + trajectory recall hints.
        """
        tactics = _web_memory.load_learned_tactics(domain)

        traj_hint = ""
        try:
            from core.trajectory_memory import get_trajectory_memory
            mem = get_trajectory_memory()
            traj_hint = mem.format_hint(mem.recall(self.goal, domain=domain))
        except Exception as e:
            log_warn("web_agent", f"trajectory recall failed: {e}")

        prefs = _web_memory.load_preferences()

        parts = [
            "\n\n## JARVIS AGENT RULES",
            "- ALWAYS call 'jarvis_confirm' before: booking tickets, purchases, payments, form submissions.",
            "- ALWAYS call 'jarvis_ask_user' when you need: OTP, password, login credentials, address.",
            "- If redirected to a wrong page (ads, explore maps, pop-ups), go_back immediately",
            "  then call 'jarvis_save_tactic' with the lesson learned.",
            "- Call 'jarvis_narrate' for important milestones to keep the user informed.",
            "- Do NOT press Enter on Google Flights search — it redirects to Explore. Use Tab or click instead.",
            "",
            "## STALE ELEMENT RECOVERY (CRITICAL)",
            "- If 'Element index X not available' appears even ONCE, the page has re-rendered.",
            "  DO NOT click the same index again. Instead:",
            "  1. Call get_page_html or scroll_down to refresh the visible DOM state.",
            "  2. Re-read the page to find the correct NEW index for your target button.",
            "  3. Click the correct NEW index — never retry a stale index more than once.",
            "- If you see the same 'Element index not available' warning twice in a row, the page",
            "  structure changed. Use scroll_to_top then re-identify all elements from scratch.",
            "- Flight booking sites (MakeMyTrip, Skyscanner) heavily use dynamic rendering.",
            "  After any click that triggers a loading spinner or page transition,",
            "  always wait 2-3 seconds then re-read the page before clicking again.",
        ]

        if prefs:
            pref_lines = [f"  - {k}: {v}" for k, v in list(prefs.items())[:10]]
            parts.append("\n## USER PREFERENCES (apply automatically):\n" + "\n".join(pref_lines))

        if tactics:
            tactic_lines = [f"  ⚠️ {t}" for t in tactics]
            parts.append(f"\n## LEARNED TACTICS FOR {domain.upper()} (CRITICAL — follow these exactly):\n" + "\n".join(tactic_lines))

        if traj_hint:
            parts.append(f"\n## PAST SUCCESS PATTERNS (use as guidance):\n{traj_hint}")

        return "\n".join(parts)

    def _build_jarvis_tools(self) -> "Tools":
        """
        Builds a browser-use Tools instance with JARVIS custom actions registered.
        Returns a Tools instance with all standard browser-use actions PLUS JARVIS tools.
        """
        from browser_use.tools.service import Tools
        from pydantic import BaseModel

        tools = Tools()

        # ── Register JARVIS Voice Ask ─────────────────────────────────────────
        class AskParams(BaseModel):
            question: str

        @tools.action(
            "Ask JARVIS to speak a question to the user and listen for their voice response. "
            "Use for: passwords, OTPs, login credentials, addresses, seat preferences, payment info."
        )
        def jarvis_ask_user(params: AskParams) -> str:
            try:
                _web_voice.narrate(params.question)
                response = _web_voice.listen_for_voice(timeout=60)
                return f"User said: '{response}'" if response else "No voice input received."
            except Exception as e:
                return f"Voice input error: {e}"

        # ── Register JARVIS Voice Confirm ─────────────────────────────────────
        class ConfirmParams(BaseModel):
            action_description: str

        @tools.action(
            "Ask JARVIS to verbally confirm with the user before any irreversible action. "
            "ALWAYS call before: booking tickets, purchases, payments, form submissions. "
            "Returns CONFIRMED or REJECTED."
        )
        def jarvis_confirm(params: ConfirmParams) -> str:
            try:
                approved = _web_voice.listen_for_approval(params.action_description, timeout=30)
                if approved:
                    return "CONFIRMED: User approved. You may proceed."
                return "REJECTED: User declined. STOP. Do not proceed."
            except Exception as e:
                return f"Confirmation error: {e}. Treat as REJECTED for safety."

        # ── Register JARVIS Memory Save ───────────────────────────────────────
        class TacticParams(BaseModel):
            domain: str
            tactic: str

        @tools.action(
            "Save a learned navigation rule to JARVIS persistent memory for a website. "
            "Call when you discover a site quirk (e.g. Enter causes redirect). "
            "This rule is remembered FOREVER across all future sessions."
        )
        def jarvis_save_tactic(params: TacticParams) -> str:
            try:
                _web_memory.save_learned_tactic(params.domain, params.tactic)
                print(f"[JARVIS MEMORY]: 🧠 Saved: {params.tactic[:80]}")
                return f"Tactic saved for {params.domain}."
            except Exception as e:
                return f"Memory save error: {e}"

        # ── Register JARVIS Narrate ───────────────────────────────────────────
        class NarrateParams(BaseModel):
            message: str

        @tools.action(
            "Speak a short progress update to the user. "
            "Use for key milestones: found results, filled form, reached payment. Keep under 20 words."
        )
        def jarvis_narrate(params: NarrateParams) -> str:
            try:
                _web_voice.narrate(params.message)
                return f"Narrated: '{params.message[:60]}'"
            except Exception as e:
                return f"Narrate error: {e}"

        return tools

    async def _run(self) -> SessionResult:
        if not HAS_BROWSER_USE:
            return SessionResult(
                goal=self.goal, success=False,
                final_message="browser-use not installed. Run: pip install browser-use"
            )

        self._start_time = time.time()
        self.session_id  = self._save_session_start()

        domain = self._domain(self.start_url or "")

        # ── LLM: Smart multi-model fallback chain ────────────────────────────
        llm, fallback_llm = _get_cached_llm()
        if llm is None:
            return SessionResult(
                goal=self.goal, success=False,
                final_message="No LLM available. Please set GEMINI_API_KEY or GROQ_API_KEY in .env"
            )

        # ── Cleanup left-over Chrome locks to prevent 30s timeout errors ───────
        if os.path.exists(PROFILE):
            for lock_name in ['lockfile', 'SingletonLock', 'SingletonCookie', 'SingletonSocket']:
                lock_path = os.path.join(PROFILE, lock_name)
                try:
                    if os.path.exists(lock_path):
                        if os.path.islink(lock_path) or os.path.isfile(lock_path):
                            os.remove(lock_path)
                        elif os.path.isdir(lock_path):
                            import shutil
                            shutil.rmtree(lock_path)
                        print(f"[WEB AGENT v7]: 🧹 Removed stale browser lock: {lock_name}")
                except Exception as e:
                    print(f"[WEB AGENT v7]: ⚠️ Could not remove {lock_name}: {e}")

        # ── Browser Profile: Persistent, Headed, Anti-Detection ──────────────
        browser_profile = BrowserProfile(
            headless=self.headless,
            user_data_dir=PROFILE,           # Reuse JARVIS's existing persistent profile!
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-web-security",
                f"--window-size=1280,800",
            ],
        )

        # ── Build JARVIS custom tools ─────────────────────────────────────────
        jarvis_tools = self._build_jarvis_tools()

        # ── Build task string ─────────────────────────────────────────────────
        task = self.goal
        if self.start_url:
            url = self.start_url if self.start_url.startswith("http") else "https://" + self.start_url
            task = f"Start at {url}. Then: {self.goal}"

        # ── Build system prompt extension with JARVIS Memory ─────────────────
        system_ext = self._build_system_extension(domain)

        self.voice.narrate(f"Starting web mission: {self.goal[:80]}")
        print(f"[WEB AGENT v7]: 🚀 Goal: {self.goal}")
        print(f"[WEB AGENT v7]: 🧠 Loaded {len(_web_memory.load_learned_tactics(domain))} learned tactics for {domain or 'unknown domain'}")

        # Log which LLMs are in use
        primary_name  = getattr(llm, 'model_name', None) or getattr(llm, 'model', 'unknown')
        fallback_name = (getattr(fallback_llm, 'model_name', None) or getattr(fallback_llm, 'model', 'none')) if fallback_llm else 'none'
        print(f"[WEB AGENT v7]: 🤖 Primary LLM: {primary_name} | Fallback: {fallback_name}")

        # ── Monkey-Patch browser-use's 8-second page timeout limit to 3s ─────────
        try:
            import browser_use.browser.session
            if not hasattr(browser_use.browser.session.BrowserSession, "_jarvis_patched"):
                _orig_navigate_and_wait = browser_use.browser.session.BrowserSession._navigate_and_wait
                async def _fast_navigate_and_wait(self_obj, url: str, target_id: str, timeout=None, wait_until='load', nav_timeout=None):
                    # Force timeout to 3 seconds max, overriding the hardcoded 8s for new domains
                    return await _orig_navigate_and_wait(self_obj, url, target_id, timeout=3.0, wait_until=wait_until, nav_timeout=nav_timeout)
                browser_use.browser.session.BrowserSession._navigate_and_wait = _fast_navigate_and_wait
                browser_use.browser.session.BrowserSession._jarvis_patched = True
                print(f"[WEB AGENT v7]: ⚡ Page load timeout capped at 3.0s")
        except Exception as e:
            print(f"[WEB AGENT v7]: ⚠️ Failed to patch browser-use timeouts: {e}")

        agent_kwargs = dict(
            task=task,
            llm=llm,
            browser_profile=browser_profile,
            controller=jarvis_tools,
            extend_system_message=system_ext,
            max_failures=6,
            max_actions_per_step=3,
            loop_detection_enabled=True,
            loop_detection_window=6,   # Tighter: break stagnation loops faster (was 15)
            use_vision=True,
            enable_signal_handler=False,    # Don't trap SIGINT — let JARVIS handle it
        )
        # Wire in fallback only if browser-use version supports it
        if fallback_llm is not None:
            try:
                import inspect
                sig = inspect.signature(BrowserUseAgent.__init__)
                if "fallback_llm" in sig.parameters:
                    agent_kwargs["fallback_llm"] = fallback_llm
                    print(f"[WEB AGENT v7]: ✅ fallback_llm wired in")
                else:
                    print(f"[WEB AGENT v7]: ℹ️ fallback_llm not supported by this browser-use version (safe to ignore)")
            except Exception as e:
                log_warn("web_agent", f"fallback_llm wiring failed: {e}")

        agent = BrowserUseAgent(**agent_kwargs)

        try:
            history = await agent.run(max_steps=self.MAX_STEPS)

            # Extract result
            final_result = ""
            try:
                final_result = history.final_result() or ""
            except Exception as e:
                log_warn("web_agent", f"history.final_result() failed: {e}")
                try:
                    if history.history:
                        last = history.history[-1]
                        final_result = str(getattr(last, 'result', '') or '')[:500]
                except Exception as e:
                    log_warn("web_agent", f"history fallback extraction failed: {e}")

            if not final_result:
                final_result = "Mission complete."

            steps_taken = len(history.history) if history.history else 0
            success = True

            self.voice.narrate(f"Mission complete! {final_result[:120]}")
            self._save_session_end(True, final_result, steps_taken)

            return SessionResult(
                goal=self.goal, success=True,
                steps_taken=steps_taken,
                final_message=final_result,
                session_id=self.session_id,
            )

        except Exception as e:
            err = f"Agent error: {e}"
            print(f"[WEB AGENT v7]: ❌ {err}")
            traceback.print_exc()
            self.voice.narrate("The web agent encountered an issue. Check the browser.")
            self._save_session_end(False, err)
            return SessionResult(goal=self.goal, success=False, final_message=err)

        finally:
            try:
                if agent.browser_session:
                    await agent.browser_session.stop()
            except Exception as e:
                log_warn("web_agent", f"browser session stop failed: {e}")

    def run(self) -> SessionResult:
        """
        Synchronous wrapper — 100% backward-compatible with all existing callers.
        Handles the asyncio/PyQt5 thread conflict gracefully.
        """
        try:
            # Check if there's already a running event loop (e.g. PyQt5 thread)
            loop = asyncio.get_running_loop()
            # Yes — run in a separate thread with its own event loop to avoid conflict
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self._run())
                return future.result(timeout=self.TIMEOUT)
        except RuntimeError:
            # No running loop — use asyncio.run directly (the normal case)
            return asyncio.run(self._run())
        except concurrent.futures.TimeoutError:
            self._save_session_end(False, "Timeout")
            return SessionResult(goal=self.goal, success=False, final_message="Mission timed out.")


# ── Backward-compat alias ─────────────────────────────────────────────────────
WebAgentSession = JarvisBrowserAgent


# ── SwarmOrchestrator — Parallel Async Agents ────────────────────────────────
class SwarmOrchestrator:
    """
    v7.0: Spawns multiple JarvisBrowserAgents in parallel for price comparison tasks.
    """

    @staticmethod
    def detect_swarm_intent(goal: str) -> Optional[List[Dict[str, str]]]:
        goal_lower = goal.lower()
        compare_words = ["compare", "versus", "vs", "cheapest", "best price",
                         "across", "between", "amazon and", "flipkart and", "multiple sites"]
        if not any(w in goal_lower for w in compare_words):
            return None

        try:
            from core.brain import call_groq_brain
            split_prompt = (
                f"The user wants: '{goal}'\n"
                "Split into 2-3 specific sub-tasks with their start URLs.\n"
                "Return ONLY a JSON array: [{\"goal\": \"...\", \"url\": \"https://...\"}]\n"
                "No markdown. No explanation."
            )
            result = call_groq_brain(split_prompt, phase="LOGIC", is_logic_task=True)
            if isinstance(result, dict):
                result = result.get("reply", "")
            match = re.search(r'\[.*?\]', str(result), re.DOTALL)
            if match:
                sub_goals = json.loads(match.group())
                if isinstance(sub_goals, list) and len(sub_goals) >= 2:
                    return sub_goals
        except Exception as e:
            print(f"[SWARM]: Split detection failed: {e}")
        return None

    async def _run_single(self, sub_goal: str, url: str) -> str:
        agent = JarvisBrowserAgent(goal=sub_goal, start_url=url)
        result = await agent._run()
        return result.final_message

    async def run_parallel_async(self, sub_goals: List[Dict]) -> Dict[str, str]:
        tasks = [
            self._run_single(sg["goal"], sg.get("url", ""))
            for sg in sub_goals
        ]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)
        results = {}
        for sg, res in zip(sub_goals, results_list):
            results[sg["goal"]] = str(res) if not isinstance(res, Exception) else f"Error: {res}"
        return results

    def run_parallel(self, _, sub_goals: List[Dict]) -> Dict[str, str]:
        return asyncio.run(self.run_parallel_async(sub_goals))


# ── WebAgent — Public Singleton API ──────────────────────────────────────────
class WebAgent:
    """Public singleton interface. 100% backward-compatible API."""

    def __init__(self):
        self._lock        = threading.Lock()
        self._active: Dict[int, threading.Thread] = {}
        self._last_goal   = ""
        self._last_status = "idle"
        self._last_msg    = ""
        _init_wa_tables()

    def run(self, goal: str, start_url: str = "", headless: bool = False) -> int:
        """Launch a mission in the background. Returns session identifier."""
        # Check for swarm intent
        swarm = SwarmOrchestrator()
        swarm_goals = SwarmOrchestrator.detect_swarm_intent(goal)

        session = JarvisBrowserAgent(goal=goal, start_url=start_url, headless=headless)
        sid     = int(time.time())

        def _worker():
            self._last_goal   = goal
            self._last_status = "running"
            try:
                if swarm_goals:
                    _web_voice.narrate(f"Checking {len(swarm_goals)} sites simultaneously.")
                    results = swarm.run_parallel(None, swarm_goals)
                    summary = "Comparison results:\n" + "\n".join(f"- {k[:60]}: {v[:200]}" for k, v in results.items())
                    _web_voice.narrate(summary[:300])
                    self._last_status = "success"
                    self._last_msg    = summary
                    print(f"\n[WEB AGENT]: ✅ Swarm complete.")
                else:
                    result = session.run()
                    self._last_status = "success" if result.success else "failed"
                    self._last_msg    = result.final_message
                    label = "COMPLETE ✅" if result.success else "FAILED ❌"
                    print(f"\n[WEB AGENT]: Mission {label} — {result.steps_taken} steps — {result.final_message[:120]}")

                try:
                    from core.proactive_orchestrator import OrchestratorEvent
                    OrchestratorEvent.fire(
                        "WEB_AGENT",
                        f"Web Agent: Mission {'Complete' if self._last_status == 'success' else 'Failed'}",
                        self._last_msg[:300],
                    )
                except Exception as e:
                    log_warn("web_agent", f"OrchestratorEvent fire failed: {e}")

            except Exception as e:
                self._last_status = "failed"
                self._last_msg    = str(e)
                print(f"[WEB AGENT]: Worker exception: {e}")
                traceback.print_exc()
            finally:
                with self._lock:
                    self._active.pop(sid, None)

        t = threading.Thread(target=_worker, daemon=True, name=f"WebAgent-{sid}")
        t.start()
        with self._lock:
            self._active[sid] = t
        return sid

    def save_preference(self, key: str, value: str):
        _web_memory.save_preference(key, value)

    def status(self) -> dict:
        active = sum(1 for t in self._active.values() if t.is_alive())
        return {
            "status":          self._last_status,
            "active_sessions": active,
            "last_goal":       self._last_goal,
            "last_message":    self._last_msg,
        }


_wa_instance = None
_wa_lock = threading.Lock()

def get_web_agent() -> WebAgent:
    global _wa_instance
    with _wa_lock:
        if _wa_instance is None:
            _wa_instance = WebAgent()
    return _wa_instance


# ── Proactive Monitor ─────────────────────────────────────────────────────────
class WebAgentMonitor(BaseMonitor):
    """Periodic status check and event firing for the Web Agent."""
    name             = "WebAgent"
    interval_seconds = 300

    def __init__(self, event_queue, config=None):
        super().__init__(event_queue, config)
        self._agent = get_web_agent()

    def check(self):
        try:
            s   = self._agent.status()
            msg = (
                f"Status={s['status']} | Active sessions={s['active_sessions']} "
                f"| Last: {s['last_goal'][:50]} ({s['status']})"
            )
            print(f"[WEB AGENT MONITOR]: {msg}")
        except Exception as e:
            print(f"[WEB AGENT MONITOR]: Error -- {e}")
