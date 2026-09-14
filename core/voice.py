"""
core/voice.py — 100% Pure Gemini 2.5 Flash Native Audio Dialog Voice Engine
============================================================================
Exclusively powered by Google Gemini 2.5 Flash Native Audio Dialog (Live API).
- Model: gemini-2.5-flash-native-audio-preview-12-2025 (UNLIMITED Quota)
- Voice: Aoede (Lila's natural warm feminine persona)
- Pure In-Memory 24kHz PCM Playback via PyAudio (zero disk writes).
- Progressive Real-Time Audio Chunk Streaming (plays as chunks arrive).
- Zero External TTS APIs (no EdgeTTS, no SAPI5, no pyttsx3, no REST fallbacks).
- Full Sentence Delivery: Never cuts off or truncates mid-sentence.
- Dynamic Generous Timeout: max(45s, len*0.3) to allow full vocalization of long paragraphs.
"""

import os
import re
import time
import queue
import threading
import logging
import asyncio
from typing import Optional, List

from dotenv import load_dotenv

# Ensure .env is always loaded from project root
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_env_file = os.path.join(_project_root, ".env")
if os.path.exists(_env_file):
    load_dotenv(_env_file, override=True)
load_dotenv()

from core.jarvis_logger import log_error, log_warn, log_info

# ─────────────────────────────────────────────────────────────────────────────
# PyAudio Engine Setup
# ─────────────────────────────────────────────────────────────────────────────
try:
    import pyaudio
    HAS_PYAUDIO = True
    FORMAT = pyaudio.paInt16
except ImportError:
    pyaudio = None
    HAS_PYAUDIO = False
    FORMAT = 2
    log_warn("voice", "PyAudio not installed. Audio output unavailable.")

# ─────────────────────────────────────────────────────────────────────────────
# Google GenAI SDK Setup (100% PURE GEMINI LIVE ONLY)
# ─────────────────────────────────────────────────────────────────────────────
try:
    from google import genai
    from google.genai import types
    HAS_GENAI = True
except ImportError:
    genai = None
    types = None
    HAS_GENAI = False
    log_error("voice", "import", "google-genai SDK not installed.")

# ─────────────────────────────────────────────────────────────────────────────
# Audio Specifications & Gemini 2.5 Flash Native Audio Model
# ─────────────────────────────────────────────────────────────────────────────
OUTPUT_SAMPLE_RATE = 24000  # 24kHz native audio output from Gemini Live
CHANNELS = 1                # Mono
CHUNK_BYTES = 2048          # Playback slice size (~42ms at 24kHz 16-bit mono)

# Pure Gemini Flash Native Audio Dialog (Unlimited Quota on User Account)
LIVE_AUDIO_MODELS = [
    "gemini-2.5-flash-native-audio-preview-12-2025",
    "gemini-2.5-flash-native-audio-latest",
    "gemini-2.5-flash-native-audio-preview-09-2025",
]
LIVE_AUDIO_MODEL = LIVE_AUDIO_MODELS[0]
QUOTA_RETRY_COOLDOWN_SECONDS = 300
_direct_voice_cooldown_until = 0.0


def _is_quota_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in (
        "exceeded your current quota", "resource_exhausted", "quota exceeded",
        "rate limit", "429",
    ))

# Native Gemini Prebuilt Voices
SPEAKERS = {
    "lila": "Aoede",        # 18-year-old girlfriend persona (warm, spirited, feminine)
    "jarvis": "Aoede",
    "astra": "Aoede",
    "friday": "Kore",
    "indian": "Aoede",
    "aoede": "Aoede",
    "kore": "Kore",
    "puck": "Puck",
    "charon": "Charon",
    "fenrir": "Fenrir",
    "male": "Fenrir",
    "female": "Aoede",
}

SYSTEM_INSTRUCTION = types.Content(
    parts=[types.Part.from_text(
        text=(
            "You are an exact audio neural voice narrator for Lila. "
            "The user input contains a script line in quotes for you to vocalize aloud to Rishabh. "
            "Your ONLY task is to read the exact quoted script aloud verbatim word-for-word in the warm, playful Aoede voice. "
            "CRITICAL RULES: "
            "1. DO NOT answer, reply, or comment on the script. "
            "2. DO NOT converse with the user. "
            "3. Speak ONLY the exact words inside the quotes, starting from the first word to the last. "
            "4. NEVER say 'Read this script' or introductory phrases. Start directly with the script words. "
            "5. If the script is about Lila dancing for Rishabh (e.g. 'Dekho meri moves!'), speak that exact line directly. Never assume Rishabh is dancing."
        )
    )]
) if types else None

