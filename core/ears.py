import sounddevice as sd
import numpy as np
# Lazy whisper loading to prevent torch c10.dll 0xC0000005 crash at startup
HAS_WHISPER = False
whisper = None
import warnings
import time
import os
import re
import soundfile as sf
from groq import Groq
from dotenv import load_dotenv

# Load your secret keys from the .env file
load_dotenv()

warnings.filterwarnings("ignore", message="FP16 is not supported on CPU; using FP32 instead")

# ── Centralized logger (Bug #1 Fix) ──────────────────────────────────────────
from core.jarvis_logger import log_error, log_warn, log_info

# ✅ NEXUS-2: faster_whisper for streaming partial STT and Silero VAD
# Loaded lazily in background — does NOT block startup
_fw_model = None
import threading as _th_mod
_fw_ready_event = _th_mod.Event()

def _load_faster_whisper():
    global _fw_model
    try:
        from faster_whisper import WhisperModel
        import torch
        _device = "cuda" if torch.cuda.is_available() else "cpu"
        _ctype  = "float16" if _device == "cuda" else "int8"
        _fw_model = WhisperModel("base", device=_device, compute_type=_ctype)
        log_info("ears", f"[NEXUS-2] faster_whisper base loaded on {_device} (Silero VAD active)")
    except Exception as _e:
        log_warn("ears", f"[NEXUS-2] faster_whisper not available: {_e}. Streaming STT disabled.")
    finally:
        _fw_ready_event.set()

# Lazy load: _th_mod.Thread(target=_load_faster_whisper, daemon=True).start() (disabled for latency)

def _stream_partial_transcript(audio_chunks: list, ui_callback) -> str:
    """✅ NEXUS-2: Emit partial transcript to UI while user is still speaking.
    Uses faster_whisper with Silero VAD — zero API cost, runs locally on GPU.
    """
    global _fw_model
    if _fw_model is None or not audio_chunks:
        return ""
    try:
        audio = np.concatenate(audio_chunks, axis=0).flatten()
        audio_f32 = audio.astype(np.float32) / 32768.0
        # vad_filter=True uses Silero VAD to skip silence — faster + more accurate
        segments, _ = _fw_model.transcribe(
            audio_f32, beam_size=1, language="en",
            without_timestamps=True, vad_filter=True
        )
        partial = " ".join(seg.text for seg in segments).strip()
        if partial and ui_callback:
            ui_callback("listening", f"SYSTEM_REPLY:<i>[🎙️ {partial}]</i>")
        return partial
    except Exception as _e:
        log_error("ears", "stream_partial_transcript", _e)
        return ""

# ⚡ WINDOWS VOICE TURBO ENGINE — Zero-latency OS command pre-processor ⚡
try:
    from core.win_fast_voice import try_fast_command, is_fast_command, WINDOWS_VOICE_CAPABILITIES
    HAS_WIN_FAST_VOICE = True
    print("[WIN TURBO]: Windows Voice Fast Engine loaded. Basic OS commands will execute instantly.")
except ImportError as _e:
    HAS_WIN_FAST_VOICE = False
    log_warn("ears", f"win_fast_voice not available ({_e}). Using AI pipeline for all commands.")
    def try_fast_command(text): return False, text
    def is_fast_command(text): return False
    WINDOWS_VOICE_CAPABILITIES = ""

print("[Initializing JARVIS Neural Net...]")
import queue
from core.shared_mic import shared_mic

# We load the local turbo model lazily — loaded on first offline fallback OR preloaded in background.
import threading
_model_lock = threading.Lock()
local_model = None

# ── Bug #2 Fix: Preload local model in background thread so cold-start fast path works ──
# This runs silently in the background. By the time the user first speaks (~5-10s after boot),
# the model is already loaded and the Turbo Engine can check BEFORE calling cloud API.
def _preload_local_model():
    # Modern 2026: Gemini Live Native Audio handles voice; do not waste 2GB VRAM preloading Whisper
    return

import threading
# Lazy load: threading.Thread(target=_preload_local_model, daemon=True).start() (disabled for latency)

# Initialize the Cloud connection
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))



