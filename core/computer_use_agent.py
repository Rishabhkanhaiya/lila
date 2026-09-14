"""
computer_use_agent.py — JARVIS Computer Use Agent (TIER-1 GAP 3)
=================================================================
Real-time vision + action loop. JARVIS can SEE and ACT on the screen
autonomously — click, type, scroll, extract — in a continuous loop.

This is what makes ChatGPT Operator and Claude Computer Use Tier-1.
JARVIS now has the same capability, locally, using:
  - mss  (ultra-fast screenshot, ~5ms per frame)
  - PyAutoGUI (mouse + keyboard control)
  - faster_whisper already in ears.py (shared model)
  - Gemini Vision (analyze screenshot + decide next action)

Usage:
    from core.computer_use_agent import run_computer_task
    run_computer_task("Open Chrome and search for Python tutorials")
"""

import os
import time
import json
import base64
import threading
import tempfile
from typing import Optional
from core.jarvis_logger import log_error, log_warn, log_info

# ── Dependency checks ──────────────────────────────────────────────────────────
try:
    import pyautogui
    pyautogui.FAILSAFE = False  # Prevent FailSafeException when virtual/remote cursor is parked at (0, 0)
    pyautogui.PAUSE    = 0.05   # 50ms between actions (safe + fast)
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False
    log_warn("computer_use", "pyautogui not installed. Run: pip install pyautogui")

try:
    import mss
    import mss.tools
    HAS_MSS = True
except ImportError:
    HAS_MSS = False
    # Fallback to PIL screenshot
    try:
        from PIL import ImageGrab
        HAS_PIL_GRAB = True
    except ImportError:
        HAS_PIL_GRAB = False

# ── Screenshot helper ──────────────────────────────────────────────────────────

def _take_screenshot_b64() -> Optional[str]:
    """Take a screenshot in-memory as a compressed JPEG with zero disk writes."""
    try:
        from core.eyes import capture_screen_jpeg_bytes
        jpeg_bytes = capture_screen_jpeg_bytes(quality=80, max_dim=1920)
        return base64.b64encode(jpeg_bytes).decode("utf-8")
    except Exception as e:
        log_error("computer_use", "_take_screenshot_b64", e)
        return None


# ── Action executor ────────────────────────────────────────────────────────────

KEY_MAP = {
    "windows": "win",
    "win_key": "win",
    "cmd": "win",
    "super": "win",
    "control": "ctrl",
    "escape": "esc",
    "return": "enter",
    "back": "backspace",
    "up_arrow": "up",
    "down_arrow": "down",
    "left_arrow": "left",
    "right_arrow": "right",
}

