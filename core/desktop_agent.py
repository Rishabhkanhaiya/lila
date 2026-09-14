"""
desktop_agent.py — OS-Level Desktop UI Automation Engine (Windows UIA + Win32)
==============================================================================
Empowers Lila/JARVIS to inspect and interact with native Windows applications
(File Explorer, Task Manager, Word, VS Code, Settings, Discord, etc.)
with accessibility tree parsing, control references (@w1, @w2), and coordinate clicks.
"""

import time
import os
from typing import Optional, List, Dict, Any

from core.jarvis_logger import log_info, log_warn, log_error
from core.win_os_agent import get_open_windows, find_window, force_foreground_window, attach_to_user_desktop

try:
    import uiautomation as auto
    HAS_UIA = True
except ImportError:
    HAS_UIA = False

try:
    import pyautogui
    import pyperclip
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False


class DesktopAgent:
    """Autonomous OS-level desktop automation agent."""

    def __init__(self):
        self._last_snapshot: Dict[str, Any] = {}

    def list_windows(self) -> List[Dict[str, Any]]:
        """Returns all visible desktop windows."""
        attach_to_user_desktop()
        return get_open_windows()

    def inspect_window(self, window_query: str = "", max_controls: int = 35) -> Dict[str, Any]:
        """
        Inspects an active window and extracts actionable UI controls with @w1, @w2 refs.
        """
        attach_to_user_desktop()
        target = None
        if window_query:
            target = find_window(window_query)
        else:
            wins = get_open_windows()
            if wins:
                target = wins[0]

        if not target:
            return {
                "error": f"No active window found matching '{window_query}'.",
                "summary": "No matching window found.",
                "controls": [],
                "count": 0
            }

        hwnd = target["hwnd"]
        title = target["title"]
        proc_name = target["proc_name"]

        # Bring to foreground so controls are active and visible
        force_foreground_window(hwnd)
        time.sleep(0.2)

        if not HAS_UIA:
            return {
                "window": title,
                "proc_name": proc_name,
                "summary": f"Window '{title}' focused, but UIAutomation library is unavailable.",
                "controls": [],
                "count": 0
            }

        try:
            root = auto.ControlFromHandle(hwnd)
            elements: List[Dict[str, Any]] = []

            def walk(ctrl, depth=0):
                if depth > 4 or len(elements) >= max_controls:
                    return
                try:
                    children = ctrl.GetChildren()
                except Exception:
                    children = []

                for child in children:
                    try:
                        t_name = child.ControlTypeName.replace("Control", "")
                        name = (child.Name or "").strip()
                        aid = child.AutomationId or ""
                        
                        # Filter to interactive controls
                        if any(k in t_name for k in [
                            "Button", "Edit", "MenuItem", "TabItem", "CheckBox",
                            "RadioButton", "ListItem", "ComboBox", "Hyperlink", "Pane", "TreeItem"
                        ]):
                            if name or aid:
                                rect = child.BoundingRectangle
                                ref_id = f"@w{len(elements) + 1}"
                                elements.append({
                                    "ref": ref_id,
                                    "tag": t_name,
                                    "name": name,
                                    "id": aid,
                                    "rect": (rect.left, rect.top, rect.right, rect.bottom) if rect else None,
                                    "_ctrl": child
                                })
                        walk(child, depth + 1)
                    except Exception:
                        continue

            walk(root)

            summary_lines = []
            for el in elements:
                desc = f"{el['ref']}: [{el['tag']}]"
                if el['name']:
                    clean_n = el['name'].replace('\n', ' ')[:60]
                    desc += f' "{clean_n}"'
                if el['id'] and el['id'] != el['name']:
                    desc += f' (id: {el["id"]})'
                summary_lines.append(desc)

            summary_text = "\n".join(summary_lines) if summary_lines else "No interactive controls discovered."

            snapshot = {
                "window": title,
                "hwnd": hwnd,
                "proc_name": proc_name,
                "controls": elements,
                "summary": summary_text,
                "count": len(elements)
            }
            self._last_snapshot = snapshot
            return snapshot

        except Exception as e:
            log_error("desktop_agent", "inspect_window", e)
            return {
                "window": title,
                "error": str(e),
                "summary": f"Error inspecting window controls: {e}",
                "controls": [],
                "count": 0
            }

    def act_on_element(self, window_query: str = "", ref: str = "", action: str = "click", value: str = "") -> str:
        """
        Performs an action ('click', 'type', 'press', 'switch') on a targeted UI element.
        """
        act = action.strip().lower()

        # If switching window
        if act == "switch":
            target = find_window(window_query)
            if target:
                force_foreground_window(target["hwnd"])
                return f"Switched focus to '{target['title']}'."
            return f"Window '{window_query}' not found."

        # Ensure we have a fresh snapshot if window specified
        if window_query or not self._last_snapshot.get("controls"):
            snap = self.inspect_window(window_query)
            if snap.get("error"):
                return snap["error"]

        controls = self._last_snapshot.get("controls", [])
        target_el = None

        clean_ref = ref.strip().lower()
        if clean_ref.startswith("@w"):
            for el in controls:
                if el["ref"].lower() == clean_ref:
                    target_el = el
                    break

        if not target_el and clean_ref:
            # Match by name or automation id
            for el in controls:
                if clean_ref in el["name"].lower() or clean_ref in el["id"].lower():
                    target_el = el
                    break

        if not target_el:
            return f"Control ref '{ref}' not found in window '{self._last_snapshot.get('window')}'."

        ctrl_obj = target_el.get("_ctrl")
        rect = target_el.get("rect")

        if act == "click":
            clicked = False
            # Method 1: UIA Native Click / Invoke
            if ctrl_obj:
                try:
                    ctrl_obj.Click()
                    clicked = True
                except Exception:
                    pass

            # Method 2: Coordinate click via PyAutoGUI
            if not clicked and rect and HAS_PYAUTOGUI:
                cx = (rect[0] + rect[2]) // 2
                cy = (rect[1] + rect[3]) // 2
                pyautogui.click(cx, cy)
                clicked = True

            return f"Clicked {target_el['ref']} [{target_el['tag']}] '{target_el['name']}'." if clicked else f"Failed to click {ref}."

        elif act in ["type", "fill", "write"]:
            if ctrl_obj:
                try:
                    ctrl_obj.Click()
                    time.sleep(0.1)
                except Exception:
                    pass
            if rect and HAS_PYAUTOGUI:
                cx = (rect[0] + rect[2]) // 2
                cy = (rect[1] + rect[3]) // 2
                pyautogui.click(cx, cy)
                time.sleep(0.1)

            if HAS_PYAUTOGUI and HAS_UIA:
                # Select all and replace with value using clipboard injection for 100% Unicode safety
                pyautogui.hotkey("ctrl", "a")
                pyperclip.copy(value)
                pyautogui.hotkey("ctrl", "v")
                return f"Typed '{value}' into {target_el['ref']} [{target_el['tag']}]."
            return f"Could not type into {ref}."

        elif act == "press":
            key = (value or "enter").lower()
            if HAS_PYAUTOGUI:
                pyautogui.press(key)
                return f"Pressed key '{key}'."
            return "Keyboard emulation not available."

        return f"Unsupported action '{action}'."


