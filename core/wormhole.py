"""
core/wormhole.py — JARVIS Wormhole Tunneling Engine
====================================================
Exposes any local port to the public internet via ngrok.
Manages one active tunnel at a time. Thread-safe.

Usage (voice):
  "Jarvis, expose port 3000"           → opens ngrok tunnel on port 3000
  "Jarvis, open a tunnel on port 8080" → opens ngrok tunnel on port 8080
  "Jarvis, close the tunnel"           → kills active tunnel
  "Jarvis, what's my tunnel URL"       → reads the current URL
"""

import threading
from core.jarvis_logger import log_info, log_warn, log_error

# ── State ─────────────────────────────────────────────────────────────────────
_tunnel    = None   # active pyngrok tunnel object
_lock      = threading.Lock()

# ── pyngrok availability ──────────────────────────────────────────────────────
try:
    from pyngrok import ngrok as _ngrok, conf as _ngconf, exception as _ngex
    HAS_NGROK = True
except ImportError:
    HAS_NGROK = False
    log_warn("wormhole", "pyngrok not installed. Run: pip install pyngrok")


def open_tunnel(port: int, protocol: str = "http") -> str:
    """
    Opens an ngrok tunnel on the given port.
    Returns the public URL string, or an error message.
    """
    global _tunnel

    if not HAS_NGROK:
        return "pyngrok is not installed. Please run: pip install pyngrok"

    with _lock:
        # Close any previously active tunnel first
        if _tunnel is not None:
            try:
                _ngrok.disconnect(_tunnel.public_url)
                log_info("wormhole", f"Closed previous tunnel: {_tunnel.public_url}")
            except Exception as e:
                log_warn("wormhole", f"Failed to close previous tunnel: {e}")
            _tunnel = None

        try:
            _tunnel = _ngrok.connect(port, proto=protocol)
            url = _tunnel.public_url
            log_info("wormhole", f"Tunnel opened: {url} → localhost:{port}")
            return url
        except _ngex.PyngrokNgrokError as e:
            log_error("wormhole", "open_tunnel", e)
            return f"ngrok error: {e}"
        except Exception as e:
            log_error("wormhole", "open_tunnel", e)
            return f"Failed to open tunnel: {e}"


def close_tunnel() -> str:
    """Closes the active ngrok tunnel."""
    global _tunnel

    if not HAS_NGROK:
        return "pyngrok not installed."

    with _lock:
        if _tunnel is None:
            return "No active tunnel to close, Sir."
        try:
            url = _tunnel.public_url
            _ngrok.disconnect(url)
            _tunnel = None
            log_info("wormhole", f"Tunnel closed: {url}")
            return f"Tunnel {url} has been closed, Sir."
        except Exception as e:
            log_error("wormhole", "close_tunnel", e)
            return f"Failed to close tunnel: {e}"


def get_active_url() -> str:
    """Returns the current active public URL, or empty string if none."""
    with _lock:
        if _tunnel is None:
            return ""
        return _tunnel.public_url


def get_status() -> dict:
    """Returns status dict for the UI dashboard."""
    with _lock:
        if _tunnel is None:
            return {"active": False, "url": "", "port": ""}
        return {
            "active": True,
            "url": _tunnel.public_url,
            "port": str(_tunnel.config.get("addr", "?"))
        }
