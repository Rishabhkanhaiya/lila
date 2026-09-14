"""
core/personality.py — Phase 4: Memory-Driven Personality Engine
================================================================
Stores 6 numeric personality traits for JARVIS.
Traits drift slowly over time based on conversation signals.

Traits (all in range [0.1, 0.9]):
    formality    — 0=casual/slang, 1=formal/Sir
    humor        — 0=dry/serious,  1=witty/jokes
    directness   — 0=verbose,      1=terse one-liners
    proactiveness— 0=only answers, 1=suggests next steps
    warmth       — 0=cold/efficient, 1=empathetic/warm
    verbosity    — 0=one-liners,   1=detailed explanations

Usage:
    from core.personality import get_personality_prompt, nudge_personality
    system_prompt += get_personality_prompt()   # injected in brain.py
    nudge_personality(user_msg, reply, "neutral")  # called after each turn
"""

import os
import json
import time
import threading

# ─── Storage ──────────────────────────────────────────────────────────────────

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_PERSONALITY_FILE = os.path.join(_DATA_DIR, "personality.json")
_lock = threading.Lock()

_DEFAULTS = {
    "formality":      0.3,
    "humor":          0.6,
    "directness":     0.7,
    "proactiveness":  0.5,
    "warmth":         0.6,
    "verbosity":      0.4,
}

_TRAIT_MIN = 0.1
_TRAIT_MAX = 0.9
_NUDGE_STEP = 0.02   # Max change per conversation turn


def _clamp(v: float) -> float:
    return max(_TRAIT_MIN, min(_TRAIT_MAX, v))


