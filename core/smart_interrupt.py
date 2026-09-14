"""
core/smart_interrupt.py — JARVIS Smart Interrupt Engine (Modern 2026 Engine)
=============================================================================
Zero-latency voice barge-in monitor.
- Zero local speech model VRAM burn
- Detects user speech volume spikes & voice activity while JARVIS speaks
- Immediately cuts off playback in <10ms via core.voice.stop_speaking()
"""

import threading
import time
import numpy as np
from core.jarvis_logger import log_error, log_info, log_warn

# Voice Activity Thresholds
_WARMUP_SEC = 0.3      # Skip initial speaker acoustic bleed
_ENERGY_THRESHOLD = 800 # RMS amplitude threshold for user voice detection

class SmartInterruptListener:
    """Lightweight real-time voice barge-in listener."""

    def __init__(self):
        self._active = False
        self._thread: threading.Thread | None = None
        self._mic_cb = None
        self._stop_event = threading.Event()

    def start(self):
        """Non-blocking start alongside speech playback."""
        self._active = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="SmartInterrupt"
        )
        self._thread.start()

    def stop(self):
        """Halt listener when speech ends."""
        self._active = False
        self._stop_event.set()

    def _mic_callback(self, chunk: np.ndarray):
        if not self._active or self._stop_event.is_set():
            return
        try:
            # Fast RMS calculation
            rms = np.sqrt(np.mean(chunk.astype(np.float32) ** 2))
            if rms > _ENERGY_THRESHOLD:
                # User spoke over playback -> cut off speech instantly
                self._active = False
                self._stop_event.set()
                try:
                    from core.voice import stop_speaking
                    stop_speaking()
                    log_info("smart_interrupt", f"[BARGE-IN]: Interrupted by user voice (RMS: {rms:.0f})")
                except Exception:
                    pass
        except Exception:
            pass

    def _run(self):
        try:
            from core.shared_mic import shared_mic
            self._mic_cb = self._mic_callback
            if self._stop_event.wait(_WARMUP_SEC):
                return
            shared_mic.subscribe(self._mic_cb)

            # Reactive wait until stop() or voice barge-in sets the event
            self._stop_event.wait()
        except Exception as e:
            log_warn("smart_interrupt", f"Smart interrupt monitor failed: {e}")
        finally:
            try:
                from core.shared_mic import shared_mic
                if self._mic_cb:
                    shared_mic.unsubscribe(self._mic_cb)
            except Exception:
                pass


_listener = SmartInterruptListener()

def start_smart_interrupt():
    _listener.start()

def stop_smart_interrupt():
    _listener.stop()
