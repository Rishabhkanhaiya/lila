"""
core/command_decomposer.py — Compound Command Decomposer
=========================================================
Breaks multi-step commands like "open notepad and write a story" into
ordered sub-commands with route labels (OS_FAST | AI_BRAIN | AUTO).

Strategy:
  Online  → LLM classifier (Gemini Flash Lite, ~200ms) — accurate
  Offline → Regex + keyword classifier — zero latency

Usage:
  from core.command_decomposer import decompose_command, is_compound

  steps = decompose_command("open notepad and write a story")
  # [
  #   {"cmd": "open notepad",            "route": "OS_FAST",  "delay_after_ms": 1800},
  #   {"cmd": "write a story about...",  "route": "AI_BRAIN", "delay_after_ms": 0}
  # ]
"""

import re
import socket
import json
import os
from core.jarvis_logger import log_error, log_info, log_warn

# ── OS-action verb set (single word) ─────────────────────────────────────────
_OS_VERBS = {
    "open", "close", "launch", "start", "run", "play", "pause", "stop",
    "mute", "unmute", "minimize", "maximize", "search", "find", "go",
    "navigate", "increase", "decrease", "set", "turn", "switch",
    "show", "hide", "shutdown", "restart", "sleep", "lock",
    "screenshot", "type", "click", "scroll", "zoom", "volume",
    "brightness", "kill", "exit", "quit",
}

# ── Patterns that only signal a compound command ──────────────────────────────
_COMPOUND_RE = re.compile(
    r"\b(and\s+then|then\s+(?:please\s+)?|after\s+(?:that|which|opening|launching|it\s+opens?)"
    r"|,\s*then|and\s+also|and\s+write|and\s+type|and\s+tell|and\s+search|and\s+play"
    r"|and\s+send|and\s+create|and\s+make|and\s+set|and\s+summarize|and\s+save"
    r"|and\s+vault|and\s+open|and\s+analyze|and\s+download)\b",
    re.IGNORECASE,
)

# ── Split points (in priority order) ─────────────────────────────────────────
_SPLIT_PATTERNS = [
    r"\s+and\s+then\s+",
    r"\s+then\s+",
    r"\s+after\s+(?:that|which|opening|launching|it\s+opens?)\s+",
    r",\s*then\s+",
    r"\s+and\s+(?:also\s+)?(?:write|type|tell|search|play|send|create|make|set|summarize|save|vault|open|analyze|download)\s+",
]

# ── LLM system prompt ─────────────────────────────────────────────────────────
_LLM_PROMPT = (
    "You are a command parser for JARVIS AI assistant.\n"
    "Analyze this user command and determine if it is a compound multi-step task.\n\n"
    'Command: "{command}"\n\n'
    "Reply ONLY with valid JSON (no markdown, no explanation):\n"
    '{{\n'
    '  "is_compound": true,\n'
    '  "steps": [\n'
    '    {{"cmd": "open notepad", "route": "OS_FAST", "delay_after_ms": 1800}},\n'
    '    {{"cmd": "write a short story about space", "route": "AI_BRAIN", "delay_after_ms": 0}}\n'
    '  ]\n'
    '}}\n\n'
    "Route values:\n"
    "  OS_FAST  → opening apps/files/websites, system controls, volume, brightness\n"
    "  AI_BRAIN → generating text, writing, answering questions, creative tasks\n\n"
    "If NOT compound, respond:\n"
    '{{"is_compound": false, "steps": [{{"cmd": "{command}", "route": "AUTO", "delay_after_ms": 0}}]}}\n\n'
    "Keep cmd strings clean and actionable. delay_after_ms: 1800 for app open, 400 otherwise."
)


