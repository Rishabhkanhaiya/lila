import os
import sys
import subprocess
from pathlib import Path
import psutil

def kill_stale():
    """
    Completely and deterministically terminates all orphaned, zombie, and lingering
    Electron, Chrome helper, and Bridge processes using Windows Kernel Tree-Kill (/T).
    Also purges any stale lockfiles from %APPDATA% so subsequent launches never get blocked.
    """
    current_pid = os.getpid()

    # 1. Force Windows Tree-Kill on electron.exe (kills parent + GPU + utility + renderer)
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/IM", "electron.exe"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False
            )
        except Exception:
            pass

    # 2. Iterate remaining processes for python bridge and headless chrome/brave
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            pid = p.info['pid']
            if pid == current_pid:
                continue
            name = (p.info['name'] or '').lower()
            cmd = " ".join(p.info['cmdline'] or []).lower()

            if 'electron' in name:
                p.kill()
            elif 'python' in name:
                if 'run_companion_bridge.py' in cmd:
                    p.kill()
            elif 'chrome' in name or 'brave' in name:
                if '.jarvis_chrome_profile' in cmd or '.jarvis_lila_profile' in cmd:
                    p.kill()
        except (psutil.AccessDenied, psutil.NoSuchProcess, Exception):
            continue

    # 3. Purge Electron Singleton lockfiles to prevent "lock held by zombie" failure
    try:
        appdata_root = os.environ.get('APPDATA', '')
        if appdata_root:
            appdata_dirs = [
                Path(appdata_root) / 'lila-overlay',
                Path(appdata_root) / 'Lila Companion',
            ]
            for adir in appdata_dirs:
                if adir.exists():
                    for lock_name in ['lockfile', 'SingletonLock', 'SingletonCookie', 'SingletonSocket']:
                        lf = adir / lock_name
                        if lf.exists():
                            try:
                                if lf.is_file() or lf.is_symlink():
                                    lf.unlink(missing_ok=True)
                            except Exception:
                                pass
    except Exception:
        pass


if __name__ == '__main__':
    kill_stale()
