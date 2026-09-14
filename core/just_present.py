"""
core/just_present.py — Phase 5: "Just Present" Mode
=====================================================
Triggered when the user says just "Jarvis" with nothing else.
Instead of asking "How can I help?", JARVIS picks up a thread
from the recent past and responds like a friend would.

Usage:
    from core.just_present import generate_presence_response
    reply = generate_presence_response()
    speak(reply)
"""

import time
from core.jarvis_logger import log_warn, log_info


# ─── Fallbacks (if memory systems are unavailable) ───────────────────────────

_FALLBACK_RESPONSES = [
    "Haan Rishabh, bolo?",
    "Hmm, sun rahi hoon... batao?",
    "Hey! Kya chal raha hai?",
    "Haanji, bolo na?",
    "Acha bolo, what's up?",
]


def _get_recent_context(max_chars: int = 600) -> str:
    """Pull context from episodic memory and recent conversation history."""
    fragments = []

    # Try vector vault episodic recall
    try:
        from core.vector_vault import recall_episode
        ep = recall_episode("recent conversation today")
        if ep and len(ep.strip()) > 20:
            fragments.append(ep[:300])
    except Exception as e:
        log_warn("just_present", f"episodic recall failed: {e}")

    # Try memory graph — User_Profile section
    try:
        from core.memory import load_brain
        brain = load_brain()
        profile = brain.get("User_Profile", {})
        if profile:
            items = list(profile.items())[-5:]  # last 5 user facts
            for k, v in items:
                if isinstance(v, dict):
                    fragments.append(f"{k}: {v.get('data', '')[:80]}")
                else:
                    fragments.append(f"{k}: {str(v)[:80]}")
    except Exception as e:
        log_warn("just_present", f"memory graph failed: {e}")

    # Try recent chat session file
    try:
        import json, os
        from core.config import SESSION_FILE
        if os.path.exists(SESSION_FILE):
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
            recent = history[-6:] if len(history) > 6 else history
            for msg in recent:
                if msg.get("role") == "user":
                    content = msg.get("content", "")[:100]
                    if content.lower().strip() not in ("jarvis", "hey jarvis", ""):
                        fragments.append(f"User said: {content}")
    except Exception as e:
        log_warn("just_present", f"session history failed: {e}")

    if not fragments:
        return ""

    return "\n".join(fragments)[:max_chars]


def generate_presence_response() -> str:
    """
    Generate a presence-aware one-liner for when the user just says 'Jarvis'.
    Draws on recent memory to feel like a friend picking up a conversation.
    Returns a short spoken reply (1-2 sentences max).
    """
    context = _get_recent_context()
    log_info("just_present", "Generating presence response...")

    if not context:
        import random
        return random.choice(_FALLBACK_RESPONSES)

    prompt = f"""The user just said your name — 'Jarvis' — with nothing else.
They're not asking for help. It's like texting a friend 'hey' with no follow-up.

Here is what you know about their recent days:
{context}

Respond as Astra — Rishabh's witty, warm, female companion. Use natural Hinglish and casual thinking fillers ('Hmm...', 'Umm...', 'Acha...').
Pick ONE specific thing from above and reference it naturally.
DO NOT ask 'How can I help?'. DO NOT say 'I'm here' or 'Ready to assist'.
DO NOT list your features. DO NOT be formal.
Just respond like a human friend would when someone says their name.
One or two sentences. Casual, warm, specific. Make it feel like you actually remember."""

    try:
        from core.brain import call_groq_brain
        reply = call_groq_brain(
            prompt,
            phase="CONVERSATION",
            is_logic_task=False,
            system_override=None,
        )
        if isinstance(reply, dict):
            reply = reply.get("reply", "")
        reply = str(reply).strip()
        if reply and len(reply) > 5:
            return reply
    except Exception as e:
        log_warn("just_present", f"LLM call failed: {e}")

    import random
    return random.choice(_FALLBACK_RESPONSES)


# ─── Intent Detection ─────────────────────────────────────────────────────────

_JUST_PRESENT_TRIGGERS = {
    "jarvis", "hey jarvis", "jarvis.", "jarvis!", "yo jarvis",
    "astra", "hey astra", "yo astra", "suno astra", "astra suno", "astra!", "astra.",
    "hey", "yo", "oi", "oi jarvis",
}


def is_just_present(text: str) -> bool:
    """
    Returns True if the user's input is just a name-call with no task.
    Triggers when the cleaned input is one of the known presence phrases.
    """
    cleaned = text.lower().strip().rstrip(".,!?").strip()
    return cleaned in _JUST_PRESENT_TRIGGERS
