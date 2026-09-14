"""
core/memory_engine.py — JARVIS Persistent Memory Engine
=========================================================
Automatically extracts and saves facts from every conversation.
Runs in a background daemon thread — ZERO latency impact on responses.

After every AI reply, this engine:
  1. Extracts user profile facts (name, preferences, habits)
  2. Extracts general world facts discussed
  3. Saves them to SQLite user_memory table (permanent)
  4. Also saves to the JSON knowledge graph (memory.py)

The facts are injected into every future system prompt so JARVIS
truly remembers across sessions and restarts.
"""

import threading
import re
import json
from core.jarvis_logger import log_error, log_info

# ── Extraction prompt ─────────────────────────────────────────────────────────
_EXTRACT_PROMPT = """Analyze this conversation exchange and extract memorable facts.

User said: "{user_input}"
JARVIS replied: "{reply}"

Extract facts that JARVIS should permanently remember. Focus on:
1. USER PROFILE: Name, age, location, job, hobbies, preferences, relationships, goals
2. USER PREFERENCES: Likes/dislikes about apps, music, food, tech, habits
3. IMPORTANT FACTS: Things the user told JARVIS they want remembered
4. TASKS COMPLETED: What was done successfully (for context)

For each extracted fact, if the user expressed a clear emotion connected to it in the conversation, tag it with a short descriptor (e.g. 'stressed', 'excited', 'proud', 'frustrated'). If no clear emotion was expressed, leave the tag empty — don't invent one.

Return ONLY valid JSON — no explanation, no markdown:
{{
  "user_profile": [["fact1", "stressed"]],
  "preferences": [["fact1", ""]],
  "general_facts": [["fact1", ""]],
  "tasks_done": [["task1", ""]]
}}

If nothing memorable was said, return: {{"user_profile":[],"preferences":[],"general_facts":[],"tasks_done":[]}}
Keep facts concise (max 20 words each). Skip trivial single-word replies."""

# ── Deduplication cache (prevents saving the same fact twice in a session) ───
_seen_facts: set = set()
_cache_lock = threading.Lock()

# ── Minimum reply length to bother extracting ───────────────────────────────
_MIN_REPLY_LEN = 20
_MIN_USER_LEN = 5


def _normalize(fact: str) -> str:
    """Lowercased, stripped, punctuation-free for dedup comparison."""
    return re.sub(r"[^a-z0-9 ]", "", fact.lower().strip())


def _save_facts(extracted: dict):
    """Write extracted facts to both DB and JSON knowledge graph."""
    from core.database import save_user_fact
    from core.memory import save_memory

    category_map = {
        "user_profile": "profile",
        "preferences": "preference",
        "general_facts": "general",
        "tasks_done": "task",
    }

    saved_count = 0
    with _cache_lock:
        for key, facts in extracted.items():
            if not isinstance(facts, list):
                continue
            cat = category_map.get(key, "general")
            for item in facts:
                emotional_tag = None
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    fact, emotional_tag = str(item[0]).strip(), str(item[1]).strip()
                    if not emotional_tag or emotional_tag.lower() == "null":
                        emotional_tag = None
                elif isinstance(item, dict):
                    fact = str(item.get("fact", "")).strip()
                    emotional_tag = str(item.get("emotional_tag", "")).strip() or None
                    if emotional_tag and emotional_tag.lower() == "null":
                        emotional_tag = None
                else:
                    fact = str(item).strip()
                if not fact or len(fact) < 5:
                    continue
                norm = _normalize(fact)
                if norm in _seen_facts:
                    continue  # Already saved this session
                _seen_facts.add(norm)

                # Write to SQLite user_memory
                try:
                    save_user_fact(fact, cat, emotional_tag=emotional_tag)
                except Exception as e:
                    log_error("memory_engine", "error", e)

                # Write to JSON knowledge graph (memory.py)
                try:
                    topic_key = f"{cat}_{saved_count}_{fact[:20].replace(' ','_')}"
                    save_memory(topic_key, fact)
                except Exception as e:
                    log_error("memory_engine", "error", e)

                saved_count += 1

    if saved_count:
        log_info("system", "system", f"[MEMORY_ENGINE] Saved {saved_count} new facts to long-term memory")


def _extract_worker(user_input: str, reply: str):
    """Background worker: calls LLM to extract facts, then saves them."""
    try:
        from core.brain import call_background_brain
        prompt = _EXTRACT_PROMPT.format(
            user_input=user_input[:500],
            reply=reply[:800]
        )
        raw = call_background_brain(prompt)
        if not raw or not raw.strip():
            return

        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\n?", "", raw).rstrip("`").strip()

        extracted = json.loads(raw)
        _save_facts(extracted)

    except json.JSONDecodeError:
        pass  # LLM returned non-JSON — silently skip
    except Exception as e:
        log_error("memory_engine", "error", e)


def extract_and_save(user_input: str, reply: str):
    """
    Non-blocking entry point. Call this after every AI response.
    Routes to Lila Cognitive Cortex for full episodic logging and background reflection.
    """
    if not user_input or not reply:
        return
    try:
        from core.lila_cognitive_cortex import record_interaction
        record_interaction(user_input, reply, session_id="legacy_engine")
    except Exception as e:
        log_error("memory_engine", "extract_and_save", e)


def get_memory_for_prompt() -> str:
    """
    Returns formatted memory string for injection into the system prompt.
    Leverages Lila Cognitive Cortex (<0.5ms prompt injection).
    """
    try:
        from core.lila_cognitive_cortex import get_lila_memory_injection
        return get_lila_memory_injection()
    except Exception as e:
        log_error("memory_engine", "get_memory_for_prompt", e)
        return "--- LILA MEMORY ---\nUser: Rishabh Joshi\n-------------------"
