"""
win_os_agent.py — Modern Win32 & UIAutomation OS Controller (2026 Engine)
========================================================================
Replaces traditional keyboard simulations (Win+S, Win+Shift+S, Alt+F4, taskkill /f)
with deterministic, high-accuracy Windows Win32 APIs:
  • Exact window handle (HWND) enumeration & foreground focusing
  • Dynamic Windows application discovery via Registry, Start Menu, & AppData
  • Graceful WM_CLOSE message dispatch (prevents data loss)
  • Non-blocking in-memory screenshot capture & Gemini Vision analysis
  • Instant clipboard-injected Unicode & emoji typing (Ctrl+V)
  • Deterministic window snapping via SetWindowPos
"""

import os
import sys
import re
import time
import glob
import winreg
import difflib
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

import psutil
import pyperclip
import ctypes

# Win32 dependencies
try:
    import win32gui
    import win32con
    import win32process
    import win32api
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

try:
    import mss
    import mss.tools
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

from core.jarvis_logger import log_info, log_warn, log_error

# ─────────────────────────────────────────────────────────────────────────────
# 1. WINDOW ENUMERATION & TARGET RESOLUTION
# ─────────────────────────────────────────────────────────────────────────────

_hdesk_cached = None

def attach_to_user_desktop():
    """Attaches thread to the interactive user desktop winsta0\\default."""
    global _hdesk_cached
    if _hdesk_cached is not None:
        return _hdesk_cached
    try:
        import win32service
        winsta0 = win32service.OpenWindowStation('winsta0', False, 0x037F)
        winsta0.SetProcessWindowStation()
        _hdesk_cached = win32service.OpenDesktop('default', 0, False, 0x01FF)
        _hdesk_cached.SetThreadDesktop()
        return _hdesk_cached
    except Exception:
        return None


def get_open_windows() -> List[Dict[str, Any]]:
    """Returns a list of all visible top-level windows with their HWND, title, and process name."""
    if not HAS_WIN32:
        return []

    windows = []
    hdesk = attach_to_user_desktop()

    def enum_cb(hwnd, extra):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return True

            rect = win32gui.GetWindowRect(hwnd)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            if w <= 10 or h <= 10:
                return True

            proc_name = ""
            pid = 0
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid > 0:
                    p = psutil.Process(pid)
                    proc_name = p.name()
            except Exception:
                pass

            if proc_name.lower() in ["shellexperiencehost.exe"] and title in ["Start", "Search"]:
                return True

            windows.append({
                "hwnd": hwnd,
                "title": title,
                "pid": pid,
                "proc_name": proc_name,
                "rect": rect,
            })
        except Exception:
            pass
        return True

    try:
        if hdesk:
            win32gui.EnumDesktopWindows(hdesk, enum_cb, None)
        else:
            win32gui.EnumWindows(enum_cb, None)
    except Exception as e:
        log_error("win_os_agent", "get_open_windows", e)

    return windows


BROWSER_PROCESS_NAMES = {
    "brave.exe", "chrome.exe", "msedge.exe", "firefox.exe",
    "opera.exe", "vivaldi.exe", "arc.exe"
}


def find_browser_window() -> Optional[Dict[str, Any]]:
    """Finds the most recently active or visible browser window on desktop."""
    windows = get_open_windows()
    if not windows:
        return None

    # Priority 1: Currently foreground window if it's a browser
    if HAS_WIN32:
        try:
            fg = win32gui.GetForegroundWindow()
            for win in windows:
                if win["hwnd"] == fg and win["proc_name"].lower() in BROWSER_PROCESS_NAMES:
                    return win
        except Exception:
            pass

    # Priority 2: Brave browser (User's preferred browser)
    for win in windows:
        if win["proc_name"].lower() == "brave.exe":
            return win

    # Priority 3: Any window from a recognized browser process (excluding DevTools, Lila Companion, VS Code, Antigravity)
    for win in windows:
        t_low = win["title"].lower()
        if any(ex in t_low for ex in ["lila companion", "visual studio code", "antigravity"]):
            continue
        if win["proc_name"].lower() in BROWSER_PROCESS_NAMES:
            return win

    # Priority 3: Any window matching recognized browser processes even if title is generic
    for win in windows:
        if win["proc_name"].lower() in BROWSER_PROCESS_NAMES:
            return win

    return None


