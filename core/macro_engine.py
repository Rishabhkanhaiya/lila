"""
core/macro_engine.py — JARVIS Macro / Sequence Execution Engine
================================================================
Loads named command sequences from data/macros.json.
One spoken phrase fires all commands in order with optional delays.

Voice usage:
  "Jarvis, run my morning routine"
  "Jarvis, execute the work mode macro"
  "Jarvis, create a macro called night mode"
  "Jarvis, list my macros"

macros.json format:
{
  "morning routine": {
    "description": "Start the work day",
    "steps": [
      {"command": "open chrome", "delay": 1.0},
      {"command": "open vs code", "delay": 0.5},
      {"command": "set volume 50", "delay": 0.0}
    ]
  }
}
"""

import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor
import os
import tempfile
from pathlib import Path
from core.jarvis_logger import log_error, log_warn, log_info

_macro_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="MacroWorker")

MACROS_PATH = Path(os.path.dirname(os.path.dirname(__file__))) / "data" / "macros.json"
MACROS_PATH.parent.mkdir(parents=True, exist_ok=True)

_DEFAULT_MACROS = {
    "morning routine": {
        "description": "Start the work day",
        "steps": [
            {"command": "open chrome", "delay": 1.5},
            {"command": "set volume 60", "delay": 0.5},
            {"command": "take screenshot", "delay": 0.0}
        ]
    },
    "focus mode": {
        "description": "Enter deep work — mute notifications, open VS Code",
        "steps": [
            {"command": "mute", "delay": 0.5},
            {"command": "open vs code", "delay": 1.5}
        ]
    },
    "night mode": {
        "description": "Wind down — lower volume, close apps",
        "steps": [
            {"command": "set volume 20", "delay": 0.5},
            {"command": "close chrome", "delay": 0.5}
        ]
    }
}


def _load_macros() -> dict:
    if MACROS_PATH.exists():
        try:
            return json.loads(MACROS_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            log_warn("macro_engine", f"Failed to load macros.json: {e}")
    return dict(_DEFAULT_MACROS)


def _save_macros(macros: dict) -> None:
    try:
        content = json.dumps(macros, indent=2, ensure_ascii=False)
        fd, tmp_path = tempfile.mkstemp(dir=str(MACROS_PATH.parent), prefix="macros_tmp_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, str(MACROS_PATH))
    except Exception as e:
        log_error("macro_engine", "_save_macros", e)


# Ensure the file exists with defaults
if not MACROS_PATH.exists():
    _save_macros(_DEFAULT_MACROS)


def list_macros() -> str:
    """Returns a human-readable list of all available macros."""
    macros = _load_macros()
    if not macros:
        return "Abhi koi macros saved nahi hain, Rishabh."
    lines = ["Here are your saved macros, Rishabh:"]
    for name, data in macros.items():
        steps = len(data.get("steps", []))
        desc  = data.get("description", "")
        lines.append(f"  • {name} — {desc} ({steps} steps)")
    return "\n".join(lines)


def get_macro(name: str) -> dict | None:
    """Returns the macro dict for a given name (case-insensitive fuzzy match)."""
    macros = _load_macros()
    name_lower = name.lower().strip()
    # Exact match
    if name_lower in macros:
        return macros[name_lower]
    # Partial match
    for key in macros:
        if name_lower in key or key in name_lower:
            return macros[key]
    return None


def save_macro(name: str, steps: list[dict], description: str = "") -> str:
    """Saves a new macro to macros.json. Returns confirmation message."""
    macros = _load_macros()
    macros[name.lower().strip()] = {
        "description": description,
        "steps": steps
    }
    _save_macros(macros)
    return f"Macro '{name}' saved with {len(steps)} steps, Rishabh."


def delete_macro(name: str) -> str:
    """Deletes a macro from macros.json."""
    macros = _load_macros()
    name_lower = name.lower().strip()
    if name_lower in macros:
        del macros[name_lower]
        _save_macros(macros)
        return f"Macro '{name}' deleted, Rishabh."
    return f"No macro named '{name}' found, Rishabh."


def run_macro(name: str, ui_signal=None) -> str:
    """
    Executes all steps of a macro in a background thread.
    Each step is run as a JARVIS fast command or forwarded to the AI pipeline.
    Returns a status message immediately; execution continues in background.
    """
    macro = get_macro(name)
    if macro is None:
        return f"No macro named '{name}' found. Use 'list my macros' to see available macros."

    steps = macro.get("steps", [])
    if not steps:
        return f"Macro '{name}' has no steps."

    def _execute_steps():
        log_info("macro_engine", f"Running macro '{name}' — {len(steps)} steps")
        from core.win_fast_voice import try_fast_command

        for i, step in enumerate(steps):
            cmd     = step.get("command", "").strip()
            delay   = float(step.get("delay", 0.5))

            if ui_signal:
                ui_signal.emit("thinking", f"SYSTEM_REPLY:<i>[⚡ MACRO]: Step {i+1}/{len(steps)} — {cmd}</i>")

            print(f"[⚡ MACRO '{name}']: Step {i+1}/{len(steps)} — {cmd}")

            try:
                handled, result = try_fast_command(cmd)
                if not handled:
                    # Fall through to AI pipeline for complex steps
                    from core.master_router import process_user_input
                    process_user_input(cmd, ui_signal)
            except Exception as e:
                log_error("macro_engine", f"step {i+1}", e)

            if delay > 0:
                time.sleep(delay)

        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:✅ Macro '{name}' completed ({len(steps)} steps).")

        from core.voice import speak
        speak(f"Macro {name} completed.")

    _macro_executor.submit(_execute_steps)

    return f"Running macro '{name}' — {len(steps)} steps in the background, Rishabh."


def get_all_macros() -> dict:
    """Returns all macros as dict (for UI rendering)."""
    return _load_macros()
