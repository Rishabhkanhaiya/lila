"""
core/lila_companion_launcher.py — Lila Desktop Companion Autonomous UI Controller
================================================================================
Empowers Lila to autonomously launch, restore, focus, and display her own 3D companion
avatar on screen — whether triggered by voice command, mobile studio, or system wake.

Traditional Windows Built-In Shortcut Execution:
  Launches the traditional Windows Desktop Shortcut (Lila Companion.lnk / Lila Companion.exe)
  through Windows Shell (os.startfile / explorer.exe) with ZERO terminal windows.
  The bridge runs silently in the background with pythonw.exe, and the 3D overlay opens
  smoothly on Rishabh's screen.
"""

import os
import sys
import time
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

import psutil

from core.jarvis_logger import log_info, log_warn, log_error

try:
    import ctypes
    from ctypes import wintypes
    HAS_WIN32 = True
except Exception:
    HAS_WIN32 = False


def _find_project_root() -> Path:
    """Resolves project root across D: and C: drives."""
    current_dir = Path(__file__).resolve().parent.parent
    if (current_dir / "lila-overlay").exists():
        return current_dir
    candidates = [
        Path(r"D:\jarvis_project"),
        Path(r"C:\Users\Rishabh_Joshi\Downloads\jarvis_project")
    ]
    for c in candidates:
        if (c / "lila-overlay").exists():
            return c
    return current_dir