def get_desktop_focus_topology() -> Dict[str, Any]:
    """
    Returns real-time desktop window topology:
      - foreground: { hwnd, title, proc_name, pid, rect, is_minimized, is_maximized, category }
      - background: [ { hwnd, title, proc_name, pid, rect, is_minimized, is_maximized, category }, ... ]
      - browser: { status: 'foreground' | 'background' | 'minimized' | 'not_running', window: dict or None }
    """
    attach_to_user_desktop()
    windows = get_open_windows()
    fg_hwnd = win32gui.GetForegroundWindow() if HAS_WIN32 else 0

    fg_info = None
    bg_list = []

    def _get_category(proc: str, title: str) -> str:
        p = (proc or "").lower()
        t = (title or "").lower()
        if p in BROWSER_PROCESS_NAMES:
            return "browser"
        elif any(ide in p for ide in ["antigravity", "code", "devenv"]):
            return "ide"
        elif "electron" in p or "lila" in t:
            return "companion_overlay"
        elif any(term in p for term in ["powershell", "cmd", "windowsterminal", "conhost"]):
            return "terminal"
        elif any(med in p for med in ["spotify", "vlc", "wmplayer"]):
            return "media_player"
        elif "explorer" in p:
            return "file_manager"
        return "application"

    for w in windows:
        h = w["hwnd"]
        is_iconic = bool(win32gui.IsIconic(h)) if HAS_WIN32 else False
        is_zoomed = bool(ctypes.windll.user32.IsZoomed(h)) if HAS_WIN32 else False
        cat = _get_category(w.get("proc_name", ""), w.get("title", ""))
        item = {
            "hwnd": h,
            "title": w.get("title", ""),
            "proc_name": w.get("proc_name", ""),
            "pid": w.get("pid", 0),
            "rect": w.get("rect", (0, 0, 0, 0)),
            "is_minimized": is_iconic,
            "is_maximized": is_zoomed,
            "category": cat
        }
        if h == fg_hwnd and fg_hwnd != 0:
            fg_info = item
        else:
            bg_list.append(item)

    if not fg_info and fg_hwnd and HAS_WIN32:
        try:
            _, pid = win32process.GetWindowThreadProcessId(fg_hwnd)
            pname = psutil.Process(pid).name() if pid > 0 else ""
            t = win32gui.GetWindowText(fg_hwnd).strip()
            fg_info = {
                "hwnd": fg_hwnd,
                "title": t,
                "proc_name": pname,
                "pid": pid,
                "rect": win32gui.GetWindowRect(fg_hwnd) if win32gui.IsWindow(fg_hwnd) else (0, 0, 0, 0),
                "is_minimized": bool(win32gui.IsIconic(fg_hwnd)),
                "is_maximized": bool(ctypes.windll.user32.IsZoomed(fg_hwnd)),
                "category": _get_category(pname, t)
            }
        except Exception:
            fg_info = {"hwnd": fg_hwnd, "title": "", "proc_name": "", "pid": 0, "rect": (0, 0, 0, 0), "is_minimized": False, "is_maximized": False, "category": "unknown"}

    # Determine browser specific status
    browser_win = find_browser_window()
    browser_status = "not_running"
    if browser_win and browser_win.get("hwnd"):
        bh = browser_win["hwnd"]
        if bh == fg_hwnd:
            browser_status = "foreground"
        elif HAS_WIN32 and win32gui.IsIconic(bh):
            browser_status = "minimized"
        else:
            browser_status = "background"

    return {
        "foreground": fg_info or {"hwnd": 0, "title": "Desktop", "proc_name": "explorer.exe", "pid": 0, "rect": (0, 0, 0, 0), "is_minimized": False, "is_maximized": False, "category": "desktop"},
        "background": bg_list,
        "browser": {
            "status": browser_status,
            "window": browser_win
        }
    }


def get_app_focus_status(app_name_or_proc: str) -> Dict[str, Any]:
    """
    Resolves the exact desktop state for a specific application:
    Returns:
      {
        "status": "foreground" | "background" | "minimized" | "not_running",
        "hwnd": int or None,
        "title": str,
        "proc_name": str,
        "current_foreground": { "hwnd", "title", "proc_name" }
      }
    """
    topology = get_desktop_focus_topology()
    fg = topology["foreground"]
    target_low = (app_name_or_proc or "").lower().strip()
    if target_low.endswith(".exe"):
        target_low = target_low[:-4]

    # Check if target matches foreground
    fg_proc = fg.get("proc_name", "").lower()
    fg_title = fg.get("title", "").lower()
    if target_low in fg_proc or target_low in fg_title:
        return {
            "status": "foreground",
            "hwnd": fg.get("hwnd"),
            "title": fg.get("title", ""),
            "proc_name": fg.get("proc_name", ""),
            "current_foreground": fg
        }

    # Check background windows
    for w in topology["background"]:
        p = w.get("proc_name", "").lower()
        t = w.get("title", "").lower()
        if target_low in p or target_low in t:
            status = "minimized" if w.get("is_minimized") else "background"
            return {
                "status": status,
                "hwnd": w.get("hwnd"),
                "title": w.get("title", ""),
                "proc_name": w.get("proc_name", ""),
                "current_foreground": fg
            }

    # Check if running process exists without window
    try:
        for p in psutil.process_iter(['name']):
            pname = (p.info['name'] or "").lower()
            if target_low in pname:
                return {
                    "status": "background",
                    "hwnd": None,
                    "title": "",
                    "proc_name": p.info['name'],
                    "current_foreground": fg
                }
    except Exception:
        pass

    return {
        "status": "not_running",
        "hwnd": None,
        "title": "",
        "proc_name": "",
        "current_foreground": fg
    }


def dismiss_browser_popups():
    """Dismisses browser crash recovery prompts ('Restore pages?') or dialogs by sending Escape."""
    try:
        import ctypes
        # VK_ESCAPE is 0x1B
        ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
        time.sleep(0.04)
        ctypes.windll.user32.keybd_event(0x1B, 0, 2, 0)
    except Exception:
        pass