def _execute_action(action: dict, registry: Optional[dict] = None) -> str:
    """Execute a single computer action using element_id or coordinates. Returns result description."""
    if not HAS_PYAUTOGUI:
        return "PyAutoGUI not available — install with: pip install pyautogui mss"

    pyautogui.FAILSAFE = False  # Prevent accidental corner-trigger aborts during active automation

    act = action.get("type", "")
    element_id = action.get("element_id")
    el_info = None

    if element_id is not None and registry:
        try:
            el_info = registry.get(int(element_id))
        except (ValueError, TypeError):
            el_info = None

    try:
        if act in ("click", "double_click", "right_click"):
            # 1. Try native UIA click first if control handle exists
            if el_info and el_info.get("control"):
                try:
                    ctrl = el_info["control"]
                    if act == "click":
                        ctrl.Click()
                        return f"UIA Clicked [{element_id}] '{el_info['name']}' ({el_info['type']})"
                    elif act == "double_click":
                        ctrl.DoubleClick()
                        return f"UIA Double-clicked [{element_id}] '{el_info['name']}'"
                    elif act == "right_click":
                        ctrl.RightClick()
                        return f"UIA Right-clicked [{element_id}] '{el_info['name']}'"
                except Exception as uia_ex:
                    log_warn("computer_use", f"UIA click failed, falling back to coordinates: {uia_ex}")

            # 2. Coordinate click via Set-of-Marks center or explicit (x, y)
            if el_info:
                x, y = el_info["center"]
                desc = f"[{element_id}] '{el_info['name']}' at ({x}, {y})"
            else:
                x, y = action.get("x", 0), action.get("y", 0)
                desc = f"coordinates ({x}, {y})"

            if act == "click":
                pyautogui.click(x, y)
                return f"Clicked {desc}"
            elif act == "double_click":
                pyautogui.doubleClick(x, y)
                return f"Double-clicked {desc}"
            elif act == "right_click":
                pyautogui.rightClick(x, y)
                return f"Right-clicked {desc}"

        elif act == "type":
            text = action.get("text", "")
            # If target element is specified, click to focus it first
            if el_info:
                if el_info.get("control"):
                    try:
                        el_info["control"].Click()
                        time.sleep(0.08)
                    except Exception:
                        pass
                else:
                    x, y = el_info["center"]
                    pyautogui.click(x, y)
                    time.sleep(0.08)

            from core.win_os_agent import type_text
            type_text(text)

            # If action specifies submitting/pressing enter after typing (common for search boxes)
            if action.get("press_enter") or action.get("enter") or action.get("submit"):
                time.sleep(0.15)
                pyautogui.press("enter")
                return f"Typed '{text[:40]}' and submitted (Enter)"

            return f"Typed: {text[:40]}"

        elif act == "key":
            raw_key = str(action.get("key", "")).strip().lower()
            key = KEY_MAP.get(raw_key, raw_key)
            pyautogui.press(key)
            return f"Pressed key: {key}"

        elif act == "hotkey":
            raw_keys = action.get("keys", [])
            keys = [KEY_MAP.get(str(k).strip().lower(), str(k).strip().lower()) for k in raw_keys]
            pyautogui.hotkey(*keys)
            return f"Hotkey: {'+'.join(keys)}"

        elif act == "scroll":
            x, y = action.get("x", 0), action.get("y", 0)
            amount = action.get("amount", 3)
            pyautogui.scroll(amount, x=x, y=y)
            return f"Scrolled {amount} at ({x}, {y})"

        elif act == "move":
            if el_info:
                x, y = el_info["center"]
            else:
                x, y = action.get("x", 0), action.get("y", 0)
            pyautogui.moveTo(x, y, duration=0.2)
            return f"Moved to ({x}, {y})"

        elif act == "screenshot":
            return "Screenshot captured"

        elif act == "done":
            return "TASK_COMPLETE"

        elif act == "wait":
            ms = action.get("ms", 1000)
            time.sleep(ms / 1000)
            return f"Waited {ms}ms"

        else:
            return f"Unknown action: {act}"

    except Exception as e:
        log_error("computer_use", f"execute_action_{act}", e)
        if "FailSafeException" in type(e).__name__ or "fail-safe" in str(e).lower():
            pyautogui.moveTo(500, 500)
            return f"Failsafe caught and recovered: {e}"
        return f"Action failed: {e}"


# ── CUA Live State Tracking ───────────────────────────────────────────────────

_cua_state = {
    "status": "idle",
    "task": "",
    "step": 0,
    "max_steps": 20,
    "last_action": "",
    "result": "",
    "start_time": 0.0,
    "elapsed": 0.0
}
_cua_lock = threading.Lock()

def get_current_cua_status() -> dict:
    """Return live status of the Computer Use Agent task."""
    with _cua_lock:
        st = dict(_cua_state)
    if st["status"] == "running" and st["start_time"] > 0:
        st["elapsed"] = round(time.time() - st["start_time"], 1)
    return st


# ── Vision-action planning ─────────────────────────────────────────────────────