# Global singleton
_desktop_agent = None

def get_desktop_agent() -> DesktopAgent:
    global _desktop_agent
    if _desktop_agent is None:
        _desktop_agent = DesktopAgent()
    return _desktop_agent


def desktop_act(action: str, window: str = "", ref: str = "", text: str = "") -> str:
    """
    Universal Desktop Computer-Use Tool for Gemini Live.
    """
    agent = get_desktop_agent()
    act = (action or "inspect").strip().lower()

    if act in ["list", "list_windows", "windows"]:
        wins = agent.list_windows()
        if not wins:
            return "No visible desktop windows found."
        lines = [f"• [{w['proc_name']}] '{w['title']}' (HWND: {w['hwnd']})" for w in wins[:12]]
        return "Active Desktop Windows:\n" + "\n".join(lines)

    elif act in ["inspect", "view", "read"]:
        snap = agent.inspect_window(window)
        if snap.get("error"):
            return snap["error"]
        return f"Interactive UI Controls for '{snap['window']}' ({snap['count']} controls):\n{snap['summary']}"

    elif act in ["click", "type", "fill", "press", "switch"]:
        return agent.act_on_element(window_query=window, ref=ref, action=act, value=text)

    else:
        return f"Unknown desktop action '{action}'. Supported: 'list', 'inspect', 'click', 'type', 'switch'."
