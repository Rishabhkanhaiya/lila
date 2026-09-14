"""
emotional_intelligence.py — JARVIS Emotional Intelligence v1.0
===============================================================
Provides an EmotionalState snapshot for omni_synthesis.py.
Sources emotion data from the biometrics engine (multi-signal fusion).
Provides a safe, graceful fallback when biometrics isn't available.

Public API:
  get_ei()                  → EmotionalIntelligence singleton
  get_ei().get_current_state() → EmotionalState dataclass
"""

import threading
import time
from dataclasses import dataclass, field

from core.jarvis_logger import log_error, log_warn


@dataclass
class EmotionalState:
    """Unified emotional state consumed by omni_synthesis.py."""
    primary:      str   = "neutral"   # mood label
    stress_level: float = 0.0         # 0.0 – 1.0
    energy_level: float = 0.5         # 0.0 – 1.0
    trend:        str   = "stable"    # stable / improving / worsening
    confidence:   float = 0.3         # how confident we are in this reading
    source:       str   = "default"   # biometrics / time_of_day / default


class EmotionalIntelligence:
    """
    Thin adapter that reads from the biometrics engine and
    translates it into the EmotionalState format omni_synthesis expects.
    """

    _STRESS_MAP = {
        "stressed":  0.80,
        "rushed":    0.70,
        "angry":     0.85,
        "anxious":   0.75,
        "tired":     0.45,
        "groggy":    0.35,
        "focused":   0.25,
        "neutral":   0.20,
        "relaxed":   0.10,
        "happy":     0.05,
    }

    _ENERGY_MAP = {
        "tired":    0.20,
        "groggy":   0.25,
        "stressed": 0.40,
        "neutral":  0.50,
        "focused":  0.70,
        "relaxed":  0.65,
        "happy":    0.80,
        "rushed":   0.60,
    }

    def __init__(self):
        self._lock  = threading.Lock()
        self._state = EmotionalState()
        self._last_update = 0.0
        self._UPDATE_TTL  = 30.0   # seconds before re-fetching

    def get_current_state(self) -> EmotionalState:
        """Return the current emotional state. Refreshes if stale."""
        now = time.time()
        with self._lock:
            if now - self._last_update > self._UPDATE_TTL:
                self._refresh()
                self._last_update = now
            return EmotionalState(
                primary      = self._state.primary,
                stress_level = self._state.stress_level,
                energy_level = self._state.energy_level,
                trend        = self._state.trend,
                confidence   = self._state.confidence,
                source       = self._state.source,
            )

    def _refresh(self):
        """Pull the latest mood from biometrics engine."""
        try:
            from core.biometrics import analyze_emotion
            bio = analyze_emotion()
            if bio:
                mood       = bio.get("mood", "neutral")
                confidence = bio.get("confidence", 0.3)
                sources    = bio.get("signals", [])

                stress = self._STRESS_MAP.get(mood, 0.20)
                energy = self._ENERGY_MAP.get(mood, 0.50)

                # Trend heuristic — compare to previous state
                prev_stress = self._state.stress_level
                if stress > prev_stress + 0.15:
                    trend = "worsening"
                elif stress < prev_stress - 0.10:
                    trend = "improving"
                else:
                    trend = "stable"

                self._state = EmotionalState(
                    primary      = mood,
                    stress_level = stress,
                    energy_level = energy,
                    trend        = trend,
                    confidence   = confidence,
                    source       = ", ".join(sources) if sources else "biometrics",
                )
                return

        except Exception as e:
            log_error("emotional_intelligence", "refresh_from_biometrics", e)

        # Fallback: time-of-day heuristic
        from datetime import datetime
        hour = datetime.now().hour
        if 0 <= hour < 6:
            mood, stress, energy = "tired",   0.45, 0.20
        elif 6 <= hour < 9:
            mood, stress, energy = "groggy",  0.35, 0.30
        elif 9 <= hour < 12:
            mood, stress, energy = "focused", 0.25, 0.70
        elif 22 <= hour < 24:
            mood, stress, energy = "tired",   0.40, 0.25
        else:
            mood, stress, energy = "neutral", 0.20, 0.55

        prev_stress = self._state.stress_level
        trend = ("worsening" if stress > prev_stress + 0.10
                 else "improving" if stress < prev_stress - 0.10
                 else "stable")

        self._state = EmotionalState(
            primary      = mood,
            stress_level = stress,
            energy_level = energy,
            trend        = trend,
            confidence   = 0.35,
            source       = "time_of_day",
        )


# ── Singleton ─────────────────────────────────────────────────────────────────
_ei_instance: EmotionalIntelligence | None = None
_ei_lock = threading.Lock()


def get_ei() -> EmotionalIntelligence:
    global _ei_instance
    if _ei_instance is None:
        with _ei_lock:
            if _ei_instance is None:
                _ei_instance = EmotionalIntelligence()
    return _ei_instance