def get_preferred_browser_executable(preferred_proc_name: Optional[str] = None) -> str:
    """
    Returns the absolute path of the preferred browser executable.
    Dynamically respects running browser process name, Windows UserChoice default browser, and Brave priority.
    """
    if preferred_proc_name:
        p_low = preferred_proc_name.lower()
        if "brave" in p_low:
            for b in [
                r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
            ]:
                if os.path.exists(b):
                    return b
        elif "chrome" in p_low:
            for b in [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ]:
                if os.path.exists(b):
                    return b
        elif "edge" in p_low or "msedge" in p_low:
            for b in [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            ]:
                if os.path.exists(b):
                    return b

    # Check Windows Registry default browser (UserChoice)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as key:
            prog_id, _ = winreg.QueryValueEx(key, "ProgId")
            if "brave" in prog_id.lower():
                b = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
                if os.path.exists(b):
                    return b
            elif "chrome" in prog_id.lower():
                b = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
                if os.path.exists(b):
                    return b
            elif "edge" in prog_id.lower():
                b = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
                if os.path.exists(b):
                    return b
    except Exception:
        pass

    # Standard candidate search prioritizing Brave
    candidates = [
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "brave.exe"


def launch_or_focus_browser(url: Optional[str] = None) -> bool:
    """
    Brings Brave (or active browser) to the visible foreground window.
    If already open, restores it and brings it in front of Antigravity.
    If not open, spawns it via direct subprocess with --start-maximized, waits for HWND, and foregrounds it.
    """
    attach_to_user_desktop()
    bw = find_browser_window()
    if bw and bw.get("hwnd"):
        force_foreground_window(bw["hwnd"])
        dismiss_browser_popups()
        if url:
            navigate_active_browser(url)
        return True

    browser_exe = get_preferred_browser_executable()
    target_url = url or "https://www.google.com"
    if browser_exe and os.path.exists(browser_exe):
        try:
            subprocess.Popen([browser_exe, "--new-window", "--start-maximized", target_url])
        except Exception:
            cmd = f'cmd.exe /c start "" "{browser_exe}" --new-window --start-maximized "{target_url}"'
            subprocess.Popen(cmd, shell=True)
    else:
        try:
            os.startfile(target_url)
        except Exception:
            import webbrowser
            webbrowser.open(target_url)

    # Wait up to 3.5 seconds for browser window to appear and bring it to front
    for _ in range(14):
        time.sleep(0.25)
        bw = find_browser_window()
        if bw and bw.get("hwnd"):
            force_foreground_window(bw["hwnd"])
            dismiss_browser_popups()
            return True
    return True


def navigate_active_browser(url: str) -> bool:
    """
    Intelligently navigates active browser based on desktop focus & background state:
    1. Checks if browser is in FOREGROUND, BACKGROUND, MINIMIZED, or NOT RUNNING.
    2. If NOT RUNNING: launches browser with target URL.
    3. If BACKGROUND or MINIMIZED:
       - Restores and brings browser to foreground via verified Windows 11 lock-breaker.
       - If browser is verified in foreground: navigates address bar (Ctrl+L -> paste URL -> Enter).
       - If keystroke injection or focus is delayed/blocked: immediately dispatches URL directly
         via browser executable (subprocess.Popen([browser_exe, url])) which forces Windows OS
         to raise the active browser tab natively!
    4. If ALREADY FOREGROUND:
       - Executes address bar navigation immediately with zero lag.
    """
    attach_to_user_desktop()
    browser_status = get_app_focus_status("brave")
    if browser_status["status"] == "not_running":
        for b_name in ["chrome", "msedge", "firefox"]:
            st = get_app_focus_status(b_name)
            if st["status"] != "not_running":
                browser_status = st
                break

    # Scenario 1: Browser not running at all
    if browser_status["status"] == "not_running":
        return launch_or_focus_browser(url)

    target_hwnd = browser_status.get("hwnd")
    proc_name = browser_status.get("proc_name", "brave.exe")
    browser_exe = get_preferred_browser_executable(proc_name)

    log_info("win_os_agent", f"navigate_active_browser: target '{proc_name}' is in state '{browser_status['status']}' (current foreground: '{browser_status.get('current_foreground', {}).get('proc_name')}')")

    # Scenario 2: Browser is in BACKGROUND or MINIMIZED
    is_fg = False
    if target_hwnd:
        force_foreground_window(target_hwnd)
        dismiss_browser_popups()
        time.sleep(0.2)
        is_fg = (win32gui.GetForegroundWindow() == target_hwnd) if HAS_WIN32 else False

    # If foreground window is Lila Companion, demote Lila and re-force browser
    if HAS_WIN32 and target_hwnd:
        try:
            fg_hwnd = win32gui.GetForegroundWindow()
            if fg_hwnd and fg_hwnd != target_hwnd:
                fg_title = win32gui.GetWindowText(fg_hwnd).lower()
                if "lila" in fg_title or "companion" in fg_title:
                    win32gui.SetWindowPos(fg_hwnd, win32con.HWND_BOTTOM, 0, 0, 0, 0,
                                          win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
                    force_foreground_window(target_hwnd)
                    time.sleep(0.15)
                    is_fg = (win32gui.GetForegroundWindow() == target_hwnd)
        except Exception:
            pass

    nav_ok = False
    # If browser is in foreground (or brought to front), open new foreground tab (Ctrl+T -> Ctrl+V -> Enter)
    if is_fg:
        try:
            import ctypes, pyperclip
            pyperclip.copy(url)
            time.sleep(0.06)

            # Ctrl + T (Open new foreground tab with address bar automatically selected)
            ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x54, 0, 0, 0)
            time.sleep(0.04)
            ctypes.windll.user32.keybd_event(0x54, 0, 2, 0)
            ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
            time.sleep(0.25)

            # Ctrl + V (Paste URL)
            ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x56, 0, 0, 0)
            time.sleep(0.04)
            ctypes.windll.user32.keybd_event(0x56, 0, 2, 0)
            ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
            time.sleep(0.12)

            # Enter
            ctypes.windll.user32.keybd_event(0x0D, 0, 0, 0)
            time.sleep(0.04)
            ctypes.windll.user32.keybd_event(0x0D, 0, 2, 0)
            nav_ok = True
        except Exception as ex:
            log_warn("win_os_agent", f"navigate_active_browser keystroke warning: {ex}")
            nav_ok = False

    # Scenario 3: If not yet foregrounded or keystroke didn't succeed, use native browser executable launch
    # In Windows 11, running `browser.exe <url>` sends an IPC command to the running browser process,
    # opens the URL, and natively foregrounds the tab!
    if not nav_ok:
        try:
            if browser_exe and os.path.exists(browser_exe):
                subprocess.Popen([browser_exe, url])
            else:
                os.startfile(url)
            time.sleep(0.3)
            if target_hwnd:
                force_foreground_window(target_hwnd)
            nav_ok = True
        except Exception as ex_direct:
            log_warn("win_os_agent", f"Direct browser launch fallback error: {ex_direct}")

    return nav_ok


def find_window(query: str) -> Optional[Dict[str, Any]]:
    """Finds the best matching open window by title or process name."""
    query = (query or "").strip().lower()
    if not query:
        return None

    # 0. Browser alias resolution (matches active Brave, Chrome, Edge, Firefox, etc.)
    if query in ["browser", "chrome", "brave", "edge", "web", "internet"]:
        bw = find_browser_window()
        if bw:
            return bw

    windows = get_open_windows()
    if not windows:
        return None

    # 1. Exact match on process name or title
    for win in windows:
        if query == win["proc_name"].lower() or query == win["proc_name"].lower().replace(".exe", ""):
            return win
        if query == win["title"].lower():
            return win

    # 2. Substring match on title
    for win in windows:
        if query in win["title"].lower():
            return win

    # 3. Substring match on process name
    for win in windows:
        if query in win["proc_name"].lower():
            return win

    # 4. Fuzzy title match
    titles = [w["title"] for w in windows]
    matches = difflib.get_close_matches(query, titles, n=1, cutoff=0.45)
    if matches:
        for win in windows:
            if win["title"] == matches[0]:
                return win

    return None


