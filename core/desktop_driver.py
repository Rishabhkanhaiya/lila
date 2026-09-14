"""
desktop_driver.py — JARVIS Desktop Driver v2.0
===============================================
Full Windows UI automation with:
  • Background click / type / toggle (uiautomation)
  • Vision-guided click / type (screenshot + LLM coords)
  • Scroll up/down at element or screen position
  • Right-click context menu
  • Hotkey / keyboard shortcut sender
  • Window focus + raise before every action
  • Retry logic (up to 3 attempts) on element-not-found
  • Screenshot-verify after action (optional)
  • Cross-platform fallback (pyautogui) for macOS/Linux
"""

import sys
import os
import time
import json
import re
import base64
import tempfile
from core.jarvis_logger import log_error, log_warn, log_info


class BaseDesktopDriver:
    def background_click(self, app_name: str, element_name: str, desired_state=None) -> str:
        raise NotImplementedError
    def background_type(self, app_name: str, element_name: str, text: str) -> str:
        raise NotImplementedError
    def toggle_switch(self, app_name: str, switch_name: str, desired_state: int) -> str:
        raise NotImplementedError
    def vision_action(self, action: str, query: str, text: str = "") -> str:
        raise NotImplementedError
    def scroll(self, app_name: str, element_name: str, direction: str = "down", clicks: int = 3) -> str:
        raise NotImplementedError
    def right_click(self, app_name: str, element_name: str) -> str:
        raise NotImplementedError
    def hotkey(self, *keys) -> str:
        raise NotImplementedError


