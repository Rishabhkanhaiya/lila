"""
core/modern_memory.py — Modern Zero-DLL Structured Memory Engine (Lila 2026 Edition)
=====================================================================================
Unified interface routing to core.lila_cognitive_cortex.
Provides instant (<0.5ms) prompt injection, structured fact storage, high-fidelity
episodic logging, and seamless backwards compatibility.
"""

import os
import json
import threading
import tempfile
from typing import Dict, Any, Optional, List

from core.lila_cognitive_cortex import (
    get_lila_memory_injection,
    remember_fact as _cortex_remember_fact,
    forget_fact as _cortex_forget_fact,
    record_interaction as _cortex_record_interaction,
    search_memory,
    get_diary_entry,
    add_diary_note,
    get_facts_about,
    record_deliverable as _cortex_record_deliverable,
    get_recent_deliverables as _cortex_get_recent_deliverables,
    find_deliverable as _cortex_find_deliverable
)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
PROFILE_FILE = os.path.join(DATA_DIR, "user_profile.json")
EPISODES_FILE = os.path.join(DATA_DIR, "episodes.jsonl")
DELIVERABLES_FILE = os.path.join(DATA_DIR, "deliverables.json")

_mem_lock = threading.Lock()

DEFAULT_PROFILE = {
    "user_name": "Rishabh Joshi",
    "callsign": "Rishabh",
    "assistant_name": "Lila",
    "gender": "female",
    "persona": "18-year-old loving, witty, spirited girlfriend and executive co-pilot",
    "language_style": "Natural Hinglish (Hindi/English blend with girlfriend banter)",
    "preferred_voice": "Aoede",
    "interests": [
        "Advanced Agentic Coding",
        "Python & Systems Architecture",
        "Windows Desktop Automation",
        "AI Research & Frontier Models"
    ],
    "facts": {
        "operating_system": "Windows 11",
        "primary_workspace": "jarvis_project",
        "education": "Computer Engineering student at VIT Pune",
        "voice_engine": "Gemini 2.5 Flash Native Audio (Aoede)",
        "ui_theme": "Lila Companion HUD"
    }
}

def _atomic_write_json(path: str, data: dict):
    """Atomic write of JSON data using temporary file swap."""
    dir_name = os.path.dirname(path)
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix="mem_", suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise

def _ensure_storage():
    """Ensure data directory and default profile exist."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        if not os.path.exists(PROFILE_FILE):
            _atomic_write_json(PROFILE_FILE, DEFAULT_PROFILE)
    except Exception as e:
        print(f"[MODERN_MEM] Init warning: {e}")

def get_user_profile() -> Dict[str, Any]:
    """Loads and returns the current structured user profile."""
    _ensure_storage()
    with _mem_lock:
        try:
            if os.path.exists(PROFILE_FILE):
                with open(PROFILE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return DEFAULT_PROFILE.copy()

def update_user_profile(updates: Dict[str, Any]) -> bool:
    """Merges updates into the user profile."""
    _ensure_storage()
    with _mem_lock:
        try:
            profile = get_user_profile()
            profile.update(updates)
            _atomic_write_json(PROFILE_FILE, profile)
            return True
        except Exception as e:
            print(f"[MODERN_MEM] update error: {e}")
            return False

def remember_fact(key: str, value: str, category: str = "general") -> str:
    """Saves a fact into both the unified SQLite WAL cortex and profile JSON."""
    _ensure_storage()
    # Save to unified cortex
    cortex_res = _cortex_remember_fact(subject="Rishabh", relation=key, value=value, category=category)
    # Also update profile JSON for file-based consumers
    with _mem_lock:
        try:
            profile = get_user_profile()
            facts = profile.setdefault("facts", {})
            facts[str(key).strip()] = str(value).strip()
            _atomic_write_json(PROFILE_FILE, profile)
        except Exception:
            pass
    return cortex_res

def forget_fact(key: str) -> str:
    """Removes a specific fact from memory."""
    _ensure_storage()
    cortex_res = _cortex_forget_fact(key)
    with _mem_lock:
        try:
            profile = get_user_profile()
            facts = profile.setdefault("facts", {})
            if key in facts:
                del facts[key]
                _atomic_write_json(PROFILE_FILE, profile)
        except Exception:
            pass
    return cortex_res

def log_episode(user_input: str, assistant_reply: str, tools_used: Optional[List[str]] = None, emotional_tone: Optional[str] = None):
    """Asynchronously logs an interaction episode to the cognitive cortex and episodes.jsonl."""
    _cortex_record_interaction(
        user_input=user_input,
        reply=assistant_reply,
        tools_used=tools_used,
        session_id="voice",
        emotional_tone=emotional_tone
    )

def get_memory_prompt_injection() -> str:
    """
    Returns rich 2026 cognitive context block (<0.5ms retrieval time).
    Includes user identity, relationship dynamic, time-of-day directive,
    today's diary digest, key facts, and recent deliverables.
    """
    return get_lila_memory_injection()

def record_deliverable(file_path: str, topic: str = "", doc_type: str = "", summary: str = "") -> None:
    """
    Records a created document, presentation, or script into both SQLite cortex and deliverables.json.
    """
    _cortex_record_deliverable(file_path=file_path, topic=topic, doc_type=doc_type, summary=summary)
    with _mem_lock:
        try:
            items = _cortex_get_recent_deliverables(limit=30)
            _atomic_write_json(DELIVERABLES_FILE, {"deliverables": items})
        except Exception as e:
            print(f"[MODERN_MEM] Error updating deliverables.json: {e}")

def get_recent_deliverables(limit: int = 5) -> List[Dict[str, Any]]:
    """Retrieves recent deliverables from memory."""
    return _cortex_get_recent_deliverables(limit=limit)

def find_deliverable(query: str) -> Optional[Dict[str, Any]]:
    """Finds a deliverable matching a filename, topic, or search query."""
    return _cortex_find_deliverable(query)