def force_foreground_window(hwnd) -> bool:
    """Activates and brings a window to the foreground, bypassing Windows focus restrictions and punching through Antigravity fullscreen."""
    if not HAS_WIN32 or not hwnd or not win32gui.IsWindow(hwnd):
        return False
    try:
        attach_to_user_desktop()

        # 1. Unlock Windows foreground lock restrictions
        try:
            ctypes.windll.user32.SystemParametersInfoW(0x2001, 0, ctypes.c_void_p(0), 0x0001 | 0x0002)
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        except Exception:
            pass

        # 2. If minimized, restore first
        if win32gui.IsIconic(hwnd):
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.04)
            except Exception:
                pass
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWMAXIMIZED)
        except Exception:
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            except Exception:
                pass

        # 3. Attach thread inputs: caller, foreground, and target threads
        cur_tid = win32api.GetCurrentThreadId()
        fg_hwnd = win32gui.GetForegroundWindow()
        fg_tid = 0
        if fg_hwnd and fg_hwnd != hwnd:
            try:
                fg_tid, _ = win32process.GetWindowThreadProcessId(fg_hwnd)
                if fg_tid and fg_tid != cur_tid:
                    win32process.AttachThreadInput(cur_tid, fg_tid, True)
            except Exception:
                pass

        target_tid = 0
        try:
            target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
            if target_tid and target_tid != cur_tid:
                win32process.AttachThreadInput(cur_tid, target_tid, True)
        except Exception:
            pass

        try:
            # 4. Bypass Windows SetForegroundWindow lock using Alt key tap
            try:
                ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # Alt down
                time.sleep(0.01)
                ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)  # Alt up
                time.sleep(0.01)
            except Exception:
                pass

            # 5. Bring to top, activate, and switch
            win32gui.BringWindowToTop(hwnd)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            try:
                ctypes.windll.user32.SwitchToThisWindow(hwnd, True)
            except Exception:
                pass

            # 6. Bring window directly to front of Z-order
            try:
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, 0, 0, 0, 0,
                                      win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
        finally:
            if fg_tid and fg_tid != cur_tid:
                try:
                    win32process.AttachThreadInput(cur_tid, fg_tid, False)
                except Exception:
                    pass
            if target_tid and target_tid != cur_tid:
                try:
                    win32process.AttachThreadInput(cur_tid, target_tid, False)
                except Exception:
                    pass

        # 7. Verification wait loop (up to 400ms)
        for _ in range(10):
            if win32gui.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.04)

        return (win32gui.GetForegroundWindow() == hwnd)
    except Exception as e:
        log_warn("win_os_agent", f"force_foreground_window failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 2. SWITCH & CLOSE APP (HIGH ACCURACY)
# ─────────────────────────────────────────────────────────────────────────────

def switch_to_app(app_name: str) -> str:
    """Switches focus to an already-open application window deterministically."""
    name_clean = (app_name or "").strip().lower()
    if name_clean in ("brave", "brave browser", "chrome", "google chrome", "edge", "browser"):
        if launch_or_focus_browser():
            return f"Switched to {name_clean.title()} on screen."

    win = find_window(app_name)
    if not win:
        # Fallback: check if app is not running, offer to open it
        return f"Could not find an active window for '{app_name}'. Is it running?"

    success = force_foreground_window(win["hwnd"])
    if success:
        return f"Switched to '{win['title']}' ({win['proc_name']})."
    else:
        return f"Found window '{win['title']}', but could not bring to foreground."


def close_app(app_name: str) -> str:
    """Closes an application gracefully using WM_CLOSE, without data loss."""
    if not HAS_WIN32:
        return "Win32 API not available."

    hwnd = None
    target_desc = ""

    if not app_name or app_name.lower() in ["window", "this", "current"]:
        hwnd = win32gui.GetForegroundWindow()
        target_desc = win32gui.GetWindowText(hwnd) or "active window"
    else:
        win = find_window(app_name)
        if win:
            hwnd = win["hwnd"]
            target_desc = f"'{win['title']}' ({win['proc_name']})"
        else:
            # Check if running process exists even without visible window
            proc_name = app_name if app_name.endswith(".exe") else f"{app_name}.exe"
            killed = False
            for p in psutil.process_iter(['name']):
                if p.info['name'] and p.info['name'].lower() == proc_name.lower():
                    try:
                        p.terminate()
                        killed = True
                    except Exception:
                        pass
            if killed:
                return f"Terminated background process '{proc_name}'."
            return f"No open window or process found for '{app_name}'."

    if hwnd:
        try:
            # Graceful WM_CLOSE message
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            return f"Closed {target_desc}."
        except Exception as e:
            return f"Failed to close {target_desc}: {e}"

    return "No window found to close."


# ─────────────────────────────────────────────────────────────────────────────
# 3. DYNAMIC APP DISCOVERY & LAUNCHER (ZERO KEYBOARD OVERTAKE)
# ─────────────────────────────────────────────────────────────────────────────

_APP_INDEX_CACHE: Dict[str, str] = {}
_LAST_INDEX_TIME: float = 0.0

def build_app_index() -> Dict[str, str]:
    """Scans the Windows Registry and Start Menu shortcuts to dynamically index installed applications."""
    global _APP_INDEX_CACHE, _LAST_INDEX_TIME
    now = time.time()
    if _APP_INDEX_CACHE and (now - _LAST_INDEX_TIME) < 300:
        return _APP_INDEX_CACHE

    apps = {}

    # 1. Windows Registry: App Paths
    reg_keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    ]
    for root, subkey in reg_keys:
        try:
            with winreg.OpenKey(root, subkey) as key:
                count = winreg.QueryInfoKey(key)[0]
                for i in range(count):
                    try:
                        app_exe = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, app_exe) as app_key:
                            exe_path, _ = winreg.QueryValueEx(app_key, "")
                            if exe_path and os.path.exists(exe_path):
                                base = app_exe.lower().replace(".exe", "")
                                apps[base] = exe_path
                                apps[app_exe.lower()] = exe_path
                    except Exception:
                        continue
        except Exception:
            pass

    # 2. Desktop and Start Menu Shortcuts (.lnk files)
    start_dirs = [
        os.path.expandvars(r"%USERPROFILE%\OneDrive\Desktop"),
        os.path.expandvars(r"%USERPROFILE%\Desktop"),
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%ALLUSERSPROFILE%\Microsoft\Windows\Start Menu\Programs"),
    ]
    for sdir in start_dirs:
        if os.path.exists(sdir):
            for root, _, files in os.walk(sdir):
                for f in files:
                    if f.lower().endswith(".lnk"):
                        name_clean = f[:-4].lower().strip()
                        full_lnk = os.path.join(root, f)
                        apps[name_clean] = full_lnk

    # 3. Known Common System & AppData Tools
    common_tools = {
        "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "google chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "brave": r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        "code": os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
        "vs code": os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
        "vscode": os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
        "visual studio code": os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
        "cursor": os.path.expandvars(r"%LOCALAPPDATA%\Programs\cursor\Cursor.exe"),
        "discord": os.path.expandvars(r"%LOCALAPPDATA%\Discord\Update.exe --processStart Discord.exe"),
        "spotify": os.path.expandvars(r"%APPDATA%\Spotify\Spotify.exe"),
        "notepad": "notepad.exe",
        "calculator": "calc.exe",
        "calc": "calc.exe",
        "paint": "mspaint.exe",
        "cmd": "cmd.exe",
        "terminal": "wt.exe",
        "windows terminal": "wt.exe",
        "explorer": "explorer.exe",
        "task manager": "taskmgr.exe",
        "settings": "ms-settings:",
    }
    lila_exe = os.path.expandvars(r"%LOCALAPPDATA%\BrowserClaw\Application\chrome.exe")
    if os.path.exists(lila_exe):
        common_tools["lila"] = lila_exe
        common_tools["lila browser"] = lila_exe
        common_tools["lila's browser"] = lila_exe
        common_tools["browser"] = lila_exe
    for k, v in common_tools.items():
        path_candidate = v.split(".exe")[0] + ".exe" if ".exe" in v else v
        if v.endswith(":") or os.path.exists(v) or os.path.exists(path_candidate):
            apps[k] = v

    _APP_INDEX_CACHE = apps
    _LAST_INDEX_TIME = now
    return apps



