"""
core/response_cache.py — Phase 1: Instant response cache
=========================================================
LRU cache for repeated greetings and common phrases.
These bypass the LLM entirely and return in <10ms.

Usage:
    from core.response_cache import get_cached_response
    reply = get_cached_response("hello")
    if reply:
        speak(reply)
        return
    # Otherwise call LLM normally
"""

import time
import random
import hashlib
import threading
from collections import OrderedDict

# ─── Pre-seeded instant reply pools ─────────────────────────────────────────

_GREETING_REPLIES = [
    "Haan Rishabh, bolo?",
    "Hmm, sun rahi hoon... batao?",
    "Hey! Kya chal raha hai?",
    "Acha bolo, what's on your mind?",
    "Umm, all systems green. Ready!",
    "Hey — what's going on?",
    "I'm here. Talk to me.",
]

_HOW_ARE_YOU_REPLIES = [
    "Running clean. Everything's good on my end.",
    "All systems nominal. You?",
    "Good. Sharp. Ready.",
    "Optimal. What's on your mind?",
    "Running smooth. What do you need?",
]

_STATUS_REPLIES = [
    "All critical systems green. Memory, voice, and routing all nominal.",
    "Everything's running clean. No errors in the last cycle.",
    "Systems healthy — all modules loaded and active.",
    "Fully operational. All modules green.",
]

_THANKS_REPLIES = [
    "Of course!",
    "Anytime.",
    "That's what I'm here for.",
    "No problem at all.",
    "Always ready to help.",
]

_OK_REPLIES = [
    "Got it.",
    "Understood.",
    "Done.",
    "On it.",
    "Noted.",
]

# ─── Pattern → reply pool mapping ───────────────────────────────────────────

_PATTERNS: list[tuple[list[str], list[str]]] = [
    (
        ["hello", "hey jarvis", "hi jarvis", "hey there", "hello jarvis", "hi there", "hey astra", "hi astra", "hello astra", "astra"],
        _GREETING_REPLIES
    ),
    (
        ["how are you", "how are you doing", "how's it going", "how you doing"],
        _HOW_ARE_YOU_REPLIES
    ),
    (
        ["system status", "status check", "how are systems", "everything okay", "all good"],
        _STATUS_REPLIES
    ),
    (
        ["thank you", "thanks", "thanks jarvis", "thank you jarvis", "thanks astra", "thank you astra", "cheers"],
        _THANKS_REPLIES
    ),
    (
        ["okay", "ok", "got it", "alright", "sure", "yes", "yeah"],
        _OK_REPLIES
    ),
]

# ─── LRU Cache ───────────────────────────────────────────────────────────────

_CACHE_MAX = 100          # max unique keys
_CACHE_TTL = 600          # seconds (10 minutes)
_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation/filler words for matching."""
    import re
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\bjarvis\b|\bastra\b|\bplease\b|\bcan you\b|\bcould you\b", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def get_cached_response(user_input: str) -> str | None:
    """
    Returns an instant reply if the input matches a known pattern.
    Returns None if no match (caller should hit the LLM normally).
    """
    normalized = _normalize(user_input)
    if not normalized:
        return None

    # Check pre-seeded patterns first (no TTL — always valid)
    for triggers, replies in _PATTERNS:
        if normalized in triggers or any(normalized.startswith(t) or t.startswith(normalized) for t in triggers):
            return random.choice(replies)

    # Check LRU cache (LLM replies cached for repeated questions)
    key = _hash(normalized)
    with _cache_lock:
        entry = _cache.get(key)
        if entry:
            text, ts = entry
            if time.time() - ts < _CACHE_TTL:
                _cache.move_to_end(key)  # Mark as recently used
                return text
            else:
                del _cache[key]  # Expired

    return None


def cache_response(user_input: str, reply: str):
    """
    Store an LLM reply in the cache for future instant retrieval.
    Called after successful LLM response, in a background thread.
    """
    if not reply or not user_input:
        return
    normalized = _normalize(user_input)
    if not normalized or len(normalized) < 4:
        return
    key = _hash(normalized)
    with _cache_lock:
        _cache[key] = (reply, time.time())
        _cache.move_to_end(key)
        # Evict oldest if over limit
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def cache_stats() -> dict:
    """Returns current cache statistics."""
    with _cache_lock:
        return {
            "size": len(_cache),
            "max": _CACHE_MAX,
            "ttl_seconds": _CACHE_TTL,
            "pattern_pools": len(_PATTERNS),
        }
