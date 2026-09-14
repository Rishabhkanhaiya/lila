"""
narrator.py — JARVIS Conversational Narration Engine

Every action JARVIS takes should be narrated in a natural, conversational tone.
This module provides smart speech templates for every action type, barrier type,
and OS task so JARVIS sounds like a real intelligent assistant rather than a silent bot.

Usage:
    from core.narrator import narrate, barrier_narrate, task_narrate

Example output:
    "Opening the MakeMyTrip website for flight booking."
    "Sir, I have found the search bar. Entering your departure city now."
    "Sir, I have detected a sign-in wall. Attempting to use your saved session."
    "Sir, that step failed. I am trying a different approach — one moment."
"""

import threading
import random
from typing import Optional

from concurrent.futures import ThreadPoolExecutor
_narrator_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="NarratorWorker")

# ── Speech Engine ──────────────────────────────────────────────────────────────
def _speak_async(text: str):
    """Fire-and-forget speech via managed thread pool."""
    def _do():
        try:
            from core.voice import speak
            speak(text)
        except Exception as e:
            from core.jarvis_logger import log_warn
            log_warn('narrator', f'silent swallow: {e}')
            from core.jarvis_logger import log_error
            log_error("narrator", "speak_async", e)
    _narrator_executor.submit(_do)
    print(f"[🗣️ JARVIS]: {text}")


# ── Web Action Narration Templates ────────────────────────────────────────────
# For each action type, a set of phrases to rotate through for variety
_WEB_TEMPLATES = {
    "navigate": [
        "Opening {url_name} now.",
        "Navigating to {url_name}.",
        "Opening {url_name} for you.",
    ],
    "click": [
        "Clicking {desc}.",
        "Selecting {desc}.",
        "Pressing {desc}.",
    ],
    "type": [
        "Entering {value} now.",
        "Typing {value}.",
        "Filling in {value}.",
    ],
    "select": [
        "Choosing {value} from the options.",
        "Selecting {value}.",
    ],
    "scroll": [
        "Scrolling to find what we need.",
        "Scanning the page.",
    ],
    "press_key": [
        "Pressing {value} key.",
        "Confirming with {value}.",
    ],
    "extract": [
        "Reading the page information.",
        "Extracting the relevant data.",
    ],
    "wait": None,   # Silent — no narration for waits
    "ask_user": None,   # Verbatim from action value
    "confirm_user": None,  # Verbatim from action value
    "done": [
        "Mission complete. {summary}",
        "I have finished. {summary}",
        "All done. {summary}",
    ],
}