SETTINGS_SUBPAGES = {
    "storage": "ms-settings:storagesense",
    "storagesense": "ms-settings:storagesense",
    "disk": "ms-settings:storagesense",
    "sound": "ms-settings:sound",
    "audio": "ms-settings:sound",
    "volume": "ms-settings:sound",
    "display": "ms-settings:display",
    "screen": "ms-settings:display",
    "resolution": "ms-settings:display",
    "bluetooth": "ms-settings:bluetooth",
    "bt": "ms-settings:bluetooth",
    "wifi": "ms-settings:network-wifi",
    "wi-fi": "ms-settings:network-wifi",
    "network": "ms-settings:network",
    "internet": "ms-settings:network",
    "battery": "ms-settings:powersleep",
    "power": "ms-settings:powersleep",
    "sleep": "ms-settings:powersleep",
    "update": "ms-settings:windowsupdate",
    "updates": "ms-settings:windowsupdate",
    "windows update": "ms-settings:windowsupdate",
    "apps": "ms-settings:appsfeatures",
    "applications": "ms-settings:appsfeatures",
    "installed apps": "ms-settings:appsfeatures",
    "default apps": "ms-settings:defaultapps",
    "notifications": "ms-settings:notifications",
    "notification": "ms-settings:notifications",
    "mouse": "ms-settings:mousetouchpad",
    "touchpad": "ms-settings:mousetouchpad",
    "printers": "ms-settings:printers",
    "printer": "ms-settings:printers",
    "about": "ms-settings:about",
    "system info": "ms-settings:about",
    "taskbar": "ms-settings:taskbar",
    "personalization": "ms-settings:personalization",
    "background": "ms-settings:personalization-background",
    "theme": "ms-settings:personalization",
    "date and time": "ms-settings:dateandtime",
    "time": "ms-settings:dateandtime",
    "privacy": "ms-settings:privacy",
    "camera": "ms-settings:privacy-webcam",
    "microphone": "ms-settings:privacy-microphone",
    "mic": "ms-settings:privacy-microphone",
    "gaming": "ms-settings:gaming-gamebar",
}

def open_or_search_settings(query: str = "") -> str:
    """
    Opens Windows Settings directly to a subpage or searches a specific query.
    If query matches a known subpage (e.g. storage, sound, bluetooth), opens it instantly via direct URI.
    Otherwise opens Settings, brings it to foreground, and types into search box (Ctrl+E) and submits with Enter.
    """
    raw = (query or "").strip().lower()
    q = raw
    for word in ["settings", "setting", "windows", "page", "search", "for", "open", "kholo", "in", "the", "please"]:
        q = re.sub(rf'\b{word}\b', '', q).strip()

    # 1. Check direct subpage URI match
    matched_uri = None
    target_name = q or raw
    for key, uri in SETTINGS_SUBPAGES.items():
        if key in q or (q and q in key) or key in raw:
            matched_uri = uri
            target_name = key
            break

    if matched_uri:
        try:
            os.startfile(matched_uri)
            time.sleep(0.4)
            wins = get_open_windows()
            swin = next((w for w in wins if "setting" in w["title"].lower() or "systemsettings" in w["proc_name"].lower()), None)
            if swin:
                force_foreground_window(swin["hwnd"])
            return f"Opened Windows {target_name.title()} Settings."
        except Exception as e:
            log_warn("win_os_agent", f"Settings URI launch error: {e}")

    # 2. General Settings launch or interactive search
    try:
        os.startfile("ms-settings:")
        time.sleep(0.5)
        wins = get_open_windows()
        swin = next((w for w in wins if "setting" in w["title"].lower() or "systemsettings" in w["proc_name"].lower()), None)
        if swin:
            force_foreground_window(swin["hwnd"])
            time.sleep(0.2)

        search_term = q if q else (raw if raw not in ("setting", "settings") else "")
        if search_term:
            import pyautogui
            pyautogui.FAILSAFE = False
            pyautogui.hotkey("ctrl", "e")
            time.sleep(0.2)
            pyautogui.typewrite(search_term, interval=0.03)
            time.sleep(0.2)
            pyautogui.press("enter")
            return f"Opened Windows Settings and searched for '{search_term}'."
        return "Opened Windows Settings."
    except Exception as ex:
        return f"Failed to open or search Windows Settings: {ex}"