# ── Internet probe ────────────────────────────────────────────────────────────
def _is_online(timeout: float = 1.0) -> bool:
    """TCP probe to Google DNS — ~1 ms round-trip when online."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(("8.8.8.8", 53))
        sock.close()
        return True
    except Exception as e:
        log_warn("command_decomposer", f"online check failed: {e}")
        return False


# ── Local route classifier ────────────────────────────────────────────────────
def _classify_local(cmd: str) -> str:
    words = cmd.lower().split()
    if not words:
        return "AUTO"
    if words[0] in _OS_VERBS:
        return "OS_FAST"
    for w in words[:3]:
        if w in _OS_VERBS:
            return "OS_FAST"
    return "AI_BRAIN"


def _open_delay(cmd: str) -> int:
    """Milliseconds to pause AFTER this step so the next step runs correctly."""
    first = cmd.lower().split()[0] if cmd.split() else ""
    if first in {"open", "launch", "start", "run"}:
        return 1800   # App needs time to load
    if first in {"play", "close", "switch", "minimize", "maximize"}:
        return 600
    return 300


# ── Offline decomposer ────────────────────────────────────────────────────────
def _offline_decompose(command: str) -> list:
    for pat in _SPLIT_PATTERNS:
        m = re.search(pat, command.strip(), flags=re.IGNORECASE)
        if m:
            s1 = command[:m.start()].strip()
            rest = command[m.end():].strip()
            words = m.group(0).strip().split()
            verb = words[-1] if words and words[-1].lower() not in {"and", "then", "also", "after", "that", "which"} else ""
            s2 = f"{verb} {rest}".strip() if verb else rest
            if s1 and s2:
                log_info("system", "system", f"[DECOMPOSER/offline] Split: '{s1}' | '{s2}'")
                return [
                    {"cmd": s1, "route": _classify_local(s1), "delay_after_ms": _open_delay(s1)},
                    {"cmd": s2, "route": _classify_local(s2), "delay_after_ms": 0},
                ]
    # Single step
    return [{"cmd": command, "route": "AUTO", "delay_after_ms": 0}]


# ── Online decomposer (LLM) ───────────────────────────────────────────────────
def _online_decompose(command: str) -> list:
    try:
        # Use first available Gemini key (same provider pool as brain.py)
        key = (
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GEMINI_API_KEY_2")
            or ""
        )
        if not key:
            log_info("system", "system", "[DECOMPOSER/online] No Gemini key — falling back to offline")
            return _offline_decompose(command)

        prompt = _LLM_PROMPT.format(command=command)
        raw = ""

        # 1. Primary: Official google.genai SDK (2026 standard)
        try:
            from google import genai
            from google.genai import types
            genai_client = genai.Client(api_key=key)
            response = genai_client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=256,
                    response_mime_type="application/json"
                )
            )
            raw = response.text.strip() if response and response.text else ""
        except Exception as ge:
            log_warn("command_decomposer", f"google.genai decomposition fallback to OpenAI client: {ge}")

        # 2. Fallback: OpenAI compatibility layer
        if not raw:
            from openai import OpenAI
            client = OpenAI(
                api_key=key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
            resp = client.chat.completions.create(
                model="gemini-3.1-flash-lite",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=256,
                timeout=4,
            )
            raw = resp.choices[0].message.content.strip()

        # Strip any accidental markdown fences
        raw = re.sub(r"^```(?:json)?\n?", "", raw).rstrip("`").strip()

        data = json.loads(raw)
        steps = data.get("steps", [])
        if steps:
            log_info("system", "system", f"[DECOMPOSER/online] {len(steps)}-step plan: {[s['cmd'] for s in steps]}")
            return steps

    except Exception as exc:
        log_error("command_decomposer", "online_decompose", exc)

    return _offline_decompose(command)


# ── Public API ────────────────────────────────────────────────────────────────
def is_compound(command: str) -> bool:
    """
    Fast pre-check: returns True if the command MIGHT be compound.
    Zero LLM calls — pure regex. Used to skip decomposition for simple commands.
    """
    return bool(_COMPOUND_RE.search(command))


def decompose_command(command: str) -> list:
    """
    Decomposes a command into an ordered list of steps.

    Returns:
        list of dicts: [{"cmd": str, "route": "OS_FAST"|"AI_BRAIN"|"AUTO",
                          "delay_after_ms": int}, ...]

    Online  → LLM (accurate, ~200ms overhead only for compound commands)
    Offline → Regex splitter (zero latency)
    """
    if not command or not command.strip():
        return []

    if _is_online():
        return _online_decompose(command)
    else:
        return _offline_decompose(command)
