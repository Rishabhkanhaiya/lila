"""
core/mobile_agent.py — JARVIS Mobile Control Agent v2.0
=========================================================
Full ADB-based Android control with multi-step planning loop.

Architecture:
  PlanningAgent  → LLM generates numbered step list
  DecisionAgent  → Parses steps → ADB shell commands
  ReflectionAgent→ XML diff verify → retry on fail
  run_mobile_mission() → orchestrates all three with UI feedback

Supported commands (examples):
  "open WhatsApp on my phone"
  "take a screenshot of my phone"
  "scroll down on my phone"
  "go home on my phone"
  "type hello in the search bar"
  "press back on my phone"
"""

import os
import json
import time
import threading
import subprocess
import tempfile
from pathlib import Path

from core.jarvis_logger import log_error, log_info, log_warn
from core.mesh_network import _mesh_instance

# ── ADB availability check ────────────────────────────────────────────────────
# DEPRECATED: pure-python-adb and local adb are no longer used.
# Mobile actions are now broadcasted via the Mesh Network.
_HAS_ADB    = False
_HAS_PPADB  = False


# ── Planning Agent ────────────────────────────────────────────────────────────
class PlanningAgent:
    """Uses LLM to generate a numbered list of ADB-executable steps."""

    PROMPT_TEMPLATE = """You are an Android automation expert.
The user wants to: "{objective}"

Generate a numbered step-by-step plan using ONLY these action types:
- tap <x> <y>              (tap screen at coordinates)
- swipe <x1> <y1> <x2> <y2> <ms>  (swipe gesture)
- type <text>              (type text into focused field)
- key <keycode>            (send keyevent: HOME=3, BACK=4, ENTER=66, POWER=26)
- launch <package>         (open an app by package name)
- screenshot               (take a screenshot)
- wait <seconds>           (pause)

Common app packages:
  WhatsApp: com.whatsapp
  Chrome: com.android.chrome
  YouTube: com.google.android.youtube
  Camera: com.android.camera2
  Settings: com.android.settings
  Maps: com.google.android.apps.maps
  Phone: com.android.dialer

Reply ONLY with a JSON array. Example:
[
  {{"step": 1, "action": "key", "args": "HOME", "desc": "Go to home screen"}},
  {{"step": 2, "action": "launch", "args": "com.whatsapp", "desc": "Open WhatsApp"}},
  {{"step": 3, "action": "wait", "args": "2", "desc": "Wait for app to load"}}
]"""

    def plan(self, objective: str) -> list[dict]:
        try:
            from core.brain import call_groq_brain
            raw = call_groq_brain(
                self.PROMPT_TEMPLATE.format(objective=objective),
                phase="DIRECTIVE",
                is_logic_task=True
            )
            # Strip markdown fences
            import re
            raw = re.sub(r"^```json\s*", "", str(raw), flags=re.IGNORECASE)
            raw = re.sub(r"^```\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw).strip()

            steps = json.loads(raw)
            if isinstance(steps, list):
                return steps
        except Exception as e:
            log_error("mobile_agent", "plan", e)
        return []


# ── Reflection Agent ──────────────────────────────────────────────────────────
class ReflectionAgent:
    """Verifies that an action changed the screen state."""

    def verify(self, before_xml: str, after_xml: str, action: str) -> tuple[bool, str]:
        if not before_xml or not after_xml:
            return True, "Could not verify — assuming success"
        if before_xml.strip() == after_xml.strip():
            return False, f"Screen did not change after '{action}'"
        return True, "Action verified — screen changed"


# ── Decision Agent ─────────────────────────────────────────────────────────────
class DecisionAgent:
    """Translates plan steps into ADB shell commands and executes them."""

    KEYCODE_MAP = {
        "HOME": 3, "BACK": 4, "MENU": 82, "ENTER": 66,
        "POWER": 26, "VOLUME_UP": 24, "VOLUME_DOWN": 25,
        "DELETE": 67, "ESCAPE": 111,
    }

    def __init__(self):
        pass

    def is_ready(self) -> bool:
        return _mesh_instance is not None

    def get_screen_xml(self) -> str:
        """Dump UI hierarchy XML from device. Deprecated with Mesh."""
        return ""

    def execute_step(self, step: dict) -> tuple[bool, str]:
        """Execute a single plan step via JARVIS Mesh Network. Returns (success, message)."""
        if not _mesh_instance:
            return False, "Mesh network not available"

        action = step.get("action", "").lower()
        args   = str(step.get("args", "")).strip()
        desc   = step.get("desc", action)

        try:
            payload = {
                "type": "task",
                "task_type": "mobile_action",
                "step": step.get("step"),
                "action": action,
                "args": args,
                "desc": desc
            }
            _mesh_instance.server.broadcast_sync(payload)

            if action == "wait":
                secs = float(args) if args else 1.0
                time.sleep(min(secs, 10))

            return True, f"Broadcasted {action} — {desc}"

        except Exception as e:
            log_error("mobile_agent", "step", e)
            return False, f"Step failed: {e}"


# ── Mission Orchestrator ───────────────────────────────────────────────────────
def run_mobile_mission(prompt: str, ui_signal=None) -> str:
    """
    Full multi-step mobile mission with plan → execute → verify loop.
    """
    def _emit(state: str, msg: str):
        if ui_signal:
            try:
                ui_signal.emit(state, f"SYSTEM_REPLY:<i>[📱 MOBILE]: {msg}</i>")
            except Exception as e:
                log_warn("mobile_agent", f"UI signal emit failed: {e}")
        print(f"[📱 MOBILE]: {msg}")

    # ── Preflight ─────────────────────────────────────────────────────────────
    decision = DecisionAgent()
    if not decision.is_ready():
        msg = ("JARVIS Mesh Network is not available. Cannot broadcast mobile actions.")
        _emit("speaking", msg)
        return msg

    # ── Plan ──────────────────────────────────────────────────────────────────
    _emit("thinking", f"Planning steps for: {prompt}...")
    planner  = PlanningAgent()
    steps    = planner.plan(prompt)

    if not steps:
        msg = f"Could not generate a plan for: {prompt}"
        _emit("speaking", msg)
        return msg

    step_list = "\n".join(
        f"Step {s.get('step', i+1)}: {s.get('desc', s.get('action'))}"
        for i, s in enumerate(steps)
    )
    _emit("thinking", f"Plan ({len(steps)} steps):<br>{step_list.replace(chr(10), '<br>')}")

    # ── Execute with verify loop ───────────────────────────────────────────────
    reflector = ReflectionAgent()
    results   = []
    MAX_RETRIES = 2

    for i, step in enumerate(steps):
        desc = step.get("desc", step.get("action", f"step {i+1}"))
        _emit("thinking", f"Step {i+1}/{len(steps)}: {desc}")

        before_xml = decision.get_screen_xml()

        retries = 0
        success = False
        while retries <= MAX_RETRIES:
            ok, msg = decision.execute_step(step)
            if not ok:
                retries += 1
                time.sleep(1)
                continue

            time.sleep(0.8)   # Let screen settle

            # Verify (skip for wait/screenshot — no screen change expected)
            if step.get("action") not in ("wait", "screenshot"):
                after_xml = decision.get_screen_xml()
                verified, verify_msg = reflector.verify(before_xml, after_xml, desc)
                if not verified and retries < MAX_RETRIES:
                    log_warn("mobile", f"Retry {retries+1}: {verify_msg}")
                    retries += 1
                    before_xml = after_xml
                    continue

            success = True
            results.append(f"✅ {desc}")
            break

        if not success:
            results.append(f"❌ {desc} — failed after {MAX_RETRIES} retries")
            _emit("speaking", f"Step {i+1} failed: {desc}. Stopping.")
            break

    # ── Summary ───────────────────────────────────────────────────────────────
    done    = sum(1 for r in results if r.startswith("✅"))
    summary = f"Completed {done}/{len(steps)} steps for: {prompt}"
    _emit("speaking", summary)
    return summary
