"""
biometrics.py — JARVIS Biometric Engine v3.0 (Modern 2026 Engine)
==================================================================
Multi-signal emotion/state inference optimized for zero-footprint desktop execution.

Key 2026 Architectural Changes:
- NO background webcam polling loop (prevents unwanted camera LED flashes & quota burn).
- Webcam face analysis is strictly ON-DEMAND via `scan_user_face()`.
- Realtime acoustic emotion is natively handled by Gemini 2.5 Native Audio Affective Dialog.
- Passive signals (command speed, circadian rhythm) provide non-intrusive prompt context.

Public API:
  analyze_emotion()          → dict | None  (non-blocking cache read)
  get_mood_context_string()  → str  (ready for system prompt injection)
  record_command_speed(ms)   → None (feed command timing for urgency signal)
  scan_user_face()           → dict | None  (explicit on-demand webcam scan)
"""

import base64
import time
import tempfile
import os
import json
import re
import threading
from datetime import datetime

from core.jarvis_logger import log_error, log_info, log_warn

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL     = 120    # seconds between passive scans (low-overhead)
_URGENCY_WINDOW   = 60     # seconds to average command speed over

# ── Shared state ──────────────────────────────────────────────────────────────
_lock             = threading.Lock()
_cached_result    = {"mood": "neutral", "adjustment": "natural warm tone",
                     "confidence": 0.5, "signals": ["gemini_native_affective"]}
_command_times: list = []    # timestamps of recent commands (for urgency)
_has_cv2          = False

try:
    import cv2
    _has_cv2 = True
except ImportError:
    cv2 = None


# ── Explicit On-Demand Camera Scan ────────────────────────────────────────────

def scan_user_face() -> dict | None:
    """
    On-demand webcam face capture + modern Gemini Vision mood analysis.
    Triggered only when explicitly requested by user or tool.
    """
    if not _has_cv2 or not cv2:
        return None

    cap = None
    try:
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return None

        # Warmup frame then capture
        cap.read()
        ret, frame = cap.read()
        cap.release()
        cap = None

        if not ret or frame is None:
            return None

        import io
        from PIL import Image
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb_frame)
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=80)
        img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        prompt = (
            "You are JARVIS Astra's Biometric Emotion Analyzer.\n"
            "Analyze facial expression, posture, and micro-expressions concisely.\n"
            "Return ONLY valid JSON with keys:\n"
            "  'mood': primary emotion (happy/stressed/focused/sad/angry/tired/neutral)\n"
            "  'adjustment': how Astra should adapt tone/behavior\n"
            "  'confidence': float 0.0-1.0\n"
            "Example: {\"mood\": \"focused\", \"adjustment\": \"be concise and sharp\", \"confidence\": 0.85}"
        )

        from core.eyes import call_vision
        raw = call_vision(img_b64, prompt)
        if not raw:
            return None

        clean = re.sub(r"^```json\s*", "", raw, flags=re.IGNORECASE)
        clean = re.sub(r"```$", "", clean).strip()
        data  = json.loads(clean)
        data["signal_source"] = "webcam_face"
        
        with _lock:
            _cached_result.clear()
            _cached_result.update({
                "mood": data.get("mood", "neutral"),
                "adjustment": data.get("adjustment", "natural warm tone"),
                "confidence": float(data.get("confidence", 0.7)),
                "signals": ["webcam_face", "gemini_native_affective"],
                "timestamp": time.time()
            })
        return data
    except Exception as e:
        log_warn("biometrics", f"On-demand face scan error: {e}")
        return None
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


# ── Passive Signal Collectors (Zero-API cost) ──────────────────────────────────

def _signal_system_stress() -> dict:
    """Proxy stress from CPU load — high CPU + many processes = focused/intense session."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=None)
        if cpu > 85:
            return {"mood": "focused", "confidence": 0.45,
                    "adjustment": "be concise and responsive",
                    "signal_source": "system_high_load"}
    except Exception:
        pass
    return {"mood": "neutral", "confidence": 0.2,
            "adjustment": "normal tone", "signal_source": "system_normal"}


def _signal_time_of_day() -> dict:
    """Circadian rhythm heuristic — late night = gentler tone."""
    hour = datetime.now().hour
    if 0 <= hour < 5:
        return {"mood": "tired",   "confidence": 0.5,
                "adjustment": "softer tone, concise responses",
                "signal_source": "time_late_night"}
    if 5 <= hour < 9:
        return {"mood": "groggy",  "confidence": 0.4,
                "adjustment": "warm and encouraging morning tone",
                "signal_source": "time_early_morning"}
    if 9 <= hour < 18:
        return {"mood": "focused", "confidence": 0.5,
                "adjustment": "sharp, energetic and efficient",
                "signal_source": "time_working_hours"}
    return {"mood": "relaxed", "confidence": 0.35,
            "adjustment": "chill evening conversational tone",
            "signal_source": "time_evening"}


def _signal_command_urgency() -> dict | None:
    """Rapid short commands → user is rushed."""
    with _lock:
        now   = time.time()
        recent = [t for t in _command_times if now - t < _URGENCY_WINDOW]
    if len(recent) >= 5:
        return {"mood": "rushed", "confidence": 0.65,
                "adjustment": "answer immediately, skip pleasantries",
                "signal_source": "rapid_commands"}
    return None


def _fuse_passive_signals(signals: list[dict]) -> dict:
    if not signals:
        return {"mood": "neutral", "adjustment": "natural warm tone",
                "confidence": 0.5, "signals": ["gemini_native_affective"]}

    signals_sorted = sorted(signals, key=lambda x: x.get("confidence", 0), reverse=True)
    winner = signals_sorted[0]

    return {
        "mood":        winner.get("mood", "neutral"),
        "adjustment":  winner.get("adjustment", "natural warm tone"),
        "confidence":  winner.get("confidence", 0.5),
        "signals":     [s.get("signal_source", "?") for s in signals_sorted] + ["gemini_native_affective"],
        "timestamp":   time.time(),
    }


# ── Passive Poller Thread (Zero Webcam, Zero API) ──────────────────────────────
class _BiometricsPoller:
    def __init__(self):
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="BiometricsPoller"
        )

    def start(self):
        self._stop_event.clear()
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                signals = [
                    _signal_time_of_day(),
                    _signal_system_stress(),
                ]
                urgency = _signal_command_urgency()
                if urgency:
                    signals.append(urgency)

                fused = _fuse_passive_signals(signals)
                with _lock:
                    _cached_result.clear()
                    _cached_result.update(fused)

            except Exception as e:
                log_error("biometrics", "poller_loop", e)

            if self._stop_event.wait(POLL_INTERVAL):
                break


# ── Public API ─────────────────────────────────────────────────────────────────
def record_command_speed(timestamp: float = None):
    with _lock:
        _command_times.append(timestamp or time.time())
        cutoff = time.time() - 300
        while _command_times and _command_times[0] < cutoff:
            _command_times.pop(0)


def analyze_emotion() -> dict | None:
    with _lock:
        result = dict(_cached_result)
    return result


def get_mood_context_string() -> str:
    with _lock:
        result = dict(_cached_result)
    mood = result.get("mood", "neutral")
    conf = result.get("confidence", 0.5)
    adj  = result.get("adjustment", "natural warm tone")
    return f"[AFFECTIVE STATE: mood={mood} ({conf:.0%} conf) — {adj}]"


# ── Auto-start Passive Poller ──────────────────────────────────────────────────
_poller = _BiometricsPoller()
_poller.start()