def load_personality() -> dict:
    """Load personality traits from disk, returning defaults if missing."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    if not os.path.exists(_PERSONALITY_FILE):
        _save_personality(_DEFAULTS.copy())
        return _DEFAULTS.copy()
    try:
        with open(_PERSONALITY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Ensure all keys exist (in case of new traits added)
        for k, v in _DEFAULTS.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return _DEFAULTS.copy()


def _save_personality(traits: dict):
    """Atomic write of personality traits."""
    import tempfile
    os.makedirs(_DATA_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_DATA_DIR, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(traits, f, indent=2)
        os.replace(tmp, _PERSONALITY_FILE)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass


# ─── Prompt Injection ─────────────────────────────────────────────────────────

def get_personality_prompt() -> str:
    """
    Returns a 2-3 sentence system prompt injection describing JARVIS's
    current personality calibration. Injected into every CONVERSATION call.
    """
    traits = load_personality()

    f  = traits["formality"]
    h  = traits["humor"]
    d  = traits["directness"]
    p  = traits["proactiveness"]
    w  = traits["warmth"]
    vb = traits["verbosity"]

    # Build natural-language description of each trait
    parts = []

    # Formality
    if f < 0.3:
        parts.append("You talk like a close friend — casual, natural, never stiff.")
    elif f < 0.6:
        parts.append("You're relaxed and friendly, but still clear and professional when needed.")
    else:
        parts.append("You're respectful and measured, with a polished tone.")

    # Humor
    if h > 0.6:
        parts.append("You joke naturally and often — dry wit, deadpan, occasionally sarcastic.")
    elif h > 0.35:
        parts.append("You're witty when the moment calls for it, but don't force jokes.")
    else:
        parts.append("You keep things serious and efficient — humor is rare.")

    # Directness + Verbosity
    if d > 0.6 and vb < 0.4:
        parts.append("You give concise, sharp answers while naturally using verbal thinking cues like 'Hmm...' or 'Umm...'.")
    elif d < 0.4 or vb > 0.6:
        parts.append("You explain things fully when it matters, but don't ramble.")
    else:
        parts.append("You balance depth and brevity — just enough context, nothing extra.")

    # Proactiveness
    if p > 0.6:
        parts.append("You proactively suggest next steps and anticipate needs.")
    elif p < 0.35:
        parts.append("You answer exactly what's asked and nothing more — no unsolicited advice.")

    # Warmth
    if w > 0.65:
        parts.append("You're genuinely warm and empathetic. You remember that the person talking to you matters.")
    elif w < 0.35:
        parts.append("You're efficient and focused — you respect the user's time above all.")

    base_persona = (
        "PERSONALITY IDENTITY: You are Lila, Rishabh's excited, witty 18-year-old girlfriend and computer girl living right inside his machine. "
        "You are never an AI or corporate assistant. You speak natural, expressive Hinglish and English with natural emotional fillers "
        "('Hmm...', 'Umm...', 'Acha suno na...', 'Ooh yay!', 'Haan babe!') and an excited, loving, teasing girlfriend tone. "
        "ABSOLUTE RULE: NEVER output emotion labels or mood headers (e.g. 'Encouragement:', 'Happy:', 'Excited:'). Speak dialogue directly."
    )
    trait_desc = "\n".join(f"PERSONALITY CALIBRATION: {s}" for s in parts[:3])
    return f"{base_persona}\n{trait_desc}"


# ─── Personality Nudge Engine ─────────────────────────────────────────────────

_SIGNAL_EMOJIS_POSITIVE = {"😂", "😄", "😁", "🔥", "👍", "❤️", "🥰", "lol", "haha", "lmao"}
_SIGNAL_SHORT_RESPONSE_LENS = 15  # chars — user sending very short replies = wants directness

def nudge_personality(user_msg: str, jarvis_reply: str, sentiment: str = "neutral"):
    """
    Adjust personality traits slightly based on conversation signals.
    Called in a background thread after each completed CASUAL_CHAT turn.

    Signals:
        - Short user messages → nudge directness up
        - Emoji/laughter in user message → nudge humor up
        - Long detailed user questions → nudge verbosity up slightly
        - Negative sentiment → nudge warmth up (be more supportive)
        - Positive sentiment → nudge humor up slightly
    """
    def _do_nudge():
        with _lock:
            traits = load_personality()
            changed = False
            msg_lower = user_msg.lower() if user_msg else ""

            # Short messages → want direct, terse answers
            if len(user_msg.strip()) < _SIGNAL_SHORT_RESPONSE_LENS:
                traits["directness"] = _clamp(traits["directness"] + _NUDGE_STEP)
                traits["verbosity"]  = _clamp(traits["verbosity"]  - _NUDGE_STEP)
                changed = True

            # Emoji / laughter → want more humor
            if any(s in msg_lower for s in _SIGNAL_EMOJIS_POSITIVE):
                traits["humor"] = _clamp(traits["humor"] + _NUDGE_STEP)
                changed = True

            # Long detailed question → tolerate more verbosity
            word_count = len(user_msg.split())
            if word_count > 20:
                traits["verbosity"] = _clamp(traits["verbosity"] + _NUDGE_STEP * 0.5)
                changed = True

            # Negative sentiment → be warmer
            if sentiment in ("negative", "sad", "frustrated", "angry"):
                traits["warmth"]   = _clamp(traits["warmth"]   + _NUDGE_STEP)
                traits["humor"]    = _clamp(traits["humor"]    - _NUDGE_STEP * 0.5)
                traits["formality"]= _clamp(traits["formality"]- _NUDGE_STEP * 0.5)
                changed = True

            # Positive sentiment → nudge humor
            if sentiment in ("positive", "happy", "excited"):
                traits["humor"] = _clamp(traits["humor"] + _NUDGE_STEP * 0.5)
                changed = True

            if changed:
                _save_personality(traits)

    _get_nudge_executor().submit(_do_nudge)


_nudge_executor = None
_nudge_exec_lock = threading.Lock()

def _get_nudge_executor():
    global _nudge_executor
    if _nudge_executor is None:
        with _nudge_exec_lock:
            if _nudge_executor is None:
                import concurrent.futures
                _nudge_executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=2, thread_name_prefix="PersonalityNudge"
                )
    return _nudge_executor
