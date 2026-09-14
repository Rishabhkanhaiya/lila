"""
core/post_wake_executor.py — Post-Wake Script & Action Orchestration Engine
===========================================================================
Executes designated scripts, background daemons, and notification actions when the
laptop wakes from sleep or standby. Features debounce protection, execution timeouts,
detailed audit logging, and background system resume detection.
"""

import os
import sys
import time
import json
import logging
import threading
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any, Union

from core.jarvis_logger import log_info, log_warn, log_error

logger = logging.getLogger("JARVIS.PostWake")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
POST_WAKE_LOG_PATH = LOGS_DIR / "post_wake_execution.log"

# Windows creation flags to prevent flashing console windows
WIN32_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0

# Debounce tracking
_last_execution_time = 0.0
_execution_lock = threading.Lock()
DEBOUNCE_WINDOW_SEC = 30.0


def _append_execution_log(entry: Dict[str, Any]) -> None:
    """Appends an execution audit entry to logs/post_wake_execution.log."""
    try:
        with open(POST_WAKE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as ex:
        logger.debug(f"[PostWake] Log write error: {ex}")


def execute_single_action(action: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes a single post-wake action definition:
    Action Schema:
      - name: str (Human-readable label)
      - type: 'command' | 'batch' | 'python' | 'powershell'
      - command: str (Shell command to execute)
      - is_daemon: bool (If true, starts process in background without blocking)
      - timeout_sec: int (Max execution time for synchronous commands)
      - enabled: bool
    """
    name = action.get("name", "Unnamed Action")
    action_type = action.get("type", "command").lower()
    enabled = action.get("enabled", True)
    is_daemon = action.get("is_daemon", False)
    timeout_sec = action.get("timeout_sec", 30)

    result: Dict[str, Any] = {
        "name": name,
        "type": action_type,
        "success": False,
        "skipped": False,
        "output": "",
        "error": None,
        "pid": None,
        "duration_sec": 0.0
    }

    if not enabled:
        result["skipped"] = True
        result["success"] = True
        result["status"] = "skipped"
        result["output"] = "Action is disabled in configuration."
        return result

    start_t = time.time()
    try:
        # Resolve Python virtual environment executable if available
        python_exe = sys.executable
        venv_python = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
        if venv_python.exists():
            python_exe = str(venv_python)

        if action_type in ("command", "batch"):
            cmd_str = action.get("command", "").strip()
            if not cmd_str:
                result["error"] = "Empty command string"
                return result

            # Replace generic 'python' with active python executable
            if cmd_str.startswith("python "):
                cmd_str = f'"{python_exe}" ' + cmd_str[7:]

            # Check if this is an interactive UI or batch script action
            is_interactive_ui = (
                action_type in ("batch", "executable")
                or cmd_str.lower().endswith(".bat")
                or action.get("ui", False)
                or "companion" in cmd_str.lower()
                or "companion" in name.lower()
                or "lila" in name.lower()
            )

            if is_interactive_ui and sys.platform == "win32":
                # If this is Lila Companion, prioritize Lila Companion.exe for full interactive desktop UI
                if "companion" in cmd_str.lower() or "companion" in name.lower() or "lila" in name.lower():
                    exe_candidates = [
                        Path(r"C:\Users\Rishabh_Joshi\OneDrive\Desktop\Lila Companion.exe"),
                        Path(r"C:\Users\Rishabh_Joshi\Desktop\Lila Companion.exe"),
                        PROJECT_ROOT / "Lila Companion.exe",
                        Path(r"D:\jarvis_project\Lila Companion.exe"),
                    ]
                    target_exe = next((e for e in exe_candidates if e.exists()), None)
                    if target_exe:
                        try:
                            os.startfile(str(target_exe))
                            result["success"] = True
                            result["output"] = f"Launched Lila Desktop Companion UI via native executable: {target_exe}"
                            return result
                        except Exception as e_start:
                            logger.debug(f"[PostWake] Failed os.startfile for {target_exe}: {e_start}")

                bat_file = PROJECT_ROOT / cmd_str
                if not bat_file.exists():
                    bat_file = Path(r"D:\jarvis_project") / cmd_str

                if bat_file.exists():
                    try:
                        os.startfile(str(bat_file))
                        result["success"] = True
                        result["output"] = f"Launched interactive UI '{cmd_str}' via Windows Shell."
                        return result
                    except Exception:
                        pass

                proc = subprocess.Popen(
                    f'cmd.exe /c start "{name}" {cmd_str}',
                    shell=True,
                    cwd=str(PROJECT_ROOT),
                    creationflags=WIN32_NO_WINDOW
                )
                result["success"] = True
                result["pid"] = proc.pid
                result["output"] = f"Launched interactive window (PID {proc.pid})"
                return result

            if is_daemon:
                # Spawn background daemon process
                creation_flags = WIN32_NO_WINDOW
                if sys.platform == "win32":
                    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | WIN32_NO_WINDOW

                proc = subprocess.Popen(
                    cmd_str,
                    shell=True,
                    cwd=str(PROJECT_ROOT),
                    creationflags=creation_flags,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                result["success"] = True
                result["pid"] = proc.pid
                result["output"] = f"Spawned background daemon (PID {proc.pid})"
            else:
                proc = subprocess.run(
                    cmd_str,
                    shell=True,
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    text=True,
                    timeout=timeout_sec,
                    creationflags=WIN32_NO_WINDOW
                )
                result["success"] = proc.returncode == 0
                result["output"] = proc.stdout.strip()
                if proc.returncode != 0:
                    result["error"] = proc.stderr.strip() or f"Exit code {proc.returncode}"

        elif action_type == "powershell":
            script = action.get("script", action.get("command", "")).strip()
            ps_cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
            proc = subprocess.run(
                ps_cmd,
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                creationflags=WIN32_NO_WINDOW
            )
            result["success"] = proc.returncode == 0
            result["output"] = proc.stdout.strip()
            if proc.returncode != 0:
                result["error"] = proc.stderr.strip() or f"Exit code {proc.returncode}"

        elif action_type == "python":
            mod_name = action.get("module", "")
            fn_name = action.get("function", "")
            args = action.get("args", [])
            kwargs = action.get("kwargs", {})

            if mod_name and fn_name:
                import importlib
                mod = importlib.import_module(mod_name)
                func = getattr(mod, fn_name)
                res = func(*args, **kwargs)
                result["success"] = True
                result["output"] = str(res)
            else:
                result["error"] = "Missing module or function attribute for python action."

        else:
            result["error"] = f"Unsupported action type: {action_type}"

    except subprocess.TimeoutExpired:
        result["error"] = f"Execution timed out after {timeout_sec}s."
    except Exception as ex:
        result["error"] = str(ex)
    finally:
        result["duration_sec"] = round(time.time() - start_t, 3)

    return result


def execute_post_wake_actions(
    actions: Optional[List[Dict[str, Any]]] = None,
    force: bool = False,
    debounce_sec: Optional[float] = None,
    custom_actions: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Executes all configured post-wake actions in sequence or daemonized.
    Guarded by a debounce lock to prevent duplicate runs from network bursts.
    """
    global _last_execution_time
    effective_debounce = debounce_sec if debounce_sec is not None else DEBOUNCE_WINDOW_SEC

    with _execution_lock:
        now = time.time()
        elapsed_since_last = now - _last_execution_time
        if not force and elapsed_since_last < effective_debounce:
            msg = f"Debounced post-wake execution (last run {elapsed_since_last:.1f}s ago < {effective_debounce}s window)."
            log_warn("post_wake", msg)
            return {
                "success": True,
                "executed": False,
                "debounced": True,
                "reason": msg,
                "message": msg,
                "results": [],
                "actions": []
            }
        _last_execution_time = now

    target_actions = custom_actions if custom_actions is not None else actions
    if target_actions is None:
        try:
            from core.wake_on_lan import load_wol_config
            cfg = load_wol_config()
            target_actions = cfg.post_wake_actions
        except Exception:
            target_actions = [
                {
                    "name": "Start Companion Bridge",
                    "type": "command",
                    "command": "python run_companion_bridge.py",
                    "is_daemon": True,
                    "enabled": True
                }
            ]

    log_info("post_wake", f"Executing {len(target_actions)} post-wake actions...")
    action_results = []
    overall_success = True

    for act in target_actions:
        res = execute_single_action(act)
        action_results.append(res)
        if not res["success"] and not res.get("skipped", False):
            overall_success = False
            log_warn("post_wake", f"Post-wake action '{act.get('name')}' failed: {res.get('error')}")
        else:
            log_info("post_wake", f"Post-wake action '{act.get('name')}' succeeded ({res.get('duration_sec')}s).")

    summary = {
        "timestamp": time.time(),
        "success": overall_success,
        "executed": True,
        "overall_success": overall_success,
        "total_actions": len(target_actions),
        "actions": action_results,
        "results": action_results
    }
    _append_execution_log(summary)
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# In-Process Monotonic System Resume Detector
# ─────────────────────────────────────────────────────────────────────────────

class SystemWakeMonitor:
    """
    Detects system resume from sleep by tracking wall-clock jumps vs monotonic sleep intervals.
    A jump > 15.0 seconds indicates the system went to sleep and resumed.
    """

    def __init__(self, check_interval_sec: float = 3.0, threshold_sec: float = 15.0):
        self.check_interval = check_interval_sec
        self.threshold = threshold_sec
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="SystemWakeMonitor")
        self._thread.start()
        log_info("post_wake", "SystemWakeMonitor background listener started.")

    def stop(self):
        self._running = False

    def _monitor_loop(self):
        last_time = time.time()
        while self._running:
            time.sleep(self.check_interval)
            now = time.time()
            drift = now - last_time - self.check_interval
            if drift > self.threshold:
                log_info("post_wake", f"System resume from sleep detected! (clock jump of {drift:.1f}s)")
                try:
                    execute_post_wake_actions()
                except Exception as ex:
                    log_error("post_wake", f"Error during post-wake trigger on resume: {ex}")
            last_time = now


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="JARVIS Post-Wake Script Execution Engine")
    parser.add_argument("--on-wake", action="store_true", help="Triggered immediately after system wakes from sleep.")
    parser.add_argument("--force", action="store_true", help="Bypass 30-second debounce window.")
    args = parser.parse_args()

    print("[PostWake] Triggering post-wake action execution...")
    result = execute_post_wake_actions(force=args.force)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("overall_success", True) else 1)
