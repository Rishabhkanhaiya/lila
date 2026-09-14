"""
jarvis_logger.py — JARVIS Centralized Error Logging System
===========================================================
Replaces the 40+ silent `except: pass` blocks across the codebase.

Usage (drop-in replacement):
    from core.jarvis_logger import log, log_error, log_warn, log_info

    # Instead of: except Exception: pass
    except Exception as e:
        log_error("ears", "transcription", e)

    # Instead of: except Exception as e: print(f"error: {e}")
    except Exception as e:
        log_error("brain", "provider_call", e)

Levels:
    log_error(module, action, exc)  — ERROR level, always printed + stored
    log_warn(module, message)       — WARNING level, printed
    log_info(module, message)       — INFO level, printed only in debug mode
    log(module, message)            — Alias for log_info

All errors are:
    1. Printed to console (so you see them in the terminal)
    2. Stored in logs/jarvis_errors.log (for self_evolution.py to analyze)
    3. Timestamped and formatted consistently
"""

import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
_ROOT      = Path(__file__).parent.parent
_LOG_DIR   = _ROOT / "logs"
_LOG_FILE  = _LOG_DIR / "jarvis_errors.log"
_DEBUG     = os.environ.get("JARVIS_DEBUG", "0") == "1"

_log_lock  = threading.Lock()
_MAX_LOG_BYTES = 5 * 1024 * 1024   # 5 MB cap — rotate when exceeded


def _ensure_log_dir():
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass  # Can't create log dir — graceful degradation only here


def _rotate_if_needed():
    """Keep log file under 5 MB by truncating the oldest half."""
    try:
        if _LOG_FILE.exists() and _LOG_FILE.stat().st_size > _MAX_LOG_BYTES:
            with open(_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            # Keep the newest 50% of lines
            keep = lines[len(lines) // 2:]
            with open(_LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(["[LOG ROTATED]\n"] + keep)
    except Exception:
        pass


def _write_to_log(level: str, module: str, message: str, exc_info: str = ""):
    """Thread-safe write to log file."""
    _ensure_log_dir()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level}] [{module}] {message}"
    if exc_info:
        line += f"\n    {exc_info}"
    line += "\n"
    try:
        with _log_lock:
            _rotate_if_needed()
            with open(_LOG_FILE, "a", encoding="utf-8", errors="replace") as f:
                f.write(line)
    except Exception:
        pass  # Absolutely last resort — if we can't write logs, silently skip


# ── Public API ─────────────────────────────────────────────────────────────────

def log_error(module: str, action: str, exc: Exception, context: str = ""):
    """
    Log an exception with full traceback.
    Use this instead of `except Exception: pass` or `except Exception as e: print(e)`.

    Args:
        module:  Name of the module (e.g. "ears", "brain", "web_agent")
        action:  What was being attempted (e.g. "cloud_transcription", "groq_call")
        exc:     The caught exception
        context: Optional extra context string
    """
    exc_text = traceback.format_exc().strip()
    summary  = f"{type(exc).__name__}: {exc}"
    msg      = f"[ACTION: {action}] {summary}"
    if context:
        msg += f" | Context: {context[:200]}"

    # Always print — this is the key change vs the old `pass`
    try:
        print(f"\n[ERROR | {module.upper()}]: {action} → {summary}", flush=True)
        if _DEBUG and exc_text and exc_text != "NoneType: None":
            print(f"  Traceback:\n  {exc_text.replace(chr(10), chr(10) + '  ')}", flush=True)
    except Exception:
        pass

    # Write to file for self_evolution.py to analyze
    _write_to_log("ERROR", module, msg, exc_text if _DEBUG else "")


def log_warn(module: str, *args):
    """Log a non-fatal warning."""
    message = " ".join(str(a) for a in args) if args else ""
    try:
        print(f"[WARN | {module.upper()}]: {message}", flush=True)
    except Exception:
        pass
    _write_to_log("WARN", module, message)


def log_info(module: str, *args):
    """Log an info message (only printed in debug mode, always written to file)."""
    message = " ".join(str(a) for a in args) if args else ""
    if _DEBUG:
        try:
            print(f"[INFO | {module.upper()}]: {message}", flush=True)
        except Exception:
            pass
    _write_to_log("INFO", module, message)


# Alias
log = log_info