def open_app(app_name: str) -> str:
    """Launches any installed application directly by name without keyboard simulation."""
    name_clean = (app_name or "").strip().lower()
    for sfx in [" app", " please", " now", " application"]:
        if name_clean.endswith(sfx):
            name_clean = name_clean[:-len(sfx)].strip()

    if not name_clean:
        return "Please specify the app name to open."

    # Windows Settings Protocol & Subpage Launch
    if "setting" in name_clean:
        return open_or_search_settings(name_clean)

    # Lila 3D Desktop Companion Intercept (opens Lila Companion.exe on Desktop)
    LILA_COMPANION_ALIASES = {"lila", "lila companion", "lila avatar", "lila 3d", "lila overlay", "lila companion app", "companion", "desktop companion"}
    if name_clean in LILA_COMPANION_ALIASES:
        candidates = [
            os.path.expandvars(r"%USERPROFILE%\OneDrive\Desktop\Lila Companion.exe"),
            os.path.expandvars(r"%USERPROFILE%\Desktop\Lila Companion.exe"),
            r"D:\jarvis_project\Lila Companion.exe",
            r"D:\jarvis_project\start_lila_companion.bat",
            str(Path(__file__).resolve().parent.parent / "start_lila_companion.bat"),
        ]
        target_exe = next((c for c in candidates if os.path.exists(c)), None)
        if target_exe:
            try:
                os.startfile(target_exe)
                return "Opening Lila Desktop Companion."
            except Exception:
                subprocess.Popen(f'cmd.exe /c start "" "{target_exe}"', shell=True)
                return "Opening Lila Desktop Companion."
        return "Lila Companion executable not found."

    # Lila's Browser Intercept
    if any(b == name_clean or name_clean.startswith(b) for b in ["lila browser", "lila's browser", "browserclaw", "lilas browser"]):
        lila_exe = os.path.expandvars(r"%LOCALAPPDATA%\BrowserClaw\Application\chrome.exe")
        start_page = str(Path(__file__).resolve().parent.parent / "lila-browser-integration" / "branding" / "start-page.html")
        if os.path.exists(lila_exe):
            ext_candidates = [
                os.path.expandvars(r"%LOCALAPPDATA%\BrowserClaw\Extensions\uBlock0\uBlock0.chromium"),
                os.path.expandvars(r"%LOCALAPPDATA%\BrowserClaw\Extensions\ChromiumWebStore"),
            ]
            valid_exts = [e for e in ext_candidates if os.path.exists(e)]
            cmd = [lila_exe]
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

    if name_clean in ("chrome", "google chrome", "brave", "brave browser", "edge", "microsoft edge", "browser"):
        launch_or_focus_browser()
        return f"Brought {name_clean.title()} to the foreground window."

    # Direct Web Service Intercept (Amazon, YouTube, Flipkart, etc.)
    COMMON_WEB_SERVICES = {
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
        "prime video": "https://www.primevideo.com",
        "hotstar": "https://www.hotstar.com",
    }
    if name_clean in COMMON_WEB_SERVICES:
        from core.omniforge import launch_in_chrome
        launch_in_chrome(COMMON_WEB_SERVICES[name_clean])
        return f"Opening {name_clean.title()} in browser."

    # ── VS Code special case ────────────────────────────────────────────────
    _VSCODE_ALIASES = {"vs code", "vscode", "code", "visual studio code", "vs-code"}
    if name_clean in _VSCODE_ALIASES:
        _code_exe = os.path.expandvars(
            r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"
        )
        _code_lnk = os.path.expandvars(
            r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Visual Studio Code\Visual Studio Code.lnk"
        )
        if not os.path.exists(_code_exe):
            return "VS Code not found on this machine."

        # Step 1: Kill ALL zombie Code.exe so no stale offscreen server exists.
        try:
            subprocess.run(["taskkill", "/F", "/IM", "Code.exe"],
                           capture_output=True, timeout=5)
        except Exception:
            pass
        import psutil
        for _ in range(25):          # wait up to 5 s for all to exit
            time.sleep(0.2)
            if not any(p.info.get("name", "").lower() == "code.exe"
                       for p in psutil.process_iter(["name"])):
                break

        # Step 2: Launch via ShellExecute (os.startfile) — identical to the user
        # clicking the icon. ShellExecute gives the process foreground activation
        # rights and SW_SHOWNORMAL so the window actually appears on screen.
        try:
            if os.path.exists(_code_lnk):
                os.startfile(_code_lnk)
            else:
                os.startfile(_code_exe)
        except Exception as e:
            return f"Could not launch VS Code: {e}"

        return "Opening VS Code."

    # Check if window is already open — if so, simply switch to it!
    open_win = find_window(name_clean)
    if open_win:
        force_foreground_window(open_win["hwnd"])
        return f"'{open_win['title']}' was already running. Brought to foreground."

    index = build_app_index()

    # 1. Exact match in index
    target = index.get(name_clean)

    # 2. Substring match
    if not target:
        for k, v in index.items():
            if name_clean == k or name_clean in k or k in name_clean:
                target = v
                break

    # 3. Fuzzy match
    if not target:
        matches = difflib.get_close_matches(name_clean, list(index.keys()), n=1, cutoff=0.55)
        if matches:
            target = index[matches[0]]

    # 4. Execution
    if target:
        try:
            if target.endswith(":"):
                os.startfile(target)
            elif target.lower().endswith(".lnk") or target.lower().endswith(".exe"):
                os.startfile(target)
            else:
                subprocess.Popen(target, shell=True)

            # Bring the newly opened application window to foreground as soon as it appears
            def _bring_to_front_later(name: str):
                for _ in range(16):
                    time.sleep(0.25)
                    w = find_window(name)
                    if w and w.get("hwnd"):
                        force_foreground_window(w["hwnd"])
                        return
            import threading
            threading.Thread(target=_bring_to_front_later, args=(name_clean,), daemon=True, name="BringAppToFront").start()

            return f"Opening {app_name}."
        except Exception as e:
            return f"Found '{target}' for {app_name}, but launch failed: {e}"

    # Fallback to system start
    try:
        subprocess.Popen(f"start {name_clean}", shell=True)
        def _bring_to_front_fallback(name: str):
            for _ in range(16):
                time.sleep(0.25)
                w = find_window(name)
                if w and w.get("hwnd"):
                    force_foreground_window(w["hwnd"])
                    return
        import threading
        threading.Thread(target=_bring_to_front_fallback, args=(name_clean,), daemon=True, name="BringAppFallback").start()
        return f"Attempting to launch {app_name} via Windows shell."
    except Exception as e:
        return f"Could not launch {app_name}: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# 4. WINDOW MANIPULATION (MINIMIZE, MAXIMIZE, SNAP)
