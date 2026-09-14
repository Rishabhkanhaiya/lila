"""
core/omni_synthesis.py — JARVIS Omni-Synthesis Engine (Modern 2026 Standard)
=============================================================================
Central multimodal synthesis module uniting:
- Persistent vision cues and real-time screen state
- Emotional intelligence and biometric signals
- Sixth-sense proactive alerts and developer error telemetry
- Zero-VRAM event-driven monitoring for the Proactive Orchestrator
"""

import time
import threading
from typing import Any, Dict, List, Optional
from core.jarvis_logger import log_error, log_info, log_warn

# Recent sixth sense event buffer (thread-safe)
_EVENT_BUFFER: List[Dict[str, Any]] = []
_buffer_lock = threading.Lock()
_MAX_BUFFER_LEN = 50


def notify_sixth_sense(event_type: str, payload: dict) -> None:
    """
    Called by persistent_vision.py and other sensory modules when notable
    real-time events occur (e.g. screen_errors, code compilation failure).
    """
    event_entry = {
        "event_type": event_type,
        "payload": payload,
        "timestamp": time.time(),
    }
    with _buffer_lock:
        _EVENT_BUFFER.append(event_entry)
        if len(_EVENT_BUFFER) > _MAX_BUFFER_LEN:
            _EVENT_BUFFER.pop(0)

    log_info("omni_synthesis", f"[SIXTH SENSE EVENT]: {event_type} -> {str(payload)[:80]}")


def synthesize_data(data: Any) -> Dict[str, Any]:
    """
    Synthesizes raw sensory and execution data into a coherent contextual state.
    Consumes emotional state, recent screen context, and error buffers.
    """
    summary = {
        "status": "synthesized",
        "timestamp": time.time(),
        "input_summary": str(data)[:120] if data else "empty",
    }
    try:
        from core.emotional_intelligence import get_ei
        ei_state = get_ei().get_current_state()
        summary["user_mood"] = ei_state.primary
        summary["stress_level"] = ei_state.stress_level
    except Exception:
        summary["user_mood"] = "neutral"
        summary["stress_level"] = 0.2

    with _buffer_lock:
        summary["recent_events_count"] = len(_EVENT_BUFFER)
        if _EVENT_BUFFER:
            summary["latest_event"] = _EVENT_BUFFER[-1]

    return summary


# ── OmniSynthesisMonitor for Proactive Orchestrator ───────────────────────────
try:
    from core.monitors.base_monitor import BaseMonitor
except ImportError:
    class BaseMonitor(threading.Thread):
        def __init__(self, event_queue, config=None):
            super().__init__(daemon=True)
            self.event_queue = event_queue
            self.config = config or {}
            self._stop_event = threading.Event()
        def check(self): return []
        def stop(self): self._stop_event.set()


class OmniSynthesisMonitor(BaseMonitor):
    """
    AGI Tier 4 proactive monitor that correlates sensory alerts, screen exceptions,
    and biometrics into synthesized proactive interventions.
    """
    name = "OmniSynthesis"
    interval_seconds = 30

    def __init__(self, event_queue, config=None):
        super().__init__(event_queue, config)
        self._last_processed_ts = time.time()

    def check(self) -> list:
        """
        Inspects buffered sixth-sense events (e.g. screen exceptions) and publishes
        prioritized ProactiveEvents when actionable patterns emerge.
        """
        new_events = []
        with _buffer_lock:
            pending = [e for e in _EVENT_BUFFER if e["timestamp"] > self._last_processed_ts]
            if pending:
                self._last_processed_ts = pending[-1]["timestamp"]

        for entry in pending:
            evt_type = entry.get("event_type", "")
            payload = entry.get("payload", {})
            if evt_type == "screen_errors":
                errs = payload.get("errors", [])
                err_text = "; ".join(errs) if isinstance(errs, list) else str(errs)
                try:
                    from core.proactive_orchestrator import ProactiveEvent
                    event = ProactiveEvent(
                        priority=2,
                        category="development",
                        title="Screen Error Detected",
                        message=f"I noticed an error in your active window: {err_text[:100]}",
                        data={"source": "omni_synthesis", "errors": errs},
                        timestamp=entry.get("timestamp", time.time())
                    )
                    new_events.append(event)
                except Exception as ex:
                    log_warn("omni_synthesis", f"Failed to construct ProactiveEvent: {ex}")

        return new_events

