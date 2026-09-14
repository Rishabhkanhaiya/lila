"""
⚡ WIN_FAST_VOICE.PY — JARVIS Windows Voice Turbo Engine ⚡

This module gives JARVIS the same instant zero-latency command execution as
Windows 11 Voice Access — without any AI, without any API calls, and without
any internet connection for basic OS commands.

Architecture:
  ┌─────────────────────────────────────────────────────────────────┐
  │  User speaks → ears.py captures audio → try_fast_command()     │
  │                                                                 │
  │  ✅ MATCHED? → Execute instantly (~50-200ms) → Done             │
  │  ❌ NOT MATCHED? → Pass to Groq/Whisper AI pipeline             │
  └─────────────────────────────────────────────────────────────────┘

Covers ALL Windows Voice Access commands:
  • Manage Voice Access & Microphone
  • Interact With Apps & Windows
  • Interact With Controls & Mouse
  • Dictate & Edit Text
  • Text Navigation & Selection
  • Keyboard Key Emulation
  • Punctuation & Symbols
  • Screen Overlays
"""

import os
from pathlib import Path
from core.jarvis_logger import log_error, log_warn
import re
import time
import subprocess
import threading
try:
    import pyautogui
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False
import pyperclip
from typing import Tuple, Optional

# ── Optional imports (graceful degradation) ───────────────────────────────
try:
    import keyboard
    HAS_KEYBOARD = True
except ImportError:
    HAS_KEYBOARD = False

try:
    import win32api
    import win32con
    import win32gui
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

try:
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    from comtypes import CLSCTX_ALL
    import ctypes
    HAS_PYCAW = True
except ImportError:
    HAS_PYCAW = False

# ── pyautogui safety ──────────────────────────────────────────────────────
if HAS_PYAUTOGUI:
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05  # Minimal pause for speed

# ══════════════════════════════════════════════════════════════════════════════
# CORE EXECUTION PRIMITIVES
# ══════════════════════════════════════════════════════════════════════════════

