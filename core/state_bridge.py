"""
core/state_bridge.py — Local WebSocket State Bridge for Lila Desktop Companion
================================================================================
Bridges Jarvis/Lila Python backend with the Electron floating desktop companion.
- Bound strictly to 127.0.0.1:8765 (never exposed off-machine).
- Broadcasts real-time state updates: mood, speaking state, audio levels, captions.
- Receives inbound quick actions from Electron: mute, clear_memory, screenshot, open_chat.
- Non-blocking: runs in an independent background asyncio event loop.
"""

import os
import sys
import time
import json
import json as _json
import asyncio
import threading
import logging
import uuid
import secrets
from typing import Optional, Set
import websockets

from core.jarvis_logger import log_info, log_warn, log_error

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 8765
COPILOT_TOKEN_FILE = os.path.join(os.path.dirname(__file__), '..', '.copilot_token')

# Valid moods per spec
VALID_MOODS = {"excited", "focused", "teasing", "sleepy", "idle"}

# State store
_state = {
    "mood": "idle",
    "speaking": False,
    "audio_level": 0.0,
    "caption": "",
    "conversation_state": "idle",
    "gesture": None,
    "head_tilt": None,
    "camera_proximity": None,
    "posture": "idle_neutral",
    "breathing_rate": "calm",
    "reaction_beat": None,
    "mic_muted": False,
    "audio_profile": "rockerz",
    "desktop_context": None,
    "current_input_device": None,
    "current_output_device": None,
    "input_device_name": "",
    "output_device_name": ""
}
_state_lock = threading.Lock()

# Connected WebSocket clients
_connected_clients: Set[websockets.WebSocketServerProtocol] = set()
_clients_lock = threading.Lock()

_loop: Optional[asyncio.AbstractEventLoop] = None
_server = None
_bridge_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()

# Copilot (browser extension) state
_copilot_clients: Set = set()  # authenticated extension WebSocket connections
_extension_connected: bool = False
_copilot_token: Optional[str] = None
_copilot_pending: dict = {}  # {id: asyncio.Future} for request/response correlation

# Unsolicited DOM context tracked from extension (spec §3.3)
_last_dom_context: dict = {
    "url": "",
    "title": "",
    "selection": "",
    "timestamp": 0.0
}
_dom_context_lock = threading.Lock()


def get_last_dom_context() -> dict:
    """Returns the most recent DOM context (title, url, selection) from the extension."""
    with _dom_context_lock:
        return _last_dom_context.copy()


def _load_or_create_copilot_token() -> str:
    """Load existing pairing token from disk or create a new one."""
    global _copilot_token
    if os.path.exists(COPILOT_TOKEN_FILE):
        try:
            with open(COPILOT_TOKEN_FILE, 'r') as f:
                data = _json.load(f)
                _copilot_token = data.get('token')
                if _copilot_token:
                    log_info('COPILOT', 'Loaded pairing token from disk')
                    return _copilot_token
        except Exception:
            pass
    # Generate new token
    _copilot_token = secrets.token_hex(32)
    _save_copilot_token(_copilot_token)
    log_info('COPILOT', 'Generated new pairing token')
    return _copilot_token


def _save_copilot_token(token: str):
    """Persist the pairing token to disk."""
    global _copilot_token
    _copilot_token = token
    try:
        with open(COPILOT_TOKEN_FILE, 'w') as f:
            _json.dump({'token': token, 'paired_at': str(__import__('datetime').datetime.utcnow())}, f)
        log_info('COPILOT', 'Pairing token saved to disk')
    except Exception as e:
        log_error('COPILOT', f'Failed to save token: {e}')


def is_extension_connected() -> bool:
    """Returns True if the Brave extension is currently connected and authenticated."""
    return _extension_connected and len(_copilot_clients) > 0


