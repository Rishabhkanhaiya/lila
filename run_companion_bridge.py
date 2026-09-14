"""
run_companion_bridge.py — Full Lila Companion Engine & Reactive State Bridge
=============================================================================
Powers the Lila Desktop Companion with:
1. Local WebSocket State Bridge (ws://127.0.0.1:8765) for the Electron overlay.
2. 100% Pure Gemini Live Native Audio Dialog Engine (gemini-2.5-flash-native-audio-preview-12-2025).
3. All 63 JARVIS tools registered for autonomous voice & typed tool calling.
4. Continuous hands-free voice dialog with acoustic echo suppression and barge-in.
5. Instant spoken greeting at startup and affectionate goodbye at shutdown.
"""

import os
import sys
import time
import signal
import threading

# Force UTF-8 on Windows console for emojis, or redirect to devnull if running under pythonw
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
elif hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')
elif hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.state_bridge import start_bridge, get_current_state, broadcast_state, stop_bridge
from core.voice import speak

live_voice_thread = None

def on_voice_status(state, message):
    if state == "SPEAKING":
        broadcast_state(speaking=True, mood="excited")
    elif state == "LISTENING":
        broadcast_state(speaking=False, mood="excited", caption="")
    elif state == "PROCESSING":
        broadcast_state(speaking=False, mood="focused", caption=message)
    elif state == "IDLE":
        broadcast_state(speaking=False, mood="idle", caption="")
    elif state == "RECONNECTING":
        broadcast_state(speaking=False, mood="focused", caption="Reconnecting...")

def on_voice_transcript(role, text):
    if role == "user":
        broadcast_state(caption=f"Rishabh: {text}")
    elif role == "lila":
        broadcast_state(speaking=True, caption=text, mood="excited")
    elif role == "system":
        # System debug logs remain internal and never overwrite speech bubble captions
        pass

def on_voice_error(err):
    print(f"[LILA VOICE ERROR]: {err}", flush=True)

def main():
    global live_voice_thread

    # 0. Strict Single-Instance Enforcement: Terminate any duplicate run_companion_bridge processes
    curr_pid = os.getpid()
    parent_pid = os.getppid() if hasattr(os, 'getppid') else None
    try:
        import psutil
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                pid = p.info['pid']
                if pid == curr_pid or (parent_pid and pid == parent_pid):
                    continue
                name = (p.info['name'] or '').lower()
                if not any(py in name for py in ['python', 'pythonw']):
                    continue
                cmd = " ".join(p.info['cmdline'] or []).lower()
                if 'run_companion_bridge.py' in cmd:
                    print(f"[LILA ENGINE] Terminating duplicate companion bridge PID {pid}...", flush=True)
                    p.kill()
            except Exception:
                pass


        # Also ensure port 8765 is freed
        for conn in psutil.net_connections(kind='inet'):
            if conn.laddr and conn.laddr.port == 8765 and conn.status == 'LISTEN':
                if conn.pid and conn.pid != curr_pid:
                    try:
                        p = psutil.Process(conn.pid)
                        print(f"[LILA ENGINE] Freeing port 8765 from PID {conn.pid}...", flush=True)
                        p.kill()
                        time.sleep(0.3)
                    except Exception:
                        pass
    except Exception:
        pass


    print("=" * 60, flush=True)
    print("  LILA DESKTOP COMPANION UNIFIED ENGINE ONLINE", flush=True)
    print("=" * 60, flush=True)

    # 1. Start WebSocket State Bridge on ws://127.0.0.1:8765
    print("[LILA ENGINE] Starting WebSocket State Bridge (127.0.0.1:8765)...", flush=True)
    start_bridge()
    time.sleep(0.3)

    # 2. Start Dedicated Gemini Live Voice Thread (Aoede voice)
    try:
        from live_voice import LiveVoiceThread
        from core.live_tools import ALL_DECLARATIONS
        print(f"[LILA ENGINE] Initializing Gemini Live Native Voice & {len(ALL_DECLARATIONS)} Tools...", flush=True)
        live_voice_thread = LiveVoiceThread()
        live_voice_thread.status_changed.connect(on_voice_status)
        live_voice_thread.transcript_received.connect(on_voice_transcript)
        live_voice_thread.error_occurred.connect(on_voice_error)
        live_voice_thread.start()
        print("[LILA ENGINE] Gemini Live Native Voice Thread active!", flush=True)
    except Exception as ex:
        print(f"[LILA ENGINE WARNING] Live voice thread could not start: {ex}", flush=True)

    # 3. Ensure OmniForge Pro Server Daemon (port 5252) is online
    try:
        from core.omniforge import ensure_master_server_running
        ensure_master_server_running()
        print("[LILA ENGINE] OmniForge Master Server active on http://localhost:5252", flush=True)
    except Exception as ex:
        print(f"[LILA ENGINE WARNING] OmniForge server daemon could not start: {ex}", flush=True)

    # 4. Start Zero-ADB Remote Mobile Server Daemon (port 8766)
    try:
        from core.remote_mobile_server import run_server, get_remote_urls
        remote_thread = threading.Thread(target=run_server, args=(8766,), daemon=True, name="LilaRemoteMobileServer")
        remote_thread.start()
        urls = get_remote_urls(8766)
        print(f"[LILA ENGINE] Zero-ADB Remote Mobile Studio online on {urls['lan']} (Local: {urls['local']})", flush=True)
    except Exception as ex:
        print(f"[LILA ENGINE WARNING] Remote mobile server could not start: {ex}", flush=True)

    print("[LILA ENGINE] System running. Hands-free listening, desktop overlay, and remote mobile link ready.", flush=True)

    # 4. Supervision Loop
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[LILA ENGINE] Shutting down cleanly...", flush=True)
    except BaseException as ex:
        import traceback
        print(f"\n[LILA ENGINE CRASH]: {ex}\n{traceback.format_exc()}", flush=True)
    finally:
        if live_voice_thread:
            try:
                live_voice_thread.stop()
            except Exception:
                pass
        stop_bridge()
        print("[LILA ENGINE] Shutdown complete.", flush=True)

if __name__ == "__main__":
    main()