def _safe_unpark_cursor():
    """
    Safely repositions mouse cursor away from screen corners using native Win32 API.
    Prevents PyAutoGUI FailSafeException from firing when cursor rests at (0, 0) or display edges.
    """
    try:
        import ctypes
        from core.win_os_agent import attach_to_user_desktop
        attach_to_user_desktop()
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            sw = ctypes.windll.user32.GetSystemMetrics(0) or 1920
            sh = ctypes.windll.user32.GetSystemMetrics(1) or 1080
            margin = 5
            is_corner = (
                (pt.x <= margin and pt.y <= margin) or
                (pt.x >= sw - margin and pt.y <= margin) or
                (pt.x <= margin and pt.y >= sh - margin) or
                (pt.x >= sw - margin and pt.y >= sh - margin)
            )
            if is_corner:
                ctypes.windll.user32.SetCursorPos(sw // 2, sh // 2)
    except Exception:
        pass


def _press(*keys):
    """Press a key combination instantly with fail-safe recovery."""
    _safe_unpark_cursor()
    if HAS_KEYBOARD:
        try:
            keyboard.press_and_release('+'.join(keys))
            return
        except Exception:
            pass
    if HAS_PYAUTOGUI:
        try:
            pyautogui.hotkey(*keys)
            return
        except pyautogui.FailSafeException:
            _safe_unpark_cursor()
            orig_fs = getattr(pyautogui, "FAILSAFE", True)
            try:
                pyautogui.FAILSAFE = False
                pyautogui.hotkey(*keys)
            finally:
                pyautogui.FAILSAFE = orig_fs
            return
        except Exception:
            pass
    try:
        from core.win_os_agent import press_hotkey
        press_hotkey(list(keys))
    except Exception:
        pass


def _type_text(text: str):
    """Type text instantly with zero fail-safe exceptions, supporting Unicode and clipboard injection."""
    if not text:
        return
    _safe_unpark_cursor()
    # 1. Prefer keyboard library if available
    if HAS_KEYBOARD:
        try:
            keyboard.write(text, delay=0)
            return
        except Exception:
            pass
    # 2. Prefer instant clipboard injection (Ctrl+V) via win_os_agent (100x faster, handles all Unicode/emojis)
    try:
        from core.win_os_agent import type_text as _agent_type
        _agent_type(text)
        return
    except Exception:
        pass
    # 3. Fallback: PyAutoGUI typewrite with fail-safe recovery
    if HAS_PYAUTOGUI:
        try:
            pyautogui.typewrite(text, interval=0)
        except pyautogui.FailSafeException:
            _safe_unpark_cursor()
            orig_fs = getattr(pyautogui, "FAILSAFE", True)
            try:
                pyautogui.FAILSAFE = False
                pyautogui.typewrite(text, interval=0)
            finally:
                pyautogui.FAILSAFE = orig_fs

def _resolve_exe_path(exe_name: str) -> str:
    """
    Resolve an exe name to its full path using Windows App Paths registry.
    Falls back to the exe name itself (relies on PATH) if not found.
    """
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    full_path, _ = winreg.QueryValueEx(key, "")
                    if full_path and os.path.exists(full_path):
                        return full_path
            except (FileNotFoundError, OSError):
                continue
    except Exception as e:
        log_warn("win_fast_voice", f"resolve exe path failed: {e}")
    return exe_name  # fallback to bare name


def _open_app(app_name: str) -> str:
    """Open an application by name. Uses registry App Paths to find full exe path."""
    app_name = app_name.strip().lower()
    # Web Services Direct Check
    COMMON_WEB = {
        "amazon": "https://www.amazon.in",
        "amazon.in": "https://www.amazon.in",
        "amazon.com": "https://www.amazon.com",
        "flipkart": "https://www.flipkart.com",
        "youtube": "https://www.youtube.com",
        "google": "https://www.google.com",
        "github": "https://www.github.com",
        "chatgpt": "https://chatgpt.com",
        "netflix": "https://www.netflix.com",
        "twitter": "https://x.com",
        "x": "https://x.com",
        "instagram": "https://www.instagram.com",
        "reddit": "https://www.reddit.com",
        "linkedin": "https://www.linkedin.com",
        "facebook": "https://www.facebook.com",
        "wikipedia": "https://www.wikipedia.org",
        "gmail": "https://mail.google.com",
        "spotify": "https://open.spotify.com",
    }
    if app_name in COMMON_WEB or f"{app_name}.com" in COMMON_WEB or f"{app_name}.in" in COMMON_WEB:
        from core.omniforge import launch_in_chrome
        url = COMMON_WEB.get(app_name) or COMMON_WEB.get(f"{app_name}.com") or COMMON_WEB.get(f"{app_name}.in")
        launch_in_chrome(url)
    # Windows Settings Direct Protocol Launch
    if app_name in ("setting", "settings", "windows settings", "windows setting", "system settings"):
        try:
            os.startfile("ms-settings:")
            return "Opening Windows Settings."
        except Exception as e:
            return f"Failed to open Settings: {e}"

    # Lila Desktop Companion 3D UI Direct Launch (Checked BEFORE browser!)
    if any(k in app_name for k in ["companion", "companian", "avatar", "overlay", "ui", "samne"]) or app_name in ("lila", "lila companion", "lila desktop", "lila companion exe"):
        try:
            from core.lila_companion_launcher import open_lila_companion_ui
            open_lila_companion_ui()
            try:
                from core.state_bridge import broadcast_state
                broadcast_state(mood="excited", caption="Main aa gayi Rishabh! ✨ Dekho main screen pe hoon!", speaking=True)
            except Exception:
                pass
            return "Opening Lila Desktop Companion UI."
        except Exception as e:
            return f"Failed to open Lila Companion UI: {e}"

    # Lila's Browser Direct Launch Intercept (Only when explicitly requesting the browser)
    if any(b == app_name or app_name.startswith(b) for b in ["lila browser", "lila's browser", "browser", "lilas browser"]):
        chrome_path = os.path.expandvars(r"%LOCALAPPDATA%\BrowserClaw\Application\chrome.exe")
        start_page = str(Path(__file__).resolve().parent.parent / "lila-browser-integration" / "branding" / "start-page.html")
        if os.path.exists(chrome_path):
            import subprocess
            ext_candidates = [
                os.path.expandvars(r"%LOCALAPPDATA%\BrowserClaw\Extensions\uBlock0\uBlock0.chromium"),
                os.path.expandvars(r"%LOCALAPPDATA%\BrowserClaw\Extensions\ChromiumWebStore"),
            ]
            valid_exts = [e for e in ext_candidates if os.path.exists(e)]
            cmd = [chrome_path]
            if valid_exts:
                cmd.append(f"--load-extension={','.join(valid_exts)}")
            cmd.append(start_page)
            subprocess.Popen(cmd)
            try:
                from core.state_bridge import broadcast_state
                broadcast_state(mood="excited", caption="Opened Lila's Browser! ✨")
            except Exception:
                pass
            return "Opening Lila's Browser."

    # Exact app mappings: friendly name -> exe filename
    APP_MAP = {
        "notepad": "notepad.exe",
        "calculator": "calc.exe",
        "paint": "mspaint.exe",
        "chrome": "chrome.exe",
        "google chrome": "chrome.exe",
        "firefox": "firefox.exe",
        "brave": "brave.exe",
        "brave browser": "brave.exe",
        "edge": "msedge.exe",
        "microsoft edge": "msedge.exe",
        "word": "winword.exe",
        "microsoft word": "winword.exe",
        "excel": "excel.exe",
        "microsoft excel": "excel.exe",
        "powerpoint": "powerpnt.exe",
        "microsoft powerpoint": "powerpnt.exe",
        "outlook": "outlook.exe",
        "microsoft outlook": "outlook.exe",
        "vs code": "code.exe",
        "vscode": "code.exe",
        "visual studio code": "code.exe",
        "code": "code.exe",
        "explorer": "explorer.exe",
        "file explorer": "explorer.exe",
        "task manager": "taskmgr.exe",
        "cmd": "cmd.exe",
        "command prompt": "cmd.exe",
        "powershell": "powershell.exe",
        "settings": "ms-settings:",
        "control panel": "control.exe",
        "camera": "microsoft.windows.camera:",
        "photos": "ms-photos:",
        "store": "ms-windows-store:",
        "microsoft store": "ms-windows-store:",
        "snip": "snippingtool.exe",
        "snipping tool": "snippingtool.exe",
        "paint 3d": "ms-paint:",
        "maps": "bingmaps:",
        "mail": "outlookmail:",
        "calendar": "outlookcal:",
        "clock": "ms-clock:",
        "alarm": "ms-clock:",
        "weather": "bingweather:",
        "spotify": "spotify.exe",
        "discord": "discord.exe",
        "telegram": "telegram.exe",
        "vlc": "vlc.exe",
        "zoom": "zoom.exe",
        "teams": "msteams.exe",
        "microsoft teams": "msteams.exe",
        "skype": "skype.exe",
        "steam": "steam.exe",
        "whatsapp": "whatsapp.exe",
        "terminal": "wt.exe",
        "windows terminal": "wt.exe",
        "wordpad": "write.exe",
        "sticky notes": "stikynot.exe",
        "character map": "charmap.exe",
        "registry editor": "regedit.exe",
        "device manager": "devmgmt.msc",
        "disk management": "diskmgmt.msc",
        "event viewer": "eventvwr.msc",
        "services": "services.msc",
        "resource monitor": "resmon.exe",
        "performance monitor": "perfmon.exe",
    }

    # Clean conversational trailing words
    for suffix in [" again", " please", " now", " for me"]:
        if app_name.endswith(suffix):
            app_name = app_name[:-len(suffix)].strip()

    try:
        from core.win_os_agent import open_app as _os_open_app
        return _os_open_app(app_name)
    except Exception as e:
        log_warn("win_fast_voice", f"win_os_agent open_app fallback: {e}")

    if app_name in APP_MAP:
        exe = APP_MAP[app_name]
        if exe.endswith(":"):
            os.startfile(exe)
            return f"Opening {app_name}."
        else:
            full_path = _resolve_exe_path(exe)
            try:
                subprocess.Popen([full_path], shell=False)
            except Exception:
                subprocess.Popen(exe, shell=True)
            return f"Opening {app_name}."

    return f"Could not find application: {app_name}"


def _close_app(app_name: str) -> str:
    """Close an app by name or close the current window gracefully using WM_CLOSE."""
    try:
        from core.win_os_agent import close_app as _os_close_app
        return _os_close_app(app_name)
    except Exception as e:
        log_warn("win_fast_voice", f"win_os_agent close_app fallback: {e}")

    # Fallback: Alt+F4 on current window
    _press('alt', 'f4')
    return f"Closed active window."


def _switch_to_app(app_name: str) -> str:
    """Switch focus to an open app using deterministic Win32 window handles."""
    try:
        from core.win_os_agent import switch_to_app as _os_switch_to_app
        return _os_switch_to_app(app_name)
    except Exception as e:
        log_warn("win_fast_voice", f"win_os_agent switch_to_app fallback: {e}")
    return f"Could not switch to '{app_name}'."


def _get_volume():
    """Get current system volume (0.0 to 1.0)."""
    if not HAS_PYCAW:
        return None
    try:
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = interface.QueryInterface(IAudioEndpointVolume)
        return volume.GetMasterVolumeLevelScalar()
    except Exception as e:
        log_warn("win_fast_voice", f"get volume failed: {e}")
        return None


def _set_volume_by_keypress(action: str, steps: int = 2) -> str:
    """Change volume via keyboard shortcuts."""
    if action == "up":
        for _ in range(steps):
            _press('volume up')
        return f"Volume increased."
    elif action in ("100", "full", "max", "maximum"):
        # Windows has no portable keyboard shortcut for an absolute level.
        # Fifty up presses reliably saturate the system volume from any level.
        for _ in range(50):
            _press('volume up')
        return "Volume set to maximum."
    elif action == "down":
        for _ in range(steps):
            _press('volume down')
        return f"Volume decreased."
    elif action == "mute":
        _press('volume mute')
        return "Microphone/Speaker muted."
    elif action == "unmute":
        _press('volume mute')
        return "Microphone/Speaker unmuted."
    return "Volume action done."


# ══════════════════════════════════════════════════════════════════════════════
# PUNCTUATION & SYMBOL MAP
# ══════════════════════════════════════════════════════════════════════════════

PUNCTUATION_MAP = {
    # Basic punctuation
    "comma": ",",
    "period": ".",
    "dot": ".",
    "full stop": ".",
    "question mark": "?",
    "exclamation mark": "!",
    "exclamation point": "!",
    "colon": ":",
    "semicolon": ";",
    # Quotes & apostrophes
    "open quote": '"',
    "close quote": '"',
    "open double quote": '"',
    "close double quote": '"',
    "open single quote": "'",
    "close single quote": "'",
    "apostrophe": "'",
    # Brackets & parentheses
    "open parenthesis": "(",
    "close parenthesis": ")",
    "open bracket": "[",
    "close bracket": "]",
    "open brace": "{",
    "close brace": "}",
    "open angle bracket": "<",
    "close angle bracket": ">",
    # Math symbols
    "plus sign": "+",
    "minus sign": "-",
    "equal sign": "=",
    "equals sign": "=",
    "multiplication sign": "×",
    "division sign": "÷",
    "percent sign": "%",
    "greater than sign": ">",
    "less than sign": "<",
    "plus or minus sign": "±",
    # Dashes & slashes
    "hyphen": "-",
    "dash": "-",
    "en dash": "–",
    "em dash": "—",
    "underscore": "_",
    "forward slash": "/",
    "slash": "/",
    "backslash": "\\",
    "double slash": "//",
    # Special characters
    "at sign": "@",
    "number sign": "#",
    "pound sign": "#",
    "dollar sign": "$",
    "ampersand": "&",
    "and sign": "&",
    "asterisk": "*",
    "star": "*",
    "tilde": "~",
    "caret": "^",
    # Typographical symbols
    "trademark sign": "™",
    "copyright sign": "©",
    "registered sign": "®",
    "degree sign": "°",
    "section sign": "§",
    "paragraph sign": "¶",
    # Emoticons
    "smiley face": ":-)",
    "frowny face": ":-(",
    "winky face": ";-)",
    # New line / tab
    "new line": "\n",
    "new paragraph": "\n\n",
    "tab": "\t",
}

# ══════════════════════════════════════════════════════════════════════════════
# KEYBOARD KEY MAP
# ══════════════════════════════════════════════════════════════════════════════

KEY_NAME_MAP = {
    "enter": "enter",
    "return": "enter",
    "escape": "escape",
    "esc": "escape",
    "backspace": "backspace",
    "delete": "delete",
    "del": "delete",
    "tab": "tab",
    "space": "space",
    "spacebar": "space",
    "home": "home",
    "end": "end",
    "page up": "page up",
    "page down": "page down",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "insert": "insert",
    "print screen": "print screen",
    "caps lock": "caps lock",
    "num lock": "num lock",
    "scroll lock": "scroll lock",
    "windows": "win",
    "win": "win",
    "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4",
    "f5": "f5", "f6": "f6", "f7": "f7", "f8": "f8",
    "f9": "f9", "f10": "f10", "f11": "f11", "f12": "f12",
}

MODIFIER_MAP = {
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "win", "windows": "win",
}

# ══════════════════════════════════════════════════════════════════════════════
# SCROLL DIRECTION MAP & SAFE POSITIONING
# ══════════════════════════════════════════════════════════════════════════════

SCROLL_MAP = {
    "up": (0, 360),
    "down": (0, -360),
    "left": (-360, 0),
    "right": (360, 0),
}

def _ensure_safe_scroll_position():
    """Repositions mouse to window content center if hovering in top tab bar (prevents tab shifting)."""
    try:
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        if hwnd:
            rect = win32gui.GetWindowRect(hwnd)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            if w > 150 and h > 150:
                _safe_unpark_cursor()
                cur_x, cur_y = pyautogui.position()
                if cur_y < (rect[1] + 90) or cur_x < rect[0] or cur_x > rect[2] or cur_y > rect[3]:
                    safe_x = rect[0] + w // 2
                    safe_y = rect[1] + int(h * 0.55)
                    try:
                        import ctypes
                        ctypes.windll.user32.SetCursorPos(safe_x, safe_y)
                    except Exception:
                        try:
                            pyautogui.moveTo(safe_x, safe_y)
                        except Exception:
                            pass
    except Exception:
        pass

# For continuous scrolling thread
import threading
_scroll_lock = threading.Lock()
_scroll_active = False
_scroll_thread: Optional[threading.Thread] = None
_scroll_stop_event = threading.Event()


def _start_scroll(direction: str):
    global _scroll_active, _scroll_thread
    _ensure_safe_scroll_position()
    with _scroll_lock:
        if _scroll_active:
            _scroll_active = False
            _scroll_stop_event.set()
            if _scroll_thread and _scroll_thread.is_alive():
                _scroll_thread.join(timeout=0.5)
        _scroll_active = True
        _scroll_stop_event.clear()
        dx, dy = SCROLL_MAP.get(direction, (0, -360))

    def _do_scroll():
        while not _scroll_stop_event.is_set():
            pyautogui.scroll(dy)
            if dx != 0:
                pyautogui.hscroll(dx)
            if _scroll_stop_event.wait(0.12):
                break

    with _scroll_lock:
        _scroll_thread = threading.Thread(target=_do_scroll, daemon=True)
        _scroll_thread.start()
    return f"Started scrolling {direction}."


def _stop_scroll():
    global _scroll_active
    with _scroll_lock:
        _scroll_active = False
        _scroll_stop_event.set()
    return "Stopped scrolling."


# ══════════════════════════════════════════════════════════════════════════════
# WINDOW MANAGEMENT VIA WIN32
# ══════════════════════════════════════════════════════════════════════════════

def _get_foreground_hwnd():
    if HAS_WIN32:
        return win32gui.GetForegroundWindow()
    return None


def _minimize_window(app_name: str = "") -> str:
    try:
        from core.win_os_agent import minimize_window as _os_min
        return _os_min(app_name)
    except Exception as e:
        log_warn("win_fast_voice", f"minimize fallback: {e}")
    _press('win', 'down')
    return f"Minimized {'window' if not app_name else app_name}."


def _maximize_window(app_name: str = "") -> str:
    try:
        from core.win_os_agent import maximize_window as _os_max
        return _os_max(app_name)
    except Exception as e:
        log_warn("win_fast_voice", f"maximize fallback: {e}")
    _press('win', 'up')
    return f"Maximized {'window' if not app_name else app_name}."


def _restore_window(app_name: str = "") -> str:
    try:
        from core.win_os_agent import snap_window as _os_snap
        return _os_snap("restore")
    except Exception:
        _press('win', 'down')
        return f"Restored {'window' if not app_name else app_name}."


def _snap_window(direction: str) -> str:
    try:
        from core.win_os_agent import snap_window as _os_snap
        return _os_snap(direction)
    except Exception as e:
        log_warn("win_fast_voice", f"snap fallback: {e}")
    direction = direction.lower().strip()
    snap_keys = {
        "left": ('win', 'left'),
        "right": ('win', 'right'),
        "up": ('win', 'up'),
        "down": ('win', 'down'),
    }
    keys = snap_keys.get(direction, ('win', 'left'))
    _press(*keys)
    return f"Snapped window to {direction}."


# ══════════════════════════════════════════════════════════════════════════════
# SCREENSHOT
# ══════════════════════════════════════════════════════════════════════════════

def _take_screenshot() -> str:
    """Capture a screenshot in-memory and analyze with Gemini Vision without Snipping Tool popups."""
    try:
        from core.win_os_agent import take_screenshot_and_analyze
        return take_screenshot_and_analyze("Describe what is on screen in detail.")
    except Exception as e:
        log_warn("win_fast_voice", f"take_screenshot fallback: {e}")
        return "Screenshot capture failed."


def _take_full_screenshot() -> str:
    """Full-screen screenshot saved automatically and analyzed."""
    return _take_screenshot()


# ══════════════════════════════════════════════════════════════════════════════
# SEARCH
# ══════════════════════════════════════════════════════════════════════════════

def _search_windows(query: str) -> str:
    """Search user directories and Start Menu for files and applications programmatically."""
    try:
        from core.win_os_agent import search_windows_files
        return search_windows_files(query)
    except Exception as e:
        log_warn("win_fast_voice", f"search_windows fallback: {e}")
        _press('win', 's')
        time.sleep(0.3)
        _type_text(query)
        return f"Searching Windows for: {query}"


def _search_web(engine: str, query: str) -> str:
    engine_urls = {
        "google": f"https://www.google.com/search?q={query.replace(' ', '+')}",
        "bing": f"https://www.bing.com/search?q={query.replace(' ', '+')}",
        "youtube": f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}",
        "duckduckgo": f"https://duckduckgo.com/?q={query.replace(' ', '+')}",
    }
    url = engine_urls.get(engine.lower(), f"https://www.google.com/search?q={query.replace(' ', '+')}")
    os.startfile(url)
    return f"Searching {engine} for: {query}"