# ─────────────────────────────────────────────────────────────────────────────
# State Flags & Interruption Controls
# ─────────────────────────────────────────────────────────────────────────────
is_speaking = False
was_interrupted = False
_stop_event = threading.Event()
_stream_lock = threading.Lock()
speak_lock = threading.Lock()

STAGE_EXPRESSION_WORDS = {
    'encouragement', 'encouraging', 'happy', 'happiness', 'excited', 'excitement',
    'playful', 'playfulness', 'teasing', 'affectionate', 'affection', 'loving', 'love',
    'supportive', 'comforting', 'comfort', 'reassurance', 'reassuring', 'greeting',
    'enthusiastic', 'enthusiasm', 'curious', 'curiosity', 'thoughtful', 'pride', 'proud',
    'sympathetic', 'sympathy', 'empathy', 'empathetic', 'cheerful', 'cheerfulness',
    'warm', 'warmth', 'friendly', 'gentle', 'caring', 'sweet', 'surprised', 'surprise',
    'amused', 'amusement', 'giggle', 'giggles', 'laugh', 'laughs', 'smile', 'smiles',
    'chuckle', 'chuckles', 'sigh', 'sighs', 'blush', 'blushes', 'wink', 'winks',
    'gasp', 'gasps', 'grin', 'grins', 'whisper', 'whispers', 'softly', 'pout', 'pouts',
    'nod', 'nods', 'snicker', 'snickers', 'yawn', 'yawns', 'clear', 'throat',
    'cough', 'coughs', 'shrug', 'shrugs', 'relieved', 'concentration', 'concentrating',
    'focus', 'focused', 'thinking'
}

STARTING_EMOTION_PREFIX_PATTERN = re.compile(
    r'^\s*(?:\[|\*|\()? *(?:'
    r'encouragement|encouraging|happy|happiness|excited|excitement|'
    r'playful|playfulness|teasing|affectionate|affection|loving|love|'
    r'supportive|comforting|comfort|reassurance|reassuring|greeting|'
    r'enthusiastic|enthusiasm|curious|curiosity|thoughtful|pride|proud|'
    r'sympathetic|sympathy|empathy|empathetic|cheerful|cheerfulness|'
    r'warm|warmth|friendly|gentle|caring|sweet|surprised|surprise|'
    r'amused|amusement|giggles?|laughs?|smiles?|sighs?|blushes?|'
    r'winks?|chuckles?|gasps?|pouts?|softly|whispering|relieved|'
    r'concentration|concentrating|focus|focused|thinking'
    r') *(?:\]|\*|\))?\s*[:,\-!.]*\s*',
    re.IGNORECASE
)


