"""
cua_driver.py — JARVIS Computer Use Agent (CUA) Driver v2.0
============================================================
Deep recursive Windows UI automation with:
  • Fuzzy + exact window/element search (unchanged — already excellent)
  • Scroll up/down at any element
  • Right-click → context menu support
  • Hotkey/keyboard shortcut sender
  • Screenshot-verify after action (checks screen changed)
  • Retry loop (3 attempts) on element-not-found
  • Window focus + SetForeground before every action
  • element list dump for debugging
"""

import time
import os
import json
import base64
import hashlib
import tempfile

from core.jarvis_logger import log_error, log_warn, log_info

try:
    import uiautomation as auto
except ImportError:
    auto = None

try:
    import pyautogui as _pag
    _pag.FAILSAFE = False
except ImportError:
    _pag = None


class CUADriver:
    """
    ⚡ JARVIS Computer Use Agent (CUA) Driver v2.0 ⚡
    Deep recursive UI automation — fuzzy matching, retry, verify.
    """

    MAX_RETRIES = 3

    def __init__(self):
        log_info("system", "system", "[CUA]: Desktop GUI Automation ready.")

    # ──────────────────────────────────────────────────────────────────────────
    # Internal: window + element search
    # ──────────────────────────────────────────────────────────────────────────

    def _find_window(self, app_name: str):
        """Find window — exact → SubName → case-insensitive scan."""
        if not auto:
            return None
        for _ in range(self.MAX_RETRIES):
            win = auto.WindowControl(searchDepth=1, Name=app_name)
            if win.Exists(2, 1):
                return win
            win = auto.WindowControl(searchDepth=1, SubName=app_name)
            if win.Exists(2, 1):
                return win
            app_low = app_name.lower()
            root = auto.GetRootControl()
            for child in root.GetChildren():
                try:
                    if app_low in (child.Name or "").lower():
                        return child
                except Exception:
                    continue
            time.sleep(0.5)
        return None

    def _focus(self, window) -> None:
        """Bring window to front before acting."""
        try:
            if window:
                window.SetFocus()
                time.sleep(0.25)
        except Exception:
            pass

    def _deep_find_element(self, parent, element_name: str, max_depth: int = 9):
        """
        Deep recursive search with fuzzy/partial name matching.
        Returns the best-scoring control or None.
        """
        if not parent:
            return None
        target     = element_name.lower().strip()
        best_match = None
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
            nonlocal best_match, best_score
            if depth > max_depth:
                return
            try:
                for child in ctrl.GetChildren():
                    s = _score(child.Name or "")
                    if s > best_score:
                        best_score = s
                        best_match = child
                    if best_score == 100:
                        return
                    _walk(child, depth + 1)
                    if best_score == 100:
                        return
            except Exception:
                pass

        _walk(parent, 0)
        return best_match

    def _screen_hash(self) -> str:
        """MD5 of a downsampled screenshot — used for verify-after-action."""
        if not _pag:
            return ""
        try:
            shot  = _pag.screenshot()
            data  = shot.tobytes()[::8]   # sample every 8th byte
            return hashlib.md5(data).hexdigest()
        except Exception:
            return ""

    def _get_root(self, app_name: str):
        """Get window or fall back to desktop root."""
        if not auto:
            return None
        window = self._find_window(app_name)
        if window:
            self._focus(window)
            return window
        root = auto.GetRootControl()
        return root

    # ──────────────────────────────────────────────────────────────────────────
    # Public actions
    # ──────────────────────────────────────────────────────────────────────────

    def background_click(self, app_name: str, element_name: str,
                         desired_state=None) -> str:
        """Click an element by name using deep fuzzy search."""
        if not auto:
            return "[⚠️ CUA] uiautomation not installed."
        root = self._get_root(app_name)
        log_info("system", "system", f"[CUA]: Click '{element_name}' in '{app_name}'")

        for attempt in range(self.MAX_RETRIES):
            element = self._deep_find_element(root, element_name)
            if element:
                break
            time.sleep(0.5)
        else:
            return f"[❌ CUA] Could not find '{element_name}' in '{app_name}'."

        if desired_state is not None:
            try:
                tp = element.GetTogglePattern()
                if tp and tp.ToggleState == desired_state:
                    return f"[ℹ️ ALREADY DONE] '{element.Name}' is already in the requested state."
            except Exception:
                pass

        before = self._screen_hash()
        element.Click(simulateMove=False)
        time.sleep(0.5)
        after = self._screen_hash()
        changed = before != after if (before and after) else True

        status = "✅" if changed else "⚠️ (screen unchanged)"
        return f"[{status} CUA] Clicked '{element.Name}' in '{app_name}'."

    def background_type(self, app_name: str, element_name: str, text: str) -> str:
        """Type text into an element using deep fuzzy search."""
        if not auto:
            return "[⚠️ CUA] uiautomation not installed."
        root = self._get_root(app_name)

        for attempt in range(self.MAX_RETRIES):
            element = self._deep_find_element(root, element_name)
            if element:
                break
            time.sleep(0.5)
        else:
            return f"[❌ CUA] Could not find '{element_name}'."

        log_info("system", "system", f"[CUA]: Type '{text[:30]}' into '{element.Name}'")
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
        return f"[✅ CUA] Typed into '{element.Name}'."

    def toggle_switch(self, app_name: str, switch_name: str,
                      desired_state=None) -> str:
        """Find and toggle a switch/checkbox by name."""
        if not auto:
            return "[⚠️ CUA] uiautomation not installed."
        root    = self._get_root(app_name)
        element = self._deep_find_element(root, switch_name)
        if not element:
            return f"[❌ CUA] Toggle '{switch_name}' not found."

        try:
            tp = element.GetTogglePattern()
            if tp:
                if desired_state is not None and tp.ToggleState == desired_state:
                    return f"[ℹ️ ALREADY DONE] Toggle '{element.Name}' already in requested state."
                tp.Toggle()
                time.sleep(0.4)
                return f"[✅ CUA] Toggled '{element.Name}'."
        except Exception:
            pass

        element.Click(simulateMove=False)
        time.sleep(0.4)
        return f"[✅ CUA] Clicked toggle '{element.Name}'."

    def scroll(self, app_name: str, element_name: str = "",
               direction: str = "down", clicks: int = 4) -> str:
        """Scroll inside a named element or active window content, preventing tab bar interference."""
        if not _pag:
            return "[⚠️ CUA] pyautogui missing for scroll."
        try:
            dir_clean = (direction or "down").lower().strip()
            element_found = False
            if element_name and auto:
                root    = self._get_root(app_name)
                element = self._deep_find_element(root, element_name)
                if element:
                    rect = element.BoundingRectangle
                    cx = (rect.left + rect.right) // 2
                    cy = (rect.top  + rect.bottom) // 2
                    _pag.moveTo(cx, cy, duration=0.15)
                    element_found = True

            # If no element specified, ensure cursor is safely in content area away from top tab strip
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
                            safe_y = rect[1] + int(h * 0.55)
                            cur_x, cur_y = _pag.position()
                            if cur_y < (rect[1] + 90) or cur_x < rect[0] or cur_x > rect[2] or cur_y > rect[3]:
                                _pag.moveTo(safe_x, safe_y)
                except Exception:
                    pass

            notch_count = max(1, clicks)
            delta = notch_count * 120 if notch_count < 100 else notch_count
            amount = -delta if dir_clean in ("down", "niche", "neeche") else delta
            _pag.scroll(amount)
            time.sleep(0.15)
            return f"[✅ CUA] Scrolled {dir_clean} {notch_count} notches ({amount} units)."
        except Exception as e:
            return f"[⚠️ CUA] Scroll error: {e}"

    def right_click(self, app_name: str, element_name: str) -> str:
        """Right-click an element to open its context menu."""
        if not auto:
            return "[⚠️ CUA] uiautomation not installed."
        root    = self._get_root(app_name)
        element = self._deep_find_element(root, element_name)
        if not element:
            return f"[❌ CUA] '{element_name}' not found."
        element.RightClick(simulateMove=False)
        time.sleep(0.3)
        return f"[✅ CUA] Right-clicked '{element.Name}'."

    def hotkey(self, *keys) -> str:
        """Send a keyboard shortcut. e.g. hotkey('ctrl','s') → Ctrl+S."""
        if not _pag:
            return "[⚠️ CUA] pyautogui missing for hotkey."
        try:
            _pag.hotkey(*keys)
            time.sleep(0.2)
            combo = "+".join(keys)
            log_info("system", "system", f"[CUA]: Hotkey {combo}")
            return f"[✅ CUA] Sent hotkey {combo}."
        except Exception as e:
            return f"[⚠️ CUA] Hotkey error: {e}"

    def list_elements(self, app_name: str, max_depth: int = 3) -> list:
        """Dump all named interactive elements in a window (for debugging)."""
        if not auto:
            return []
        window = self._find_window(app_name)
        if not window:
            return []
        elements = []

        def _walk(ctrl, depth):
            if depth > max_depth:
                return
            try:
                for child in ctrl.GetChildren():
                    name = child.Name or ""
                    if name.strip():
                        elements.append({
                            "name":  name,
                            "type":  child.ControlTypeName,
                            "depth": depth,
                        })
                    _walk(child, depth + 1)
            except Exception:
                pass

        _walk(window, 0)
        return elements

    def vision_click(self, query: str, action: str = "click", text: str = "") -> str:
        """Vision-guided action to locate and interact with any on-screen element."""
        try:
            from core.eyes import call_vision
            from core.cua_grounding import capture_raw_screen_pil
            import re as _re
            import io

            pil_img = capture_raw_screen_pil()
            if not pil_img and _pag:
                try:
                    pil_img = _pag.screenshot()
                except Exception:
                    pass

            if not pil_img:
                return "[CUA] Could not capture screen for vision interaction."

            buf = io.BytesIO()
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            pil_img.save(buf, format="JPEG", quality=85)
            img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            # Clean spoken / conversational noise from query
            clean_query = (query or "").strip()
            for drop_phrase in [
                "screen par dikhra hoga click karo", "screen par dikh raha hoga click karo",
                "screen par dikhra hoga", "screen par dikh raha hoga",
                "dikhra hoga click karo", "dikh raha hoga click karo",
                "screen par", "screen pe", "desktop par", "desktop pe",
                "us per click karo", "us pe click karo", "per click karo", "pe click karo",
                "click karo", "click kar", "click on", "clickar", "click", "daba", "press", "kholo", "open karo", "open",
                "dikhta hoga", "dikh raha hoga", "dikhra hoga", "please", "plz", "babe", "lila"
            ]:
                clean_query = _re.sub(rf"\b{_re.escape(drop_phrase)}\b", "", clean_query, flags=_re.I)
            clean_query = " ".join(clean_query.split()).strip() or query

            prompt = (
                f"You are a precision GUI automation agent. Locate the button, link, card, video thumbnail, icon, or text element corresponding to: '{clean_query}'.\n"
                "Return ONLY a JSON object with 'x' and 'y' pixel coordinates of the clickable center.\n"
                'Example: {"x": 540, "y": 320}'
            )
            result = call_vision(img_b64, prompt)
            m = _re.search(r'\{[^{}]*"x"[^{}]*"y"[^{}]*\}', result, _re.DOTALL)
            if not m:
                m = _re.search(r'\{.*?\}', result, _re.DOTALL)
            if not m:
                return f"[CUA] Could not visually locate '{clean_query}' on screen."
            coords = json.loads(m.group(0))
            x, y = int(coords["x"]), int(coords["y"])
            print(f"[DEBUG vision_click] query={clean_query!r}, raw_vision={result!r}, parsed_coords=({x}, {y})")

            if x <= 0 or y <= 0:
                return f"[CUA] Could not visually locate '{clean_query}' on screen."

            # Ensure active window has focus before clicking so click is not absorbed by background
            try:
                from core.win_os_agent import find_browser_window, force_foreground_window
                bw = find_browser_window()
                if bw and bw.get("hwnd"):
                    force_foreground_window(bw["hwnd"])
                    time.sleep(0.08)
            except Exception:
                pass

            if _pag:
                _pag.moveTo(x, y, duration=0.3)
                if action == "double_click":
                    _pag.doubleClick()
                elif action == "right_click":
                    _pag.rightClick()
                elif action == "type":
                    _pag.click()
                    time.sleep(0.15)
                    _pag.write(text, interval=0.04)
                else:
                    _pag.click()
            else:
                import ctypes
                user32 = ctypes.windll.user32
                user32.SetCursorPos(x, y)
                time.sleep(0.05)
                user32.mouse_event(0x0002, 0, 0, 0, 0)
                time.sleep(0.05)
                user32.mouse_event(0x0004, 0, 0, 0, 0)

            return f"[CUA] Successfully clicked '{clean_query}' at screen coordinates ({x}, {y})."
        except Exception as e:
            return f"[CUA] Vision action error: {e}"



# ── Singleton ─────────────────────────────────────────────────────────────────
cua_driver = CUADriver()