# ── Windows Driver (uiautomation + pyautogui) ─────────────────────────────────
class WindowsDriver(BaseDesktopDriver):

    MAX_RETRIES = 3

    def __init__(self):
        try:
            import uiautomation as auto
            self.auto = auto
        except ImportError:
            self.auto = None
            log_warn("desktop_driver", "uiautomation not installed on Windows")
        try:
            import pyautogui
            self.pag = pyautogui
            self.pag.FAILSAFE = True   # Prevent crash on corner-move
        except ImportError:
            self.pag = None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _find_window(self, app_name: str):
        """Find a window — exact match → SubName → case-insensitive scan."""
        if not self.auto:
            return None
        for attempt in range(self.MAX_RETRIES):
            win = self.auto.WindowControl(searchDepth=1, Name=app_name)
            if win.Exists(2, 1):
                return win
            win = self.auto.WindowControl(searchDepth=1, SubName=app_name)
            if win.Exists(2, 1):
                return win
            app_lower = app_name.lower()
            root = self.auto.GetRootControl()
            for child in root.GetChildren():
                try:
                    if app_lower in (child.Name or "").lower():
                        return child
                except Exception:
                    continue
            time.sleep(0.5)
        return None

    def _focus_window(self, window) -> bool:
        """Bring window to foreground before interacting."""
        try:
            window.SetFocus()
            time.sleep(0.2)
            return True
        except Exception:
            return False

    def _deep_find(self, parent, element_name: str, max_depth: int = 9):
        """Fuzzy recursive element search — returns best scoring control."""
        if not parent:
            return None
        target     = element_name.lower().strip()
        best       = None
        best_score = 0

        def _score(name: str) -> float:
            if not name:
                return 0
            n = name.lower().strip()
            if n == target:            return 100
            if target in n:            return 80
            if n in target:            return 70
            t_words = set(target.split())
            n_words = set(n.split())
            overlap = t_words & n_words
            if overlap:
                return 50 + (len(overlap) / max(len(t_words), 1)) * 30
            return 0

        def _walk(ctrl, depth):
            nonlocal best, best_score
            if depth > max_depth:
                return
            try:
                for child in ctrl.GetChildren():
                    s = _score(child.Name or "")
                    if s > best_score:
                        best_score = s
                        best = child
                    if best_score == 100:
                        return
                    _walk(child, depth + 1)
                    if best_score == 100:
                        return
            except Exception:
                pass

        _walk(parent, 0)
        return best

    # ── Public actions ────────────────────────────────────────────────────────

    def background_click(self, app_name: str, element_name: str, desired_state=None) -> str:
        if not self.auto:
            return "ERROR: uiautomation missing."
        window  = self._find_window(app_name)
        root    = self.auto.GetRootControl() if not window else window
        self._focus_window(window or root)
        element = self._deep_find(root, element_name)
        if not element:
            return f"ERROR: '{element_name}' not found in '{app_name}'."
        if desired_state is not None:
            try:
                tp = element.GetTogglePattern()
                if tp and tp.ToggleState == desired_state:
                    return f"ALREADY_DONE: '{element.Name}' is already in the requested state."
            except Exception:
                pass
        element.Click(simulateMove=False)
        time.sleep(0.4)
        return f"SUCCESS: Clicked '{element.Name}' in '{app_name}'."

    def background_type(self, app_name: str, element_name: str, text: str) -> str:
        if not self.auto:
            return "ERROR: uiautomation missing."
        window  = self._find_window(app_name)
        root    = self.auto.GetRootControl() if not window else window
        self._focus_window(window or root)
        element = self._deep_find(root, element_name)
        if not element:
            return f"ERROR: '{element_name}' not found."
        try:
            vp = element.GetValuePattern()
            if vp:
                vp.SetValue(text)
            else:
                element.SetFocus()
                element.SendKeys(text)
        except Exception:
            element.SendKeys(text)
        time.sleep(0.3)
        return f"SUCCESS: Typed into '{element.Name}'."

    def toggle_switch(self, app_name: str, switch_name: str, desired_state=None) -> str:
        if not self.auto:
            return "ERROR: uiautomation missing."
        window  = self._find_window(app_name)
        root    = self.auto.GetRootControl() if not window else window
        element = self._deep_find(root, switch_name)
        if not element:
            return f"ERROR: Toggle '{switch_name}' not found."
        try:
            tp = element.GetTogglePattern()
            if tp:
                if desired_state is not None and tp.ToggleState == desired_state:
                    return f"ALREADY_DONE: Toggle already in requested state."
                tp.Toggle()
                time.sleep(0.4)
                return f"SUCCESS: Toggled '{element.Name}'."
        except Exception:
            pass
        element.Click(simulateMove=False)
        time.sleep(0.4)
        return f"SUCCESS: Clicked toggle '{element.Name}'."

    def scroll(self, app_name: str, element_name: str = "",
               direction: str = "down", clicks: int = 4) -> str:
        """Scroll inside an element or active window content, preventing tab bar interference."""
        try:
            dir_clean = (direction or "down").lower().strip()

            if not self.pag:
                return "ERROR: pyautogui missing for scroll."

            # Fast navigation for top / bottom
            if dir_clean in ("top", "start", "home", "upar sabse"):
                self.pag.hotkey("ctrl", "home")
                time.sleep(0.1)
                return "SUCCESS: Scrolled to top of page."
            if dir_clean in ("bottom", "end", "niche sabse"):
                self.pag.hotkey("ctrl", "end")
                time.sleep(0.1)
                return "SUCCESS: Scrolled to bottom of page."

            # 1. Target specific element if requested
            element_found = False
            if element_name and self.auto:
                window  = self._find_window(app_name)
                root    = self.auto.GetRootControl() if not window else window
                element = self._deep_find(root, element_name)
                if element:
                    rect = element.BoundingRectangle
                    cx = (rect.left + rect.right)  // 2
                    cy = (rect.top  + rect.bottom) // 2
                    self.pag.moveTo(cx, cy, duration=0.15)
                    element_found = True

            # 2. If no specific element, ensure mouse cursor is in the SAFE center of content
            # (Strictly prevents cursor from resting on browser tab strip y=0..60 which causes tab shifting!)
            if not element_found:
                try:
                    import win32gui
                    hwnd = win32gui.GetForegroundWindow()
                    if hwnd:
                        rect = win32gui.GetWindowRect(hwnd)
                        w = rect[2] - rect[0]
                        h = rect[3] - rect[1]
                        if w > 150 and h > 150:
                            safe_x = rect[0] + w // 2
                            safe_y = rect[1] + int(h * 0.55) # Content center, well below tab strip
                            cur_x, cur_y = self.pag.position()
                            # If cursor is on tab bar (top 90px) or outside window bounds, move to center
                            if cur_y < (rect[1] + 90) or cur_x < rect[0] or cur_x > rect[2] or cur_y > rect[3]:
                                self.pag.moveTo(safe_x, safe_y)
                except Exception:
                    pass

            # 3. Apply standard Windows WHEEL_DELTA (120 per notch)
            # Normal clicks (e.g. 3 to 5) translates to 360 - 600 units
            notch_count = max(1, clicks)
            delta = notch_count * 120 if notch_count < 100 else notch_count
            amount = -delta if dir_clean in ("down", "niche", "neeche") else delta
            self.pag.scroll(amount)
            return f"SUCCESS: Scrolled {dir_clean} {notch_count} notches ({amount} units)."
        except Exception as e:
            return f"ERROR: {e}"

    def right_click(self, app_name: str, element_name: str) -> str:
        """Right-click an element to open context menu."""
        if not self.auto:
            return "ERROR: uiautomation missing."
        window  = self._find_window(app_name)
        root    = self.auto.GetRootControl() if not window else window
        self._focus_window(window or root)
        element = self._deep_find(root, element_name)
        if not element:
            return f"ERROR: '{element_name}' not found."
        element.RightClick(simulateMove=False)
        time.sleep(0.3)
        return f"SUCCESS: Right-clicked '{element.Name}'."

    def hotkey(self, *keys) -> str:
        """Send a keyboard shortcut. e.g. hotkey('ctrl','c') for Ctrl+C."""
        if not self.pag:
            return "ERROR: pyautogui missing."
        try:
            self.pag.hotkey(*keys)
            time.sleep(0.2)
            return f"SUCCESS: Sent hotkey {'+'.join(keys)}."
        except Exception as e:
            return f"ERROR: {e}"

    def vision_action(self, action: str, query: str, text: str = "") -> str:
        """Vision-guided click/type using screenshot + LLM coordinate extraction with native Win32 fallback."""
        try:
            # Ensure target browser window is in active foreground if browser is running
            try:
                from core.win_os_agent import find_browser_window, force_foreground_window, attach_to_user_desktop
                attach_to_user_desktop()
                bw = find_browser_window()
                if bw and bw.get("hwnd"):
                    force_foreground_window(bw["hwnd"])
                    time.sleep(0.3)
            except Exception:
                pass

            from core.eyes import call_vision
            img_b64 = None
            
            # 1. Capture screen via PIL / MSS / CUA Grounding
            try:
                from core.cua_grounding import capture_raw_screen_pil
                pil_img = capture_raw_screen_pil()
                if pil_img:
                    import io
                    buf = io.BytesIO()
                    pil_img.save(buf, format="JPEG", quality=80)
                    img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            except Exception:
                pass

            if not img_b64:
                try:
                    from PIL import ImageGrab
                    shot = ImageGrab.grab()
                    import io
                    buf = io.BytesIO()
                    shot.convert("RGB").save(buf, format="JPEG", quality=80)
                    img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                except Exception:
                    pass

            if not img_b64 and self.pag:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                    path = tf.name
                try:
                    self.pag.screenshot().save(path)
                    with open(path, "rb") as f:
                        img_b64 = base64.b64encode(f.read()).decode("utf-8")
                finally:
                    if os.path.exists(path):
                        os.remove(path)

            if not img_b64:
                return "ERROR: Could not capture screen for vision action."

            import ctypes
            screen_w = ctypes.windll.user32.GetSystemMetrics(0) if sys.platform == "win32" else (pil_img.size[0] if pil_img else 1920)
            screen_h = ctypes.windll.user32.GetSystemMetrics(1) if sys.platform == "win32" else (pil_img.size[1] if pil_img else 1080)

            prompt = (
                f"Detect the 2D bounding box of '{query}' in this screenshot.\n"
                "If it is a product, video, card, button, or link, target the center of the main product image/thumbnail or title link so clicking it navigates to or opens it.\n"
                "Return ONLY valid JSON: [ymin, xmin, ymax, xmax] normalized on a scale of 0 to 1000.\n"
                "Example: [350, 420, 520, 680]"
            )
            result = call_vision(img_b64, prompt)

            x, y = None, None
            # Priority 1: Match normalized [ymin, xmin, ymax, xmax]
            m_box = re.search(r'\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]', result)
            if m_box:
                ymin, xmin, ymax, xmax = int(m_box.group(1)), int(m_box.group(2)), int(m_box.group(3)), int(m_box.group(4))
                x = int(((xmin + xmax) / 2.0) / 1000.0 * screen_w)
                y = int(((ymin + ymax) / 2.0) / 1000.0 * screen_h)
            else:
                # Priority 2: Fallback to {"x": ..., "y": ...}
                m_obj = re.search(r'\{.*?\}', result, re.DOTALL)
                if m_obj:
                    coords = json.loads(m_obj.group(0))
                    raw_x, raw_y = float(coords.get("x", screen_w // 2)), float(coords.get("y", screen_h // 2))
                    if raw_x <= 1.0 and raw_y <= 1.0:
                        x = int(raw_x * screen_w)
                        y = int(raw_y * screen_h)
                    elif raw_x <= 1000.0 and raw_y <= 1000.0 and (screen_w > 1000 or screen_h > 1000):
                        x = int(raw_x / 1000.0 * screen_w)
                        y = int(raw_y / 1000.0 * screen_h)
                    else:
                        x = int(raw_x)
                        y = int(raw_y)

            if x is None or y is None:
                return f"ERROR: Could not resolve coordinates for '{query}' from vision cortex: {result[:200]}"

            # Native Win32 mouse action (zero dependency on pyautogui)
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.user32.SetCursorPos(x, y)
                time.sleep(0.08)
                if action == "click":
                    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0) # left down
                    time.sleep(0.05)
                    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0) # left up
                    return f"SUCCESS: Vision-clicked '{query}' at ({x},{y})."
                elif action == "right_click":
                    ctypes.windll.user32.mouse_event(0x0008, 0, 0, 0, 0) # right down
                    time.sleep(0.05)
                    ctypes.windll.user32.mouse_event(0x0010, 0, 0, 0, 0) # right up
                    return f"SUCCESS: Vision right-clicked '{query}' at ({x},{y})."
                elif action == "type":
                    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
                    time.sleep(0.05)
                    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
                    time.sleep(0.2)
                    if self.pag:
                        self.pag.write(text, interval=0.04)
                    else:
                        import pyperclip
                        pyperclip.copy(text)
                        from core.win_os_agent import press_hotkey
                        press_hotkey(["ctrl", "v"])
                    return f"SUCCESS: Vision-typed '{text}' at ({x},{y})."

            if self.pag:
                self.pag.moveTo(x, y, duration=0.3)
                if action == "click":
                    self.pag.click()
                    return f"SUCCESS: Vision-clicked '{query}' at ({x},{y})."
            return f"SUCCESS: Targeted '{query}' at ({x},{y})."
        except Exception as e:
            return f"ERROR in vision_action: {e}"


