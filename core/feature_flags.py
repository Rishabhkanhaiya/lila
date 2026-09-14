"""
feature_flags.py — JARVIS Feature Control Centre
══════════════════════════════════════════════════
Central on/off switches for every heavy or optional feature.
Settings are persisted in jarvis_features.json at the project root.

Usage:
    from core.feature_flags import is_enabled, set_flag, get_all

    if is_enabled("web_agent"):
        ...
"""

import json
import os
import threading

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FLAGS_FILE = os.path.join(_BASE, "jarvis_features.json")
_lock = threading.Lock()

# ── Default values ────────────────────────────────────────────────────────────
# True  = feature is ON  (enabled)
# False = feature is OFF (disabled) — saves API quota / CPU
DEFAULTS = {
    # ══ API-HEAVY FEATURES (consume your daily Gemini/Groq quota) ════════════
    "web_agent":            True,   # Searches web when asked — on-demand only
    "smart_yt_scraper":     True,   # intelligently matches YT channel names before clicking

    # ══ CPU-HEAVY FEATURES (local, no API but consume RAM/CPU) ══════════════
    "persistent_vision":    True,   # Screenshots every 8s + analysis
    "sixth_sense":          True,   # Combines vision + sound + emotion
    "dream_mode":           True,   # Nightly background consolidation

    # ══ NETWORK FEATURES ════════════════════════════════════════════════════
    "mesh_network":         True,   # WebSocket server for multi-device

    # ══ BACKGROUND AGI MONITORS ═════════════════════════════════════════════
    "self_evolution":       True,   # Monitors errors, suggests code fixes
    "proactive_agency":     True,   # All proactive notifications
    
    # ══ CONVERSATIONAL FEATURES ═════════════════════════════════════════════
    "casual_chat":          True,   # If False, disables LLM chatting entirely
}

_flags: dict = {}


def _load() -> dict:
    """Load flags from disk, filling missing keys with defaults."""
    try:
        if os.path.exists(_FLAGS_FILE):
            with open(_FLAGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # Merge: saved values override defaults, new defaults fill gaps
            merged = dict(DEFAULTS)
            merged.update({k: v for k, v in saved.items() if k in DEFAULTS})
            return merged
    except Exception:
        pass
    return dict(DEFAULTS)


import tempfile

def _save(flags: dict):
    """Persist flags to disk atomically."""
    try:
        content = json.dumps(flags, indent=2, ensure_ascii=False)
        dir_name = os.path.dirname(_FLAGS_FILE)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix="features_tmp_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, _FLAGS_FILE)
    except Exception as e:
        print(f"[⚠️ FEATURE FLAGS]: Could not save settings: {e}")


def _ensure_loaded():
    global _flags
    if not _flags:
        with _lock:
            if not _flags:
                _flags = _load()


# ── Public API ────────────────────────────────────────────────────────────────

def is_enabled(feature: str, default: bool = True) -> bool:
    """Returns True if the feature is turned ON."""
    _ensure_loaded()
    return _flags.get(feature, DEFAULTS.get(feature, default))


def set_flag(feature: str, value: bool):
    """Turn a feature ON (True) or OFF (False) and persist immediately."""
    _ensure_loaded()
    with _lock:
        _flags[feature] = value
        _save(_flags)
    try:
        print(f"[SETTINGS]: {feature} -> {'ON' if value else 'OFF'}")
    except Exception:
        pass


def get_all() -> dict:
    """Return a copy of all current flags."""
    _ensure_loaded()
    return dict(_flags)


def toggle(feature: str) -> bool:
    """Toggle a feature and return its new state."""
    current = is_enabled(feature)
    set_flag(feature, not current)
    return not current


get_flag = is_enabled
get_all_flags = get_all


# ── Auto-load on import ───────────────────────────────────────────────────────
_ensure_loaded()

# Save defaults on first run (creates jarvis_features.json)
if not os.path.exists(_FLAGS_FILE):
    _save(_flags)
    print(f"[⚙️ SETTINGS]: Created jarvis_features.json with default settings.")