# ─────────────────────────────────────────────────────────────────────────────
# Text Sanitization (Never truncates sentences, eliminates spoken expressions)
# ─────────────────────────────────────────────────────────────────────────────
def _clean_text(raw_text: str) -> str:
    """Sanitizes text by stripping markdown symbols, code blocks, URLs, emotion labels, and vocalized expressions."""
    if not raw_text:
        return ""
    text = str(raw_text)
    # Strip markdown code blocks & URLs
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    text = re.sub(r'https?://\S+', '', text)
    # Preserve **bold** content
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    # Strip asterisk stage directions (*giggles*, *laughs warmly*, etc.)
    def _asterisk_sub(m):
        inner = m.group(1).strip()
        words = [w.lower() for w in re.findall(r'[a-zA-Z]+', inner)]
        if any(w in STAGE_EXPRESSION_WORDS for w in words):
            return ''
        return inner
    text = re.sub(r'\*([^*]+)\*', _asterisk_sub, text)
    # Strip bracketed stage directions [giggles], [laughs], [encouragement], [happy]
    text = re.sub(r'\[([^\]]+)\]', lambda m: '' if any(w in STAGE_EXPRESSION_WORDS for w in re.findall(r'[a-zA-Z]+', m.group(1).lower())) else m.group(0), text)
    # Strip parenthetical stage directions (giggles), (laughs), (happy)
    text = re.sub(r'\(([^)]+)\)', lambda m: '' if any(w in STAGE_EXPRESSION_WORDS for w in re.findall(r'[a-zA-Z]+', m.group(1).lower())) else m.group(0), text)

    # Strip any starting single/double word labels followed by colon (e.g. "concentration:", "Encouragement:", "Lila:")
    text = re.sub(r'^\s*(?:[A-Za-z\s_-]{2,25}\s*:|\([^\)]+\)|\[[^\]]+\])\s*', '', text).strip()

    # Strip starting emotion labels (e.g. "Encouragement:", "Happy:", "Encouragement,", "[Happy]")
    for _ in range(5):
        m = STARTING_EMOTION_PREFIX_PATTERN.match(text)
        if m:
            text = text[m.end():].strip()
        else:
            break

    # Strip leading or standalone vocal expression words
    text = re.sub(r'^(?:giggles?|laughs?|smiles?|sighs?|chuckles?|winks?|blushes?|pouts?|gasps?)\b\s*[:,-]?\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(?:giggles?|laughs?|smiles?|sighs?|chuckles?|winks?|blushes?|pouts?|gasps?)\b(?=\s*(?:[A-Z]|Arey|Haan|Oye|Dekho|Babe|Rishabh))', '', text, flags=re.IGNORECASE)
    # Strip remaining symbols & clean spaces
    text = re.sub(r'[#*`_~]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Audio Playback Worker (Modern Non-Blocking WASAPI Callback Ring-Buffer Engine)
# ─────────────────────────────────────────────────────────────────────────────
try:
    from core.modern_audio_worker import ModernAudioPlaybackWorker as VoicePlaybackWorker
except ImportError:
    class VoicePlaybackWorker:
        """Fallback worker if modern_audio_worker is unavailable."""
        def __init__(self, p_audio: Optional['pyaudio.PyAudio']):
            self.p_audio = p_audio
            self.stream = None
            self.queue = queue.Queue(maxsize=500)
            self.running = False
            self.interrupted = False
            self.is_playing = False
            self.thread: Optional[threading.Thread] = None

        def start(self):
            if not self.p_audio:
                return
            self.running = True
            self.thread = threading.Thread(target=self._playback_loop, daemon=True, name="LilaVoicePlayback")
            self.thread.start()

        def _playback_loop(self):
            try:
                self.stream = self.p_audio.open(format=FORMAT, channels=CHANNELS, rate=OUTPUT_SAMPLE_RATE, output=True, frames_per_buffer=2400)
            except Exception:
                self.stream = None
            while self.running:
                try:
                    chunk = self.queue.get(timeout=0.2)
                    if self.stream and not self.interrupted:
                        self.stream.write(chunk)
                    self.queue.task_done()
                except queue.Empty:
                    pass

        def enqueue(self, chunk: bytes):
            if not self.interrupted:
                try:
                    self.queue.put_nowait(chunk)
                except Exception:
                    pass

        def wait_until_done(self, timeout: float = 45.0) -> bool:
            start = time.time()
            while not self.interrupted and (time.time() - start) < timeout:
                if self.queue.empty() and not self.is_playing:
                    return True
                time.sleep(0.03)
            return False

        def interrupt(self):
            self.interrupted = True
            while not self.queue.empty():
                try:
                    self.queue.get_nowait()
                except Exception:
                    break
            self.is_playing = False
            self.interrupted = False

        def stop(self):
            self.running = False
            self.interrupt()


def _get_voice_api_key(attempt: int = 0) -> str:
    """Rotates available Gemini API keys on quota exhaustion or connection failures."""
    keys = []
    for k in ["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GOOGLE_API_KEY"]:
        v = os.environ.get(k, "").strip()
        if v and len(v) > 10:
            keys.append(v)
    if not keys:
        load_dotenv(override=True)
        for k in ["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GOOGLE_API_KEY"]:
            v = os.environ.get(k, "").strip()
            if v and len(v) > 10:
                keys.append(v)
    if not keys:
        return ""
    return keys[attempt % len(keys)]


# ─────────────────────────────────────────────────────────────────────────────
# Persistent Gemini Live Voice Engine
# ─────────────────────────────────────────────────────────────────────────────
class PersistentLiveVoiceEngine:
    """
    Maintains a persistent, warm Gemini Live WebSocket connection to
    gemini-2.5-flash-native-audio-preview-12-2025. Streams audio chunks
    to PyAudio via decoupled VoicePlaybackWorker in real time.
    """

    def __init__(self):
        self.client = None
        self.p_audio = None
        self.playback_worker = None
        self.async_queue = None
        self.ready_event = threading.Event()
        self.stop_event = threading.Event()
        self.active_session = None
        self.loop = None
        self.thread = None

        if not HAS_GENAI or not HAS_PYAUDIO:
            return

        self.p_audio = pyaudio.PyAudio()
        self.playback_worker = VoicePlaybackWorker(self.p_audio)
        self.playback_worker.start()

        self.thread = threading.Thread(target=self._run_loop, daemon=True, name="LilaPersistentVoice")
        self.thread.start()
        self.ready_event.wait(timeout=4.0)

    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._supervision_loop())

    async def _supervision_loop(self):
        """Keeps the Gemini 2.5 Live session alive, auto-reconnecting on disconnect."""
        self.async_queue = asyncio.Queue()
        backoff = 1.0
        attempt = 0

        while not self.stop_event.is_set():
            api_key = _get_voice_api_key(attempt)
            attempt += 1

            if not api_key:
                await asyncio.sleep(5.0)
                continue

            try:
                self.client = genai.Client(api_key=api_key)
            except Exception as ce:
                log_warn("voice", f"GenAI client init error: {ce}")
                await asyncio.sleep(3.0)
                continue

            config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                system_instruction=SYSTEM_INSTRUCTION,
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
                    )
                ),
                thinking_config=types.ThinkingConfig(thinking_budget=0)
            )

            connected = False
            for model_cand in LIVE_AUDIO_MODELS:
                if self.stop_event.is_set():
                    break
                try:
                    log_info("voice", f"Connecting persistent Live session to {model_cand}...")
                    async with self.client.aio.live.connect(model=model_cand, config=config) as session:
                        self.active_session = session
                        self.ready_event.set()
                        backoff = 1.0
                        connected = True
                        log_info("voice", f"Gemini Live session connected & warm ({model_cand})!")

                        # Speech request processing loop
                        while not self.stop_event.is_set():
                            text, done_evt = await self.async_queue.get()
                            if text is None:
                                self.async_queue.task_done()
                                break

                            clean_script = _clean_text(text).replace('"', '')
                            prompt_text = f'Read this script verbatim aloud: "{clean_script}"'
                            content = types.Content(
                                role="user",
                                parts=[types.Part.from_text(text=prompt_text)]
                            )
                            await session.send_client_content(turns=[content], turn_complete=True)

                            # Stream audio chunks asynchronously to playback worker
                            async for response in session.receive():
                                if _stop_event.is_set():
                                    break
                                sc = response.server_content
                                if sc and sc.model_turn:
                                    for part in sc.model_turn.parts:
                                        if getattr(part, 'thought', False):
                                            continue
                                        if part.inline_data and part.inline_data.data:
                                            if self.playback_worker and not _stop_event.is_set():
                                                self.playback_worker.enqueue(part.inline_data.data)
                                if sc and getattr(sc, "turn_complete", False):
                                    break

                            # Wait for all buffered audio chunks to finish vocalization
                            if self.playback_worker and not _stop_event.is_set():
                                self.playback_worker.wait_until_done(timeout=max(30.0, len(text) * 0.3))

                            done_evt.set()
                            self.async_queue.task_done()
                        return
                except Exception as session_err:
                    log_warn("voice", f"Live model {model_cand} connection failed: {session_err}")
                    self.ready_event.clear()
                    if _is_quota_error(session_err):
                        log_warn("voice", "Gemini Live quota exhausted; pausing persistent voice reconnects for five minutes.")
                        await asyncio.sleep(QUOTA_RETRY_COOLDOWN_SECONDS)
                        break
                    continue

            if not connected and not self.stop_event.is_set():
                await asyncio.sleep(backoff)
                backoff = min(10.0, backoff * 1.5)

    def speak(self, text: str, timeout: Optional[float] = None) -> bool:
        """Sends text to the persistent live session and streams playback out loud without premature cutoff."""
        if not self.ready_event.is_set() or self.loop is None or self.async_queue is None:
            return False

        if timeout is None:
            # Generous dynamic timeout: never cut off long sentences prematurely
            timeout = max(45.0, len(text) * 0.3)

        done_evt = threading.Event()
        try:
            self.loop.call_soon_threadsafe(self.async_queue.put_nowait, (text, done_evt))
            return done_evt.wait(timeout=timeout)
        except Exception as e:
            log_warn("voice", f"Persistent speak enqueue failed: {e}")
            return False

    def interrupt(self):
        """Immediately halts active audio output."""
        if self.playback_worker:
            self.playback_worker.interrupt()