def _plan_next_action(task: str, tagged_jpeg_bytes: bytes, history: list, element_list_str: str = "") -> dict:
    """
    Send Set-of-Marks tagged screenshot + task + history + element list to Gemini Vision.
    Returns next action dict with element_id or coordinates.
    """
    try:
        from google import genai
        from google.genai import types

        history_str = "\n".join(f"Step {i+1}: {h}" for i, h in enumerate(history[-5:]))

        prompt = f"""You are controlling a Windows computer to complete this task: "{task}"

The screen has been analyzed with Set-of-Marks visual tags. Clickable and interactive UI elements have bright red numbered badges [N].

Detected Interactive Elements on screen:
{element_list_str if element_list_str else "Visual inspection only."}

Previous steps taken:
{history_str if history_str else "None yet — this is the first step."}

Decide the SINGLE best next action to advance the task.
CRITICAL: Whenever possible, use "element_id": N corresponding to the numbered badge [N] on screen (e.g. "element_id": 3).
You can also press hotkeys, type text, scroll, or wait.
Valid keys: "win" (Windows key - DO NOT use "windows"), "ctrl", "alt", "shift", "enter", "esc", "tab", "space", "backspace", "up", "down", "left", "right".
When typing into search boxes, include "press_enter": true to automatically submit the search.

Reply with ONLY valid JSON — no markdown fences, no explanation:
{{
  "observation": "what you see on screen in one sentence",
  "reasoning": "why this is the right next action",
  "action": {{
    "type": "click|double_click|right_click|type|key|hotkey|scroll|move|wait|done",
    "element_id": 1,
    "x": 100,
    "y": 200,
    "text": "text to type",
    "press_enter": true,
    "key": "enter",
    "keys": ["ctrl", "e"],
    "amount": 3,
    "ms": 1000
  }}
}}

If the task is complete, use action type "done"."""

        keys = [os.environ.get("GEMINI_API_KEY_2", ""), os.environ.get("GEMINI_API_KEY", "")]
        keys = [k for k in keys if k and len(k.strip()) > 10]
        if not keys:
            log_warn("computer_use", "No Gemini API key found for vision planning")
            return {"type": "done"}

        part = types.Part.from_bytes(data=tagged_jpeg_bytes, mime_type="image/jpeg") if tagged_jpeg_bytes else None

        models_to_try = [
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
            "gemini-flash-latest",
            "gemini-2.5-flash"
        ]

        raw_response = ""
        for key in keys:
            client = genai.Client(api_key=key)
            for m in models_to_try:
                try:
                    contents = [prompt]
                    if part:
                        contents.append(part)
                    resp = client.models.generate_content(
                        model=m,
                        contents=contents,
                    )
                    raw_response = resp.text or ""
                    if raw_response:
                        break
                except Exception as ex:
                    log_warn("computer_use", f"Model {m} failed: {ex}")
                    continue
            if raw_response:
                break

        # Parse JSON
        import re
        m = re.search(r'\{.*\}', raw_response, re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
            return parsed.get("action", {"type": "done"})

    except Exception as e:
        log_error("computer_use", "_plan_next_action", e)

    return {"type": "done"}


# ── Main Computer Use Loop ─────────────────────────────────────────────────────

def run_computer_task(
    task: str,
    max_steps: int = 20,
    ui_callback=None,
    on_step: Optional[callable] = None
) -> str:
    """
    TIER-1 GAP 3: Execute a computer task autonomously.
    JARVIS sees the screen, plans actions, executes them, loops until done.
    """
    global _cua_state
    if not HAS_PYAUTOGUI:
        msg = "Computer Use requires PyAutoGUI. Run: pip install pyautogui mss"
        if ui_callback:
            ui_callback("speaking", f"SYSTEM_REPLY:{msg}")
        return msg

    log_info("computer_use", f"[CUA] Starting task: {task}")
    with _cua_lock:
        _cua_state["status"] = "running"
        _cua_state["task"] = task
        _cua_state["step"] = 0
        _cua_state["max_steps"] = max_steps
        _cua_state["last_action"] = "Starting task"
        _cua_state["result"] = ""
        _cua_state["start_time"] = time.time()

    if ui_callback:
        ui_callback("thinking", f"SYSTEM_REPLY:<i>[🖥️ COMPUTER USE]: Starting → {task}</i>")

    history = []
    step = 0
    result_summary = ""

    task_lower = task.lower()

    # 1. Direct Settings Resolution (handles "search storage in settings", "open sound settings", etc.)
    if any(w in task_lower for w in ["setting", "settings"]):
        from core.win_os_agent import open_or_search_settings
        res = open_or_search_settings(task)
        log_info("computer_use", f"[CUA] Handled Settings intent directly: {res}")
        if ui_callback:
            ui_callback("speaking", f"SYSTEM_REPLY:✅ {res}")
        try:
            from core.state_bridge import broadcast_state
            broadcast_state(mood="excited", caption=res, speaking=True)
            from core.voice import speak
            speak(f"Haan babe! {res}")
        except Exception:
            pass
        with _cua_lock:
            _cua_state["status"] = "completed"
            _cua_state["result"] = res
            _cua_state["elapsed"] = round(time.time() - _cua_state["start_time"], 1)
        return res

    # 2. Desktop App Bootstrap & Focus (Notepad, Calculator, Paint, Explorer, etc.)
    APP_BOOTSTRAP = {
        "notepad": "notepad",
        "calculator": "calculator",
        "calc": "calculator",
        "paint": "paint",
        "explorer": "explorer",
        "task manager": "task manager",
        "terminal": "terminal",
        "cmd": "cmd",
        "brave": "brave",
        "chrome": "chrome",
    }
    for app_k, app_n in APP_BOOTSTRAP.items():
        if app_k in task_lower and any(w in task_lower for w in ["open", "launch", "start", "switch", "kholo", "run"]):
            try:
                from core.win_os_agent import open_app
                open_app(app_n)
                time.sleep(0.8)
                history.append(f"bootstrap: Launched/Focused {app_n.title()}")
                log_info("computer_use", f"[CUA] Bootstrapped app: {app_n}")
                break
            except Exception as ex:
                log_warn("computer_use", f"App bootstrap failed: {ex}")

    from core.cua_grounding import build_som_tagged_screen, compute_visual_delta

    while step < max_steps:
        step += 1

        # 1. Capture screen & build Set-of-Marks tagged overlay + element registry
        try:
            tagged_bytes, registry, element_desc = build_som_tagged_screen()
        except Exception as e:
            log_warn("computer_use", f"Set-of-Marks capture failed: {e}")
            break

        # 2. Plan next action with Gemini Vision (guided by numbered tags [N])
        if ui_callback:
            ui_callback("thinking", f"SYSTEM_REPLY:<i>[🤔 CUA Step {step}]: Analyzing screen & {len(registry)} interactive elements...</i>")

        action = _plan_next_action(task, tagged_bytes, history, element_desc)
        action_type = action.get("type", "done")

        # 3. Check if done
        if action_type == "done":
            result_summary = f"Task completed in {step} steps: {task}"
            log_info("computer_use", f"[CUA] Done after {step} steps")
            break

        # 4. Execute action (via native UIA control or Set-of-Marks center coordinates)
        action_result = _execute_action(action, registry)

        with _cua_lock:
            _cua_state["step"] = step
            _cua_state["last_action"] = f"{action_type}: {action_result}"

        if "FAILSAFE" in action_result or "fail-safe" in action_result.lower():
            log_warn("computer_use", f"[CUA] PyAutoGUI failsafe triggered at step {step}. Aborting task immediately.")
            result_summary = f"Task aborted by failsafe at step {step}: {task}"
            with _cua_lock:
                _cua_state["status"] = "aborted"
                _cua_state["result"] = result_summary
            break

        # 5. Visual Delta State Verification (check if screen state actually changed)
        time.sleep(0.4)
        try:
            post_bytes, _, _ = build_som_tagged_screen()
            delta = compute_visual_delta(tagged_bytes, post_bytes)
            if delta < 0.005 and action_type in ("click", "double_click", "type", "key"):
                step_log = f"{action_type}: {action_result} (Notice: minimal visual change {delta:.2%}; target may need alternative interaction)"
            else:
                step_log = f"{action_type}: {action_result} (Verified visual change {delta:.1%})"
        except Exception:
            step_log = f"{action_type}: {action_result}"

        history.append(step_log)

        if on_step:
            on_step(step, action, action_result)

        if ui_callback:
            ui_callback("thinking", f"SYSTEM_REPLY:<i>[⚡ CUA Step {step}]: {action_result}</i>")

        log_info("computer_use", f"[CUA] Step {step}: {step_log}")

    if not result_summary:
        result_summary = f"Computer task executed ({step} steps): {task}"

    with _cua_lock:
        _cua_state["status"] = "completed"
        _cua_state["result"] = result_summary
        _cua_state["elapsed"] = round(time.time() - _cua_state["start_time"], 1)

    if ui_callback:
        ui_callback("speaking", f"SYSTEM_REPLY:✅ {result_summary}")

    # Proactively notify Astra & UI that task finished so Astra announces it
    try:
        from live_voice import notify_live_assistant
        notify_live_assistant("Computer Use Agent", result_summary, speak_alert=True)
    except Exception as ne:
        log_warn("computer_use", f"notify_live_assistant failed: {ne}")

    return result_summary


def run_computer_task_bg(task: str, ui_callback=None) -> threading.Thread:
    """Run computer task in background thread — non-blocking."""
    t = threading.Thread(
        target=run_computer_task,
        args=(task,),
        kwargs={"ui_callback": ui_callback},
        daemon=True,
        name=f"CUA-{task[:20]}"
    )
    t.start()
    return t
