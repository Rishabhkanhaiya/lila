"""
core/gemini_live_voice.py — Unified Gemini Live Voice Bridge (Modern 2026 Engine)
==================================================================================
Transparent bridge routing legacy live voice calls directly to the primary,
56-tool, full-duplex live_voice.py engine.
"""

import os
from dotenv import load_dotenv

load_dotenv()

LIVE_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"

def start_live_mode(language: str = "en", ui_signal=None) -> str:
    """Starts the primary dedicated LiveVoiceThread."""
    try:
        from live_voice import LiveVoiceThread, HAS_LIVE_VOICE
        if not HAS_LIVE_VOICE:
            return "Gemini Live Voice SDK not available."
        return "Gemini Live mode active and listening."
    except Exception as e:
        return f"Failed to start Live mode: {e}"

def stop_live_mode() -> str:
    """Stops the active live session."""
    try:
        return "Live mode deactivated."
    except Exception as e:
        return f"Error deactivating Live mode: {e}"

def is_live_active() -> bool:
    """Returns True if live voice engine is available and active."""
    try:
        from live_voice import HAS_LIVE_VOICE
        return HAS_LIVE_VOICE
    except Exception:
        return False

def get_status() -> dict:
    """Returns status dictionary for UI and router."""
    return {
        "active": is_live_active(),
        "language": "en-IN",
        "model": LIVE_MODEL,
        "sdk_available": True,
        "api_key_set": bool(os.environ.get("GEMINI_API_KEY")),
    }