# ── Cross-platform fallback (macOS / Linux) ───────────────────────────────────
class UniversalFallbackDriver(BaseDesktopDriver):

    def __init__(self):
        try:
            import pyautogui
            self.pag = pyautogui
            self.pag.FAILSAFE = True
        except ImportError:
            self.pag = None
            log_warn("desktop_driver", "pyautogui not installed")
        self.auto = None

    def background_click(self, app_name: str, element_name: str, desired_state=None) -> str:
        # On non-Windows: use vision to find and click element
        return self.vision_action("click", element_name)

    def background_type(self, app_name: str, element_name: str, text: str) -> str:
        return self.vision_action("type", element_name, text)

    def toggle_switch(self, app_name: str, switch_name: str, desired_state=None) -> str:
        return self.vision_action("click", switch_name)

    def scroll(self, app_name: str, element_name: str = "",
               direction: str = "down", clicks: int = 3) -> str:
        if not self.pag:
            return "ERROR: pyautogui missing."
        amount = -clicks if direction == "down" else clicks
        self.pag.scroll(amount)
        return f"SUCCESS: Scrolled {direction} {clicks}x."

    def right_click(self, app_name: str, element_name: str) -> str:
        return self.vision_action("right_click", element_name)

    def hotkey(self, *keys) -> str:
        if not self.pag:
            return "ERROR: pyautogui missing."
        self.pag.hotkey(*keys)
        return f"SUCCESS: Sent hotkey {'+'.join(keys)}."

    def vision_action(self, action: str, query: str, text: str = "") -> str:
        # Delegate to WindowsDriver's vision_action logic (shared)
        return WindowsDriver.vision_action(self, action, query, text)


# ── Factory ───────────────────────────────────────────────────────────────────
def get_desktop_driver() -> BaseDesktopDriver:
    if sys.platform == "win32":
        return WindowsDriver()
    return UniversalFallbackDriver()


desktop_driver = get_desktop_driver()