# ⚡ THE UPGRADE: Added ui_callback so the Ears can talk to the Face
def listen(ui_callback=None, timeout: float = None, abort_event: threading.Event = None):
    global local_model
    samplerate = 16000
    
    # ⚡ Ultra-Fast 50ms chunks & 0.35s silence cutoff — captures silence & processes immediately
    chunk_duration = 0.05  
    chunk_samples = int(samplerate * chunk_duration)
    
    volume_threshold = 280  
    silence_limit = 0.35     
    
    print("\n[🎙️ JARVIS is Listening...]")
    
    audio_chunks = []
    is_speaking = False
    silent_chunks = 0
    _partial_chunk_counter = 0  # ✅ NEXUS-2: streaming STT counter
    
    audio_queue = queue.Queue()
    def audio_callback(chunk):
        audio_queue.put(chunk)
        
    shared_mic.subscribe(audio_callback)
    
    try:
        start_t = time.time()
        while True:
            try:
                if abort_event and abort_event.is_set():
                    print("[⏰ EARS ABORTED]: abort_event was set.")
                    break
                chunk = audio_queue.get(timeout=0.1)
            except queue.Empty:
                if timeout is not None and not is_speaking and (time.time() - start_t) > timeout:
                    print(f"[⏰ EARS TIMEOUT]: No voice detected for {timeout}s.")
                    break
                continue
            
            volume = np.abs(chunk).mean()
            
            if volume > volume_threshold:
                if not is_speaking:
                    print("[🗣️ Voice Detected. Recording...]")
                    # ⚡ NERVE SIGNAL: Tell UI to turn Blue/Pink (Listening)
                    if ui_callback: 
                        ui_callback("listening", "LISTENING...")
                        
                is_speaking = True
                silent_chunks = 0
                audio_chunks.append(chunk)

                # ✅ NEXUS-2: Emit partial transcript every 5 chunks (~500ms)
                _partial_chunk_counter += 1
                if _partial_chunk_counter % 5 == 0 and _fw_model is not None:
                    _th_mod.Thread(
                        target=_stream_partial_transcript,
                        args=(list(audio_chunks), ui_callback),
                        daemon=True
                    ).start()

            elif is_speaking:
                silent_chunks += 1
                audio_chunks.append(chunk)
                # Calculates exactly when 0.5 seconds of pure silence is reached
                if silent_chunks * chunk_duration >= silence_limit:
                    print("[🛑 Silence Detected. Processing...]")
                    # ⚡ NERVE SIGNAL: Tell UI to turn Orange/Red (Thinking)
                    if ui_callback: 
                        ui_callback("thinking", "PROCESSING DATA...")
                    break
    finally:
        shared_mic.unsubscribe(audio_callback)
    
    if not audio_chunks:
        return ""

    audio_data = np.concatenate(audio_chunks, axis=0).flatten()
    
    # ---------------------------------------------------------
    # ⚡ LAYER 1: WINDOWS VOICE TURBO (Zero-Latency Fast Path)
    # ---------------------------------------------------------
    # BUG #2 FIX: We no longer require local_model to be pre-loaded here.
    # The background thread (_preload_local_model) loads it silently at startup.
    # If it's ready → instant fast-path check before any cloud call.
    # If not ready yet → fall through to cloud, fast-path checked AFTER cloud text.
    # Either way, every command eventually goes through the fast-path check.
    # ---------------------------------------------------------
    if HAS_WIN_FAST_VOICE and local_model is not None:
        # ⚡ Phase 1 Fix: Only run local Whisper pre-check for SHORT utterances (< 6 words).
        # Complex queries will never match a fast command, so skip the 200-400ms local inference.
        _word_count_estimate = len(audio_data) / (16000 * 0.5)  # rough estimate: 0.5s ~ 1 word
        _likely_short = _word_count_estimate < 6

        if _likely_short:
            # Model is ready — do a quick pre-cloud transcription for instant fast commands
            _raw_text_for_check = None
            try:
                _af32 = audio_data.astype(np.float32) / 32768.0
                _quick_result = local_model.transcribe(_af32, fp16=False, language="en")
                _raw_text_for_check = _quick_result["text"].strip()
            except Exception as e:
                log_error("ears", "layer1_pre_cloud_transcription", e)

            if _raw_text_for_check:
                _handled, _fast_result = try_fast_command(_raw_text_for_check)
                if _handled:
                    print(f"[⚡ WIN TURBO EXECUTED]: '{_raw_text_for_check}' → {_fast_result}")
                    if ui_callback:
                        ui_callback("speaking", "LISTENING...")
                    return f"__FAST_COMMAND__:{_raw_text_for_check}"

    # ---------------------------------------------------------
    # 🚀 LAYER 2: HYBRID AI BRAIN (Cloud → Local fallback)
    # ---------------------------------------------------------
    print("[🌐 Trying Cloud Brain (Groq Large-v3)...]")
    try:
        import uuid
        # The Cloud needs a tiny temporary file to read
        temp_file = f"temp_cloud_audio_{uuid.uuid4().hex}.wav"
        sf.write(temp_file, audio_data, samplerate)
        
        with open(temp_file, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(temp_file, file.read()),
                model="whisper-large-v3",
                # ⚡ PROFANITY FIX: Explicit prompt to transcribe ALL words exactly as
                # spoken, including slang, informal language, and any word without 
                # censorship. temperature=0 forces literal, accurate transcription.
                prompt="Transcribe all words exactly as spoken. Do not censor, filter, or replace any words with symbols. I am speaking Hindi, Marathi, and English. Namaste JARVIS. Chal bhai, open VS Code. Kasa ahes?",
                language="en",
                temperature=0,  # ⚡ PROFANITY FIX: temperature=0 = most literal transcription
            )

        # Bug #1 Fix: Cleanup temp file safely with error logging
        try:
            os.remove(temp_file)
        except Exception as e:
            log_error("ears", "remove_temp_audio_file", e)
        
        text = transcription.text.strip()
        
        # ⚡ PROFANITY FILTER RECOVERY: If Groq still censored a word (shows as *** or ****),
        # re-run the audio through local Whisper which has no content filter.
        if re.search(r'\*{2,}', text):
            log_warn("ears", f"Groq censored word in: '{text}'. Checking if local recovery available...")
            if HAS_WHISPER and whisper is not None:
                if local_model is None:
                    with _model_lock:
                        if local_model is None:
                            try:
                                local_model = whisper.load_model("turbo")
                            except Exception as e:
                                log_error("ears", "profanity_model_load", e)
                if local_model is not None:
                    audio_float32 = audio_data.astype(np.float32) / 32768.0
                    try:
                        recovery_result = local_model.transcribe(
                            audio_float32, fp16=False,
                            initial_prompt="Transcribe exactly what is spoken. Do not filter or censor any words."
                        )
                        recovered_text = recovery_result["text"].strip()
                        print(f"[✅ RECOVERY]: '{text}' → '{recovered_text}'")
                        text = recovered_text
                    except Exception as e:
                        log_error("ears", "profanity_recovery_transcription", e)
        
        print(f"[☁️ JARVIS (Cloud) Heard]: {text}")
        
        # ── Emotional Intelligence: analyse voice tone ──
        try:
            from core.emotional_intelligence import record_audio_emotion
            record_audio_emotion(audio_data, samplerate)
        except ImportError:
            pass  # Module not present — expected, not an error
        except Exception as e:
            log_error("ears", "emotional_intelligence_record", e)
        
        # ⚡ POST-CLOUD FAST-PATH CHECK: Works even on cold start when model wasn't ready above
        if HAS_WIN_FAST_VOICE and text:
            _handled, _fast_result = try_fast_command(text)
            if _handled:
                print(f"[⚡ WIN TURBO (post-cloud)]: '{text}' → {_fast_result}")
                return f"__FAST_COMMAND__:{text}"
        
        return text

    except Exception as e:
        # Bug #1 Fix: Log the actual cloud error so we know WHY it failed
        log_error("ears", "groq_cloud_transcription", e,
                  "Switching to local Whisper fallback.")
        print("[⚠️ No Wi-Fi or Cloud Error. Switching to Local Backup!]")
        
        if _fw_model is None:
            print("[⚙️ Waiting for faster_whisper local model...]")
            _fw_ready_event.wait()
            
        if _fw_model is None:
            log_error("ears", "faster_whisper_fallback", "Model failed to load.")
            return ""
            
        audio_float32 = audio_data.astype(np.float32) / 32768.0
        
        try:
            segments, _ = _fw_model.transcribe(
                audio_float32, 
                language="en",
                initial_prompt="Transcribe exactly what is spoken. Do not censor or filter any words. Namaste JARVIS. Chal bhai, listen to my Hindi and English commands."
            )
            text = " ".join(seg.text for seg in segments).strip()
        except Exception as transcribe_e:
            log_error("ears", "local_faster_whisper_transcription", transcribe_e)
            return ""
        
        print(f"[💻 JARVIS (Local) Heard]: {text}")
        
        # ⚡ POST-LOCAL FAST-PATH CHECK
        if HAS_WIN_FAST_VOICE and text:
            _handled, _fast_result = try_fast_command(text)
            if _handled:
                print(f"[⚡ WIN TURBO (post-local)]: '{text}' → {_fast_result}")
                return f"__FAST_COMMAND__:{text}"
        
        return text