# Singleton Persistent Engine
_persistent_engine = None
_engine_lock = threading.Lock()

def _get_persistent_engine():
    global _persistent_engine
    # CRITICAL: If LiveVoiceThread is active in this process, NEVER instantiate PersistentLiveVoiceEngine.
    # Google allows only 1 active Live stream per project; a second session kills the microphone stream!
    try:
        from live_voice import get_active_live_voice
        if get_active_live_voice() is not None:
            if _persistent_engine is not None:
                try:
                    _persistent_engine.stop()
                except Exception:
                    pass
                _persistent_engine = None
            return None
    except Exception:
        pass

    if _persistent_engine is None and HAS_GENAI and HAS_PYAUDIO:
        with _engine_lock:
            if _persistent_engine is None:
                _persistent_engine = PersistentLiveVoiceEngine()
    return _persistent_engine

# Note: Initialized lazily on first speak() call to prevent WebSocket collision with LiveVoiceThread


# ─────────────────────────────────────────────────────────────────────────────
# On-Demand Live Streaming (Direct fallback if persistent session is connecting)
# ─────────────────────────────────────────────────────────────────────────────
def _stream_direct_live_audio(text: str, voice_name: str = "Aoede") -> bool:
    """Streams Gemini Live audio directly via VoicePlaybackWorker using 100% pure Gemini 2.5 Flash Native Audio."""
    global _direct_voice_cooldown_until
    if time.monotonic() < _direct_voice_cooldown_until:
        return False

    # Prevent concurrent WebSocket collision if LiveVoiceThread is running
    try:
        from live_voice import get_active_live_voice
        if get_active_live_voice() is not None:
            return False
    except Exception:
        pass

    api_key = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY_2", "").strip()
    if not api_key or not HAS_GENAI or not HAS_PYAUDIO:
        return False

    try:
        client = genai.Client(api_key=api_key)
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=SYSTEM_INSTRUCTION,
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
                )
            ),
            thinking_config=types.ThinkingConfig(thinking_budget=0)
        )

        p = pyaudio.PyAudio()
        worker = VoicePlaybackWorker(p)
        worker.start()

        async def _direct_run():
            global _direct_voice_cooldown_until
            try:
                async with client.aio.live.connect(model=LIVE_AUDIO_MODEL, config=config) as session:
                    clean_script = _clean_text(text).replace('"', '')
                    prompt_text = f'Read this script verbatim aloud: "{clean_script}"'
                    content = types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=prompt_text)]
                    )
                    await session.send_client_content(turns=[content], turn_complete=True)

                    async for response in session.receive():
                        if _stop_event.is_set():
                            break
                        sc = response.server_content
                        if sc and sc.model_turn:
                            for part in sc.model_turn.parts:
                                if getattr(part, 'thought', False):
                                    continue
                                if part.inline_data and part.inline_data.data:
                                    worker.enqueue(part.inline_data.data)
                        if sc and getattr(sc, "turn_complete", False):
                            break

                    worker.wait_until_done(timeout=max(30.0, len(text) * 0.3))
                    return True
            except Exception as me:
                log_warn("voice", f"Direct stream model {LIVE_AUDIO_MODEL} error: {me}")
                if _is_quota_error(me):
                    _direct_voice_cooldown_until = time.monotonic() + QUOTA_RETRY_COOLDOWN_SECONDS
                    log_warn("voice", "Gemini Live quota exhausted; suppressing direct voice retries for five minutes.")
                return False

        try:
            return asyncio.run(_direct_run())
        finally:
            try:
                worker.stop()
                p.terminate()
            except Exception:
                pass
    except Exception as e:
        log_warn("voice", f"Direct live audio exception: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Public Stop Function
# ─────────────────────────────────────────────────────────────────────────────
def stop_speaking():
    """Immediately halt ongoing speech synthesis and audio playback (<15ms)."""
    global is_speaking, was_interrupted
    was_interrupted = True
    is_speaking = False
    _stop_event.set()

    pe = _get_persistent_engine()
    if pe:
        pe.interrupt()

    try:
        import pygame
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
    except Exception:
        pass

    try:
        from core.state_bridge import broadcast_state
        broadcast_state(speaking=False, audio_level=0.0)
    except Exception:
        pass

    # Clear any queued async speech items
    while not _voice_queue.empty():
        try:
            _voice_queue.get_nowait()
            _voice_queue.task_done()
        except Exception:
            break


def set_voice_audio_profile(profile: str) -> str:
    """Switch audio profile for the persistent TTS voice engine."""
    pe = _get_persistent_engine()
    if pe and pe.playback_worker and hasattr(pe.playback_worker, "switch_profile"):
        try:
            return pe.playback_worker.switch_profile(profile)
        except Exception as e:
            log_warn("voice", f"Failed to switch voice engine audio profile: {e}")
    return profile


# ─────────────────────────────────────────────────────────────────────────────
# Asynchronous Voice Queue
# ─────────────────────────────────────────────────────────────────────────────
_voice_queue = queue.Queue()

def _voice_worker():
    while True:
        item = _voice_queue.get()
        if item is None:
            _voice_queue.task_done()
            break
        text, speaker, speed = item
        try:
            sleep_echo = _voice_queue.empty()
            speak(text, speaker, speed, sleep_echo=sleep_echo)
        except Exception as e:
            log_error("voice", "voice_worker", e)
        finally:
            _voice_queue.task_done()

_voice_thread = threading.Thread(target=_voice_worker, daemon=True, name="LILA-GeminiLiveQueue")
_voice_thread.start()

def speak_async(text: str, speaker_name="lila", speed=None):
    """Enqueues text to be spoken asynchronously without blocking."""
    _voice_queue.put((text, speaker_name, speed))


# ─────────────────────────────────────────────────────────────────────────────
# Main Speak Entry Point (100% Pure Gemini Live Native Voice)
# ─────────────────────────────────────────────────────────────────────────────
def speak(text, speaker_name="lila", speed=None, sleep_echo=True) -> bool:
    """
    Synthesizes and speaks text using 100% Pure Gemini 2.5 Flash Native Audio Dialog.
    Features persistent session reuse and instant chunk-by-chunk streaming playback.
    """
    global is_speaking, was_interrupted

    clean_txt = _clean_text(text)
    if not clean_txt:
        return False

    with speak_lock:
        is_speaking = True
        was_interrupted = False
        _stop_event.clear()

        # Broadcast speech start with caption to state_bridge
        try:
            from core.state_bridge import broadcast_state
            broadcast_state(speaking=True, caption=clean_txt)
        except Exception:
            pass

        voice_name = SPEAKERS.get(speaker_name.lower(), "Aoede")
        try:
            print(f"\n[LILA GEMINI 2.5 LIVE ({voice_name})]: {clean_txt[:80]}")
        except Exception:
            pass

        # 0. Primary: If LiveVoiceThread is active in this process, route exclusively through its session
        try:
            from live_voice import get_active_live_voice
            active_live = get_active_live_voice()
            if active_live is not None:
                # Wait briefly if LiveVoiceThread is establishing its connection
                for _ in range(30):
                    if active_live.is_connected:
                        break
                    time.sleep(0.1)

                is_busy = (
                    getattr(active_live, "_is_executing_tool", False) or
                    (active_live.playback_worker and active_live.playback_worker.is_playing)
                )
                if is_busy:
                    # If LiveVoiceThread is already vocalizing speech or actively executing a tool, prevent duplicate overlapping speech
                    log_warn("voice", f"LiveVoiceThread is busy (speaking or executing tool), skipping concurrent speak: {clean_txt[:40]}")
                    is_speaking = False
                    return True


                if active_live.is_connected:
                    sent = active_live.send_text_command(f"Say aloud verbatim: \"{clean_txt}\"")
                    if sent:
                        is_speaking = False
                        return True
                else:
                    log_warn("voice", "LiveVoiceThread connecting; suppressed fallback to preserve single-stream WebSocket.")
                    is_speaking = False
                    return False
        except Exception as e_live:
            log_warn("voice", f"LiveVoiceThread routing error: {e_live}")

        # 1. Secondary: Persistent Warm Live Session (Gemini Live Native Audio)
        engine = _get_persistent_engine()
        success = False
        if engine and engine.ready_event.is_set():
            success = engine.speak(clean_txt)

        # 2. Secondary: Direct Live Stream (If persistent session was reconnecting)
        if not success and not was_interrupted and not _stop_event.is_set():
            success = _stream_direct_live_audio(clean_txt, voice_name=voice_name)

        is_speaking = False
        try:
            from core.state_bridge import broadcast_state
            broadcast_state(speaking=False, audio_level=0.0, caption="")
        except Exception:
            pass

        if sleep_echo and not was_interrupted:
            _stop_event.wait(0.1)

        return bool(success)