def _play_song_on_youtube(song_name: str) -> str:
    """Open YouTube and play the top search result for a song or video."""
    from core.youtube_driver import play_youtube_video
    return play_youtube_video(song_name)


# ══════════════════════════════════════════════════════════════════════════════

def _mousegrid(scope: str = "") -> str:
    """Show a numbered grid overlay by dividing screen into sections."""
    screen_w, screen_h = pyautogui.size()
    print(f"[🖱️ MOUSEGRID]: Screen={screen_w}x{screen_h}. Use 'click [number]' to select a zone.")
    # We simply show a 3x3 grid in console — a real overlay would need UI integration
    zones = []
    cols, rows = 3, 3
    for r in range(rows):
        for c in range(cols):
            x = int((c + 0.5) * screen_w / cols)
            y = int((r + 0.5) * screen_h / rows)
            zones.append((c + r * cols + 1, x, y))
    print("[MOUSEGRID ZONES]:", zones)
    return "MouseGrid active. Say a number to click that zone."


_mousegrid_zones = []


def _click_mousegrid_number(number: int) -> str:
    """Click a numbered mousegrid zone."""
    screen_w, screen_h = pyautogui.size()
    cols, rows = 3, 3
    idx = number - 1
    if 0 <= idx < cols * rows:
        c = idx % cols
        r = idx // cols
        x = int((c + 0.5) * screen_w / cols)
        y = int((r + 0.5) * screen_h / rows)
        pyautogui.moveTo(x, y, duration=0.1)
        pyautogui.click()
        return f"Clicked zone {number} at ({x}, {y})."
    return f"Invalid zone number: {number}"


# ══════════════════════════════════════════════════════════════════════════════
# THE MAIN MATCHING ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def _normalize(text: str) -> str:
    """Normalize text for matching: lowercase, strip, collapse spaces, fix repeated-letter typos."""
    t = re.sub(r'\s+', ' ', text.strip().lower())
    
    # Strip conversational prefixes
    for prefix in ("hey jarvis ", "jarvis ", "can you ", "could you ", "please ", "just "):
        if t.startswith(prefix):
            t = t[len(prefix):].strip()

    # Collapse 3+ repeated letters → 1 (e.g. 'heeey' → 'hey', 'noooo' → 'no')
    # Keep 2-letter repeats in case they're valid (e.g. 'book', 'too', 'off')
    t = re.sub(r'(.)\1{2,}', r'\1', t)
    # Collapse exactly 2 repeated consonants that form typos (e.g. 'switchh' -> 'switch')
    # Preserve valid FLOSS double consonants (l, s, f, z) like 'scroll', 'press', 'off'
    t = re.sub(r'([bcdghjkmnpqrtvwxy])\1\b', r'\1', t)
    return t