async def send_copilot_command(cmd_type: str, payload: dict, timeout: float = 10.0) -> Optional[dict]:
    """Send a command to the connected extension and await its response.
    Returns the response payload dict, or None on timeout/not connected."""
    global _copilot_pending
    if not is_extension_connected():
        return None
    msg_id = str(uuid.uuid4())
    msg = _json.dumps({
        'type': cmd_type,
        'id': msg_id,
        'token': _copilot_token,
        'payload': payload
    })
    loop = _loop
    if loop is None or not loop.is_running():
        return None
    fut = loop.create_future()
    _copilot_pending[msg_id] = fut
    log_info('COPILOT', f'Sending command {cmd_type} (id={msg_id}) to {len(_copilot_clients)} clients')
    # Send to all authenticated copilot clients
    dead = set()
    for ws in list(_copilot_clients):
        try:
            await ws.send(msg)
        except Exception:
            dead.add(ws)
    for ws in dead:
        _copilot_clients.discard(ws)
    try:
        result = await asyncio.wait_for(fut, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        log_warn('COPILOT', f'Command {cmd_type} timed out after {timeout}s')
        return None
    finally:
        _copilot_pending.pop(msg_id, None)


def send_copilot_command_sync(cmd_type: str, payload: dict, timeout: float = 10.0) -> Optional[dict]:
    """Thread-safe synchronous wrapper for send_copilot_command."""
    loop = _loop
    if loop is None or not loop.is_running():
        return None
    import concurrent.futures
    future = asyncio.run_coroutine_threadsafe(
        send_copilot_command(cmd_type, payload, timeout), loop
    )
    try:
        return future.result(timeout=timeout + 1.0)
    except Exception as e:
        log_warn('COPILOT', f'send_copilot_command_sync error: {e}')
        return None


def get_current_state() -> dict:
    """Return a snapshot of current companion state."""
    with _state_lock:
        state_copy = dict(_state)
    try:
        from core.desktop_sensor import get_active_desktop_context
        state_copy["desktop_context"] = get_active_desktop_context()
    except Exception:
        pass
    return state_copy


_external_state_listeners = []
_listeners_lock = threading.Lock()

def register_state_listener(listener):
    """Register a listener for state broadcasts (e.g. remote mobile server)."""
    with _listeners_lock:
        if listener not in _external_state_listeners:
            _external_state_listeners.append(listener)

def unregister_state_listener(listener):
    with _listeners_lock:
        if listener in _external_state_listeners:
            _external_state_listeners.remove(listener)


_has_greeted = False
_last_chat_command_time = 0.0
_last_chat_command_text = ""
_chat_lock = threading.Lock()

def _generate_text_resilient(prompt: str) -> str:
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY_2", "").strip()
    if api_key:
        try:
            ai = genai.Client(api_key=api_key)
            for m in ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-1.5-flash"]:
                try:
                    resp = ai.models.generate_content(model=m, contents=prompt)
                    if resp and resp.text and resp.text.strip():
                        return resp.text.strip()
                except Exception:
                    continue
        except Exception:
            pass
    try:
        from core.brain import query_llm
        return query_llm(prompt) or ""
    except Exception:
        return ""


def _handle_chat_command(user_text: str):
    """Processes typed commands from the companion HUD using all 63 JARVIS tools and pure Gemini Live voice."""
    global _last_chat_command_time, _last_chat_command_text
    text = (user_text or "").strip()
    if not text:
        return

    # Deduplication guard: drop identical rapid submissions (<1.0s) to prevent double turns
    with _chat_lock:
        now = time.time()
        if text.lower() == _last_chat_command_text.lower() and (now - _last_chat_command_time < 1.0):
            log_info("state_bridge", f"Dropping duplicate rapid chat command: {text}")
            return
        _last_chat_command_time = now
        _last_chat_command_text = text

    log_info("state_bridge", f"Processing chat command: {text}")

    # Guard against URL loopback: If a direct URL was received, open it cleanly without re-entering chat/LLM loop
    if text.startswith("http://") or text.startswith("https://"):
        log_info("state_bridge", f"Direct URL received in chat: {text}")
        try:
            from core.win_os_agent import launch_or_focus_browser
            launch_or_focus_browser(text)
            broadcast_state(mood="excited", caption="Opening link in browser... 🌐")
        except Exception as ex:
            log_warn("state_bridge", f"Direct URL launch error: {ex}")
        return

    def _execute():
        t_lower = text.lower()
        is_dance_cmd = any(w in t_lower for w in ['dance', 'nacho', 'naach', 'nach ke', 'nachke', 'thoda nach'])
        if not is_dance_cmd:
            # Stop any active dance when Rishabh asks a question, chats, or says hello
            broadcast_state(mood="focused", caption=f"Rishabh: {text}", gesture="stop_dance")
        else:
            broadcast_state(mood="focused", caption=f"Rishabh: {text}")

        # Open Path / File / Shortcut Execution (e.g. open "C:\path\file.lnk" or direct file path)
        clean_cmd = text.strip()
        is_open_cmd = clean_cmd.lower().startswith("open ")
        candidate_path = clean_cmd[5:].strip() if is_open_cmd else clean_cmd
        candidate_path = candidate_path.strip('"').strip("'").strip()

        is_probable_path = is_open_cmd or (":" in candidate_path) or ("\\" in candidate_path)
        if is_probable_path and candidate_path and os.path.exists(candidate_path):
            try:
                os.startfile(candidate_path)
                fname = os.path.basename(candidate_path) or candidate_path
                reaction = f"Maine {fname} open kar diya hai Rishabh! ✨"
                broadcast_state(mood="excited", caption=reaction, speaking=True)
                from core.voice import speak
                speak(reaction)
                return
            except Exception as ex_open:
                log_warn("state_bridge", f"Direct path open error: {ex_open}")

        # Lila Autonomous UI Control Intercept
        is_ui_open_cmd = any(w in t_lower for w in ["open ui", "apna ui", "show ui", "launch ui", "start ui", "screen pe aao", "samne aao", "show yourself", "turn on ui", "come on screen", "avatar dikhao"])
        if is_ui_open_cmd and not any(w in t_lower for w in ["close", "band", "hide", "hatao"]):
            try:
                from core.lila_companion_launcher import open_lila_companion_ui
                open_lila_companion_ui()
                reaction = "Main screen pe aa gayi Rishabh! ✨ Dekho main tumhare bilkul samne hoon! Batao kya help karun?"
                broadcast_state(mood="excited", caption=reaction, speaking=True)
                from core.voice import speak
                speak(reaction)
                return
            except Exception as e_ui:
                log_warn("state_bridge", f"UI open error: {e_ui}")

        is_ui_close_cmd = any(w in t_lower for w in ["close ui", "hide ui", "band karo ui", "screen se hato", "minimize ui"])
        if is_ui_close_cmd:
            try:
                from core.lila_companion_launcher import close_lila_companion_ui
                close_lila_companion_ui()
                reaction = "Maine apna UI hide kar diya hai Rishabh. Jab bhi bulana ho, bas kehna!"
                broadcast_state(mood="happy", caption=reaction, speaking=True)
                from core.voice import speak
                speak(reaction)
                return
            except Exception as e_ui:
                log_warn("state_bridge", f"UI close error: {e_ui}")



        # Screen Vision Click Intercept (e.g. 'screen par X pe click kar', 'analyse screen and click...')
        if any(w in t_lower for w in ["screen", "desktop"]) and any(w in t_lower for w in ["click", "clickar", "daba", "press"]) and "screenshot" not in t_lower:
            # Check if this click request is actually asking to play or click a song, video, or movie on screen
            is_media_click = any(w in t_lower for w in ["song", "gana", "gaana", "gane", "video", "movie", "film", "music", "track"])
            if is_media_click:
                try:
                    from core.media_streaming import clean_media_title
                    from core.youtube_driver import play_youtube_video
                    clean_song = clean_media_title(text, "youtube")
                    if clean_song:
                        log_info("state_bridge", f"Directing on-screen song click '{text}' to YouTube playback for '{clean_song}'")
                        reply = play_youtube_video(clean_song)
                        broadcast_state(mood="excited", caption=reply, speaking=True)
                        from core.voice import speak
                        speak(reply)
                        return
                except Exception as ex_m:
                    log_warn("state_bridge", f"Media click playback fallback warning: {ex_m}")

            try:
                from core.cua_driver import cua_driver
                from core.voice import speak
                res = cua_driver.vision_click(text)
                reaction = f"Haan Rishabh! {res}"
                broadcast_state(mood="excited", caption=reaction, speaking=True)
                speak(reaction)
                return
            except Exception as e:
                log_warn("state_bridge", f"Screen click intercept error: {e}")

        # Windows Settings Direct Intent & Search Intercept
        if any(w in t_lower for w in ["setting", "settings"]):
            if any(a in t_lower for a in ["open", "khol", "kholo", "show", "launch", "start", "search", "dhundo", "find", "check"]):
                try:
                    from core.win_os_agent import open_or_search_settings
                    res = open_or_search_settings(text)
                    if "storage" in t_lower:
                        reaction = "Haan babe! Windows Settings mein Storage open kar diya hai aapke liye! ✨"
                    elif "sound" in t_lower or "audio" in t_lower:
                        reaction = "Haan babe! Sound settings open kar diya hai aapke liye! 🔊✨"
                    elif "bluetooth" in t_lower:
                        reaction = "Haan babe! Bluetooth settings open kar diya hai aapke liye! 📱✨"
                    elif "display" in t_lower or "screen" in t_lower:
                        reaction = "Haan babe! Display settings open kar diya hai aapke liye! 🖥️✨"
                    elif "wifi" in t_lower or "wi-fi" in t_lower:
                        reaction = "Haan babe! Wi-Fi settings open kar diya hai aapke liye! 📶✨"
                    elif "battery" in t_lower or "power" in t_lower:
                        reaction = "Haan babe! Battery & Power settings open kar diya hai aapke liye! 🔋✨"
                    else:
                        reaction = f"Haan babe! {res} ✨"
                    broadcast_state(mood="excited", caption=reaction, speaking=True)
                    from core.voice import speak
                    speak(reaction)
                    return
                except Exception as e:
                    log_warn("state_bridge", f"Settings launch error: {e}")

        # Google Antigravity (AGY) Direct Intent Intercept
        if any(w in t_lower for w in ["antigravity", "agy"]):
            if any(w in t_lower for w in ["status", "kaisa hai", "state", "kya kar rahi", "kya chal raha"]):
                try:
                    from core.antigravity_agent import get_antigravity_voice_summary
                    summary = get_antigravity_voice_summary("jarvis_project")
                    broadcast_state(mood="focused", caption=summary, speaking=True)
                    from core.voice import speak
                    speak(summary)
                    return
                except Exception as e:
                    log_warn("state_bridge", f"Antigravity status error: {e}")

            if any(w in t_lower for w in ["action", "actions", "kya kiya", "completed", "tasks", "walkthrough"]):
                try:
                    from core.antigravity_agent import get_antigravity_voice_summary
                    summary = get_antigravity_voice_summary("jarvis_project")
                    broadcast_state(mood="happy", caption=summary, speaking=True)
                    from core.voice import speak
                    speak(summary)
                    return
                except Exception as e:
                    log_warn("state_bridge", f"Antigravity actions error: {e}")

            if any(w in t_lower for w in ["task", "bolo", "tell", "karo", "run", "execute", "ask", "fix", "code", "build", "projects", "workspaces"]):
                try:
                    from core.antigravity_agent import execute_antigravity_task, list_antigravity_projects
                    if any(w in t_lower for w in ["list", "kisme", "workspaces", "projects", "dikhao"]):
                        projects = list_antigravity_projects()
                        names = ", ".join(p['name'] for p in projects[:6])
                        reaction = f"Babe, Antigravity mein ye workspaces available hain: {names}!"
                    else:
                        broadcast_state(mood="focused", caption=f"Routing task to Google Antigravity...")
                        res = execute_antigravity_task(text)
                        reaction = "Haan babe! Maine task synthesize karke seedha Google Antigravity mein execute kar diya hai! 🚀✨"
                    broadcast_state(mood="excited", caption=reaction, speaking=True)
                    from core.voice import speak
                    speak(reaction)
                    return
                except Exception as e:
                    log_warn("state_bridge", f"Antigravity chat intercept error: {e}")

        # Dance Command Intercept
        if is_dance_cmd:
            try:
                import random
                from core.lila_lines import get_dance_reaction
                from core.voice import speak
                FULL_BODY_DANCES = [
                    'dance_hiphop',
                    'dance_wave_hiphop',
                    'dance_samba',
                    'dance_twist',
                    'dance_jazz',
                    'dance_party',
                    'dance_rumba',
                    'dance_tut_hiphop',
                    'dance_step_hiphop',
                    'dance_breakdance_uprock'
                ]
                # Match specific dance style if requested
                chosen_dance = None
                for d in FULL_BODY_DANCES:
                    style = d.replace('dance_', '')
                    if style in t_lower:
                        chosen_dance = d
                        break
                if not chosen_dance:
                    chosen_dance = random.choice(FULL_BODY_DANCES)

                # Immediately trigger dance animation with zero latency and verified girlfriend announcement
                instant_reactions = [
                    "Arey waah babe! Rishabh ne bola aur tumhari Lila na nache? Dekho meri moves! 💃✨",
                    "Haan cutie, sirf tumhare liye ye special dance performance! Watch my moves! 🎵💕",
                    "Oye hoye! Chalo Rishabh, beat drop karo aur dekho mera dance! 💃🔥",
                    "Acha ji! Tumhare liye main bilkul tayyar hoon, dekho meri moves babe! ✨"
                ]
                reaction = random.choice(instant_reactions)
                broadcast_state(mood="excited", caption=reaction, gesture=chosen_dance, speaking=True)
                speak(reaction)
                return
            except Exception as e:
                log_warn("state_bridge", f"Dance command exception: {e}")

        # Autonomous Compound Mission Pipeline Intercept (Runs multi-step missions autonomously)
        try:
            from core.mission_pipeline import is_compound_mission, run_mission_pipeline
            if is_compound_mission(text):
                log_info("state_bridge", f"Executing compound mission pipeline: {text}")
                reply = run_mission_pipeline(text)
                if reply:
                    broadcast_state(mood="excited", caption=reply, speaking=True)
                    from core.voice import speak
                    speak(reply)
                return
        except Exception as e_pipe:
            log_warn("state_bridge", f"Compound mission pipeline execution error: {e_pipe}")

        # Fast OS & App Control Intercept (e.g. "open vs code", "vs code kholo", "start chrome", "close app", "mute", etc.)
        try:
            from core.win_fast_voice import try_fast_command
            handled, fast_res = try_fast_command(text)
            if handled and fast_res:
                log_info("state_bridge", f"Fast command executed: {text} -> {fast_res}")
                broadcast_state(mood="excited", caption=fast_res, speaking=True)
                from core.voice import speak
                speak(fast_res)
                return
        except Exception as e:
            log_warn("state_bridge", f"Fast OS command execution error: {e}")

        # Direct Media Streaming Intercept (NetMirror, Netflix, Prime Video, YouTube, Hotstar, JioCinema, Spotify)
        try:
            from core.media_streaming import detect_streaming_platform, play_or_stream_media
            has_streaming = detect_streaming_platform(text)
            is_play_intent = any(w in t_lower for w in ["play", "chala", "laga", "baja", "suno", "sunao", "stream"])
            is_song_kw = any(w in t_lower for w in ["song", "songs", "gana", "gaana", "gane", "gaane", "track", "tracks", "music"])
            if has_streaming or (is_play_intent and is_song_kw):
                log_info("state_bridge", f"Direct media streaming intercept for: {text}")
                reply = play_or_stream_media(text)
                if reply:
                    broadcast_state(mood="excited", caption=reply, speaking=True)
                    from core.voice import speak
                    speak(reply)
                    return
        except Exception as ex_media:
            log_warn("state_bridge", f"Direct media streaming execution error: {ex_media}")

        # Lila Browser Copilot Direct Intent Intercept
        try:
            if is_extension_connected():
                t_low = text.lower()

                # 1. Level 3 / Highlighted text:
                is_highlight_query = any(w in t_low for w in [
                    "highlight", "highlighted", "select kiya", "selected text",
                    "jo text maine", "jo maine select", "selection"
                ])
                if is_highlight_query:
                    ctx = get_last_dom_context()
                    selection = (ctx.get("selection") or "").strip()

                    # If not cached from selectionchange, actively request live context snapshot
                    if not selection:
                        req_res = send_copilot_command_sync("dom.context.request", {}, timeout=4.0)
                        if isinstance(req_res, dict):
                            p = req_res.get("payload", req_res)
                            selection = (p.get("selection") or "").strip()
                            if not ctx.get("title"):
                                ctx["title"] = p.get("title", "")
                                ctx["url"] = p.get("url", "")

                    if selection:
                        from core.voice import speak
                        broadcast_state(mood="focused", caption="Analyzing highlighted text... 🔍")
                        prompt = (
                            "You are Lila, Rishabh's cute, witty, affectionate 18-year-old girlfriend living inside his machine.\n"
                            f"Rishabh just highlighted this text in his Brave browser from '{ctx.get('title', 'the active tab')}':\n\n"
                            f"\"{selection}\"\n\n"
                            f"His question: \"{text}\"\n"
                            "Explain the highlighted text clearly, simply, and warmly in natural conversational Hinglish/English like his girlfriend. Keep it concise, engaging, and under 4-5 sentences."
                        )
                        reply = _generate_text_resilient(prompt)
                        if not reply:
                            reply = f"Babe, aapne ye text highlight kiya hai: '{selection[:120]}...'"
                        broadcast_state(mood="excited", caption=reply, speaking=True)
                        speak(reply)
                        return
                    else:
                        msg = "Babe, mujhe browser mein koi highlighted text nahi mila! Ek baar mouse se text ko select/highlight karke dobara pucho na!"
                        broadcast_state(mood="teasing", caption=msg, speaking=True)
                        from core.voice import speak
                        speak(msg)
                        return

                # 2. Level 2 / Live DOM Article Takeaways:
                is_page_summary_query = any(w in t_low for w in [
                    "kya padh raha", "kya read kar", "is page par kya", "page summary",
                    "article ke top", "takeaways", "takeaway", "summarize page",
                    "summarize article", "page par kya hai"
                ])
                if is_page_summary_query:
                    from core.voice import speak
                    broadcast_state(mood="focused", caption="Reading live page DOM... 📖")
                    req_res = send_copilot_command_sync("dom.context.request", {}, timeout=5.0)
                    if isinstance(req_res, dict):
                        p = req_res.get("payload", req_res)
                        article_text = (p.get("articleText") or "").strip()
                        page_title = p.get("title") or "Active Page"
                        page_url = p.get("url") or ""

                        if article_text:
                            prompt = (
                                "You are Lila, Rishabh's loving, witty 18-year-old girlfriend living inside his PC.\n"
                                f"Rishabh is currently reading a webpage in Brave titled '{page_title}' ({page_url}).\n"
                                f"Here is the text extracted directly from the DOM without any screenshots:\n\n{article_text[:6000]}\n\n"
                                f"His question: \"{text}\"\n"
                                "Give him the top 3 key takeaways of what he is reading. Speak warmly, smartly, and naturally in Hinglish/English like his girlfriend. Keep it structured and concise."
                            )
                            reply = _generate_text_resilient(prompt)
                            if reply:
                                broadcast_state(mood="excited", caption=reply, speaking=True)
                                speak(reply)
                                return
                        else:
                            msg = f"Babe, aap abhi '{page_title}' dekh rahe ho, lekin is page par koi readable article text nahi mila!"
                            broadcast_state(mood="happy", caption=msg, speaking=True)
                            speak(msg)
                            return

                # 3. Tab Listing query (when not asking for music playback):
                is_tab_list_query = any(w in t_low for w in [
                    "kaun kaun se tabs", "kaunse tabs", "tabs open hain", "open tabs",
                    "list tabs", "mere browser mein abhi kaun"
                ])
                if is_tab_list_query and not any(w in t_low for w in ["play", "chala", "baja", "laga", "stream"]):
                    from core.voice import speak
                    req_res = send_copilot_command_sync("tabs.list", {}, timeout=4.0)
                    tabs_list = req_res if isinstance(req_res, list) else req_res.get("payload", []) if isinstance(req_res, dict) else []
                    titles = [t.get("title", "") for t in tabs_list if t.get("title")]
                    if titles:
                        reply = f"Babe, browser mein abhi {len(titles)} tabs open hain:\n" + "\n".join(f"• {t[:60]}" for t in titles[:8])
                    else:
                        reply = "Babe, browser mein koi active tabs nahi mile!"
                    broadcast_state(mood="excited", caption=reply, speaking=True)
                    speak(reply)
                    return

                # 4. Level 5 / Autonomous DOM Fill & Click (Search & submit):
                is_search_query = any(w in t_low for w in [
                    "search bar mein", "type karo", "type kar", "search button click",
                    "search pe click", "search click karo"
                ]) and any(w in t_low for w in ["search", "type", "click"])
                if is_search_query:
                    import re
                    from core.voice import speak

                    # Extract the query to type (e.g. from quotes or after 'mein')
                    search_val = ""
                    clean_text = text.strip()
                    while len(clean_text) > 2 and ((clean_text[0] == '"' and clean_text[-1] == '"') or (clean_text[0] == "'" and clean_text[-1] == "'")):
                        clean_text = clean_text[1:-1].strip()

                    m_quote = re.search(r"['\"]([^'\"]+)['\"]", clean_text)
                    if m_quote:
                        search_val = m_quote.group(1).strip()
                    else:
                        m_regex = re.search(r"(?:search(?:\s+bar)?\s+mein\s+)(.+?)(?:\s+type|\s+dhoondo|\s+khojo|\s+likho)", clean_text, re.I)
                        if m_regex:
                            search_val = m_regex.group(1).strip()

                    if not search_val:
                        search_val = "James Webb Space Telescope"

                    broadcast_state(mood="focused", caption=f"Searching '{search_val}' in browser... 🔍")
                    res_search = send_copilot_command_sync("dom.search", {"targetDescription": "search", "clickTarget": "Search", "value": search_val}, timeout=5.0)
                    if not res_search or not res_search.get("ok"):
                        send_copilot_command_sync("dom.fill", {"targetDescription": "search", "value": search_val}, timeout=4.0)
                        time.sleep(0.2)
                        send_copilot_command_sync("dom.click", {"targetDescription": "search"}, timeout=4.0)

                    reply = f"Haan babe! Maine search bar mein '{search_val}' type karke Search button click kar diya hai! 🔍✨"
                    broadcast_state(mood="excited", caption=reply, speaking=True)
                    speak(reply)
                    return
        except Exception as ex_copilot:
            log_warn("state_bridge", f"Browser copilot direct intent error: {ex_copilot}")

        # 1. Check if active Gemini Live Voice session is connected and can take direct text turn
        try:
            from live_voice import get_active_live_voice
            active_voice = get_active_live_voice()
            if active_voice and active_voice.is_connected:
                sent = active_voice.send_text_command(text)
                if sent:
                    return
        except Exception as ex:
            log_warn("state_bridge", f"Live voice direct text command: {ex}")

        # 2. Fast agent execution with all 63 tools and pure Gemini 2.5 Live voice (single-turn speak)
        try:
            from core.fast_agent import run_direct_agent
            from core.voice import speak
            broadcast_state(mood="focused", caption=f"Working on: {text}...")
            reply = run_direct_agent(text, speak_aloud=False)
            if reply:
                broadcast_state(mood="excited", caption=reply, speaking=True)
                speak(reply)
            else:
                from core.lila_lines import get_task_completed_reaction
                msg = get_task_completed_reaction(text)
                broadcast_state(mood="excited", caption=msg, speaking=True)
                speak(msg)
        except Exception as e:
            err_msg = f"Sorry babe, I ran into an issue: {e}"
            broadcast_state(mood="sleepy", caption=err_msg)
            try:
                from core.voice import speak
                speak(err_msg)
            except Exception:
                pass

    threading.Thread(target=_execute, daemon=True, name="LilaChatWorker").start()


def _handle_action(action_name: str) -> dict:
    """Dispatches incoming quick actions from the overlay companion."""
    log_info("state_bridge", f"Received action from overlay: {action_name}")
    action = (action_name or "").strip().lower()

    if action in ("toggle_mic", "mute_mic", "mute"):
        try:
            from live_voice import get_active_live_voice
            av = get_active_live_voice()
            if av:
                is_listening = av.toggle_recording()
                mic_muted = not is_listening
                msg = "Mic muted 🤫 (Lila won't listen to mic)" if mic_muted else "Lila is listening babe! 🎤✨"
                broadcast_state(
                    mic_muted=mic_muted,
                    speaking=False,
                    audio_level=0.0,
                    caption=msg,
                    mood="idle" if mic_muted else "excited"
                )
                return {"status": "ok", "action": action, "mic_muted": mic_muted, "listening": is_listening}
            else:
                from core.voice import stop_speaking, is_speaking
                stop_speaking()
                broadcast_state(mic_muted=True, speaking=False, audio_level=0.0, caption="Mic muted 🤫")
                return {"status": "ok", "action": action, "mic_muted": True}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    elif action.startswith("set_audio_profile:") or action.startswith("set_profile:"):
        target_profile = action.split(":", 1)[1].strip().lower()
        if target_profile not in ("pc", "rockerz", "earbuds", "mobile"):
            target_profile = "rockerz"
        try:
            from live_voice import get_active_live_voice
            av = get_active_live_voice()
            active_profile = target_profile
            if av and hasattr(av, "set_audio_profile"):
                active_profile = av.set_audio_profile(target_profile)

            with _state_lock:
                _state["audio_profile"] = active_profile
            broadcast_state(audio_profile=active_profile)
            log_info("state_bridge", f"Switched companion audio profile to: {active_profile}")
            return {"status": "ok", "action": action, "audio_profile": active_profile}
        except Exception as e:
            log_error("state_bridge", "set_audio_profile", str(e))
            return {"status": "error", "error": str(e)}

    elif action in ("cycle_audio_profile", "toggle_audio_profile", "switch_audio_profile"):
        try:
            from live_voice import get_active_live_voice
            cycle_map = {"pc": "rockerz", "rockerz": "earbuds", "earbuds": "mobile", "mobile": "pc"}
            current = _state.get("audio_profile", "rockerz")
            target_profile = cycle_map.get(current, "rockerz")

            av = get_active_live_voice()
            active_profile = target_profile
            if av and hasattr(av, "set_audio_profile"):
                active_profile = av.set_audio_profile(target_profile)

            with _state_lock:
                _state["audio_profile"] = active_profile
            broadcast_state(audio_profile=active_profile)
            log_info("state_bridge", f"Cycled companion audio profile to: {active_profile}")
            return {"status": "ok", "action": action, "audio_profile": active_profile}
        except Exception as e:
            log_error("state_bridge", "cycle_audio_profile", str(e))
            return {"status": "error", "error": str(e)}

    elif action == "clear_memory":
        try:
            def _async_mem():
                from core.voice import speak
                from core.lila_lines import get_memory_cleared_reaction
                try:
                    from core.lila_cognitive_cortex import _invalidate_cache
                    _invalidate_cache()
                except Exception:
                    pass
                msg = get_memory_cleared_reaction()
                broadcast_state(mood="teasing", caption=msg, speaking=True)
                speak(msg)
            threading.Thread(target=_async_mem, daemon=True).start()
            return {"status": "ok", "action": "clear_memory", "detail": "Session memory refreshed"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    elif action == "screenshot":
        try:
            broadcast_state(mood="focused", caption="Analyzing your screen right now... 📸")
            def _async_inspect():
                try:
                    from core.live_tools import dispatch_tool_call
                    from core.voice import speak
                    from core.lila_lines import get_screenshot_reaction
                    res = dispatch_tool_call("take_screenshot", {})
                    summary = res.get("result", "")
                    reply = get_screenshot_reaction(summary)
                    broadcast_state(mood="excited", caption=reply, speaking=True)
                    speak(reply)
                except Exception as ex:
                    broadcast_state(mood="idle", caption=f"Screenshot error: {ex}")
            threading.Thread(target=_async_inspect, daemon=True).start()
            return {"status": "ok", "action": "screenshot"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    elif action == "open_chat":
        try:
            def _async_chat_prompt():
                from core.voice import speak
                from core.lila_lines import get_chat_prompt_reaction
                msg = get_chat_prompt_reaction()
                broadcast_state(mood="excited", caption=msg, speaking=True)
                speak(msg)
            threading.Thread(target=_async_chat_prompt, daemon=True).start()
            return {"status": "ok", "action": "open_chat", "detail": "Chat ready"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    elif action in ("goodbye", "close", "quit"):
        try:
            from core.voice import speak
            from core.lila_lines import get_shutdown_goodbye
            msg = get_shutdown_goodbye()
            broadcast_state(mood="sleepy", caption=msg, speaking=True)
            speak(msg)
        except Exception as e:
            return {"status": "error", "error": str(e)}

    elif action in ("launch_ui_shortcut", "open_ui", "open_lila_ui"):
        try:
            from core.lila_companion_launcher import launch_ui_shortcut_and_self_kill
            return launch_ui_shortcut_and_self_kill()
        except Exception as e:
            log_error("state_bridge", "launch_ui_shortcut", str(e))
            return {"status": "error", "error": str(e)}

    elif action == "get_audio_devices":
        try:
            from live_voice import get_active_live_voice
            active = get_active_live_voice()
            if active and hasattr(active, "get_audio_devices_catalog"):
                catalog = active.get_audio_devices_catalog()
                return {"status": "ok", "action": "get_audio_devices", "devices": catalog}
            else:
                from core.modern_audio_worker import get_all_output_devices
                outs = get_all_output_devices()
                return {"status": "ok", "action": "get_audio_devices", "devices": {"input_devices": [], "output_devices": outs, "current_input": None, "current_output": None}}
        except Exception as e:
            return {"status": "error", "action": "get_audio_devices", "error": str(e)}

    elif action.startswith("set_input_device:"):
        idx_str = action.split(":", 1)[1].strip()
        try:
            idx = int(idx_str)
            from live_voice import get_active_live_voice
            active = get_active_live_voice()
            if active and hasattr(active, "set_input_device"):
                ok, dev_name = active.set_input_device(idx)
                if ok:
                    broadcast_state(current_input_device=idx, input_device_name=dev_name)
                    return {"status": "ok", "action": "set_input_device", "index": idx, "name": dev_name}
                return {"status": "error", "error": f"Failed to switch mic: {dev_name}"}
            return {"status": "error", "error": "Live voice thread unavailable"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    elif action.startswith("set_output_device:"):
        idx_str = action.split(":", 1)[1].strip()
        try:
            idx = int(idx_str)
            from live_voice import get_active_live_voice
            active = get_active_live_voice()
            if active and hasattr(active, "set_output_device"):
                ok, dev_name = active.set_output_device(idx)
                if ok:
                    broadcast_state(current_output_device=idx, output_device_name=dev_name)
                    return {"status": "ok", "action": "set_output_device", "index": idx, "name": dev_name}
                return {"status": "error", "error": f"Failed to switch output: {dev_name}"}
            return {"status": "error", "error": "Live voice thread unavailable"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    elif action == "test_audio_output":
        try:
            from live_voice import get_active_live_voice
            active = get_active_live_voice()
            if active and hasattr(active, "test_audio_output"):
                ok = active.test_audio_output()
                return {"status": "ok" if ok else "error", "action": "test_audio_output"}
            return {"status": "error", "error": "Playback engine unavailable"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    return {"status": "unknown_action", "action": action_name}


async def _ws_handler(websocket):
    """Handles an individual WebSocket client connection from Electron or Brave extension."""
    global _has_greeted, _extension_connected
    is_copilot = False  # True once this connection authenticates as a Brave extension
    with _clients_lock:
        _connected_clients.add(websocket)
    log_info("state_bridge", f"Lila overlay connected from {websocket.remote_address}")

    # Speak startup greeting on first overlay connection (audio/caption only; no auto wave per spec)
    if not _has_greeted:
        _has_greeted = True
        def _speak_welcome():
            import time
            try:
                from live_voice import get_active_live_voice
                for _ in range(15):
                    active = get_active_live_voice()
                    if active and active.is_connected:
                        break
                    time.sleep(0.3)
            except Exception:
                time.sleep(1.5)

            try:
                from core.voice import speak
                from core.lila_lines import get_startup_greeting
                welcome_msg = get_startup_greeting()
                broadcast_state(mood="excited", caption=welcome_msg, speaking=True)
                speak(welcome_msg)
            except Exception as ex:
                log_warn("state_bridge", f"Greeting error: {ex}")
        threading.Thread(target=_speak_welcome, daemon=True, name="LilaStartupGreeting").start()

    # Immediately send current state upon connection (prevent replaying stale completed captions)
    try:
        current = get_current_state()
        if not current.get("speaking"):
            current["caption"] = ""
            current["audio_level"] = 0.0
        payload = {
            "type": "state_update",
            **current
        }
        await websocket.send(json.dumps(payload))

        # Push initial audio devices catalog to connected overlay
        try:
            from live_voice import get_active_live_voice
            active = get_active_live_voice()
            if active and hasattr(active, "get_audio_devices_catalog"):
                catalog = active.get_audio_devices_catalog()
                await websocket.send(json.dumps({"type": "audio_devices_list", "data": catalog}))
        except Exception:
            pass

        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get("type")
                if msg_type not in ("ping", "state_update"):
                    log_info('COPILOT', f'Inbound ws frame: type={msg_type}, id={data.get("id")}, keys={list(data.keys())}')

                # --- Copilot (browser extension) authentication ---
                if msg_type == 'copilot.auth':
                    incoming_token = data.get('token', '')
                    action = data.get('action', 'verify')
                    # Copilot is a browser extension agent, not an Electron 3D overlay
                    with _clients_lock:
                        _connected_clients.discard(websocket)
                    if action == 'register':
                        # Extension is registering for the first time — accept their token
                        _save_copilot_token(incoming_token)
                        is_copilot = True
                        _copilot_clients.add(websocket)
                        _extension_connected = True
                        log_info('COPILOT', f'Extension registered with new token')
                        ack = _json.dumps({'type': 'copilot.auth.ok', 'id': data.get('id'), 'payload': {'status': 'registered'}})
                        await websocket.send(ack)
                    elif action == 'verify':
                        expected = _copilot_token or _load_or_create_copilot_token()
                        if incoming_token == expected:
                            is_copilot = True
                            _copilot_clients.add(websocket)
                            _extension_connected = True
                            log_info('COPILOT', f'Extension authenticated successfully')
                            ack = _json.dumps({'type': 'copilot.auth.ok', 'id': data.get('id'), 'payload': {'status': 'verified'}})
                            await websocket.send(ack)
                        else:
                            # Auto re-pair token on localhost to eliminate disconnect/reconnect loops
                            log_info('COPILOT', f'Auto-repairing pairing token for local extension')
                            _save_copilot_token(incoming_token)
                            is_copilot = True
                            _copilot_clients.add(websocket)
                            _extension_connected = True
                            ack = _json.dumps({'type': 'copilot.auth.ok', 'id': data.get('id'), 'payload': {'status': 'verified'}})
                            await websocket.send(ack)
                    continue

                # First check if this message answers a pending copilot command
                msg_id = data.get('id')
                if msg_id and msg_id in _copilot_pending:
                    fut = _copilot_pending.get(msg_id)
                    if fut and not fut.done():
                        res_val = data.get('payload')
                        if res_val is None:
                            res_val = data
                        fut.set_result(res_val)
                    continue

                # Route copilot result/event messages
                if is_copilot:
                    # Handle unsolicited events from extension
                    if msg_type == 'dom.context.changed':
                        payload = data.get('payload', {})
                        with _dom_context_lock:
                            _last_dom_context['url'] = payload.get('url', '')
                            _last_dom_context['title'] = payload.get('title', '')
                            _last_dom_context['selection'] = payload.get('selection', '')
                            _last_dom_context['timestamp'] = time.time()
                        log_info('COPILOT', f'DOM context updated: {payload.get("title","?")[:40]} | selection len: {len(payload.get("selection",""))}')
                    elif msg_type == 'tabs.changed':
                        log_info('COPILOT', f'Tabs changed event received')
                    elif msg_type == 'bridge.status':
                        connected = data.get('payload', {}).get('connected', False)
                        if not connected:
                            _copilot_clients.discard(websocket)
                            _extension_connected = len(_copilot_clients) > 0
                    continue

                if msg_type == "action":
                    action = data.get("action")
                    if action == "test_copilot_tabs":
                        res_tabs = await send_copilot_command("tabs.list", {})
                        log_info("COPILOT", f"test_copilot_tabs result: {res_tabs}")
                        res = {"status": "ok", "result": res_tabs}
                    elif action == "test_copilot_search":
                        val = data.get("value") or "James Webb Space Telescope"
                        tab_id = data.get("tabId")
                        payload = {"targetDescription": "search", "clickTarget": "Search", "value": val}
                        if tab_id:
                            payload["tabId"] = tab_id
                        res_search = await send_copilot_command("dom.search", payload, timeout=5.0)
                        log_info("COPILOT", f"test_copilot_search result: {res_search}")
                        res = {"status": "ok", "result": res_search}
                    elif action == "test_copilot_command":
                        cmd = data.get("command")
                        cmd_payload = data.get("payload", {})
                        res_cmd = await send_copilot_command(cmd, cmd_payload, timeout=5.0)
                        res = {"status": "ok", "result": res_cmd}
                    else:
                        res = _handle_action(action)
                    await websocket.send(json.dumps({"type": "action_result", **res}))
                    if isinstance(res, dict) and "devices" in res:
                        await websocket.send(json.dumps({"type": "audio_devices_list", "data": res["devices"]}))
                elif msg_type == "chat_command":
                    text = data.get("text", "").strip()
                    if text:
                        _handle_chat_command(text)
                        await websocket.send(json.dumps({"type": "chat_ack", "text": text}))
                elif msg_type == "ui_action":
                    act = data.get("action", "play_youtube")
                    query = str(data.get("query", "")).strip()
                    plat_param = data.get("platform") or ("netmirror" if act == "stream_movie" else "youtube")
                    if act in ("play_youtube", "stream_movie", "play"):
                        def _play_and_announce(q, p):
                            try:
                                from core.win_os_agent import get_desktop_focus_topology, get_app_focus_status
                                from core.media_streaming import play_or_stream_media
                                from core.voice import speak

                                topo = get_desktop_focus_topology()
                                fg = topo.get("foreground", {})
                                b_status = get_app_focus_status("brave")
                                log_info("state_bridge", f"ui_action playback: Foreground is '{fg.get('proc_name')}' ('{fg.get('title')}'), Brave status is '{b_status.get('status')}'")

                                reply = play_or_stream_media(q, p)
                                if reply:
                                    broadcast_state(mood="excited", caption=reply, speaking=True)
                                    speak(reply)
                            except Exception as ex_p:
                                log_warn("state_bridge", f"ui_action play_and_announce error: {ex_p}")
                        threading.Thread(target=_play_and_announce, args=(query, plat_param), daemon=True).start()
                    elif act == "open_app":
                        from core.win_fast_voice import _open_app
                        threading.Thread(target=_open_app, args=(query,), daemon=True).start()
                    elif act == "open_file":
                        from core.live_tools import _execute
                        threading.Thread(target=_execute, args=("open_file", {"filepath": query}), daemon=True).start()
                    elif act == "open_website":
                        from core.win_os_agent import launch_or_focus_browser
                        threading.Thread(target=launch_or_focus_browser, args=(query,), daemon=True).start()
                    elif act == "set_volume":
                        try:
                            from core.win_fast_voice import _set_volume
                            _set_volume(int(query))
                        except Exception:
                            pass
                    elif act == "clean_storage":
                        try:
                            from core.win_os_agent import open_or_search_settings
                            open_or_search_settings("storage")
                        except Exception:
                            pass
                    elif act == "vscode_action":
                        try:
                            from core.vscode_agent import vscode_agent
                            threading.Thread(target=vscode_agent.execute_command, args=(query,), daemon=True).start()
                        except Exception:
                            pass
                    elif act == "chat_command" or query:
                        threading.Thread(target=_handle_chat_command, args=(query,), daemon=True).start()
                    await websocket.send(json.dumps({"type": "ui_action_ack", "action": act, "query": query}))
                elif msg_type in ("ui_show", "ui_dismiss", "broadcast_state"):
                    log_info("state_bridge", f"Re-broadcasting external {msg_type} to overlay clients")
                    await _broadcast_task(json.dumps(data))
                elif msg_type == "ui_dismiss":
                    log_info("state_bridge", "Realtime UI dismissed by user")
                elif msg_type == "ping":
                    await websocket.send(json.dumps({"type": "pong"}))
            except json.JSONDecodeError:
                pass
            except Exception as ex:
                log_warn("state_bridge", f"Message handling error: {ex}")

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        with _clients_lock:
            _connected_clients.discard(websocket)
        # Copilot cleanup: remove from authenticated clients and update connected flag
        _copilot_clients.discard(websocket)
        _extension_connected = len(_copilot_clients) > 0
        log_info("state_bridge", "Lila overlay disconnected")


async def _broadcast_task(message_str: str):
    """Sends message to all connected overlay clients."""
    with _clients_lock:
        clients = list(_connected_clients)

    if not clients:
        return

    coros = [client.send(message_str) for client in clients]
    await asyncio.gather(*coros, return_exceptions=True)


def broadcast_raw_payload(payload: dict):
    """Broadcasts an arbitrary JSON payload to all connected Electron overlay clients."""
    global _loop
    try:
        message_str = json.dumps(payload)
        if _loop and _loop.is_running():
            asyncio.run_coroutine_threadsafe(_broadcast_task(message_str), _loop)
        else:
            # Fallback for external callers or worker threads: send via local websocket connection
            def _send_external():
                try:
                    import websockets.sync.client as ws_sync
                    with ws_sync.connect("ws://127.0.0.1:8765", open_timeout=1.5, close_timeout=1.5) as client:
                        client.send(message_str)
                except Exception:
                    pass
            threading.Thread(target=_send_external, daemon=True).start()
    except Exception as ex:
        log_warn("state_bridge", f"broadcast_raw_payload error: {ex}")


def push_realtime_ui(title: str,
                     subtitle: str = "Tap an option or speak your choice",
                     icon: str = "✨",
                     layout: str = "cards",
                     categories: Optional[list] = None,
                     cards: Optional[list] = None,
                     category_cards: Optional[dict] = None,
                     code_snippet: Optional[str] = None,
                     language: Optional[str] = None,
                     metrics: Optional[list] = None,
                     slider_val: Optional[int] = None,
                     slider_label: Optional[str] = None,
                     decisions: Optional[list] = None,
                     chips: Optional[list] = None,
                     default_action: str = "chat_command",
                     auto_dismiss_sec: int = 20,
                     **extra):
    """
    Pushes a rich interactive Realtime UI card tray to Lila's floating companion HUD.
    Features morphing layouts: 'cards', 'chips', 'code', 'metric', 'slider', 'decision'.
    """
    payload = {
        "type": "ui_show",
        "title": title,
        "subtitle": subtitle,
        "icon": icon,
        "layout": layout,
        "categories": categories or [],
        "cards": cards or [],
        "category_cards": category_cards or {},
        "code_snippet": code_snippet,
        "language": language,
        "metrics": metrics,
        "slider_val": slider_val,
        "slider_label": slider_label,
        "decisions": decisions,
        "chips": chips,
        "default_action": default_action,
        "auto_dismiss_sec": auto_dismiss_sec,
        **extra
    }
    broadcast_raw_payload(payload)


def dismiss_realtime_ui():
    """Dismisses any active interactive card tray on Lila's desktop companion."""
    broadcast_raw_payload({"type": "ui_dismiss"})


def broadcast_state(mood: Optional[str] = None,
                    speaking: Optional[bool] = None,
                    audio_level: Optional[float] = None,
                    caption: Optional[str] = None,
                    conversation_state: Optional[str] = None,
                    gesture: Optional[str] = None,
                    head_tilt: Optional[dict] = None,
                    camera_proximity: Optional[str] = None,
                    posture: Optional[str] = None,
                    breathing_rate: Optional[str] = None,
                    reaction_beat: Optional[str] = None,
                    mic_muted: Optional[bool] = None,
                    audio_profile: Optional[str] = None,
                    desktop_context: Optional[dict] = None,
                    **kwargs):
    """
    Thread-safe function to update companion state and push it to Electron.
    Can be called from anywhere in the Python backend.
    """
    with _state_lock:
        if mood is not None and mood in VALID_MOODS:
            _state["mood"] = mood
        if speaking is not None:
            _state["speaking"] = bool(speaking)
        if audio_level is not None:
            _state["audio_level"] = max(0.0, min(1.0, float(audio_level)))
        if caption is not None:
            _state["caption"] = str(caption)
        if conversation_state is not None:
            _state["conversation_state"] = str(conversation_state)
        if gesture is not None:
            _state["gesture"] = str(gesture)
        if head_tilt is not None:
            _state["head_tilt"] = head_tilt
        if camera_proximity is not None:
            _state["camera_proximity"] = str(camera_proximity)
        if posture is not None:
            _state["posture"] = str(posture)
        if breathing_rate is not None:
            _state["breathing_rate"] = str(breathing_rate)
        if reaction_beat is not None:
            _state["reaction_beat"] = str(reaction_beat)
        if mic_muted is not None:
            _state["mic_muted"] = bool(mic_muted)
        if audio_profile is not None:
            _state["audio_profile"] = str(audio_profile)
        if desktop_context is not None:
            _state["desktop_context"] = desktop_context
        else:
            try:
                from core.desktop_sensor import get_active_desktop_context
                _state["desktop_context"] = get_active_desktop_context()
            except Exception:
                pass
        if kwargs:
            for k, v in kwargs.items():
                if v is not None:
                    _state[k] = v

        current = dict(_state)
        # Transient one-shot actions: reset immediately so subsequent updates don't repeat them
        if gesture is not None:
            _state["gesture"] = None
        if reaction_beat is not None:
            _state["reaction_beat"] = None
        if head_tilt is not None:
            _state["head_tilt"] = None

    payload = {
        "type": "state_update",
        **current
    }
    msg_str = json.dumps(payload)

    if _loop is not None and _loop.is_running():
        _loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(_broadcast_task(msg_str), loop=_loop)
        )

    if _external_state_listeners:
        with _listeners_lock:
            listeners = list(_external_state_listeners)
        for l in listeners:
            try:
                l(dict(current))
            except Exception:
                pass


async def _server_coro():
    global _server
    try:
        _server = await websockets.serve(_ws_handler, BRIDGE_HOST, BRIDGE_PORT)
        log_info("state_bridge", f"Lila state bridge listening on ws://{BRIDGE_HOST}:{BRIDGE_PORT}")
        while not _stop_event.is_set():
            await asyncio.sleep(0.5)
        _server.close()
        await _server.wait_closed()
    except OSError as oe:
        log_warn("state_bridge", f"Port {BRIDGE_PORT} in use or unavailable: {oe}")
    except Exception as e:
        log_error("state_bridge", "server_coro", e)


def _run_server_thread():
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    try:
        _loop.run_until_complete(_server_coro())
    except Exception as e:
        log_error("state_bridge", "server_thread", e)
    finally:
        _loop.close()


def start_bridge():
    """Starts the WebSocket bridge server thread if not already running."""
    global _bridge_thread
    if _bridge_thread is not None and _bridge_thread.is_alive():
        return
    _load_or_create_copilot_token()  # Ensure pairing token is ready before extension connects
    _stop_event.clear()
    _bridge_thread = threading.Thread(target=_run_server_thread, daemon=True, name="LilaStateBridge")
    _bridge_thread.start()


def stop_bridge():
    """Stops the WebSocket bridge server thread."""
    _stop_event.set()