# ─────────────────────────────────────────────────────────────────────────────

def minimize_window(app_name: str = "") -> str:
    """Minimizes the specified window or current active window."""
    if not HAS_WIN32:
        return "Win32 API not available."
    hwnd = None
    if app_name:
        win = find_window(app_name)
        if win:
            hwnd = win["hwnd"]
    if not hwnd:
        hwnd = win32gui.GetForegroundWindow()

    if hwnd:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        title = win32gui.GetWindowText(hwnd) or "Window"
        return f"Minimized '{title}'."
    return "No active window to minimize."


def maximize_window(app_name: str = "") -> str:
    """Maximizes the specified window or current active window."""
    if not HAS_WIN32:
        return "Win32 API not available."
    hwnd = None
    if app_name:
        win = find_window(app_name)
        if win:
            hwnd = win["hwnd"]
    if not hwnd:
        hwnd = win32gui.GetForegroundWindow()

    if hwnd:
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        title = win32gui.GetWindowText(hwnd) or "Window"
        return f"Maximized '{title}'."
    return "No active window to maximize."


def snap_window(direction: str) -> str:
    """Snaps the active foreground window to left, right, top, or maximize using SetWindowPos."""
    if not HAS_WIN32:
        return "Win32 API not available."

    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return "No active window to snap."

    dir_clean = direction.strip().lower()

    screen_w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
    screen_h = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
    # Exclude taskbar (~40px)
    work_h = screen_h - 45

    # Restore window first if maximized so coordinates take effect
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    if "left" in dir_clean:
        win32gui.MoveWindow(hwnd, 0, 0, screen_w // 2, work_h, True)
        return "Snapped active window to left half."
    elif "right" in dir_clean:
        win32gui.MoveWindow(hwnd, screen_w // 2, 0, screen_w // 2, work_h, True)
        return "Snapped active window to right half."
    elif "top" in dir_clean or "up" in dir_clean:
        win32gui.MoveWindow(hwnd, 0, 0, screen_w, work_h // 2, True)
        return "Snapped active window to top half."
    elif "maximize" in dir_clean or "full" in dir_clean:
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        return "Maximized active window."

    return f"Unknown snap direction '{direction}'. Use 'left', 'right', 'top', or 'maximize'."


# ─────────────────────────────────────────────────────────────────────────────
# 5. INPUT & CLIPBOARD TYPING (INSTANT UNICODE + EMOJI SUPPORT)
# ─────────────────────────────────────────────────────────────────────────────

def _safe_unpark_cursor():
    """Reposition cursor away from corner fail-safe points."""
    try:
        attach_to_user_desktop()
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            sw = ctypes.windll.user32.GetSystemMetrics(0) or 1920
            sh = ctypes.windll.user32.GetSystemMetrics(1) or 1080
            margin = 5
            if (pt.x <= margin and pt.y <= margin) or (pt.x >= sw - margin and pt.y <= margin) or                (pt.x <= margin and pt.y >= sh - margin) or (pt.x >= sw - margin and pt.y >= sh - margin):
                ctypes.windll.user32.SetCursorPos(sw // 2, sh // 2)
    except Exception:
        pass


def type_text(text: str, app_name: Optional[str] = None) -> str:
    """
    Types text instantly via clipboard injection (Ctrl+V).
    If app_name is provided, verifies if the target app is focused; if in background/minimized,
    brings it to the foreground before typing to prevent misdirected keystrokes.
    """
    if not text:
        return "No text provided to type."

    if app_name:
        status = get_app_focus_status(app_name)
        if status["status"] in ("background", "minimized") and status.get("hwnd"):
            force_foreground_window(status["hwnd"])
            time.sleep(0.15)
        elif status["status"] == "not_running":
            if any(b in app_name.lower() for b in ["brave", "chrome", "edge", "browser"]):
                launch_or_focus_browser()
            else:
                switch_to_app(app_name)
            time.sleep(0.3)

    _safe_unpark_cursor()
    try:
        old_clip = pyperclip.paste()
    except Exception:
        old_clip = ""
    try:
        pyperclip.copy(text)
        time.sleep(0.05)
        try:
            import ctypes
            ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x56, 0, 0, 0)
            time.sleep(0.03)
            ctypes.windll.user32.keybd_event(0x56, 0, 2, 0)
            ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
        except Exception:
            import pyautogui
            pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.05)
        try:
            pyperclip.copy(old_clip)
        except Exception:
            pass
        return f"Typed {len(text)} characters via instant clipboard injection."
    except Exception as e:
        try:
            _safe_unpark_cursor()
            import pyautogui
            orig_fs = getattr(pyautogui, "FAILSAFE", True)
            try:
                pyautogui.FAILSAFE = False
                pyautogui.typewrite(text, interval=0.01)
            finally:
                pyautogui.FAILSAFE = orig_fs
            return f"Typed via keystroke simulation."
        except Exception as ex:
            return f"Failed to type text: {ex}"


def press_hotkey(keys: list) -> bool:
    """Presses a hotkey combination (e.g. ['ctrl', 'w'], ['alt', 'left'])."""
    if not keys:
        return False
    _safe_unpark_cursor()
    try:
        import pyautogui
        orig_fs = getattr(pyautogui, "FAILSAFE", True)
        try:
            pyautogui.FAILSAFE = False
            pyautogui.hotkey(*keys)
        finally:
            pyautogui.FAILSAFE = orig_fs
        return True
    except Exception:
        VK_MAP = {
            'ctrl': 0x11, 'control': 0x11, 'shift': 0x10, 'alt': 0x12,
            'w': 0x57, 'l': 0x4C, 'left': 0x25, 'right': 0x27, 'up': 0x26, 'down': 0x28,
            'enter': 0x0D, 'home': 0x24, 'end': 0x23, 'esc': 0x1B, 'escape': 0x1B
        }
        try:
            import ctypes
            vks = [VK_MAP[k.lower()] for k in keys if k.lower() in VK_MAP]
            if not vks:
                return False
            for vk in vks:
                ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.05)
            for vk in reversed(vks):
                ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
            return True
        except Exception:
            return False


# ─────────────────────────────────────────────────────────────────────────────
# 6. INSTANT SCREENSHOT & SCREEN UNDERSTANDING (ZERO SNIPPING TOOL POPUP)
# ─────────────────────────────────────────────────────────────────────────────

def take_screenshot_and_analyze(question: str = "Describe what is on screen.") -> str:
    """
    Captures full screen directly in memory using mss, saves to screenshots/,
    and calls Gemini Vision to summarize what is visible. Zero Snipping Tool popups!
    """
    try:
        from core.eyes import capture_screen_jpeg_bytes, scan_screen
        jpeg_bytes = capture_screen_jpeg_bytes(quality=85, max_dim=1920)
        if not jpeg_bytes:
            return "Failed to capture screen."

        # Save to screenshots directory
        screenshots_dir = Path("screenshots")
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        filename = screenshots_dir / f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        with open(filename, "wb") as f:
            f.write(jpeg_bytes)

        # Run Gemini vision analysis
        analysis = scan_screen(question)
        return f"Screenshot saved to '{filename}'.\n\nScreen Analysis:\n{analysis}"
    except Exception as e:
        log_error("win_os_agent", "take_screenshot_and_analyze", e)
        return f"Screenshot capture failed: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# 7. SEARCH FILES & SHORTCUTS PROGRAMMATICALLY
# ─────────────────────────────────────────────────────────────────────────────

def search_windows_files(query: str, max_results: int = 8) -> str:
    """Searches Start Menu, Desktop, and Downloads for files/apps matching query."""
    query_clean = query.strip().lower()
    results = []

    search_roots = [
        os.path.expandvars(r"%USERPROFILE%\Desktop"),
        os.path.expandvars(r"%USERPROFILE%\Downloads"),
        os.path.expandvars(r"%USERPROFILE%\Documents"),
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
    ]

    for sroot in search_roots:
        if not os.path.exists(sroot):
            continue
        try:
            for root, dirs, files in os.walk(sroot):
                # Filter out deep node_modules/.git
                dirs[:] = [d for d in dirs if d not in [".git", "node_modules", "venv", "__pycache__"]]
                for f in files:
                    if query_clean in f.lower():
                        full_p = os.path.join(root, f)
                        results.append(full_p)
                        if len(results) >= max_results:
                            break
                if len(results) >= max_results:
                    break
        except Exception:
            continue

    if not results:
        return f"No files or applications matching '{query}' found in common directories."

    formatted = "\n".join([f"• {os.path.basename(r)} -> {r}" for r in results])
    return f"Search results for '{query}':\n{formatted}"


# ─────────────────────────────────────────────────────────────────────────────
# 8. UNIVERSAL MEDIA CONTROLLER (SMTC & KEY EVENTS)
# ─────────────────────────────────────────────────────────────────────────────

def control_media(action: str, app_name: str = "") -> str:
    """
    Universal System Media Transport Controls (SMTC) & Key Events.
    Controls playback across any active media application (Spotify, YouTube in Brave/Chrome, VLC, Netflix).
    Supported actions: 'play', 'pause', 'play_pause', 'next', 'previous', 'stop', 'volume_up', 'volume_down', 'mute'.
    """
    act = (action or "").strip().lower().replace(" ", "_")

    # Optional: if a specific app was requested, bring it to focus first
    if app_name:
        try:
            switch_to_app(app_name)
            time.sleep(0.15)
        except Exception:
            pass

    import ctypes
    user32 = ctypes.windll.user32

    # Virtual Key codes for Windows Media Control
    VK_MEDIA_NEXT_TRACK = 0xB0
    VK_MEDIA_PREV_TRACK = 0xB1
    VK_MEDIA_STOP       = 0xB2
    VK_MEDIA_PLAY_PAUSE = 0xB3
    VK_VOLUME_MUTE      = 0xAD
    VK_VOLUME_DOWN      = 0xAE
    VK_VOLUME_UP        = 0xAF

    def _send_vk(vk_code):
        user32.keybd_event(vk_code, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(vk_code, 0, 2, 0)

    if act in ["play", "pause", "play_pause", "toggle", "play/pause"]:
        _send_vk(VK_MEDIA_PLAY_PAUSE)
        return "Toggled media playback (Play/Pause)."

    elif act in ["next", "next_track", "skip", "forward"]:
        _send_vk(VK_MEDIA_NEXT_TRACK)
        return "Skipped to next media track."

    elif act in ["previous", "prev", "prev_track", "back", "rewind"]:
        _send_vk(VK_MEDIA_PREV_TRACK)
        return "Returned to previous media track."

    elif act in ["stop"]:
        _send_vk(VK_MEDIA_STOP)
        return "Stopped media playback."

    elif act in ["volume_up", "louder", "vol_up"]:
        for _ in range(3):
            _send_vk(VK_VOLUME_UP)
            time.sleep(0.01)
        return "Increased system volume."

    elif act in ["volume_down", "softer", "quieter", "vol_down"]:
        for _ in range(3):
            _send_vk(VK_VOLUME_DOWN)
            time.sleep(0.01)
        return "Decreased system volume."

    elif act in ["mute", "unmute", "silence"]:
        _send_vk(VK_VOLUME_MUTE)
        return "Toggled system mute."

    else:
        _send_vk(VK_MEDIA_PLAY_PAUSE)
        return f"Sent media key for action '{action}'."