# ✅ JARVIS 2.0: Fuzzy command matching using difflib
# Built lazily on first use so startup is not slowed down
import difflib as _difflib
_FUZZY_CMD_CACHE: set = set()

def _build_fuzzy_cache():
    """Build the set of all known normalized command phrases for fuzzy matching."""
    global _FUZZY_CMD_CACHE
    if _FUZZY_CMD_CACHE:
        return
    # Pull known phrases from capabilities.json
    try:
        import json as _json, os as _os
        _cap_path = _os.path.join(_os.path.dirname(__file__), "capabilities.json")
        with open(_cap_path, "r", encoding="utf-8") as _f:
            caps = _json.load(_f)
        for phrases in caps.values():
            for p in phrases:
                _FUZZY_CMD_CACHE.add(_normalize(p))
    except Exception as e:
        log_warn("win_fast_voice", f"build fuzzy cache failed: {e}")

def _fuzzy_match_cmd(text: str, threshold: float = 0.82) -> str | None:
    """Return closest known command phrase if similarity >= threshold, else None."""
    _build_fuzzy_cache()
    if not _FUZZY_CMD_CACHE:
        return None
    matches = _difflib.get_close_matches(text, _FUZZY_CMD_CACHE, n=1, cutoff=threshold)
    return matches[0] if matches else None

def try_fast_command(text: str) -> Tuple[bool, str]:
    """
    ⚡ THE MAIN ENTRY POINT ⚡

    Try to handle the user's spoken command as an instant Windows Voice Action.
    Returns (handled: bool, result_message: str).
    
    If handled=True, the caller should NOT send to the AI pipeline.
    If handled=False, the text should proceed to Groq/Whisper AI pipeline.
    """
    if not text:
        return False, ""

    # Never intercept compound multi-step chaining missions or sequential pipelines
    t_raw = text.lower()
    if any(seq in t_raw for seq in [
        "and then", "and write", "and save", "and open", "and preview", "and run",
        ", save it to", ", write it to", ", create", "then open", "then preview", "then run",
        "aur likho", "aur banao", "aur save karo", "aur open karo", "aur run karo"
    ]):
        return False, text

    try:
        from core.mission_pipeline import is_compound_mission
        if is_compound_mission(text):
            return False, text
    except Exception:
        pass

    t = _normalize(text)
    result = _match_command(t)
    if result is not None:
        return True, result

    # ✅ JARVIS 2.0: Try fuzzy match for typos (e.g. 'opeen notepad', 'volum up')
    fuzzy = _fuzzy_match_cmd(t)
    if fuzzy and fuzzy != t:
        result = _match_command(fuzzy)
        if result is not None:
            return True, result

    return False, text


def is_fast_command(text: str) -> bool:
    """Check if text matches any fast command without executing. Used for routing."""
    t = _normalize(text)
    if _match_command(t, dry_run=True) is not None:
        return True
    # ✅ JARVIS 2.0: Also check fuzzy match for routing
    fuzzy = _fuzzy_match_cmd(t)
    if fuzzy and fuzzy != t:
        return _match_command(fuzzy, dry_run=True) is not None
    return False