def _url_to_name(url: str) -> str:
    """Convert a URL to a human-readable name."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.replace("www.", "")
        parts = host.split(".")
        return parts[0].replace("-", " ").title() if parts else url[:30]
    except Exception as e:
        from core.jarvis_logger import log_warn
        log_warn('narrator', f'url_to_name failed: {e}')
        return url[:30]


def narrate_web_action(action_type: str, target: str = "", value: str = "",
                       description: str = "", async_speak: bool = True) -> Optional[str]:
    """
    Generate and speak a natural JARVIS commentary for a web browser action.
    Returns the text spoken, or None if silent.
    """
    templates = _WEB_TEMPLATES.get(action_type.lower())
    if templates is None:
        return None  # Silent action

    # Build the substitution context
    url_name = _url_to_name(target) if action_type == "navigate" else ""
    desc = description or value or target or action_type
    # Trim to keep speech snappy
    desc = desc[:50] if desc else action_type
    summary = value or description or ""

    text = random.choice(templates).format(
        url_name=url_name or target[:30],
        desc=desc,
        value=value[:40] if value else desc,
        summary=summary[:80],
    )

    if async_speak:
        _speak_async(text)
    else:
        try:
            from core.voice import speak
            speak(text)
        except Exception as e:
            from core.jarvis_logger import log_warn
            log_warn('narrator', f'silent swallow: {e}')
    return text


# ── Barrier Narration ─────────────────────────────────────────────────────────
_BARRIER_MESSAGES = {
    "login_wall": [
        "The website is asking me to sign in. Let me try your saved session first.",
        "Hit a sign-in page. Checking if you have a saved session.",
    ],
    "login_wall_fail": [
        "The saved session did not work. Please sign in to the website. I will wait and resume automatically.",
        "Manual login required manually. I will resume as soon as you are signed in.",
    ],
    "captcha": [
        "There is a CAPTCHA on the screen. I cannot solve it myself. Please complete it in the browser window. I will automatically detect when it is done and resume.",
        "CAPTCHA challenge presented a CAPTCHA challenge. Please solve it in the browser. I am watching and will continue automatically.",
    ],
    "popup": [
        "Popup detected on the page. Dismissing it now.",
    ],
    "network_error": [
        "Network error encountered error. I am retrying now.",
        "Page did not load properly. Attempting again.",
    ],
    "stuck": [
        "Tried several approaches and I am still stuck on this step. The browser is open. Please complete this step manually, then say continue and I will resume.",
        "Having difficulty with this step with this step. I have exhausted my automatic strategies. Please help me out in the browser, then say continue.",
    ],
    "retry": [
        "That did not work. Trying a different approach.",
        "First attempt failed. Switching strategy.",
        "Retrying with an alternative method.",
    ],
    "page_load": [
        "Page is loading. One moment.",
    ],
    "success_step": [
        "That worked. Moving to the next step.",
        "Step complete. Continuing.",
    ],
    "new_tab": [
        "New tab opened. I have switched to it.",
    ],
    "autocomplete": [
        "Selecting from the dropdown suggestions.",
    ],
}

def barrier_narrate(barrier_type: str, detail: str = "", async_speak: bool = True) -> str:
    """
    Speak a natural commentary for a barrier or system event.
    Returns the text spoken.
    """
    messages = _BARRIER_MESSAGES.get(barrier_type, [f"{barrier_type.replace('_', ' ').title()}."])
    text = random.choice(messages)
    if detail:
        # Append detail only if it adds useful info
        text = f"{text} {detail}"

    if async_speak:
        _speak_async(text)
    else:
        try:
            from core.voice import speak
            speak(text)
        except Exception as e:
            from core.jarvis_logger import log_warn
            log_warn('narrator', f'silent swallow: {e}')
    return text


# ── OS Task / Hands Narration ─────────────────────────────────────────────────
_TASK_MESSAGES = {
    "start": [
        "Starting task now.",
        "Bilkul, on it. Beginning execution.",
        "Initiating sequence.",
    ],
    "step_start": [
        "Executing step {n} now.",
        "Step {n} in progress.",
    ],
    "step_success": [
        "Step {n} complete. Moving forward.",
        "Step {n} done.",
    ],
    "step_fail": [
        "Step {n} encountered an error. I am self-healing and trying again.",
        "Step {n} failed. Switching to an alternative approach.",
    ],
    "complete": [
        "Mission accomplished. All steps are complete.",
        "I have finished. The task is done.",
        "All done. The sequence completed successfully.",
    ],
    "abort": [
        "Too many failures in a row in a row. I am stopping to prevent any damage. Please review the situation.",
        "Task could not be completed after several attempts. Please check what happened.",
    ],
    "self_healing": [
        "Error detected. Activating self-healing protocol.",
        "Script crashed. I am re-writing it to fix the issue.",
    ],
}

def task_narrate(event: str, n: int = 0, detail: str = "", async_speak: bool = True) -> str:
    """
    Speak commentary for an OS-level task event (hands.py, agentic loop).
    Returns the text spoken.
    """
    messages = _TASK_MESSAGES.get(event, [f"{event.title()}."])
    text = random.choice(messages).format(n=n)
    if detail:
        text = f"{text} {detail[:60]}"

    if async_speak:
        _speak_async(text)
    else:
        try:
            from core.voice import speak
            speak(text)
        except Exception as e:
            from core.jarvis_logger import log_warn
            log_warn('narrator', f'silent swallow: {e}')
    return text


# ── Mission Intent Narration (Router-level) ───────────────────────────────────
_INTENT_OPENINGS = {
    "WEB_AGENT": [
        "I will handle that autonomously. Opening the browser now.",
        "On it. Launching the web agent to complete this for you.",
        "Navigating to the right website and take care of this.",
    ],
    "SYSTEM_ACTION": [
        "Executing on your system now.",
        "Running command now.",
        "Executing now.",
    ],
    "WEB_FORGE": [
        "Building that now. This will take a moment.",
        "Generating code. Stand by.",
    ],
    "DEEP_RESEARCH": [
        "Initiating deep research. Compiling report now.",
        "Searching multiple sources, give me a moment.",
    ],
    "DATA_ANALYTICS": [
        "Generating visualization now.",
        "Processing data and building chart.",
    ],
    "CASUAL_CHAT": None,  # No pre-narration for casual chat
    "AUTO_CODER": [
        "Writing code now.",
        "Generating solution. Stand by.",
    ],
    "3D_FORGE": [
        "Generating 3D model, one moment.",
    ],
    "WIN_FAST_COMMAND": None,  # ⚡ No pre-narration for turbo commands — speed is the point
    "FINANCE_ENGINE": [
        "Checking markets now.",
        "Pulling live financial data.",
        "Accessing market data.",
    ],
    "DREAM_MODE": [
        "Initiating dream session in the background.",
        "Entering dream mode. Stand by for discoveries.",
    ],
    "OMNI_SYNTHESIS": [
        "Running sixth-sense synthesis on current signals.",
    ],
    "PROACTIVE": [
        "Checking proactive event history.",
    ],
}

def intent_narrate(intent: str, goal: str = "", async_speak: bool = True) -> Optional[str]:
    """
    Speak a natural opening statement when JARVIS begins a routed task.
    Returns text spoken or None if silent intent.
    """
    messages = _INTENT_OPENINGS.get(intent)
    if not messages:
        return None

    text = random.choice(messages)

    if async_speak:
        _speak_async(text)
    else:
        try:
            from core.voice import speak
            speak(text)
        except Exception as e:
            from core.jarvis_logger import log_warn
            log_warn('narrator', f'silent swallow: {e}')
    return text