def wait_for_wake_word():
    """
    Listens silently for a wake word using the local Whisper model.
    Returns (True, transcribed_text) so the first spoken phrase can be
    processed as a command directly — e.g. 'hi' triggers AND is answered.
    """
    samplerate = 16000
    chunk_duration = 0.2
    volume_threshold = 300   # Mean volume to start recording

    print("\n[JARVIS STANDBY]: Say 'Jarvis', 'Hi', 'Hello', or 'Hey' to wake me.")

    while True:
        audio_chunks = []
        is_speaking_local = False
        silent_chunks = 0

        audio_queue = queue.Queue()

        def _wake_audio_callback(chunk):
            audio_queue.put(chunk)

        shared_mic.subscribe(_wake_audio_callback)

        try:
            while True:
                chunk = audio_queue.get()
                volume = np.abs(chunk).mean()

                if volume > volume_threshold:
                    is_speaking_local = True
                    silent_chunks = 0
                    audio_chunks.append(chunk)
                elif is_speaking_local:
                    silent_chunks += 1
                    audio_chunks.append(chunk)
                    # 0.4s silence = end of utterance
                    if silent_chunks * chunk_duration >= 0.4:
                        break
        finally:
            shared_mic.unsubscribe(_wake_audio_callback)

        if not audio_chunks:
            continue

        # Transcribe: cloud-first zero-VRAM transcription with graceful local fallback
        audio_data = np.concatenate(audio_chunks, axis=0).flatten()
        text = ""

        # 1. Cloud-first transcription (Groq, 0 MB VRAM)
        if client is not None:
            try:
                import uuid
                temp_file = f"temp_wake_{uuid.uuid4().hex}.wav"
                sf.write(temp_file, audio_data, samplerate)
                try:
                    with open(temp_file, "rb") as f_wake:
                        tr = client.audio.transcriptions.create(
                            file=(temp_file, f_wake.read()),
                            model="whisper-large-v3",
                            language="en",
                            temperature=0,
                        )
                    text = tr.text.lower().strip()
                finally:
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass
            except Exception as ce:
                log_warn("ears", f"Cloud wake word transcription failed: {ce}")

        # 2. Local Whisper fallback only if cloud is unreachable
        if not text and HAS_WHISPER and whisper is not None:
            global local_model
            if local_model is None:
                with _model_lock:
                    if local_model is None:
                        try:
                            local_model = whisper.load_model("turbo")
                        except Exception as e:
                            log_error("ears", "wake_word_model_load", e)
            if local_model is not None:
                audio_float32 = audio_data.astype(np.float32) / 32768.0
                try:
                    result = local_model.transcribe(audio_float32, fp16=False)
                    text = result["text"].lower().strip()
                except Exception as e:
                    log_error("ears", "wake_word_transcription", e)

        if text:
            try:
                print(f"  [MIC HEARD]: '{text}'")
            except Exception as e:
                from core.jarvis_logger import log_warn
                log_warn('ears', f'silent swallow: {e}')
                log_warn('ears', f'console print failed: {e}')

        # Wake words — any of these will activate Astra / JARVIS
        wake_words = [
            "astra", "hey astra", "ok astra", "hi astra", "hello astra", "suno astra", "yo astra",
            "jarvis", "j.a.r.v.i.s", "garvis", "jarvi", "jarvas",
            "hello jarvis", "hey jarvis", "ok jarvis", "yo jarvis", "hi jarvis",
            "jarvis.", "jarvis!", "jarvis?",
            "hello", "hey", "hi",
            "boss", "wake up", "are you there",
        ]

        if text and any(word in text for word in wake_words):
            # ⚡ Return the full transcribed text so it can be used as the first command
            # e.g. if user says "hi how are you" — that full phrase gets answered
            return True, text