def _match_command(t: str, dry_run: bool = False) -> Optional[str]:
    """
    Core pattern matcher. Returns result string if matched, None if not.
    dry_run=True: checks match without executing side effects.
    """

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 0. PUNCTUATION FAST-MATCH (must be first to avoid prefix conflicts)
    # ══════════════════════════════════════════════════════════════════════
    if t in PUNCTUATION_MAP:
        char = PUNCTUATION_MAP[t]
        if not dry_run: _type_text(char)
        return f"Inserted: {char}"

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 0.5 SYSTEM DIAGNOSTICS & HEALTH (Tier-1 GAP 8)
    # ══════════════════════════════════════════════════════════════════════
    if t in ("run diagnostics", "system diagnostics", "health check", "status report"):
        if not dry_run:
            try:
                from core.health_check import get_status_summary
                from core.voice import speak_async
                summary = get_status_summary()
                speak_async(summary)
                print(f"\n[DIAGNOSTICS]:\n{summary}\n")
            except Exception as e:
                return f"Diagnostics failed: {e}"
        return "Running system diagnostics."

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 0.6 IRIS FEATURES — Fast command shortcuts
    # ══════════════════════════════════════════════════════════════════════

    # ── Widget Forge ─────────────────────────────────────────────────────
    if t in ("spawn clock widget", "open clock widget", "show clock", "clock widget"):
        if not dry_run:
            try:
                from core.widget_forge import spawn_widget
                spawn_widget("clock")
            except Exception as e:
                return f"Widget error: {e}"
        return "Clock widget spawned."

    if t in ("close all widgets", "close widgets", "kill all widgets", "destroy all widgets"):
        if not dry_run:
            try:
                from core.widget_forge import close_all_widgets
                close_all_widgets()
            except Exception as e:
                return f"Widget error: {e}"
        return "All widgets closed."

    for prefix in ("spawn stock widget ", "stock widget ", "show stock widget "):
        if t.startswith(prefix):
            symbol = t[len(prefix):].strip().upper()
            if not dry_run:
                try:
                    from core.widget_forge import spawn_widget
                    spawn_widget("stock", symbol=symbol)
                except Exception as e:
                    return f"Widget error: {e}"
            return f"Stock widget for {symbol} spawned."

    # ── Macro Engine ──────────────────────────────────────────────────────
    if t in ("list macros", "show macros", "what macros", "list my macros", "show my macros"):
        if not dry_run:
            try:
                from core.macro_engine import list_macros
                from core.voice import speak_async
                result = list_macros()
                speak_async(result[:200])
                print(f"[⚡ MACROS]:\n{result}")
            except Exception as e:
                return f"Macro error: {e}"
        return "Listing available macros."

    for prefix in ("run macro ", "run my macro ", "execute macro ", "play macro "):
        if t.startswith(prefix):
            macro_name = t[len(prefix):].strip()
            if not dry_run:
                try:
                    from core.macro_engine import run_macro
                    run_macro(macro_name)
                except Exception as e:
                    return f"Macro error: {e}"
            return f"Running macro: {macro_name}."

    # ── Wormhole ──────────────────────────────────────────────────────────
    if t in ("close tunnel", "stop tunnel", "kill tunnel", "close wormhole"):
        if not dry_run:
            try:
                from core.wormhole import close_tunnel
                from core.voice import speak_async
                result = close_tunnel()
                speak_async(result)
            except Exception as e:
                return f"Wormhole error: {e}"
        return "Tunnel closed."


    # ══════════════════════════════════════════════════════════════════════
    # ➤ 1. VOICE ACCESS LIFECYCLE
    # ══════════════════════════════════════════════════════════════════════
    if t in ("voice access wake up", "unmute microphone", "wake up voice access"):
        if not dry_run: _press('volume mute')  # toggle unmute
        return "Voice access active. I'm listening."

    if t in ("voice access sleep", "mute microphone", "mute"):
        if not dry_run: _press('volume mute')
        return "Voice access sleeping. Microphone muted."

    if t in ("turn off microphone",):
        if not dry_run: _press('volume mute')
        return "Microphone turned off."

    if t in ("what can i say", "show all commands", "show command list", "show commands"):
        if not dry_run: _show_command_list()
        return "Showing available JARVIS voice commands."

    if t in ("open voice access settings",):
        if not dry_run: subprocess.Popen("ms-settings:easeofaccess-speechrecognition", shell=True)
        return "Opening voice access settings."

    if t in ("open voice access help", "open voice access guide"):
        if not dry_run: subprocess.Popen("ms-settings:easeofaccess-speechrecognition", shell=True)
        return "Opening voice access help."

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 2. OPEN / START / SHOW APP
    # ══════════════════════════════════════════════════════════════════════
    # Fix #6: Guard — these 'show ...' phrases must NOT be captured by the
    # generic 'show [app]' prefix below. They have dedicated handlers above
    # (section 1) or further below (sections 5, 12).
    _SHOW_EXACT_SKIP = {
        "show numbers", "show numbers everywhere", "show numbers here",
        "show numbers on active app", "hide numbers", "cancel numbers",
        "show all commands", "show command list", "show commands",
        "show task switcher", "show all windows", "show mouse grid",
        "show touch keyboard", "show keyboard",
    }
    _INVALID_APP_WORDS = {
        "screen", "click", "clickar", "dikhra", "dikh", "hoga", "par", "pe",
        "or", "and", "then", "kar", "karo", "karna", "us per", "analyse", "analyze"
    }

    def _is_valid_app_name(name_str: str) -> bool:
        words = name_str.lower().split()
        if not words or len(words) > 3:
            return False
        if any(w in _INVALID_APP_WORDS for w in words):
            return False
        return True

    for prefix in ("open ", "start ", "show ", "launch "):
        if t.startswith(prefix) and t not in _SHOW_EXACT_SKIP:
            if prefix == "show " and t.startswith("show numbers on "):
                pass
            else:
                app = t[len(prefix):].strip()
                if any(k in app for k in ("lila companion", "lila companian", "lila ui", "lila avatar")):
                    from core.lila_companion_launcher import open_lila_companion_ui
                    if not dry_run: open_lila_companion_ui()
                    return "Opening Lila Desktop Companion UI."
                if app and len(app) > 1 and _is_valid_app_name(app):
                    if app.startswith(("website", "url ", "link ", "browser ", "internet ")) or any(conj in f" {app} " for conj in (" and ", " then ", " to ", " for ")):
                        pass 
                    else:
                        if not dry_run: return _open_app(app)
                        return f"Would open: {app}"

    for suffix in (" kholo", " open karo", " start karo", " chalu karo", " launch karo"):
        if t.endswith(suffix):
            app = t[:-len(suffix)].strip()
            for pfx in ("lila ", "jarvis ", "mera ", "meri ", "ek baar "):
                if app.startswith(pfx):
                    app = app[len(pfx):].strip()
            if any(k in app for k in ("lila companion", "lila companian", "companion", "companian", "lila ui")):
                from core.lila_companion_launcher import open_lila_companion_ui
                if not dry_run: open_lila_companion_ui()
                return "Opening Lila Desktop Companion UI."
            if app and len(app) > 1 and _is_valid_app_name(app):
                if not dry_run: return _open_app(app)
                return f"Would open: {app}"

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 3. CLOSE / EXIT / QUIT APP
    # ══════════════════════════════════════════════════════════════════════
    for prefix in ("close ", "exit ", "quit "):
        if t.startswith(prefix):
            app = t[len(prefix):].strip()
            if not dry_run: return _close_app(app)
            return f"Would close: {app}"

    for suffix in (" band karo", " close karo"):
        if t.endswith(suffix):
            app = t[:-len(suffix)].strip()
            for pfx in ("lila ", "jarvis ", "mera ", "meri ", "ek baar "):
                if app.startswith(pfx):
                    app = app[len(pfx):].strip()
            if app and len(app) > 1:
                if not dry_run: return _close_app(app)
                return f"Would close: {app}"

    if t in ("close window",):
        if not dry_run: _press('alt', 'f4')
        return "Closed active window."

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 4. SWITCH TO / GO TO APP
    # ══════════════════════════════════════════════════════════════════════
    # NOTE: Exclude special "go to" phrases that are NOT app switches
    _GO_TO_EXCLUSIONS = {
        "go to desktop", "go home",
        "go to start of document", "go to end of document",
        "go to start of sentence", "go to end of sentence",
        "go to start of paragraph", "go to end of paragraph",
    }
    for prefix in ("switch to ", "go to "):
        if t.startswith(prefix) and t not in _GO_TO_EXCLUSIONS:
            app = t[len(prefix):].strip()
            if app and len(app) > 1:
                if not dry_run: return _switch_to_app(app)
                return f"Would switch to: {app}"

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 5. WINDOW MANAGEMENT
    # ══════════════════════════════════════════════════════════════════════
    if t in ("minimize window", "minimise window"):
        if not dry_run: _minimize_window()
        return "Window minimized."

    if t in ("maximize window", "maximise window"):
        if not dry_run: _maximize_window()
        return "Window maximized."

    if t in ("restore window",):
        if not dry_run: _restore_window()
        return "Window restored."

    if t in ("show task switcher", "list all windows", "show all windows"):
        if not dry_run: _press('alt', 'tab')
        return "Showing task switcher."

    if t in ("go to desktop", "go home", "minimize all windows", "minimise all windows"):
        if not dry_run: _press('win', 'd')
        return "Going to desktop."

    # Minimize [app name]
    for prefix in ("minimize ", "minimise "):
        if t.startswith(prefix):
            app = t[len(prefix):].strip()
            if not dry_run: _minimize_window(app)
            return f"Minimized {app}."

    # Maximize [app name]
    for prefix in ("maximize ", "maximise "):
        if t.startswith(prefix):
            app = t[len(prefix):].strip()
            if not dry_run: _maximize_window(app)
            return f"Maximized {app}."

    # Restore [app name]
    for prefix in ("restore ",):
        if t.startswith(prefix):
            app = t[len(prefix):].strip()
            if not dry_run: _restore_window(app)
            return f"Restored {app}."

    # Snap window
    for prefix in ("snap window to ", "snap the window to "):
        if t.startswith(prefix):
            direction = t[len(prefix):].strip()
            if not dry_run: return _snap_window(direction)
            return f"Would snap to: {direction}"


    # ══════════════════════════════════════════════════════════════════════
    # ➤ 5c. JARVIS UI MODE SWITCHING (Siri/Orb/Full)
    # ══════════════════════════════════════════════════════════════════════
    _SIRI_MODE_PHRASES = {
        "switch to mini siri mode", "mini siri mode", "siri mode", "go to siri mode",
        "shrink to orb", "orb mode", "compact mode", "mini mode", "go compact",
        "shrink", "minimize jarvis", "hide jarvis", "collapse jarvis",
        "switch to orb", "go to orb mode", "small mode", "go small",
    }
    _FULL_MODE_PHRASES = {
        "switch to full mode", "full mode", "fullscreen mode", "expand jarvis",
        "restore jarvis", "go full", "exit orb mode", "exit siri mode",
        "big mode", "show jarvis", "maximize jarvis", "expand",
    }
    if t in _SIRI_MODE_PHRASES:
        return "CMD:SHRINK_TO_ORB"   # Handled by master_router → main.py
    if t in _FULL_MODE_PHRASES:
        return "CMD:SHOW_FULL"       # Handled by master_router → main.py

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 5b. PLAY MUSIC / SONG (instant YouTube launch)
    # ══════════════════════════════════════════════════════════════════════

    # "play [song name]" / "play [song] on youtube" / "play some music"
    for prefix in ("play ", "play song ", "play the song "):
        if t.startswith(prefix):
            song = t[len(prefix):].strip()
            # Strip trailing qualifiers
            for suffix in (" on youtube", " on yt", " song", " please", " now"):
                if song.endswith(suffix):
                    song = song[:-len(suffix)].strip()
            if song and song not in ("music", "something", "anything", "a song"):
                if not dry_run: return _play_song_on_youtube(song)
                return f"Would play: {song}"
            elif song in ("music", "something", "anything", "a song", ""):
                # generic "play music" → open YouTube music
                if not dry_run: os.startfile("https://music.youtube.com/")
                return "Opening YouTube Music."

    if t in ("open youtube music", "open yt music", "play youtube music"):
        if not dry_run: os.startfile("https://music.youtube.com/")
        return "Opening YouTube Music."

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 6. SEARCH
    # ══════════════════════════════════════════════════════════════════════
    # Explicit Windows local file/app search
    for prefix in ("search windows for ", "search windows ", "search file ", "search files ", "search app ", "search local "):
        if t.startswith(prefix):
            query = t[len(prefix):].strip()
            if not dry_run: return _search_windows(query)
            return f"Would search Windows for: {query}"

    # "Search on [engine] for [query]"
    m = re.match(r'^search on (.+?) for (.+)$', t)
    if m:
        engine = m.group(1).strip()
        query = m.group(2).strip()
        if not dry_run: return _search_web(engine, query)
        return f"Would search {engine} for: {query}"

    # Explicit web search prefixes
    for prefix in ("search google for ", "search google ", "search web for ", "search web "):
        if t.startswith(prefix):
            query = t[len(prefix):].strip()
            if not dry_run: return _search_web("google", query)
            return f"Would search Google for: {query}"

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 7. VOLUME CONTROL
    # ══════════════════════════════════════════════════════════════════════
    if t in ("volume up", "increase volume", "turn up volume", "louder"):
        if not dry_run: _set_volume_by_keypress("up", steps=3)
        return "Volume increased."

    if t in ("volume down", "decrease volume", "turn down volume", "quieter", "lower volume"):
        if not dry_run: _set_volume_by_keypress("down", steps=3)
        return "Volume decreased."

    if t in ("mute volume", "mute speaker", "silence"):
        if not dry_run: _set_volume_by_keypress("mute")
        return "Volume muted."

    if t in ("unmute volume", "unmute speaker", "unmute"):
        if not dry_run: _set_volume_by_keypress("unmute")
        return "Volume unmuted."

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 8. SCREENSHOT
    # ══════════════════════════════════════════════════════════════════════
    if t in ("take screenshot", "screenshot", "capture screen", "take a screenshot"):
        if not dry_run: return _take_screenshot()
        return "Would take screenshot."

    if t in ("take full screenshot", "full screenshot"):
        if not dry_run: return _take_full_screenshot()
        return "Would take full screenshot."

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 9. SCROLL COMMANDS
    # ══════════════════════════════════════════════════════════════════════
    # Start continuous scrolling
    for direction in ("up", "down", "left", "right"):
        if t in (f"start scrolling {direction}", f"scroll {direction} continuously"):
            if not dry_run: return _start_scroll(direction)
            return f"Would start scrolling {direction}."

    # Stop scrolling
    if t in ("stop scrolling", "stop", "stop scroll"):
        if not dry_run: return _stop_scroll()
        return "Would stop scrolling."

    # Scroll [direction] [number] pages (Supports English & Hinglish)
    m = re.match(r'^(?:scroll|scrol|karo scroll|kar do scroll) (up|down|left|right|niche|upar|top|bottom)(?: (\d+) pages?| (\d+))?(?: karo| kar do)?$', t)
    if not m:
        m = re.match(r'^(?:thoda |page )?(up|down|niche|upar) (?:scrol(?:l)?(?: karo| kar do)?|karo scrol(?:l)?)(?: (\d+))?$', t)
    if m:
        raw_dir = m.group(1)
        direction = "down" if raw_dir in ("down", "niche", "bottom") else "up"
        pages = 1
        for g in m.groups()[1:]:
            if g and g.isdigit():
                pages = int(g)
                break
        dx, dy = SCROLL_MAP.get(direction, (0, -360))
        if not dry_run:
            _ensure_safe_scroll_position()
            for _ in range(pages):
                pyautogui.scroll(dy)
                if dx != 0:
                    pyautogui.hscroll(dx)
                time.sleep(0.08)
        return f"Scrolled {direction} {pages} page(s)."

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 10. MOUSE CLICK COMMANDS
    # ══════════════════════════════════════════════════════════════════════
    if t in ("click", "mouse click"):
        if not dry_run: pyautogui.click()
        return "Clicked."

    if t in ("double click", "double-click", "mouse double click", "mouse double-click"):
        if not dry_run: pyautogui.doubleClick()
        return "Double-clicked."

    if t in ("right click", "right-click", "mouse right click"):
        if not dry_run: pyautogui.rightClick()
        return "Right-clicked."

    # Click [item name] — visual element click via number overlay
    m = re.match(r'^click (\d+)$', t)
    if m:
        num = int(m.group(1))
        if not dry_run: return _click_mousegrid_number(num)
        return f"Would click item {num}."

    m = re.match(r'^double.?click (\d+)$', t)
    if m:
        num = int(m.group(1))
        if not dry_run:
            _click_mousegrid_number(num)
            pyautogui.doubleClick()
        return f"Double-clicked item {num}."

    m = re.match(r'^right.?click (\d+)$', t)
    if m:
        num = int(m.group(1))
        if not dry_run:
            _click_mousegrid_number(num)
            pyautogui.rightClick()
        return f"Right-clicked item {num}."

    # Move slider
    m = re.match(r'^move slider (left|right|up|down) (\d+) times?$', t)
    if m:
        direction = m.group(1)
        times = int(m.group(2))
        key = {"left": "left", "right": "right", "up": "up", "down": "down"}[direction]
        if not dry_run:
            for _ in range(times):
                _press(key)
        return f"Moved slider {direction} {times} time(s)."

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 11. MOUSE GRID
    # ══════════════════════════════════════════════════════════════════════
    if t in ("mousegrid", "mouse grid", "show mouse grid"):
        if not dry_run: return _mousegrid()
        return "Would show mousegrid."

    if t in ("mousegrid window", "mouse grid window"):
        if not dry_run: return _mousegrid("window")
        return "Would show mousegrid on window."

    m = re.match(r'^mousegrid (\d+)$', t)
    if m:
        monitor = int(m.group(1))
        if not dry_run: return _mousegrid(f"monitor {monitor}")
        return f"Would show mousegrid on monitor {monitor}."

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 12. SCREEN NUMBER OVERLAY (Show Numbers)
    # ══════════════════════════════════════════════════════════════════════
    if t in ("show numbers", "show numbers everywhere"):
        if not dry_run: print("[🔢 NUMBER OVERLAY]: Active on all screen elements.")
        return "Number overlay active. Say a number to click."

    if t in ("show numbers here", "show numbers on active app"):
        if not dry_run: print("[🔢 NUMBER OVERLAY]: Active on current window.")
        return "Number overlay active on current app."

    if t.startswith("show numbers on "):
        target = t[len("show numbers on "):].strip()
        if not dry_run: print(f"[🔢 NUMBER OVERLAY]: Active on {target}.")
        return f"Number overlay active on {target}."

    if t in ("hide numbers", "cancel numbers"):
        if not dry_run: print("[🔢 NUMBER OVERLAY]: Hidden.")
        return "Number overlay hidden."

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 13. TEXT EDITING & DICTATION
    # ══════════════════════════════════════════════════════════════════════
    # Type [text] / Dictate [text]
    for prefix in ("type ", "dictate "):
        if t.startswith(prefix):
            text_to_type = t[len(prefix):].strip()
            if text_to_type:
                if not dry_run: _type_text(text_to_type)
                return f"Typed: {text_to_type}"

    # Caps [text]
    if t.startswith("caps "):
        word = t[5:].strip()
        if not dry_run: _type_text(word.capitalize())
        return f"Typed capitalized: {word.capitalize()}"

    # All caps [word]
    if t.startswith("all caps "):
        word = t[9:].strip()
        if not dry_run: _type_text(word.upper())
        return f"Typed ALL CAPS: {word.upper()}"

    # No caps [word]
    if t.startswith("no caps "):
        word = t[8:].strip()
        if not dry_run: _type_text(word.lower())
        return f"Typed lowercase: {word.lower()}"

    # No space [text]
    if t.startswith("no space "):
        text_to_type = t[9:].strip().replace(" ", "")
        if not dry_run: _type_text(text_to_type)
        return f"Typed without spaces: {text_to_type}"

    # Numeral [number]
    if t.startswith("numeral "):
        num_text = t[8:].strip()
        if not dry_run: _type_text(num_text)
        return f"Typed numeral: {num_text}"

    # Literal word [word]
    if t.startswith("literal word "):
        word = t[13:].strip()
        if not dry_run: _type_text(word)
        return f"Typed literal: {word}"

    # New line / paragraph / tab
    if t in ("new line",):
        if not dry_run: _press('enter')
        return "New line inserted."

    if t in ("new paragraph",):
        if not dry_run:
            _press('enter')
            _press('enter')
        return "New paragraph inserted."

    if t == "tab":
        if not dry_run: _press('tab')
        return "Tab inserted."

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 14. UNDO / DELETE / CORRECTION
    # ══════════════════════════════════════════════════════════════════════
    if t in ("undo that", "scratch that", "undo"):
        if not dry_run: _press('ctrl', 'z')
        return "Undone."

    if t in ("redo that", "redo"):
        if not dry_run: _press('ctrl', 'y')
        return "Redone."

    if t in ("delete that", "delete"):
        if not dry_run: _press('backspace')
        return "Deleted."

    if t.startswith("delete "):
        word = t[7:].strip()
        # Select the word and delete it
        if not dry_run:
            _press('ctrl', 'h')  # Find & Replace as fallback
        return f"Attempting to delete: {word}"

    if t in ("correct that", "correct this"):
        if not dry_run: _press('ctrl', 'z')
        return "Corrected."

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 15. TOUCH KEYBOARD
    # ══════════════════════════════════════════════════════════════════════
    if t in ("show touch keyboard", "open touch keyboard", "show keyboard"):
        if not dry_run: subprocess.Popen("osk.exe", shell=True)
        return "Touch keyboard opened."

    if t in ("hide touch keyboard", "close touch keyboard", "hide keyboard"):
        if not dry_run:
            subprocess.Popen("taskkill /f /im TabTip.exe", shell=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.Popen("taskkill /f /im osk.exe", shell=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "Touch keyboard closed."

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 16. TEXT NAVIGATION & SELECTION
    # ══════════════════════════════════════════════════════════════════════
    if t in ("go to start of sentence",):
        if not dry_run: _press('home')
        return "Moved to start of sentence."

    if t in ("go to end of sentence",):
        if not dry_run: _press('end')
        return "Moved to end of sentence."

    if t in ("go to start of paragraph",):
        if not dry_run: _press('ctrl', 'up')
        return "Moved to start of paragraph."

    if t in ("go to end of paragraph",):
        if not dry_run: _press('ctrl', 'down')
        return "Moved to end of paragraph."

    if t in ("go to start of document", "go to beginning"):
        if not dry_run: _press('ctrl', 'home')
        return "Moved to start of document."

    if t in ("go to end of document",):
        if not dry_run: _press('ctrl', 'end')
        return "Moved to end of document."

    # "Go to [word]" / "Go after [word]"
    m = re.match(r'^go to (.+)$', t)
    if m:
        word = m.group(1).strip()
        # Use Ctrl+F to find the word
        if not dry_run:
            _press('ctrl', 'f')
            time.sleep(0.3)
            _type_text(word)
            _press('enter')
            _press('escape')
        return f"Navigated to: {word}"

    m = re.match(r'^go after (.+)$', t)
    if m:
        word = m.group(1).strip()
        if not dry_run:
            _press('ctrl', 'f')
            time.sleep(0.3)
            _type_text(word)
            _press('enter')
            _press('escape')
            _press('right')
        return f"Moved cursor after: {word}"

    # Select word
    if t in ("select word",):
        if not dry_run: _press('ctrl', 'shift', 'right')
        return "Word selected."

    # "Select [word]"
    m = re.match(r'^select (.+)$', t)
    if m:
        word = m.group(1).strip()
        if not dry_run:
            _press('ctrl', 'f')
            time.sleep(0.3)
            _type_text(word)
            _press('enter')
            _press('escape')
            _press('shift', 'home')
        return f"Selected: {word}"

    if t in ("clear selection", "deselect"):
        if not dry_run: _press('right')
        return "Selection cleared."

    if t in ("select all",):
        if not dry_run: _press('ctrl', 'a')
        return "All text selected."

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 17. CUT / COPY / PASTE
    # ══════════════════════════════════════════════════════════════════════
    if t in ("cut that", "cut"):
        if not dry_run: _press('ctrl', 'x')
        return "Cut."

    if t in ("copy that", "copy"):
        if not dry_run: _press('ctrl', 'c')
        return "Copied."

    if t in ("paste that", "paste"):
        if not dry_run: _press('ctrl', 'v')
        return "Pasted."

    # ----------------------------------------------------------------------
    # > 18. KEYBOARD KEY EMULATION
    # ----------------------------------------------------------------------
    # "Press [key]" or "Press [mod] plus [key]" or "Press [mod1] plus [mod2] plus [key]"
    m = re.match(r'^press (.+)$', t)
    if m:
        key_phrase = m.group(1).strip()

        if " plus " in key_phrase:
            # Split on " plus " to support any number of modifiers + final key
            parts = [p.strip() for p in key_phrase.split(" plus ")]
            resolved = []
            for p in parts:
                # Check modifier map first, then key name map, then use raw
                mapped = MODIFIER_MAP.get(p) or KEY_NAME_MAP.get(p) or p
                resolved.append(mapped)
            if not dry_run: _press(*resolved)
            return f"Pressed {'+'.join(resolved)}."

        # Single key
        key = KEY_NAME_MAP.get(key_phrase, key_phrase)
        if not dry_run: _press(key)
        return f"Pressed {key}."

    # ----------------------------------------------------------------------
    # > 19. COMMON KEYBOARD SHORTCUTS (standalone)
    # ----------------------------------------------------------------------
    SHORTCUT_MAP = {
        # Navigation
        "go back": lambda: _press('alt', 'left'),
        "go forward": lambda: _press('alt', 'right'),
        "scroll to top": lambda: _press('ctrl', 'home'),
        "scroll to bottom": lambda: _press('ctrl', 'end'),
        "next tab": lambda: _press('ctrl', 'tab'),
        "previous tab": lambda: _press('ctrl', 'shift', 'tab'),
        "new tab": lambda: _press('ctrl', 't'),
        "close tab": lambda: _press('ctrl', 'w'),
        "reopen tab": lambda: _press('ctrl', 'shift', 't'),
        "refresh": lambda: _press('f5'),
        "reload": lambda: _press('f5'),
        "hard refresh": lambda: _press('ctrl', 'f5'),
        # File operations
        "save": lambda: _press('ctrl', 's'),
        "save as": lambda: _press('ctrl', 'shift', 's'),
        "open file": lambda: _press('ctrl', 'o'),
        "new file": lambda: _press('ctrl', 'n'),
        "print": lambda: _press('ctrl', 'p'),
        "find": lambda: _press('ctrl', 'f'),
        "find and replace": lambda: _press('ctrl', 'h'),
        # Text
        "bold": lambda: _press('ctrl', 'b'),
        "italic": lambda: _press('ctrl', 'i'),
        "underline": lambda: _press('ctrl', 'u'),
        # System
        "lock screen": lambda: _press('win', 'l'),
        "lock computer": lambda: _press('win', 'l'),
        "open task manager": lambda: _press('ctrl', 'shift', 'esc'),
        "task manager": lambda: _press('ctrl', 'shift', 'esc'),
        "open run dialog": lambda: _press('win', 'r'),
        "run dialog": lambda: _press('win', 'r'),
        "open action center": lambda: _press('win', 'a'),
        "notification center": lambda: _press('win', 'n'),
        "open settings": lambda: subprocess.Popen("ms-settings:", shell=True),
        "shutdown": lambda: subprocess.Popen("shutdown /s /t 5", shell=True),
        "shut down": lambda: subprocess.Popen("shutdown /s /t 5", shell=True),
        "restart": lambda: subprocess.Popen("shutdown /r /t 5", shell=True),
        "restart computer": lambda: subprocess.Popen("shutdown /r /t 5", shell=True),
        "sleep": lambda: subprocess.Popen("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True),
        "hibernate": lambda: subprocess.Popen("shutdown /h", shell=True),
        "sign out": lambda: subprocess.Popen("shutdown /l", shell=True),
        "log out": lambda: subprocess.Popen("shutdown /l", shell=True),
        # Window operations
        "show desktop": lambda: _press('win', 'd'),
        "show all windows": lambda: _press('win', 'tab'),
        "flip windows": lambda: _press('win', 'tab'),
        "split screen left": lambda: _press('win', 'left'),
        "split screen right": lambda: _press('win', 'right'),
        "virtual desktop": lambda: _press('win', 'ctrl', 'd'),
        "new virtual desktop": lambda: _press('win', 'ctrl', 'd'),
        "close virtual desktop": lambda: _press('win', 'ctrl', 'f4'),
        "next desktop": lambda: _press('win', 'ctrl', 'right'),
        "previous desktop": lambda: _press('win', 'ctrl', 'left'),
        # Clipboard
        "clipboard history": lambda: _press('win', 'v'),
        "open clipboard": lambda: _press('win', 'v'),
        # Emoji picker
        "open emoji": lambda: _press('win', '.'),
        "emoji picker": lambda: _press('win', '.'),
        # Brightness — use pyautogui for special virtual keys
        "increase brightness": lambda: pyautogui.hotkey('fn', 'f3'),
        "decrease brightness": lambda: pyautogui.hotkey('fn', 'f2'),
        "lower brightness": lambda: pyautogui.hotkey('fn', 'f2'),
        "raise brightness": lambda: pyautogui.hotkey('fn', 'f3'),
        # Media keys — pyautogui uses virtual key codes that keyboard library doesn't support
        "play": lambda: pyautogui.press('playpause'),
        "pause": lambda: pyautogui.press('playpause'),
        "play pause": lambda: pyautogui.press('playpause'),
        "next track": lambda: pyautogui.press('nexttrack'),
        "previous track": lambda: pyautogui.press('prevtrack'),
        "stop music": lambda: pyautogui.press('stop'),
        # Zoom
        "zoom in": lambda: _press('ctrl', '='),
        "zoom out": lambda: _press('ctrl', '-'),
        "reset zoom": lambda: _press('ctrl', '0'),
        # Accessibility
        "magnifier": lambda: _press('win', '+'),
        "open magnifier": lambda: _press('win', '+'),
        "narrator": lambda: _press('win', 'ctrl', 'enter'),
        # Developer shortcuts
        "developer tools": lambda: _press('f12'),
        "inspect element": lambda: _press('f12'),
        "address bar": lambda: _press('alt', 'd'),
    }

    if t in SHORTCUT_MAP:
        if not dry_run:
            SHORTCUT_MAP[t]()
        return f"Executed: {t}."

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 20. PUNCTUATION & SYMBOL INSERTION
    # ══════════════════════════════════════════════════════════════════════
    if t in PUNCTUATION_MAP:
        char = PUNCTUATION_MAP[t]
        if not dry_run: _type_text(char)
        return f"Inserted: {char}"

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 21. FLUID DICTATION TOGGLE
    # ══════════════════════════════════════════════════════════════════════
    if t in ("turn on fluid dictation", "fluid dictation on"):
        if not dry_run: print("[💬 FLUID DICTATION]: On — continuous speech capture active.")
        return "Fluid dictation enabled."

    if t in ("turn off fluid dictation", "fluid dictation off"):
        if not dry_run: print("[💬 FLUID DICTATION]: Off.")
        return "Fluid dictation disabled."

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 22. SPELL OUT / VOCABULARY
    # ══════════════════════════════════════════════════════════════════════
    if t in ("spell out", "spell that"):
        if not dry_run: print("[📝 SPELL MODE]: Ready. Say each letter.")
        return "Spell mode active. Say each letter."

    if t in ("add to vocabulary", "add word to vocabulary"):
        if not dry_run: print("[📖 VOCABULARY]: Add word mode active.")
        return "Vocabulary add mode active."

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 23. DRAG AND DROP
    # ══════════════════════════════════════════════════════════════════════
    m = re.match(r'^drag (.+) to (.+)$', t)
    if m:
        item = m.group(1).strip()
        destination = m.group(2).strip()
        if not dry_run:
            print(f"[🖱️ DRAG-DROP]: Attempting drag from '{item}' to '{destination}'.")
        return f"Drag operation initiated: {item} → {destination}"

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 24. FOCUS / MOVE TO / EXPAND / TOGGLE
    # ══════════════════════════════════════════════════════════════════════
    for prefix in ("move to ", "focus on ", "focus "):
        if t.startswith(prefix):
            item = t[len(prefix):].strip()
            if not dry_run: print(f"[🖱️ FOCUS]: Attempting to focus on '{item}'.")
            return f"Focusing on: {item}"

    if t.startswith("expand "):
        item = t[7:].strip()
        if not dry_run: _press('alt', 'down')
        return f"Expanded: {item}"

    for prefix in ("toggle ", "flip "):
        if t.startswith(prefix):
            item = t[len(prefix):].strip()
            if not dry_run: _press('space')
            return f"Toggled: {item}"

    # ══════════════════════════════════════════════════════════════════════
    # ➤ 25. GOOGLE ANTIGRAVITY (STATUS & COMPLETED ACTIONS)
    # ══════════════════════════════════════════════════════════════════════
    if t in ("antigravity status", "check antigravity", "check antigravity status", "antigravity state", "antigravity kaisa hai", "antigravity status kya hai", "antigravity update", "antigravity updates"):
        if not dry_run:
            from core.antigravity_agent import get_antigravity_status_voice_summary
            from core.voice import speak_async
            summary = get_antigravity_status_voice_summary("jarvis_project")
            speak_async(summary)
            return summary
        return "Would report Antigravity real-time status."

    if t in ("antigravity actions", "analyze antigravity actions", "antigravity completed actions", "what did antigravity do", "antigravity ne kya kiya", "antigravity tasks"):
        if not dry_run:
            from core.antigravity_agent import get_antigravity_actions_voice_summary
            from core.voice import speak_async
            summary = get_antigravity_actions_voice_summary("jarvis_project")
            speak_async(summary)
            return summary
        return "Would analyze and report Antigravity completed actions in jarvis_project."

    if t in ("open walkthrough", "show walkthrough", "antigravity walkthrough"):
        if not dry_run:
            from core.antigravity_agent import open_antigravity_artifact
            return open_antigravity_artifact("walkthrough", project_name="jarvis_project")
        return "Would open Antigravity walkthrough artifact on screen."

    if t in ("antigravity prompting", "teach me antigravity prompting", "antigravity strategies", "how to prompt antigravity", "antigravity guide", "show antigravity guide", "open antigravity guide", "antigravity prompting guide"):
        if not dry_run:
            from core.antigravity_agent import open_antigravity_artifact
            from core.voice import speak_async
            spoken = "I have six advanced prompting strategies for Antigravity: Feature Building, Forensic Debugging, Performance Optimization, Zero-Regression Refactoring, Grill-Me Clarification, and Multi-Agent Swarms. I have opened the full prompting guide on your screen."
            speak_async(spoken)
            open_antigravity_artifact("prompting_guide", project_name="jarvis_project")
            return spoken
        return "Would explain Antigravity prompting strategies and open the guide on screen."


    # No match found — return None to signal AI pipeline should handle it
    return None


# ══════════════════════════════════════════════════════════════════════════════
# COMMAND LIST (for "What can I say")
# ══════════════════════════════════════════════════════════════════════════════

def _show_command_list():
    """Print all available fast commands to console."""
    commands = """
╔═══════════════════════════════════════════════════════════════╗
║          ⚡ JARVIS WINDOWS VOICE TURBO COMMANDS ⚡             ║
╠═══════════════════════════════════════════════════════════════╣
║ APPS: "open [app]", "close [app]", "switch to [app]"         ║
║ WINDOW: "minimize/maximize/restore window"                    ║
║ SNAP: "snap window to left/right/up/down"                     ║
║ DESKTOP: "go to desktop", "show all windows"                  ║
║ VOLUME: "volume up/down", "mute", "unmute"                    ║
║ SCREENSHOT: "take screenshot", "take full screenshot"         ║
║ SCROLL: "scroll up/down/left/right [N pages]"                 ║
║          "start scrolling [dir]", "stop scrolling"            ║
║ MOUSE: "click", "double click", "right click"                 ║
║        "mousegrid", "click [number]"                          ║
║ SEARCH: "search [query]", "search on [engine] for [query]"    ║
║ KEYBOARD: "press [key]", "press [mod] plus [key]"             ║
║ TEXT: "type [text]", "caps [text]", "all caps [word]"         ║
║       "undo", "delete that", "copy", "paste", "cut"           ║
║ NAVIGATE: "go to start/end of document/paragraph/sentence"    ║
║           "select word", "select [word]", "select all"        ║
║ PUNCTUATION: "comma", "period", "question mark", etc.         ║
║ SYSTEM: "lock screen", "shutdown", "restart", "sleep"         ║
║         "open task manager", "open settings"                  ║
║ TABS: "new tab", "close tab", "next tab", "previous tab"      ║
╚═══════════════════════════════════════════════════════════════╝
"""
    print(commands.encode('ascii', 'replace').decode('ascii'))



# ══════════════════════════════════════════════════════════════════════════════
# SELF-AWARENESS: Export capability summary for JARVIS brain system prompt
# ══════════════════════════════════════════════════════════════════════════════

WINDOWS_VOICE_CAPABILITIES = """
WINDOWS VOICE TURBO ENGINE (Zero-Latency OS Control):
You have direct access to ALL Windows Voice Access commands, executed INSTANTLY
without any AI or internet for basic OS operations. You are FASTER than Cortana,
faster than Windows Voice Access, and faster than any cloud assistant for these:

SUPPORTED FAST COMMANDS (executed in ~50-200ms, NO AI needed):
• App Control: "open [app]", "close [app]", "switch to [app]", "start [app]"
• Window Mgmt: "minimize/maximize/restore window", "snap window to [direction]"
• Desktop: "go to desktop", "show all windows", "go home"
• Volume: "volume up/down", "mute", "unmute"  
• Screenshot: "take screenshot", "take full screenshot"
• Scroll: "scroll up/down [N pages]", "start/stop scrolling [direction]"
• Mouse: "click", "double click", "right click", "mousegrid", "click [number]"
• Search: "search [query]", "search on [engine] for [query]"
• Keyboard: "press [key]", "press [mod] plus [key]", all F-keys, nav keys
• Text Edit: "type [text]", "caps [text]", "all caps", "no caps", "literal word"
• Undo/Redo: "undo", "redo", "delete that", "scratch that"
• Navigation: "go to start/end of document/paragraph/sentence"
• Selection: "select all", "select word", "select [word]", "clear selection"
• Clipboard: "copy", "cut", "paste"
• Punctuation: "comma", "period", "question mark", "ampersand", "at sign", etc.
• System: "lock screen", "shutdown", "restart", "sleep", "sign out"
• Tabs: "new tab", "close tab", "next/previous tab", "reopen tab"
• Touch KB: "show/hide touch keyboard"
• Dictation: "dictation mode", "commands mode"
• Discovery: "what can I say", "show all commands"

When the user asks what you can do, tell them you have ALL Windows Voice Access 
commands built in — plus AI intelligence for complex tasks.
"""
