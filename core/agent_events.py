"""
core/agent_events.py — Phase 2: Live Agent Visibility Event Bus
================================================================
Central event emitter that broadcasts JSON events to all connected
WebSocket clients in real time.

Usage (from any module):
    from core.agent_events import emit

    emit("brain",     "llm_start",    {"provider": "Gemini Flash"})
    emit("web_agent", "reading_page", {"url": "https://example.com"})
    emit("swarm",     "agent_start",  {"agent_id": 2, "task": "Research AI news"})

Events are broadcast via ws_server.manager.broadcast() and are
visible in the JARVIS UI Live Feed panel.
"""

import time
import json
import threading
from core.jarvis_logger import log_warn

# Thread-safe lazy import of WS manager to avoid circular imports
_ws_manager = None
_ws_manager_lock = threading.Lock()


def _get_manager():
    global _ws_manager
    if _ws_manager is not None:
        return _ws_manager
    with _ws_manager_lock:
        if _ws_manager is None:
            try:
                from core.ws_server import manager
                _ws_manager = manager
            except Exception as e:
                log_warn("agent_events", f"WebSocket manager not available: {e}")
        return _ws_manager


# ─── Type constants ───────────────────────────────────────────────────────────

# Sources
BRAIN      = "brain"
WEB_AGENT  = "web_agent"
SWARM      = "swarm"
ROUTER     = "router"
SYSTEM     = "system"

# Event types
LLM_START      = "llm_start"
LLM_CHUNK      = "llm_chunk"
LLM_DONE       = "llm_done"
SEARCHING      = "searching"
READING_PAGE   = "reading_page"
EXTRACTED      = "extracted"
AGENT_START    = "agent_start"
AGENT_THINKING = "agent_thinking"
AGENT_DONE     = "agent_done"
AGENT_FAILED   = "agent_failed"
MISSION_START  = "mission_start"
MISSION_DONE   = "mission_done"
STEP           = "step"


def emit(source: str, event_type: str, data: dict = None, channel: str = None):
    """
    Broadcast a live event to all connected WebSocket clients.

    Args:
        source:     Module emitting the event ("brain", "web_agent", "swarm", ...)
        event_type: Type of event ("llm_start", "reading_page", "agent_done", ...)
        data:       Optional dict with event-specific payload
        channel:    Optional channel for per-agent routing ("swarm/abc123/agent_2")
    """
    try:
        manager = _get_manager()
        if manager is None:
            return  # WS not started yet — silently skip

        payload = {
            "source":    source,
            "type":      event_type,
            "ts":        round(time.time() * 1000),  # milliseconds
            "data":      data or {},
        }
        if channel:
            payload["channel"] = channel

        manager.broadcast(payload)
    except Exception as e:
        # Never crash the caller — events are best-effort observability
        log_warn("agent_events", f"emit failed ({source}/{event_type}): {e}")


def emit_agent(mission_id: str, agent_id: int, event_type: str, data: dict = None):
    """
    Convenience wrapper for per-agent swarm events.
    Automatically sets the channel to "swarm/{mission_id}/agent_{agent_id}".
    """
    channel = f"swarm/{mission_id}/agent_{agent_id}"
    d = {"agent_id": agent_id, "mission_id": mission_id}
    if data:
        d.update(data)
    emit(SWARM, event_type, d, channel=channel)