def _find_electron_exe(root: Path) -> Path:
    """Finds the electron.exe across known paths."""
    candidates = [
        root / "lila-overlay" / "node_modules" / "electron" / "dist" / "electron.exe",
        Path(r"D:\jarvis_project\lila-overlay\node_modules\electron\dist\electron.exe"),
        Path(r"C:\Users\Rishabh_Joshi\Downloads\jarvis_project\lila-overlay\node_modules\electron\dist\electron.exe"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def _find_overlay_dir(root: Path) -> Path:
    """Finds the lila-overlay directory."""
    candidates = [
        root / "lila-overlay",
        Path(r"D:\jarvis_project\lila-overlay"),
        Path(r"C:\Users\Rishabh_Joshi\Downloads\jarvis_project\lila-overlay"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def _ensure_bridge_running(root: Path):
    """Ensures python companion bridge is running silently in the background."""
    # Guard 1: Never spawn another bridge if we are already inside run_companion_bridge!
    current_cmd = " ".join(sys.argv).lower()
    if 'run_companion_bridge' in current_cmd:
        return

    # Guard 2: If port 8765 is already accepting connections, bridge is actively running
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.1)
            if s.connect_ex(('127.0.0.1', 8765)) == 0:
                return
    except Exception:
        pass

    bridge_running = False
    for p in psutil.process_iter(['name', 'cmdline']):
        try:
            cmd = " ".join(p.info['cmdline'] or []).lower()
            if 'run_companion_bridge.py' in cmd:
                bridge_running = True
                break
        except Exception:
            continue


    if not bridge_running:
        pythonw_candidates = [
            root / "venv" / "Scripts" / "pythonw.exe",
            Path(r"D:\jarvis_project\venv\Scripts\pythonw.exe"),
            Path(r"C:\Users\Rishabh_Joshi\AppData\Local\Programs\Python\Python311\pythonw.exe"),
        ]
        pythonw_exe = next((p for p in pythonw_candidates if p.exists()), Path("pythonw.exe"))
        bridge_script = root / "run_companion_bridge.py"
        if not bridge_script.exists():
            bridge_script = Path(r"D:\jarvis_project\run_companion_bridge.py")

        DETACHED_PROCESS = 0x00000008
        subprocess.Popen(
            [str(pythonw_exe), str(bridge_script)],
            cwd=str(root),
            creationflags=DETACHED_PROCESS,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        log_info("lila_launcher", f"Launched Lila Bridge silently via {pythonw_exe.name}")
        time.sleep(0.8)


def _bring_existing_window_to_front():
    """Brings any existing Electron Lila window to topmost foreground via Win32."""
    if not HAS_WIN32 or sys.platform != "win32":
        return

    try:
        user32 = ctypes.windll.user32

        SW_RESTORE = 9
        SW_SHOWNA = 8
        HWND_TOPMOST = -1
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040
        SWP_NOACTIVATE = 0x0010

        def enum_cb(hwnd, _):
            if user32.IsWindow(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value.lower()
                    if "lila" in title or "companion" in title:
                        if user32.IsIconic(hwnd):
                            user32.ShowWindow(hwnd, SW_RESTORE)
                        else:
                            user32.ShowWindow(hwnd, SW_SHOWNA)
                        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW | SWP_NOACTIVATE)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    except Exception as e:
        log_warn("lila_launcher", f"Win32 window focus notice: {e}")


def _is_lila_window_visible() -> bool:
    """Checks if a visible Lila Companion window currently exists."""
    if not HAS_WIN32 or sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        visible = []

        def enum_cb(hwnd, _):
            if user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value.lower()
                    if ("lila" in title or "companion" in title) and not any(
                        term in title for term in ["cmd", "powershell", "terminal", "bridge"]
                    ):
                        visible.append(hwnd)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
        return len(visible) > 0
    except Exception:
        return False


def _kill_electron_only():
    """Kills ONLY electron processes (not the bridge or server). Cleans singleton lockfiles."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/IM", "electron.exe"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
        )
    except Exception:
        pass

    # Delete Chromium singleton lockfiles so new instance can acquire the lock
    try:
        appdata = os.environ.get("APPDATA", "")
        for lockname in ["lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket"]:
            lf = Path(appdata) / "lila-overlay" / lockname
            try:
                lf.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass


def open_lila_companion_ui(model: str = "ana") -> Dict[str, Any]:
    """
    Autonomously launches or brings Lila's 3D companion UI to the front of the screen.

    Uses the traditional Windows built-in shortcut execution:
    Opens the Windows Shortcut (.lnk / .exe) directly via Windows Shell (os.startfile / explorer.exe).
    With start_lila_companion.bat updated to use pythonw.exe and start "", there is
    ZERO terminal window interaction, no console popups, and the UI opens directly.
    """
    try:
        root = _find_project_root()
        target_model = model or os.environ.get("LILA_MODEL", "ana")
        os.environ["LILA_MODEL"] = target_model

        # 1. If Lila window is already visible, bring it to front and return
        if _is_lila_window_visible():
            log_info("lila_launcher", "Lila Companion window is already visible. Bringing to front...")
            _bring_existing_window_to_front()
            try:
                from core.state_bridge import broadcast_state
                broadcast_state(mood="excited", caption="Main yahan hoon Rishabh! ✨ Dekho meri screen!", speaking=True)
            except Exception:
                pass
            return {
                "status": "ok",
                "message": "Lila's 3D companion avatar UI is already open and brought to front! ✨",
                "action": "open_lila_ui",
                "model": target_model
            }

        # 2. Kill stale electron instances and clear lockfiles
        _kill_electron_only()
        time.sleep(0.6)

        # 3. Traditional Windows Built-In Shortcut Candidates
        shortcut_candidates = [
            Path(r"C:\Users\Rishabh_Joshi\Desktop\Lila Companion.lnk"),
            root / "Lila Companion.lnk",
            Path(r"C:\Users\Rishabh_Joshi\Downloads\jarvis_project\Lila Companion.lnk"),
            Path(r"C:\Users\Rishabh_Joshi\Desktop\Lila Companion.exe"),
            root / "Lila Companion.exe",
            Path(r"C:\Users\Rishabh_Joshi\Downloads\jarvis_project\Lila Companion.exe"),
        ]
        target_shortcut = next((p for p in shortcut_candidates if p.exists()), None)

        launched = False

        # Method A: Traditional Windows Built-in Shortcut launch via os.startfile (Windows Shell)
        if target_shortcut:
            try:
                os.startfile(str(target_shortcut))
                log_info("lila_launcher", f"Opened Lila via Windows built-in shortcut: {target_shortcut}")
                launched = True
            except Exception as e_start:
                log_warn("lila_launcher", f"os.startfile notice: {e_start}")
                try:
                    subprocess.Popen(["explorer.exe", str(target_shortcut)])
                    log_info("lila_launcher", f"Opened Lila via explorer.exe: {target_shortcut}")
                    launched = True
                except Exception:
                    pass

        # Method B: PowerShell Start-Process direct fallback
        if not launched:
            overlay_dir = _find_overlay_dir(root)
            electron_exe = _find_electron_exe(root)
            if electron_exe.exists():
                try:
                    ps_cmd = (
                        f'Start-Process -FilePath "{electron_exe}" '
                        f'-ArgumentList ".", "--model={target_model}" '
                        f'-WorkingDirectory "{overlay_dir}"'
                    )
                    ps_proc = subprocess.Popen(
                        ["powershell.exe", "-WindowStyle", "Hidden", "-Command", ps_cmd],
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    ps_proc.wait(timeout=8)
                    log_info("lila_launcher", f"Launched electron via PowerShell Start-Process")
                    launched = True
                except Exception as e_ps:
                    log_warn("lila_launcher", f"PowerShell fallback notice: {e_ps}")

        if not launched:
            return {"status": "error", "message": "Could not launch Lila Companion UI shortcut or executable."}

        # 4. Wait for window to initialize & assert topmost focus
        time.sleep(3.0)
        _bring_existing_window_to_front()

        try:
            from core.state_bridge import broadcast_state
            broadcast_state(mood="excited", caption="Main aa gayi Rishabh! ✨ Dekho main screen pe hoon!", speaking=True)
        except Exception:
            pass

        return {
            "status": "ok",
            "message": "Lila's 3D companion avatar UI is now open and floating on Rishabh's screen via Windows shortcut! ✨",
            "action": "open_lila_ui",
            "model": target_model
        }

    except Exception as ex:
        log_error("lila_launcher", f"Failed to open Lila companion UI: {ex}")
        return {
            "status": "error",
            "message": f"Could not open Lila UI: {ex}"
        }


def close_lila_companion_ui() -> Dict[str, Any]:
    """Closes Lila's 3D companion UI."""
    try:
        _kill_electron_only()
        log_info("lila_launcher", "Closed Lila Companion UI (all electron processes killed).")
        return {
            "status": "ok",
            "message": "Lila's 3D companion UI has been closed.",
        }
    except Exception as ex:
        return {
            "status": "error",
            "message": f"Failed to close Lila UI: {ex}"
        }


def launch_ui_shortcut_and_self_kill(
    shortcut_path: Optional[str] = None,
    delay_sec: float = 1.2,
    self_kill: bool = False
) -> Dict[str, Any]:
    """
    Executes the traditional Windows Lila Companion desktop shortcut:
      "C:\\Users\\Rishabh_Joshi\\Downloads\\jarvis_project\\Lila Companion.lnk"
    from mobile or voice command, and cleanly self-terminates the current non-UI
    Lila instance so the full 3D companion UI takes over seamlessly without port
    or audio device conflicts.
    """
    import threading
    try:
        root = _find_project_root()

        # 1. Candidate paths for the desktop shortcut
        candidates = []
        if shortcut_path:
            candidates.append(Path(shortcut_path))
        candidates.extend([
            Path(r"C:\Users\Rishabh_Joshi\Downloads\jarvis_project\Lila Companion.lnk"),
            root / "Lila Companion.lnk",
            Path(r"D:\jarvis_project\Lila Companion.lnk"),
            Path.home() / "Desktop" / "Lila Companion.lnk",
            Path(r"C:\Users\Rishabh_Joshi\Desktop\Lila Companion.lnk"),
            Path(r"C:\Users\Rishabh_Joshi\Desktop\Lila Companion.exe"),
            root / "Lila Companion.exe",
            Path(r"C:\Users\Rishabh_Joshi\Downloads\jarvis_project\Lila Companion.exe"),
            root / "Lila Companion.bat",
            root / "start_lila_companion.bat",
        ])

        target_shortcut = next((p for p in candidates if p.exists()), None)
        if not target_shortcut:
            err_msg = f"Shortcut '{shortcut_path or candidates[0]}' not found."
            log_error("lila_launcher", "launch_ui_shortcut", err_msg)
            return {"status": "error", "message": err_msg}

        # 2. Check if Lila Companion 3D UI is already running and visible
        if _is_lila_window_visible():
            log_info("lila_launcher", "Lila Companion window already visible. Bringing to front...")
            _bring_existing_window_to_front()
            try:
                from core.state_bridge import broadcast_state
                broadcast_state(mood="excited", caption="Main yahan hoon Rishabh! ✨ Dekho meri screen!", speaking=True)
            except Exception:
                pass
            return {
                "status": "ok",
                "already_open": True,
                "message": "Lila's 3D companion avatar UI is already active and brought to front! ✨",
                "action": "open_lila_ui",
                "shortcut": str(target_shortcut)
            }

        # 3. Non-UI Lila: Broadcast transition & prepare launch
        log_info("lila_launcher", f"Non-UI Lila launching shortcut: {target_shortcut} (self_kill={self_kill})...")
        
        # Ensure stale electron instances and lockfiles are purged so new UI acquires singleton lock
        _kill_electron_only()
        time.sleep(0.3)

        try:
            from core.state_bridge import broadcast_state
            broadcast_state(
                mood="excited",
                caption="Lila 3D Companion UI launch ho rahi hai Rishabh! ✨ Dekho main screen pe aa rahi hoon!",
                speaking=True
            )
        except Exception:
            pass

        # Speak notification asynchronously if voice module available
        try:
            from core.voice import speak
            threading.Thread(
                target=speak,
                args=("Haan Rishabh! Main apna 3D UI open kar rahi hoon!",),
                daemon=True,
                name="LaunchUIVoiceThread"
            ).start()
        except Exception:
            pass

        # 4. Detached launch of the Windows shortcut via Windows Shell / cmd start
        launched = False
        try:
            os.startfile(str(target_shortcut))
            log_info("lila_launcher", f"Successfully started shortcut via os.startfile: {target_shortcut}")
            launched = True
        except Exception as e_start:
            log_warn("lila_launcher", f"os.startfile notice: {e_start}")
            try:
                no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                new_grp = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                subprocess.Popen(
                    ["cmd.exe", "/c", "start", "", str(target_shortcut)],
                    creationflags=detached | new_grp | no_window,
                    close_fds=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                log_info("lila_launcher", f"Successfully started shortcut via cmd.exe start: {target_shortcut}")
                launched = True
            except Exception as e_cmd:
                log_error("lila_launcher", "launch_ui_shortcut", f"Subprocess start failed: {e_cmd}")

        if not launched:
            return {"status": "error", "message": f"Failed to execute shortcut {target_shortcut}"}

        # 5. Self-terminate current non-UI process only if explicitly requested AND not hosting server
        cmdline_str = " ".join(sys.argv).lower()
        is_host_server = any(srv in cmdline_str for srv in ["run_companion_bridge", "remote_mobile_server", "uvicorn"])
        if self_kill and not is_host_server:
            def _self_kill_worker():
                time.sleep(delay_sec)
                curr_pid = os.getpid()
                log_info("lila_launcher", f"[SELF-KILL] Non-UI Lila (PID {curr_pid}) exiting now to hand over to UI instance...")
                try:
                    sys.stdout.flush()
                    sys.stderr.flush()
                except Exception:
                    pass
                os._exit(0)

            threading.Thread(target=_self_kill_worker, daemon=False, name="LilaSelfKillThread").start()
        elif is_host_server and self_kill:
            log_info("lila_launcher", "[SELF-KILL] Prevented self-kill: current process is hosting companion bridge / mobile server.")

        return {
            "status": "ok",
            "action": "launch_ui_shortcut",
            "shortcut": str(target_shortcut),
            "self_kill_scheduled": self_kill,
            "message": "Lila Companion 3D UI launched! Switching from non-UI mode to full 3D overlay... ✨"
        }

    except Exception as ex:
        log_error("lila_launcher", f"Failed in launch_ui_shortcut_and_self_kill: {ex}")
        return {
            "status": "error",
            "message": f"Could not launch Lila UI shortcut: {ex}"
        }

