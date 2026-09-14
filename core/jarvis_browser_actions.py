"""
jarvis_browser_actions.py — JARVIS Custom Actions for browser-use v7.0

Registers JARVIS's unique superpowers as native LLM-callable tools inside
browser-use's Controller. The Agent's LLM can call these exactly like
any other action (click, type, navigate).

Custom Actions Registered:
  jarvis_ask_user     → Speak a question + listen for voice input (OTP, login)
  jarvis_confirm      → Voice confirmation gate before irreversible actions
  jarvis_save_tactic  → Save a learned rule to SQLite WebMemory for this domain
  jarvis_bust_barrier → Auto-dismiss cookie banners, popups, and age gates
  jarvis_narrate      → Speak a progress update to the user
"""

from __future__ import annotations
import threading
import time
from pydantic import BaseModel
try:
    from browser_use.tools.registry.service import RegisteredTool
    from browser_use.tools.registry.views import ToolResult
    HAS_BROWSER_USE = True
except Exception:
    RegisteredTool = None
    ToolResult = None
    HAS_BROWSER_USE = False


# ── Lazy imports to avoid circular dependencies ───────────────────────────────
def _get_voice():
    from core.web_agent import _web_voice
    return _web_voice

def _get_memory():
    from core.web_agent import _web_memory
    return _web_memory


# ── Custom Tool Definitions ───────────────────────────────────────────────────
# browser-use 0.12.x registers tools via the `tools` parameter in Agent()
# Each tool is a callable that returns a string result.

class AskUserParams(BaseModel):
    question: str

class ConfirmParams(BaseModel):
    action_description: str

class SaveTacticParams(BaseModel):
    domain: str
    tactic: str

class NarrateParams(BaseModel):
    message: str


def jarvis_ask_user(params: AskUserParams) -> str:
    """Ask JARVIS to speak a question and listen for voice input.
    Use when you need: OTP, password, login credentials, address, payment info.
    """
    try:
        voice = _get_voice()
        voice.narrate(params.question)
        response = voice.listen_for_voice(timeout=60)
        if response:
            return f"User said: '{response}'"
        return "No voice input received. Ask user to type manually or try again."
    except Exception as e:
        return f"Voice input error: {e}"


def jarvis_confirm(params: ConfirmParams) -> str:
    """Ask JARVIS to verbally confirm with the user before any irreversible action.
    ALWAYS call this before: booking tickets, making purchases, submitting payments,
    deleting data, or sending messages. Returns 'CONFIRMED' or 'REJECTED'.
    """
    try:
        voice = _get_voice()
        approved = voice.listen_for_approval(params.action_description, timeout=30)
        if approved:
            return "CONFIRMED: User approved. You may proceed."
        return "REJECTED: User declined. STOP immediately. Do not proceed with this action."
    except Exception as e:
        return f"Confirmation error: {e}. Treat as REJECTED for safety."


def jarvis_save_tactic(params: SaveTacticParams) -> str:
    """Save a learned rule to JARVIS persistent memory for this website domain.
    Call this whenever you discover a quirk: e.g. 'pressing Enter on Google Flights
    redirects to Explore page — use Tab instead'. This rule will be auto-injected
    into your system prompt on every future visit to this domain.
    """
    try:
        mem = _get_memory()
        mem.save_learned_tactic(params.domain, params.tactic)
        print(f"[JARVIS MEMORY]: 🧠 Saved tactic for {params.domain}: {params.tactic[:80]}")
        return f"Tactic saved for {params.domain}. I will remember this for all future sessions."
    except Exception as e:
        return f"Memory save error: {e}"


def jarvis_bust_barrier(page) -> str:
    """Dismiss cookie consent banners, age verification gates, or modal popups
    that are blocking interaction with the page.
    Call this at the start of each new domain or when a popup appears.
    """
    try:
        # Import here to avoid circular import at module load
        from core.web_agent import BarrierBuster, VoiceCollaboration
        buster = BarrierBuster()
        # BarrierBuster uses playwright page directly
        resolved = buster._try_cookie_accept(page)
        resolved = resolved or buster._try_popup_dismiss(page)
        resolved = resolved or buster._try_age_gate(page)
        if resolved:
            return "Barrier dismissed successfully. Page is now clear."
        return "No visible barrier found. Page appears clear."
    except Exception as e:
        return f"Barrier bust error: {e}"


def jarvis_narrate(params: NarrateParams) -> str:
    """Speak a brief progress update to the user using JARVIS voice.
    Use for important milestones: 'Found 3 flights', 'Filling passenger form', etc.
    Keep messages concise (under 20 words).
    """
    try:
        voice = _get_voice()
        voice.narrate(params.message)
        return f"Narrated: '{params.message}'"
    except Exception as e:
        return f"Narrate error: {e}"


# ── Tool Registry ─────────────────────────────────────────────────────────────
# browser-use 0.12.9 uses a `tools` list passed to Agent().
# Each tool needs: name, description, function, param schema

JARVIS_TOOLS = [
    {
        "name": "jarvis_ask_user",
        "description": (
            "Ask JARVIS to speak a question to the user and listen for their voice response. "
            "Use for: passwords, OTPs, login credentials, addresses, seat preferences, meal choices. "
            "Returns what the user said."
        ),
        "func": jarvis_ask_user,
        "schema": AskUserParams,
    },
    {
        "name": "jarvis_confirm",
        "description": (
            "Ask JARVIS to verbally confirm with the user before any irreversible action. "
            "ALWAYS call before: booking tickets, purchases, payments, form submissions. "
            "Returns CONFIRMED or REJECTED."
        ),
        "func": jarvis_confirm,
        "schema": ConfirmParams,
    },
    {
        "name": "jarvis_save_tactic",
        "description": (
            "Save a learned navigation rule to JARVIS persistent memory for a website domain. "
            "Call when you discover a site quirk like 'Enter redirects to wrong page' or "
            "'must click dropdown before typing city'. Remembered forever across all sessions."
        ),
        "func": jarvis_save_tactic,
        "schema": SaveTacticParams,
    },
    {
        "name": "jarvis_narrate",
        "description": (
            "Speak a short progress update to the user using JARVIS voice. "
            "Use for key milestones: found results, filled form, navigated to correct page. "
            "Keep under 20 words."
        ),
        "func": jarvis_narrate,
        "schema": NarrateParams,
    },
]
