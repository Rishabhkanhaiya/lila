"""
core/duplex_audio.py — Real Duplex Speech Engine (Modern 2026 Engine)
====================================================================
Seamless bridge delegating full-duplex voice activity and barge-in
detection to core.smart_interrupt with zero local Whisper VRAM footprint.
"""

from core.smart_interrupt import start_smart_interrupt, stop_smart_interrupt

HAS_WHISPER = False

class RelayS2S_Engine:
    """Lightweight duplex engine delegating to zero-VRAM RMS barge-in."""

    def __init__(self):
        pass

    def listen_for_interrupt(self) -> str:
        """Start listening for speech barge-in."""
        start_smart_interrupt()
        return ""

    def stop(self):
        stop_smart_interrupt()
