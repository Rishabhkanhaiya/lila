import json
import threading
import traceback
import re  
import os
import random
from typing import TypedDict, Any
import concurrent.futures
from core.jarvis_logger import log_error, log_warn, log_info

# ✅ POLISH P3: Response quality guard — cleans all LLM output before voice/UI
try:
    from core.response_guard import clean_response as _clean_resp, clean_for_voice as _clean_voice
    _HAS_GUARD = True
except Exception as e:
    log_warn("master_router", f"response guard import failed: {e}")
    _HAS_GUARD = False
    def _clean_resp(t): return t
    def _clean_voice(t, **kw): return t

try:
    from core.rich_formatter import maybe_richify as _richify
    _HAS_RICH = True
except Exception as _e:
    log_warn("master_router", f"rich_formatter import failed: {_e}")
    _HAS_RICH = False
    def _richify(t, q=""): return t

# ✅ JARVIS 2.0: Smart sentence-boundary trimmer for TTS
_MAX_TTS_SENTENCES = 2

def _trim_for_voice(text: str, max_sentences: int = _MAX_TTS_SENTENCES) -> str:
    """Trim reply to max N sentences for TTS. Full text stays in chat UI."""
    if not text:
        return text
    import re as _re2
    sentences = _re2.split(r'(?<=[.!?])\s+', text.strip())
    if len(sentences) <= max_sentences:
        return text
    trimmed = ' '.join(sentences[:max_sentences])
    return trimmed + " — full details in chat."

# ── Pending clarification state (JARVIS 2.0: stack-based, supports multiple pending) ────
_pending_stack: list = []
_pending_lock = threading.Lock()

def _set_pending(prompt: str, signal, metadata: dict = None) -> None:
    with _pending_lock:
        _pending_stack.append({"prompt": prompt, "signal": signal, "metadata": metadata or {}})

def _pop_pending() -> dict:
    """Return and clear the most recent pending task. Returns {} if none."""
    with _pending_lock:
        return _pending_stack.pop() if _pending_stack else {}

def _has_pending() -> bool:
    with _pending_lock:
        return bool(_pending_stack)


# ⚡ NEW PHASE 2 INTEGRATIONS ⚡
from core.database import save_node, save_skill, get_skill, get_all_skills

# ⚡ NEW PHASE 3 INTEGRATIONS ⚡
try:
    from core.mqtt_client import telemetry_client
    HAS_MQTT = True
except ImportError:
    HAS_MQTT = False

# ✅ QM-2: Background Task Queue — voice/UI add-to-queue routing
try:
    from core.queue_db import add_task as _queue_add, get_pending_count as _queue_count
    HAS_QUEUE = True
except ImportError:
    HAS_QUEUE = False
    def _queue_add(t, priority="MEDIUM", source="VOICE"): return -1
    def _queue_count(): return 0

# Queue intent patterns — match "add to queue", "do it later", "queue this" etc.
_QUEUE_PHRASES = [
    r"(?i)(add|put|queue)\s+.+\s+(to|in(to)?)\s+(the\s+)?(queue|background)",
    r"(?i)do\s+(it|this|that)\s+later",
    r"(?i)not\s+now\s*[,.]?\s*(just\s+)?(add|put|save)\s+(it|this)\s+(to|in)\s+(the\s+)?queue",
    r"(?i)(remember|save)\s+to\s+do\s+.+\s+later",
    r"(?i)queue\s+(it|this|that)\s+up",
    r"(?i)add\s+this\s+to\s+(my\s+)?background\s+(tasks?|queue)",
]
_QUEUE_RE = [re.compile(p) for p in _QUEUE_PHRASES]

_PRIORITY_WORDS = {"urgent": "HIGH", "high": "HIGH", "important": "HIGH",
                   "medium": "MEDIUM", "normal": "MEDIUM",
                   "low": "LOW", "whenever": "LOW", "whenever you can": "LOW"}

def _detect_queue_intent(text: str):
    """
    Returns (is_queue, task_text, priority) if user wants to add task to queue.
    Strips the queue-command wrapper and extracts the real task.
    """
    for pattern in _QUEUE_RE:
        if pattern.search(text):
            # Determine priority from keywords
            priority = "MEDIUM"
            lower = text.lower()
            for kw, prio in _PRIORITY_WORDS.items():
                if kw in lower:
                    priority = prio
                    break
            # Strip queue-command words to get the actual task
            strip_pat = (
                r"(add|put|queue|save|remember)\s+to\s+do\s+|"
                r"(add|put)\s+|"
                r"(to|in(to)?)\s+(the\s+)?(queue|background|later)|"
                r"(not\s+now|do\s+it\s+later|queue\s+(it|this|that)\s+up|"
                r"add\s+this\s+to\s+(my\s+)?background\s+tasks?)"
            )
            task_text = re.sub(strip_pat, "", text, flags=re.IGNORECASE).strip(" ,.")
            if not task_text:
                task_text = text  # fallback: use full text
            return True, task_text, priority
    return False, "", "MEDIUM"
try:
    from langgraph.graph import StateGraph, START, END
    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False

# Import your swarm agents
from core.brain import call_groq_brain, update_chat_history 
def generate_local_3d_model(*args, **kwargs):
    from core.local_3d_engine import generate_local_3d_model as _gen_3d
    return _gen_3d(*args, **kwargs)
try:
    from core.gemini_web import generate_tailwind_web 
except ImportError:
    generate_tailwind_web = None 
from core.hands import execute_agentic_loop

# ⚡ WINDOWS VOICE TURBO ENGINE IMPORT ⚡
try:
    from core.win_fast_voice import try_fast_command as _wfv_execute, is_fast_command as _wfv_check
    HAS_WIN_FAST_VOICE = True
except ImportError:
    HAS_WIN_FAST_VOICE = False
    def _wfv_execute(text): return False, ""
    def _wfv_check(text): return False

# ==========================================
# 1. LANGGRAPH STATE DEFINITION
# ==========================================
class AgentState(TypedDict):
    prompt: str
    ui_signal: Any
    intent_data: dict
    target_system: str
    execution_result: str
    final_output: Any

def create_filename_from_prompt(prompt):
    clean_text = re.sub(r'^(jarvis|build|make|create|generate)\s+', '', prompt, flags=re.IGNORECASE)
    clean_text = re.sub(r'[^\w\s-]', '', clean_text).strip().lower()
    slug = re.sub(r'\s+', '_', clean_text)
    return f"{slug[:40]}.html" if slug else "web_forge_output.html"

# ==========================================
# 2. THE HIGH-AVAILABILITY BRAINSTEM
# ==========================================
# ==========================================
# 2. THE HIGH-AVAILABILITY SEMANTIC ROUTER
# ==========================================
# Global semantic router cache
semantic_model = None
intent_embeddings = None
INTENT_LABELS = []

# Removed INTENT_EXAMPLES - now loaded dynamically from core/capabilities.json

# Fix #6: Semantic model state
_semantic_ready = threading.Event()   # set when model is fully loaded

def init_semantic_router():
    """Load the SentenceTransformer model and build dynamic capabilities graph. Called from background thread at startup."""
    global semantic_model, intent_embeddings, INTENT_LABELS
    try:
        from sentence_transformers import SentenceTransformer
        if semantic_model is None:
            log_info("semantic_router", "Loading all-MiniLM-L6-v2 in background...")
            try:
                semantic_model = SentenceTransformer('all-MiniLM-L6-v2')
            except Exception as e:
                log_warn("semantic_router", f"Model load failed: {e}. Falling back to CASUAL_CHAT.")
                semantic_model = None
                _semantic_ready.set()   # unblock waiters even on failure
                return False

            sentences, labels = [], []
            
            # Load static capabilities from JSON
            try:
                cap_path = os.path.join(os.path.dirname(__file__), "capabilities.json")
                with open(cap_path, "r", encoding="utf-8") as f:
                    intent_examples = json.load(f)
                    
                for intent, examples in intent_examples.items():
                    for ex in examples:
                        sentences.append(ex)
                        labels.append(intent)
            except Exception as e:
                log_warn("semantic_router", f"Failed to load capabilities.json: {e}")

            # Load dynamic skills from Database
            try:
                from core.database import get_all_skills
                known_skills = get_all_skills()
                for skill_name in known_skills:
                    sentences.append(f"run {skill_name}")
                    sentences.append(f"execute {skill_name}")
                    sentences.append(skill_name)
                    # We map custom skills to a dynamic intent label
                    labels.append(f"RUN_SKILL|{skill_name}")
            except Exception as e:
                log_warn("semantic_router", f"Failed to load dynamic skills: {e}")

            if not sentences:
                log_warn("semantic_router", "No intents found to embed.")
                _semantic_ready.set()
                return False

            intent_embeddings = semantic_model.encode(sentences, convert_to_tensor=True)
            INTENT_LABELS = labels
            log_info("semantic_router", "Intents mapped and active.")
            _semantic_ready.set()
            return True
    except (ImportError, OSError, Exception) as _sem_e:
        # Catches: ImportError (not installed), OSError WinError 4551 (AppLocker
        # blocks torch.dll which sentence_transformers imports transitively), or
        # any other unexpected failure. In all cases, fall back to keyword router.
        log_warn("semantic_router", f"sentence-transformers unavailable ({type(_sem_e).__name__}): {_sem_e}. Keyword router active.")
        _semantic_ready.set()
        return False

# Fix #6: Semantic router preload disabled on Windows to prevent torch c10.dll 0xC0000005 crash.
# Intent analysis uses instant regex/keyword and LLM router.
_semantic_ready.set()
# _preload_thread = threading.Thread(target=init_semantic_router, daemon=True, name="SemRouter-Preload")
# _preload_thread.start()

def analyze_intent_fast(user_prompt):
    # ⚡ Phase 5: JUST_PRESENT intent — bare name-call with no task
    # Must be checked BEFORE anything else, including WIN_FAST_COMMAND
    try:
        from core.just_present import is_just_present
        if is_just_present(user_prompt):
            _log_route(user_prompt, "JUST_PRESENT", 1.0, "presence_trigger")
            return {"subsystem": "JUST_PRESENT", "extracted_prompt": user_prompt}
    except Exception as _jp_e:
        log_warn("master_router", f"just_present check failed: {_jp_e}")

    # ⚡⚡ LAYER 0: WINDOWS VOICE TURBO PRE-FILTER (FASTEST PATH POSSIBLE) ⚡⚡
    if HAS_WIN_FAST_VOICE:
        if user_prompt.startswith("__FAST_COMMAND__:"):
            original_cmd = user_prompt[len("__FAST_COMMAND__:"):]
            return {"subsystem": "WIN_FAST_COMMAND", "extracted_prompt": original_cmd, "already_executed": True}
        # Check raw prompt first
        if _wfv_check(user_prompt):
            _log_route(user_prompt, "WIN_FAST_COMMAND", 1.0, "wfv")
            return {"subsystem": "WIN_FAST_COMMAND", "extracted_prompt": user_prompt, "already_executed": False}
        # ✅ TYPO SAFETY NET: also check with normalized text (fixes 'switchh', 'openn', etc.)
        try:
            from core.win_fast_voice import _normalize as _wfv_norm
            normalized_prompt = _wfv_norm(user_prompt)
            if normalized_prompt != user_prompt.lower().strip() and _wfv_check(normalized_prompt):
                _log_route(user_prompt, "WIN_FAST_COMMAND", 0.95, "wfv_normalized")
                return {"subsystem": "WIN_FAST_COMMAND", "extracted_prompt": normalized_prompt, "already_executed": False}
        except Exception as e:
            log_warn("master_router", f"win_fast_voice normalization failed: {e}")

    # 🔬 FAST RESEARCH PRE-FILTER: direct zero-latency routing to DEEP_RESEARCH for investigative queries
    _lower = user_prompt.lower().strip()
    _clean_check = re.sub(r'^(jarvis|hey jarvis|please|can you|could you)\s+', '', _lower).strip()
    if any(_clean_check.startswith(p) for p in ["research ", "deep research ", "deep dive into ", "investigate ", "astra research ", "find out about ", "summarize research on "]):
        _log_route(user_prompt, "DEEP_RESEARCH", 0.99, "fast_research_keyword")
        return {"subsystem": "DEEP_RESEARCH", "extracted_prompt": user_prompt}

    # Init if needed (wait max 1.5s for background thread — don't stall the UI)
    if semantic_model is None:
        _semantic_ready.wait(timeout=1.5)
        
    if semantic_model is not None:
        try:
            from sentence_transformers import util
            clean_prompt = user_prompt.lower().replace("jarvis", "").replace("hey ", "").replace("please ", "").strip()
            if not clean_prompt: clean_prompt = user_prompt
            
            # ✅ JARVIS 2.0 CONVERSATIONAL FIX: Short phrase bypass
            # Phrases <= 3 words (like "what twice", "why", "do it") lack semantic meaning in a vacuum.
            # We bypass vector search and send them to the context-aware LLM fallback.
            if len(clean_prompt.split()) <= 3:
                try:
                    _llm_intent = _llm_classify_intent(user_prompt)
                    if _llm_intent:
                        _log_route(user_prompt, _llm_intent, 0.99, "llm_short_phrase")
                        return {"subsystem": _llm_intent, "extracted_prompt": user_prompt}
                except Exception as e:
                    log_warn("master_router", f"llm short phrase bypass failed: {e}")

            if intent_embeddings is None:
                return None

            query_embedding = semantic_model.encode(clean_prompt, convert_to_tensor=True)
            hits = util.semantic_search(query_embedding, intent_embeddings, top_k=1)[0]
            best_hit = hits[0]
            best_intent = INTENT_LABELS[best_hit['corpus_id']]
            confidence = best_hit['score']
            
            if confidence > 0.35:
                # ✅ FINAL SAFETY NET: if semantic says SYSTEM_ACTION or CASUAL_CHAT,
                # do one last WFV check to prevent agentic overkill on simple commands
                if best_intent in ("SYSTEM_ACTION", "CASUAL_CHAT") and HAS_WIN_FAST_VOICE:
                    try:
                        from core.win_fast_voice import _normalize as _wfv_norm2
                        if _wfv_check(_wfv_norm2(user_prompt)):
                            _log_route(user_prompt, "WIN_FAST_COMMAND", confidence, "wfv_post_semantic")
                            return {"subsystem": "WIN_FAST_COMMAND", "extracted_prompt": _wfv_norm2(user_prompt), "already_executed": False}
                    except Exception as e:
                        log_warn("master_router", f"wfv post semantic check failed: {e}")

                # ✅ JARVIS 2.0 Phase 3A: LLM fallback for low-confidence ambiguous commands
                if 0.35 < confidence < 0.52:
                    try:
                        _llm_intent = _llm_classify_intent(user_prompt)
                        if _llm_intent and _llm_intent != "CASUAL_CHAT":
                            _log_route(user_prompt, _llm_intent, confidence, "llm_fallback")
                            return {"subsystem": _llm_intent, "extracted_prompt": user_prompt}
                    except Exception as e:
                        log_warn("master_router", f"llm classify intent fallback failed: {e}")

                if best_intent.startswith("RUN_SKILL|"):
                    skill_name = best_intent.split("|")[1]
                    _log_route(user_prompt, "RUN_SKILL", confidence, "semantic")
                    return {"subsystem": "RUN_SKILL", "extracted_prompt": user_prompt, "skill_name": skill_name}
                _log_route(user_prompt, best_intent, confidence, "semantic")
                return {"subsystem": best_intent, "extracted_prompt": user_prompt}
        except Exception as e:
            log_error("semantic_router", "analyze_intent_fast", e)
            
    # Default fallback if semantic routing fails completely or confidence is too low
    _log_route(user_prompt, "CASUAL_CHAT", 0.0, "default")
    return {"subsystem": "CASUAL_CHAT", "extracted_prompt": user_prompt}


def _llm_classify_intent(prompt: str) -> str:
    """✅ JARVIS 2.0: LLM fallback classifier for ambiguous low-confidence routing with CONTEXTUAL MEMORY."""
    try:
        from core.brain import chat_history
        recent_history = chat_history[-6:] if chat_history else []
        history_str = "\n".join([f"{msg.get('role', 'unknown').upper()}: {msg.get('content', '')}" for msg in recent_history if msg.get("role") != "system"])
        
        _labels = [
            "WIN_FAST_COMMAND","CASUAL_CHAT","SYSTEM_ACTION","WEB_AGENT","WEB_FORGE",
            "3D_FORGE","LIVE_FINANCE","DEEP_RESEARCH","DATA_ANALYTICS","MOBILE_ACTION",
            "AUTO_CODER","CREATE_SKILL","COMPUTER_USE",
            "WORMHOLE","CODEBASE_ORACLE","DOC_FORGE","WIDGET_FORGE","RUN_MACRO",
            "GEMINI_LIVE"
        ]
        
        system = (
            f"You are JARVIS's intent router. Classify the user's LATEST request into EXACTLY ONE category:\n"
            f"{', '.join(_labels)}\n\n"
            f"Here is the recent conversation context:\n"
            f"--- START CONTEXT ---\n{history_str}\n--- END CONTEXT ---\n\n"
            f"CRITICAL RULE: If the LATEST request is a conversational follow-up, answer to a question, or a short phrase referring to the assistant's previous message, you MUST route it to CASUAL_CHAT.\n"
            f"Reply with ONLY the category name. Nothing else."
        )
        
        # We use system_override which maps to the system prompt in call_groq_brain
        result = call_groq_brain(prompt, phase="DIRECTIVE", is_logic_task=True, system_override=system)
        
        if isinstance(result, dict):
            result = result.get("reply", "")
        result = str(result).strip().upper().replace(" ", "_").split()[0]
        if result in _labels:
            return result
    except Exception as e:
        log_warn("master_router", f"llm classify intent failed: {e}")
    return "CASUAL_CHAT"


# ==========================================
# 3. LANGGRAPH NODES (The Workflow)
# ==========================================
def node_analyze(state: AgentState) -> AgentState:
    prompt = state["prompt"]
    target = state.get("target_system")
    if target:
        state["intent_data"] = {"subsystem": target, "extracted_prompt": prompt}
        return state

    # ✅ QM-2: Check for queue intent BEFORE semantic routing
    # e.g. "research quantum computing but add it to queue" → don't do it now
    _is_queue, _task_text, _priority = _detect_queue_intent(prompt)
    if _is_queue and HAS_QUEUE:
        _task_id = _queue_add(_task_text, priority=_priority, source="VOICE")
        _count = _queue_count()
        _confirm = (
            f"Done. '{_task_text[:50]}' has been added to your background queue "
            f"with {_priority} priority. You now have {_count} task{'s' if _count != 1 else ''} queued."
        )
        state["intent_data"] = {"subsystem": "QUEUE_ADD", "extracted_prompt": prompt}
        state["target_system"] = "QUEUE_ADD"
        state["execution_result"] = _confirm
        return state

    intent = analyze_intent_fast(prompt)
    state["intent_data"] = intent
    
    target = intent.get("subsystem", "CASUAL_CHAT")
    if target == "CASUAL_CHAT":
        from core.feature_flags import is_enabled as _feat
        if not _feat("casual_chat"):
            target = "SYSTEM_ACTION"
            
    state["target_system"] = target
    return state

def node_execute(state: AgentState) -> AgentState:
    target = state["target_system"]
    prompt = state["prompt"]
    signal = state["ui_signal"]
    update_chat_history("user", prompt)
    
    try:
        from core.narrator import intent_narrate
        intent_narrate(target)
    except Exception as e:
        log_error("node_execute", "intent_narrate", e)
        
    try:
        # ✅ QM-2: Handle queue-add confirmation (task was already stored in node_analyze)
        if target == "QUEUE_ADD":
            _confirm = state.get("execution_result", "Task added to queue.")
            signal.emit("speaking", f"SYSTEM_REPLY:{_confirm}")
            from core.voice import speak
            speak(_confirm)
            update_chat_history("assistant", _confirm)
            state["final_output"] = {"type": "queue_add", "message": _confirm}
            return state

        # ⚡ Phase 5: JUST_PRESENT — bare name-call, respond like a friend
        if target == "JUST_PRESENT":
            try:
                from core.just_present import generate_presence_response
                presence_reply = generate_presence_response()
                signal.emit("speaking", f"SYSTEM_REPLY:{presence_reply}")
                from core.voice import speak_async as _vsp
                _vsp(presence_reply)
                update_chat_history("assistant", presence_reply)
                state["execution_result"] = presence_reply
                state["final_output"] = {"type": "just_present", "reply": presence_reply}
                return state
            except Exception as _jp_err:
                log_error("node_execute", "JUST_PRESENT", _jp_err)
                # Fall through to CASUAL_CHAT if presence fails

        if target == "WIN_FAST_COMMAND":
            already_done = state["intent_data"].get("already_executed", False)
            if already_done:
                signal.emit("speaking", "SYSTEM_REPLY:CMD:SHRINK_TO_ORB")
                signal.emit("speaking", f"SYSTEM_REPLY:⚡ {prompt}")
            else:
                _handled, _result = _wfv_execute(prompt)
                if _handled:
                    # Check if result is a special CMD: signal (e.g. UI mode switch)
                    if _result.startswith("CMD:"):
                        signal.emit("speaking", f"SYSTEM_REPLY:{_result}")
                        from core.voice import speak
                        if _result == "CMD:SHRINK_TO_ORB":
                            speak("Switching to mini mode.")
                        elif _result == "CMD:SHOW_FULL":
                            speak("Back to full view.")
                    else:
                        signal.emit("speaking", "SYSTEM_REPLY:CMD:SHRINK_TO_ORB")
                        signal.emit("speaking", f"SYSTEM_REPLY:⚡ {_result}")
                        from core.voice import speak; speak(_result) if len(_result) < 50 else speak("Done.")
                else:
                    executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
                    try:
                        future = executor.submit(execute_agentic_loop, prompt, "DIRECTIVE", signal)
                        future.result(timeout=90)
                    finally:
                        executor.shutdown(wait=False, cancel_futures=True)
            state["execution_result"] = "Fast command executed."

        elif target == "LIVE_FINANCE":
            try:
                from core.finance_engine import get_stock_price, extract_ticker
                ticker_symbol = extract_ticker(prompt)
                price_data = get_stock_price(ticker_symbol)
                last_price = price_data.get("price") if isinstance(price_data, dict) else price_data
                if last_price:
                    state["execution_result"] = f"Pulled {ticker_symbol} price: ${last_price}"
                    state["final_output"] = {"type": "finance", "ticker": ticker_symbol, "price": last_price}
                    reply_msg = f"The current trading price of {ticker_symbol} is ${last_price}."
                    signal.emit("speaking", f"SYSTEM_REPLY:{reply_msg}")
                    from core.voice import speak; speak(reply_msg)
                else:
                    raise ValueError(f"Could not retrieve ticker price for {ticker_symbol}")
            except Exception as e:
                try:
                    from core.brain import call_groq_brain
                    reply = call_groq_brain(f"What is the current financial status or stock price for: {prompt}", phase="DIRECTIVE")
                    if isinstance(reply, dict): reply = reply.get("reply", "")
                    signal.emit("speaking", f"SYSTEM_REPLY:{reply}")
                    from core.voice import speak; speak(reply)
                    state["execution_result"] = f"Finance handled via search/brain: {e}"
                except Exception as be:
                    msg = "Finance data is temporarily unavailable right now."
                    signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
                    from core.voice import speak; speak(msg)
                    state["execution_result"] = f"Finance unavailable: {be}"
            
        elif target == "MEDIA_CONTROL":
            import re
            # Extract song name from prompt (e.g. "play the song inaam" -> "inaam")
            clean_prompt = re.sub(r'^(hey\s+)?jarvis\s+', '', prompt, flags=re.IGNORECASE)
            clean_prompt = re.sub(r'^(can\s+you\s+|could\s+you\s+|please\s+|just\s+)', '', clean_prompt, flags=re.IGNORECASE)
            song = re.sub(r'^(play\s+the\s+song|play\s+song|play\s+music|play)\s+', '', clean_prompt, flags=re.IGNORECASE).strip()
            
            if not song or song.lower() in ("music", "a song", "something"):
                signal.emit("speaking", "SYSTEM_REPLY:Konsa song sunoge? Batao, abhi play karti hoon.")
                from core.voice import speak; speak("Konsa song sunoge? Batao, abhi play karti hoon.")
                state["execution_result"] = "Asked for song name."
            else:
                from core.win_fast_voice import _play_song_on_youtube
                result = _play_song_on_youtube(song)
                signal.emit("speaking", f"SYSTEM_REPLY:{result}")
                from core.voice import speak; speak(result)
                state["execution_result"] = result
                state["final_output"] = {"type": "media", "action": "play_youtube", "song": song}

        elif target == "3D_FORGE":
            custom_filename = create_filename_from_prompt(prompt).replace(".html", ".obj")
            signal.emit("thinking", "SYSTEM_REPLY:<i>[⚙️ 3D_FORGE]: Initiating neural point cloud...</i>")
            try:
                from core.local_3d_engine import generate_local_3d_model
                filepath = generate_local_3d_model(prompt, custom_filename)
                if filepath:
                    signal.emit("speaking", f"SYSTEM_REPLY:<i>[✅ 3D_FORGE]: Model generated.</i>")
                    state["execution_result"] = f"3D model generated: {filepath}"
                    state["final_output"] = {"type": "3d", "path": filepath}
                else:
                    state["execution_result"] = "3D model generation failed."
            except ImportError as e:
                msg = "The 3D forge module is not available."
                signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
                from core.voice import speak; speak(msg)
                state["execution_result"] = f"3D Forge unavailable: {e}"
                
        elif target == "WEB_FORGE":
            custom_filename = create_filename_from_prompt(prompt)
            theme_locked_prompt = prompt + "\n\nCRITICAL DIRECTIVE: Use exact theme."
            try:
                from core.gemini_web import generate_tailwind_web
                filepath = generate_tailwind_web(theme_locked_prompt, custom_filename)
                if filepath:
                    if os.path.isdir(filepath): filepath = os.path.join(filepath, "index.html")
                    signal.emit("speaking", f"OPEN_WEBSITE_TAB:{os.path.abspath(filepath)}") 
                    state["execution_result"] = f"Generated UI Framework."
                    state["final_output"] = {"type": "web", "path": filepath}
                else:
                    state["execution_result"] = "Web Generation failed."
            except ImportError as e:
                msg = "The web forge module is not available."
                signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
                from core.voice import speak; speak(msg)
                state["execution_result"] = f"Web Forge unavailable: {e}"
            
        elif target == "DATA_ANALYTICS":
            try:
                decision = call_groq_brain(prompt, phase="DIRECTIVE", is_logic_task=True)
                if isinstance(decision, str): decision = json.loads(decision)
                graph_data = decision.get("graph_data")
                if graph_data:
                    from core.graph_engine import process_graph_data
                    raw_json = process_graph_data(graph_data)
                    tab_id = f"tab_{random.randint(100,999)}"
                    signal.emit("speaking", f"CREATE_TAB:|{graph_data.get('title', 'Graph')}|GRAPH|{raw_json}|{tab_id}")
                    state["execution_result"] = "Graph generated successfully."
                    state["final_output"] = {"type": "graph", "data": graph_data}
            except ImportError as e:
                msg = "The graph analytics module is not available."
                signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
                from core.voice import speak; speak(msg)
                state["execution_result"] = f"Graph Analytics unavailable: {e}"
            
        elif target == "WEB_AGENT":
            signal.emit("thinking", f"SYSTEM_REPLY:<i>[🌐 WEB AGENT]: Initializing...</i>")
            try:
                from core.web_agent import get_web_agent
                agent = get_web_agent()
                session_id = agent.run(goal=prompt, headless=False)
                signal.emit("speaking", "SYSTEM_REPLY:CMD:SHRINK_TO_ORB")
                state["execution_result"] = f"Web Agent dispatched (Session {session_id})."
            except ImportError as e:
                msg = "The web agent module or its dependencies are not installed."
                signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
                from core.voice import speak; speak(msg)
                state["execution_result"] = f"Web agent unavailable: {e}"

        elif target == "SYSTEM_ACTION":
            signal.emit("speaking", "SYSTEM_REPLY:CMD:SHRINK_TO_ORB")
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
            try:
                future = executor.submit(execute_agentic_loop, prompt, "DIRECTIVE", signal)
                loop_result = future.result(timeout=600)
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

            # ── Clarification requested: store task, stop processing ──────────
            if isinstance(loop_result, str) and loop_result.startswith("[AWAITING_CLARIFICATION]"):
                _set_pending(prompt, signal, {"type": "save_location"})
                state["execution_result"] = loop_result
                # Do NOT fall through to any LLM chat — user must answer first
                return state

            # ── Verify actual success for file-creation tasks ─────────────────
            from core.voice import speak as _v_speak
            _file_created = None
            try:
                import re as _vr
                # Check if a filename was mentioned in the original prompt
                _fname_match = _vr.search(
                    r'\b([\w\-]+\.(html?|py|txt|js|css|json|md|csv|xml|ts|jsx|tsx|java|cpp|c|sh))\b',
                    prompt, _vr.IGNORECASE
                )
                if _fname_match:
                    _fname = _fname_match.group(1)
                    # Search common save locations
                    _search_dirs = [
                        os.path.expanduser("~\\Desktop"),
                        os.path.expanduser("~\\Documents"),
                        os.path.expanduser("~\\Downloads"),
                        os.getcwd(),
                    ]
                    for _d in _search_dirs:
                        _fp = os.path.join(_d, _fname)
                        if os.path.exists(_fp):
                            _file_created = _fp
                            break
                    if _file_created:
                        _ok_msg = f"Verified! {_fname} was successfully created at {_file_created}."
                        print(f"[✅ FILE VERIFIED]: {_file_created}")
                        signal.emit("speaking", f"SYSTEM_REPLY:{_ok_msg}")
                        _v_speak(_ok_msg)
                    else:
                        _fail_msg = (f"Could not find {_fname} on your system. "
                                     f"The task may have failed — please check the terminal log.")
                        print(f"[⚠️ FILE NOT FOUND]: {_fname} not found in {_search_dirs}")
                        signal.emit("speaking", f"SYSTEM_REPLY:{_fail_msg}")
                        _v_speak(_fail_msg)
            except Exception as _ve:
                log_warn("master_router", f"post-exec verify error: {_ve}")

            state["execution_result"] = str(loop_result) if loop_result else "System action executed."


        elif target == "MOBILE_ACTION":
            try:
                from core.mobile_agent import run_mobile_mission
                executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
                try:
                    future = executor.submit(run_mobile_mission, prompt, signal)
                    future.result(timeout=90)
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)
                state["execution_result"] = "Mobile action executed."
            except ImportError:
                msg = "The mobile agent module is not installed."
                signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
                from core.voice import speak; speak(msg)
                state["execution_result"] = "Mobile agent unavailable."

            
        elif target == "DEEP_RESEARCH":
            signal.emit("thinking", f"SYSTEM_REPLY:<i>[🔬 ASTRA RESEARCH]: Initiating deep intelligence scan for '{prompt[:40]}'...</i>")
            try:
                from core.astra_research import AstraResearchEngine
                engine = AstraResearchEngine()
                res = engine.deep_research(prompt, breadth=3)
                exec_sum = res.get("executive_summary", "Research synthesis complete.")
                vault_id = res.get("vault_id", "N/A")
                sources_count = res.get("sources_count", 0)
                
                reply_text = f"**Executive Intelligence Briefing**\n\n{exec_sum}\n\n*Verified across {sources_count} sources and indexed into Neural Vault (`{vault_id}`).*"
                signal.emit("speaking", f"SYSTEM_REPLY:{reply_text}")
                
                first_sentence = exec_sum.split(". ")[0] if exec_sum else "Research complete."
                from core.voice import speak
                speak(f"Research complete! {first_sentence}")
                
                state["execution_result"] = f"Astra research complete. Vault ID: {vault_id}"
                state["final_output"] = {"type": "deep_research", "report": res}
            except Exception as _ar_err:
                log_error("master_router", "DEEP_RESEARCH", _ar_err)
                err_msg = f"Deep research encountered an issue: {_ar_err}"
                signal.emit("speaking", f"SYSTEM_REPLY:{err_msg}")
                from core.voice import speak; speak("Deep research encountered an issue.")
                state["execution_result"] = err_msg

        elif target == "COMPUTER_USE":
            # ✅ TIER-1 GAP 3: Computer Use Agent — see screen + act autonomously
            signal.emit("thinking", "SYSTEM_REPLY:<i>[🖥️ COMPUTER USE]: Taking control of screen...</i>")
            from core.voice import speak as _cu_speak
            _cu_speak("On it. I'm taking control of the screen.")
            try:
                from core.computer_use_agent import run_computer_task_bg
                run_computer_task_bg(prompt, ui_callback=signal.emit)
                state["execution_result"] = f"Computer Use Agent launched: {prompt}"
            except Exception as _cu_e:
                log_error("master_router", "computer_use_agent", _cu_e)
                state["execution_result"] = f"Computer Use failed: {_cu_e}"

        elif target == "AUTO_CODER":
            signal.emit("thinking", "SYSTEM_REPLY:<i>[🧠 AUTO-CODER]: Self-modification...</i>")
            coder_prompt = f"Write Python code for skill: {prompt}"
            code_res = call_groq_brain(coder_prompt, phase="DIRECTIVE", is_logic_task=False)
            script_code = re.sub(r'^```python\s*|```\s*$', '', code_res, flags=re.MULTILINE).strip()
            os.makedirs("core/skills", exist_ok=True)
            slug = re.sub(r'[^a-zA-Z0-9]', '_', prompt.lower())[:20]
            filename = f"core/skills/skill_{slug}.py"
            with open(filename, "w", encoding="utf-8") as f: f.write(script_code)
            state["execution_result"] = f"Skill saved to {filename}."
            
        elif target == "CREATE_SKILL":
            extraction_prompt = f"Extract 'skill_name' and 'actions' as JSON. Prompt: '{prompt}'"
            raw_response = call_groq_brain(extraction_prompt, phase="DIRECTIVE", is_logic_task=True)
            data = json.loads(raw_response.replace("```json", "").replace("```", ""))
            save_skill(data.get("skill_name"), json.dumps(data.get("actions")))
            state["execution_result"] = "Skill saved."
            
        elif target == "RUN_SKILL":
            skill_name = state["intent_data"].get("skill_name")
            actions = get_skill(skill_name)
            for action in json.loads(actions):
                execute_agentic_loop(action, "DIRECTIVE", signal)
            state["execution_result"] = "Skill executed."

        # ══════════════════════════════════════════════════════════════
        # ✅ NEW IRIS FEATURES
        # ══════════════════════════════════════════════════════════════

        elif target == "WORMHOLE":
            """Expose a local port to the public internet via ngrok."""
            signal.emit("thinking", "SYSTEM_REPLY:<i>[🌀 WORMHOLE]: Opening tunnel...</i>")
            try:
                from core.wormhole import open_tunnel, close_tunnel, get_active_url
                from core.voice import speak

                p_lower = prompt.lower()
                # Detect close intent
                if any(w in p_lower for w in ["close", "stop", "kill", "disconnect"]):
                    result = close_tunnel()
                    signal.emit("speaking", f"SYSTEM_REPLY:{result}")
                    speak(result)
                    state["execution_result"] = result
                else:
                    # Extract port number from prompt
                    port_match = re.search(r'\b(\d{2,5})\b', prompt)
                    port = int(port_match.group(1)) if port_match else 3000
                    url = open_tunnel(port)
                    if url.startswith("http"):
                        import subprocess
                        subprocess.run("clip", input=url.encode(), check=False, capture_output=True)
                        msg = f"Tunnel open on port {port}. Public URL: {url} — Copied to clipboard."
                    else:
                        msg = url  # error message
                    signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
                    speak(msg)
                    state["execution_result"] = msg
            except Exception as _wh_e:
                log_error("master_router", "WORMHOLE", _wh_e)
                state["execution_result"] = f"Wormhole error: {_wh_e}"

        elif target == "CODEBASE_ORACLE":
            """Ingest a repo or answer questions about an ingested codebase."""
            try:
                from core.codebase_oracle import ingest_repository, query_oracle, clear_oracle, get_status
                from core.voice import speak

                p_lower = prompt.lower()

                if any(w in p_lower for w in ["ingest", "scan", "index", "load", "import"]):
                    # Extract path from prompt
                    path_match = re.search(r'[A-Za-z]:[\\\/][^\s"\']+|\/[\w\/\-\.]+', prompt)
                    if path_match:
                        repo_path = path_match.group(0).strip("\"'")
                    else:
                        # Fallback: ask for a path
                        msg = "Please specify the path to ingest. For example: ingest my project at C:/code/myapp"
                        signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
                        speak(msg)
                        state["execution_result"] = msg
                        return state

                    signal.emit("thinking", f"SYSTEM_REPLY:<i>[🔮 ORACLE]: Ingesting {repo_path}...</i>")
                    result = ingest_repository(repo_path, ui_callback=lambda m: signal.emit("thinking", f"SYSTEM_REPLY:<i>[🔮 ORACLE]: {m}</i>"))
                    signal.emit("speaking", f"SYSTEM_REPLY:{result}")
                    speak(result[:120])
                    state["execution_result"] = result

                elif any(w in p_lower for w in ["clear", "reset", "wipe"]):
                    result = clear_oracle()
                    signal.emit("speaking", f"SYSTEM_REPLY:{result}")
                    speak(result)
                    state["execution_result"] = result

                else:
                    # Question about the codebase
                    signal.emit("thinking", "SYSTEM_REPLY:<i>[🔮 ORACLE]: Searching codebase...</i>")
                    answer = query_oracle(prompt)
                    signal.emit("speaking", f"SYSTEM_REPLY:{answer}")
                    speak(answer[:200])
                    state["execution_result"] = answer

            except Exception as _oc_e:
                log_error("master_router", "CODEBASE_ORACLE", _oc_e)
                state["execution_result"] = f"Oracle error: {_oc_e}"

        elif target == "DOC_FORGE":
            """Generate a PowerPoint, Excel, or PDF document."""
            signal.emit("thinking", "SYSTEM_REPLY:<i>[📄 DOC FORGE]: Generating document...</i>")
            try:
                from core.doc_forge import create_document
                from core.voice import speak

                result = create_document(prompt, doc_type="auto")
                signal.emit("speaking", f"SYSTEM_REPLY:{result}")
                speak(result)
                state["execution_result"] = result
            except Exception as _df_e:
                log_error("master_router", "DOC_FORGE", _df_e)
                err_msg = f"Document generation failed: {_df_e}"
                signal.emit("speaking", f"SYSTEM_REPLY:{err_msg}")
                state["execution_result"] = err_msg

        elif target == "WIDGET_FORGE":
            """Spawn or close desktop widgets."""
            try:
                from core.widget_forge import spawn_widget, close_widget, close_all_widgets
                from core.voice import speak

                p_lower = prompt.lower()

                if "close all" in p_lower:
                    result = close_all_widgets()
                elif any(w in p_lower for w in ["close", "kill", "destroy", "remove"]):
                    # Detect which widget type
                    for wt in ["clock", "timer", "stock", "weather"]:
                        if wt in p_lower:
                            result = close_widget(wt)
                            break
                    else:
                        result = close_all_widgets()
                elif "clock" in p_lower or "time" in p_lower:
                    result = spawn_widget("clock")
                elif "timer" in p_lower or "countdown" in p_lower:
                    secs_match = re.search(r'(\d+)\s*(minute|min|second|sec|hour|hr)', p_lower)
                    if secs_match:
                        val = int(secs_match.group(1))
                        unit = secs_match.group(2)
                        seconds = val * 3600 if "hour" in unit or "hr" in unit else (val * 60 if "min" in unit else val)
                    else:
                        seconds = 300  # default 5 min
                    label = re.sub(r'(?i)(jarvis|timer|widget|spawn|create|open|set a?|for|countdown)', '', prompt).strip()
                    result = spawn_widget("timer", seconds=seconds, label=label)
                elif "stock" in p_lower or "ticker" in p_lower:
                    sym_match = re.search(r'\b([A-Z]{2,5})\b', prompt)
                    symbol = sym_match.group(1) if sym_match else "AAPL"
                    result = spawn_widget("stock", symbol=symbol)
                elif "weather" in p_lower:
                    city_match = re.search(r'(?:for|in|at)\s+([\w\s]+)$', prompt, re.IGNORECASE)
                    city = city_match.group(1).strip() if city_match else "Delhi"
                    result = spawn_widget("weather", city=city)
                else:
                    result = spawn_widget("clock")  # fallback

                signal.emit("speaking", f"SYSTEM_REPLY:{result}")
                speak(result)
                state["execution_result"] = result
            except Exception as _wf_e:
                log_error("master_router", "WIDGET_FORGE", _wf_e)
                state["execution_result"] = f"Widget error: {_wf_e}"

        elif target == "RUN_MACRO":
            """Run a named command sequence macro."""
            try:
                from core.macro_engine import run_macro, list_macros
                from core.voice import speak

                p_lower = prompt.lower()
                if any(w in p_lower for w in ["list", "show", "what", "available"]):
                    result = list_macros()
                    signal.emit("speaking", f"SYSTEM_REPLY:{result}")
                    speak(result[:200])
                    state["execution_result"] = result
                else:
                    # Extract macro name — strip command words
                    macro_name = re.sub(
                        r'(?i)(jarvis|run|execute|play|start|trigger|my|the|a|macro|routine|sequence|automation)\s*',
                        '', prompt
                    ).strip().rstrip(".,!")
                    if not macro_name:
                        macro_name = "morning routine"
                    result = run_macro(macro_name, signal)
                    signal.emit("speaking", f"SYSTEM_REPLY:{result}")
                    speak(result)
                    state["execution_result"] = result
            except Exception as _me:
                log_error("master_router", "RUN_MACRO", _me)
                state["execution_result"] = f"Macro error: {_me}"

        elif target == "GEMINI_LIVE":
            """Toggle Gemini Live ultra-low-latency bidirectional voice mode."""
            try:
                from core.gemini_live_voice import (
                    start_live_mode, stop_live_mode, is_live_active, get_status
                )
                from core.voice import speak

                p_lower = prompt.lower()

                # ── Stop / deactivate ──────────────────────────────────
                if any(w in p_lower for w in [
                    "stop", "exit", "deactivate", "disable", "off", "standard mode",
                    "normal mode", "back to normal", "turn off live", "stop live"
                ]):
                    result = stop_live_mode()
                    signal.emit("speaking", f"SYSTEM_REPLY:{result}")
                    speak(result)
                    state["execution_result"] = result

                # ── Start in Hindi ─────────────────────────────────────
                elif any(w in p_lower for w in [
                    "hindi", "हिंदी", "hindi mode", "hindi voice", "bolo hindi",
                    "hindi mein", "hindi me"
                ]):
                    result = start_live_mode(ui_signal=signal, language="hi-IN")
                    signal.emit("speaking", f"SYSTEM_REPLY:{result}")
                    speak(result)
                    state["execution_result"] = result

                # ── Start (English default) ────────────────────────────
                else:
                    if is_live_active():
                        st = get_status()
                        msg = f"Live mode is already active in {st['language']}."
                        signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
                        speak(msg)
                        state["execution_result"] = msg
                    else:
                        result = start_live_mode(ui_signal=signal, language="en-US")
                        signal.emit("speaking", f"SYSTEM_REPLY:{result}")
                        speak(result)
                        state["execution_result"] = result

            except Exception as _gl_e:
                log_error("master_router", "GEMINI_LIVE", _gl_e)
                err = f"Live mode error: {_gl_e}"
                signal.emit("speaking", f"SYSTEM_REPLY:{err}")
                state["execution_result"] = err

        elif target == "CASUAL_CHAT":

            reply = ""  # ← initialize so action_suggestion_detector never gets UnboundLocalError

            # ⚡ Phase 1: Check instant response cache FIRST (returns in <10ms for greetings/common phrases)
            try:
                from core.response_cache import get_cached_response, cache_response
                _cached = get_cached_response(prompt)
                if _cached:
                    log_info("master_router", f"Cache hit: '{prompt[:40]}' → instant reply")
                    reply = _cached
                    from core.voice import speak_async as _vs_cache
                    _vs_cache(_cached)
                    signal.emit("speaking", f"SYSTEM_REPLY:{_cached}")
                    state["execution_result"] = _cached
                    _cache_hit = True
                else:
                    _cache_hit = False
            except Exception as _ce:
                log_warn("master_router", f"response_cache error: {_ce}")
                _cache_hit = False

            if not _cache_hit:
                # ⚡ TIER-1 GAP 1: Live streaming — speak each clause as it arrives (<300ms first word)
                _spoken_clauses = []
                def _live_speak(clause: str):
                    """Called by brain.py for each clause as tokens stream in."""
                    if clause.strip():
                        _spoken_clauses.append(clause)
                        from core.voice import speak_async as _vs
                        _vs(clause)  # ✅ Non-blocking — background thread handles TTS generation

                from core.brain import call_groq_brain
                executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                try:
                    future = executor.submit(
                        call_groq_brain, prompt, "CONVERSATION", False,
                        None, False, None, _live_speak  # on_token_chunk = _live_speak
                    )
                    response = future.result(timeout=90)
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)

                # Build full reply from response (clauses already spoken live above)
                if isinstance(response, str):
                    try:
                        import re as _rre, json as _rjson
                        clean = _rre.sub(r'^```json\s*', '', response, flags=_rre.IGNORECASE)
                        clean = _rre.sub(r'^```\s*', '', clean)
                        clean = _rre.sub(r'\s*```$', '', clean).strip()
                        parsed = _rjson.loads(clean)
                        reply = parsed.get("reply", str(response))
                    except Exception as e:
                        log_warn("master_router", f"parse json response failed: {e}")
                        reply = str(response)
                elif isinstance(response, dict):
                    reply = response.get("reply", str(response))
                else:
                    reply = str(response)

                # ✅ POLISH P3: Clean LLM output before display/voice
                reply = _clean_resp(reply)

                # ✅ RICH FORMATTER: Convert lists/events/hackathons to HTML cards
                ui_reply = _richify(reply, prompt) if _HAS_RICH else reply

                # Show full reply in UI (already spoken live)
                signal.emit("speaking", f"SYSTEM_REPLY:{ui_reply}")
                # Only speak full reply if live streaming did NOT fire (fallback safety)
                if not _spoken_clauses:
                    from core.voice import speak
                    speak(_trim_for_voice(reply))   # voice always gets plain text
                state["execution_result"] = reply

                # ⚡ Phase 1: Cache this LLM reply for future instant retrieval (background)
                try:
                    import threading as _ct
                    _ct.Thread(target=cache_response, args=(prompt, reply), daemon=True).start()
                except Exception:
                    pass

                # ⚡ Phase 4: Nudge personality traits based on this turn (background, never blocks)
                try:
                    from core.personality import nudge_personality as _nudge
                    import threading as _pt
                    _pt.Thread(
                        target=_nudge,
                        args=(prompt, reply, "neutral"),
                        daemon=True,
                        name="JARVIS-PersonalityNudge"
                    ).start()
                except Exception:
                    pass



            # ⚡ UNIVERSAL ACTION SUGGESTION DETECTOR
            # If JARVIS offered to do something in its reply, set a pending so "yes" executes it
            try:
                import re as _mre

                # Patterns that detect JARVIS offering to perform an action
                # We capture the action part after the offer phrase
                _OFFER_PATTERNS = [
                    r"shall i\s+(.+?)(?:\?|$)",
                    r"want me to\s+(.+?)(?:\?|$)",
                    r"would you like me to\s+(.+?)(?:\?|$)",
                    r"should i\s+(.+?)(?:\?|$)",
                    r"i can\s+(.+?)\s+for you",
                    r"let me\s+(.+?)(?:\?|!|$)",
                    r"i'll\s+(.+?)\s+for you",
                    r"i could\s+(.+?)\s+for you",
                    r"do you want me to\s+(.+?)(?:\?|$)",
                    r"may i\s+(.+?)(?:\?|$)",
                ]

                _suggested_action = None
                for _pat in _OFFER_PATTERNS:
                    _m = _mre.search(_pat, reply, _mre.IGNORECASE)
                    if _m:
                        _action = _m.group(1).strip().rstrip("?!. ")
                        # Filter out vague/conversational false positives
                        _too_vague = {
                            "help", "assist", "know", "explain", "tell you", "say",
                            "continue", "go on", "stop", "start", "try", "check",
                            "elaborate", "clarify", "repeat"
                        }
                        _action_lower = _action.lower()
                        if (len(_action) > 4 and
                                not any(_action_lower == v for v in _too_vague) and
                                not _action_lower.startswith("tell you more")):
                            _suggested_action = _action
                            break

                if _suggested_action:
                    _set_pending(
                        _suggested_action,
                        signal,
                        {"type": "action_suggestion", "action": _suggested_action}
                    )
                    print(f"[🎯 ACTION PENDING]: JARVIS offered → '{_suggested_action}'. Waiting for 'yes'.")
            except Exception as _ae:
                log_error("master_router", "action_suggestion_detector", _ae)

            
        else:
            # ✅ TIER-1 GAP 5: Parallel dual-agent reasoning
            # Spawn 2 brain calls simultaneously: one pure reasoning + one enriched with memory
            # Merge best answer. Hard questions get 2x thinking power.
            def _agent_reason():
                """Primary: pure reasoning call"""
                return call_groq_brain(prompt, "DIRECTIVE", True)

            def _agent_research():
                """Secondary: same prompt + 'think step by step, show your reasoning'"""
                enriched = f"{prompt}\n\n[Internal: Think step by step. Be thorough.]"
                return call_groq_brain(enriched, "DIRECTIVE", True)

            executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
            try:
                f_reason   = executor.submit(_agent_reason)
                f_research = executor.submit(_agent_research)
                # Primary must succeed; secondary is best-effort
                response = f_reason.result(timeout=90)
                try:
                    alt = f_research.result(timeout=90)
                    # Pick the longer/richer response as the final answer
                    r_reply = (response or {}).get("reply", "") if isinstance(response, dict) else str(response or "")
                    a_reply = (alt or {}).get("reply", "")      if isinstance(alt, dict)      else str(alt or "")
                    if len(a_reply) > len(r_reply) * 1.2:  # alt is 20%+ richer
                        response = alt
                except Exception as e:
                    log_warn("master_router", f"dual agent alt response failed: {e}")
            except Exception as e:
                log_warn("master_router", f"dual agent primary response failed: {e}")
                response = {}

            if isinstance(response, str):
                try:
                    response = json.loads(response)
                except Exception as e:
                    log_warn("master_router", f"parse json response failed: {e}")
                    response = {"reply": str(response)}
            reply = response.get("reply", "Task complete.")
            # ✅ POLISH P3: Full response quality guard (includes persona guard)
            reply = _clean_resp(reply)
            signal.emit("speaking", f"SYSTEM_REPLY:{reply}")
            from core.voice import speak
            speak(_trim_for_voice(reply))
            state["execution_result"] = "Deep logic complete."

    except Exception as e:
        traceback.print_exc()
        state["execution_result"] = f"Error: {str(e)}"
        
    # ⚡ Log the final execution result to the visual chat UI
    if state.get("execution_result"):
        update_chat_history("assistant", state["execution_result"])
        
    return state

def node_memory_commit(state: AgentState) -> AgentState:
    output = state.get("final_output")
    if output:
        try:
            node_id = f"node_{random.randint(1000,9999)}"
            save_node(
                node_id=node_id, 
                project_name="Master Workspace", 
                title=state["target_system"], 
                node_type=output.get("type", "generic"), 
                content=output
            )
        except Exception as e:
            log_error("master_router", "node_memory_commit_save", e)
    return state

# ✅ JARVIS 2.0: Route confidence logger
def _log_route(prompt: str, intent: str, confidence: float, method: str):
    """Log every routing decision for debugging. Helps diagnose misroutes."""
    try:
        import json as _jl, datetime as _dt
        _log_path = "core/routing_log.jsonl"
        entry = {
            "ts": _dt.datetime.now().isoformat(),
            "prompt": prompt[:100],
            "intent": intent,
            "confidence": round(confidence, 4),
            "method": method  # wfv / semantic / llm_fallback / default
        }
        with open(_log_path, "a", encoding="utf-8") as _lf:
            _lf.write(_jl.dumps(entry) + "\n")
    except Exception as e:
        log_warn("master_router", f"log route failed: {e}")

# ✅ JARVIS 2.0: Retry/error nodes for LangGraph
def _route_after_execute(state: AgentState) -> str:
    """Decide next node after execute: success/retry/error."""
    result = state.get("execution_result", "")
    if state.get("_retry_count", 0) == 0 and result and "Error" in str(result):
        return "retry"
    return "memory"

def node_retry(state: AgentState) -> AgentState:
    """Retry node: increments counter so we only retry once."""
    state["_retry_count"] = state.get("_retry_count", 0) + 1
    log_info("master_router", f"Retrying execution (attempt {state['_retry_count']})")
    return state

def node_error(state: AgentState) -> AgentState:
    """Error node: emit failure message to UI."""
    signal = state.get("ui_signal")
    if signal:
        signal.emit("speaking", "SYSTEM_REPLY:I ran into a problem. Check the terminal for details.")
    return state

def build_jarvis_graph():
    workflow = StateGraph(AgentState)
    workflow.add_node("analyze", node_analyze)
    workflow.add_node("execute", node_execute)
    workflow.add_node("retry",   node_retry)    # ✅ JARVIS 2.0
    workflow.add_node("error",   node_error)    # ✅ JARVIS 2.0
    workflow.add_node("memory",  node_memory_commit)
    workflow.add_edge(START, "analyze")
    workflow.add_edge("analyze", "execute")
    workflow.add_conditional_edges("execute", _route_after_execute,
                                   {"retry": "retry", "memory": "memory"})
    workflow.add_edge("retry",  "execute")  # one retry loop
    workflow.add_edge("error",  "memory")
    workflow.add_edge("memory", END)
    return workflow.compile()

def delegate_to_swarm(user_prompt, ui_callback_signal, forced_target=None, _is_subtask=False):
    """⚡ THE CLAUDE-STYLE LIVE STREAMING DELEGATOR ⚡"""
    import time
    _t_start = time.time()

    # ⚡ ZERO-LATENCY NEURAL INTENT SPLITTER (TinyBERT)
    if not _is_subtask and forced_target is None:
        try:
            from core.intent_splitter import split_and_classify
            tasks = split_and_classify(user_prompt)
            if len(tasks) > 1:
                print(f"\n[🧠 MULTI-TASK DETECTED (TinyBERT)]: {tasks}")
                from core.voice import speak
                speak("I've identified multiple linked tasks. Processing them now.")
                
                import concurrent.futures
                
                # We separate them into parallel vs sequential execution based on Neural predictions
                for idx, task_obj in enumerate(tasks):
                    t_str = task_obj["task"]
                    is_child = task_obj["is_child"]
                    
                    print(f"[->] Executing Sub-Task {idx+1}/{len(tasks)}: '{t_str}' (Dependent: {is_child})")
                    
                    if is_child:
                        # Append explicit context wrapper to pass down state implicitly to agents
                        t_str = t_str + " (Context: This action depends on the previous task completing successfully.)"
                        
                    # Execute sequentially. Wait for parent before starting child.
                    delegate_to_swarm(t_str, ui_callback_signal, forced_target=None, _is_subtask=True)
                return
        except Exception as e:
            from core.jarvis_logger import log_error
            log_error("master_router", "TinyBERT multi-task split failed", e)

    if ui_callback_signal is None:
        class DummySignal:
            def emit(self, *args, **kwargs):
                pass
        ui_callback_signal = DummySignal()

    # \u2705 NEXUS-2 Phase E: Online correction learning
    # If user says "that was wrong" / "actually it's X" — save to memory immediately
    try:
        from core.brain import detect_correction, save_correction
        if detect_correction(user_prompt):
            save_correction(user_prompt)
            log_info("master_router", f"[NEXUS-2] Correction detected and saved: {user_prompt[:80]}")
    except Exception as _ce:
        log_error("master_router", "correction_learning", _ce)

    # \u2500\u2500 Pending task replay: user answered a clarification question \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # If JARVIS previously asked "where to save?" and stored the original task,
    # combine it with the user's answer now and run the merged prompt.
    if _has_pending():
        pending = _pop_pending()
        meta = pending.get("metadata", {})
        pending_type = meta.get("type")
        original_task = pending.get("prompt", "")
        
        if pending_type == "heavy_task_confirmation":
            answer = user_prompt.strip().lower()
            if any(answer.startswith(w) or f" {w} " in f" {answer} " for w in ["yes", "proceed", "do it", "go ahead", "y", "yep", "sure", "ok", "okay"]):
                user_prompt = original_task
                forced_target = meta.get("target")
            elif any(answer.startswith(w) or f" {w} " in f" {answer} " for w in ["no", "cancel", "stop", "nevermind", "n", "nope"]):
                ui_callback_signal.emit("speaking", "SYSTEM_REPLY:Cancelled. Let me know if you need anything else.")
                return
            else:
                user_prompt = user_prompt.strip()
                forced_target = None

        elif pending_type == "music_suggestion":
            # Legacy fallback — redirect to action_suggestion handler
            answer = user_prompt.strip().lower()
            if any(answer.startswith(w) or f" {w} " in f" {answer} " for w in ["yes", "y", "yep", "sure", "yeah", "do it", "go ahead", "play it", "play", "ok", "okay"]):
                song = meta.get("song", "")
                from core.win_fast_voice import _play_song_on_youtube
                result = _play_song_on_youtube(song)
                ui_callback_signal.emit("speaking", f"SYSTEM_REPLY:⚡ {result}")
                from core.voice import speak; speak(f"Playing {song} on YouTube!")
                return
            elif any(answer.startswith(w) or f" {w} " in f" {answer} " for w in ["no", "n", "nope", "cancel", "stop", "nevermind"]):
                ui_callback_signal.emit("speaking", "SYSTEM_REPLY:Alright, let me know if you need anything!")
                from core.voice import speak; speak("Alright!")
                return
            else:
                user_prompt = user_prompt.strip()
                forced_target = None

        elif pending_type == "action_suggestion":
            answer = user_prompt.strip().lower()
            _YES_WORDS = [
                "yes", "y", "yep", "yup", "yeah", "sure", "do it", "go ahead",
                "go for it", "please", "ok", "okay", "proceed", "go on", "alright",
                "fine", "of course", "absolutely", "definitely", "why not",
                "sounds good", "let's do it", "do that", "confirm", "affirmative",
                "please do", "go", "run it", "execute", "make it happen"
            ]
            _NO_WORDS  = [
                "no", "n", "nope", "nah", "cancel", "stop", "nevermind", "never mind",
                "don't", "dont", "skip", "abort", "negative", "decline", "pass", "not now"
            ]

            if any(answer.startswith(w) or f" {w} " in f" {answer} " for w in _YES_WORDS):
                action = meta.get("action", "")
                if action:
                    print(f"[⚙️ ACTION TRIGGER]: Executing suggested action → '{action}'")
                    from core.voice import speak as _as
                    _as(f"Sure, {action[:40]}.")
                    # Re-route the suggested action through the full pipeline
                    # No forced target — let semantic router pick the right agent
                    delegate_to_swarm(action, ui_callback_signal)
                    return
            elif any(answer.startswith(w) or f" {w} " in f" {answer} " for w in _NO_WORDS):
                ui_callback_signal.emit("speaking", "SYSTEM_REPLY:Alright, just say the word if you need anything!")
                from core.voice import speak; speak("Alright!")
                return
            else:
                # User said something else — treat as a new command
                user_prompt = user_prompt.strip()
                forced_target = None
        else:
            if original_task and original_task != user_prompt:
                merged_prompt = f"{original_task} — save location: {user_prompt}"
                print(f"[🔁 CLARIFY REPLAY]: Merged prompt → '{merged_prompt}'")
                from core.voice import speak as _r_speak
                _r_speak(f"Got it! {user_prompt}. Executing now.")
                user_prompt = merged_prompt
                forced_target = "SYSTEM_ACTION"

    if forced_target is None:
        target = analyze_intent_fast(user_prompt)
        if isinstance(target, dict):
            target = target.get("subsystem", "CASUAL_CHAT")
    else:
        target = forced_target
        
    if target in ["WEB_FORGE", "SYSTEM_ACTION", "WEB_AGENT"] and forced_target is None:
        import random
        cid = random.randint(1000, 9999)
        agent_name = target.replace('_', ' ').title()
        html = f"""<b>[ CONFIRMATION REQUIRED ]</b><br>This requires the {agent_name}. Shall I proceed?
        <div id='confirm_div_{cid}' style='margin-top:10px;'>
          <button onclick="jarvis_cmd('yes'); document.getElementById('confirm_div_{cid}').innerHTML='<span style=\\'color:#00ff88;\\'>✅ Proceeding...</span>';" style='background: rgba(0, 210, 255, 0.1); border: 1px solid #00d2ff; color: #00ff88; padding: 5px 10px; cursor: pointer; border-radius: 4px;'>YES</button>
          <button onclick="jarvis_cmd('no'); document.getElementById('confirm_div_{cid}').innerHTML='<span style=\\'color:#ff5555;\\'>❌ Cancelled.</span>';" style='background: rgba(255, 50, 50, 0.1); border: 1px solid #ff3232; color: #ff5555; padding: 5px 10px; cursor: pointer; border-radius: 4px; margin-left: 10px;'>NO</button>
        </div>"""
        _set_pending(user_prompt, ui_callback_signal, {"type": "heavy_task_confirmation", "target": target})
        ui_callback_signal.emit("speaking", f"SYSTEM_REPLY:{html}")
        from core.voice import speak as _r_speak
        _r_speak(f"This requires the {agent_name}. Shall I proceed?")
        return

    if HAS_LANGGRAPH:
        app = build_jarvis_graph()
        initial_state = {
            "prompt": user_prompt, "ui_signal": ui_callback_signal, 
            "intent_data": {}, "target_system": target if target else "", "execution_result": "", "final_output": None
        }
        

        def run_live_stream():
            def _emit(t, m):
                if ui_callback_signal:
                    ui_callback_signal.emit(t, m)
                    
            _emit("thinking", "SYSTEM_REPLY:<i>[⚙️ SYSTEM]: Initializing Swarm Protocol...</i>")
            # ⚡ Stream intercepts the graph at every step and reports to the UI!
            try:
                for event in app.stream(initial_state):
                    for node_name, state_data in event.items():
                        if node_name == "analyze":
                            target = state_data.get('target_system', 'UNKNOWN')
                            _emit("thinking", f"SYSTEM_REPLY:<i>[🧠 PLAN FORMULATED]: Routing logic to <b>{target}</b>...</i>")
                            
                            # ⚡ Feature 4: Real-time WebSocket Streaming
                            try:
                                from core.ws_server import manager
                                manager.broadcast({"type": "status", "node": "analyze", "message": f"Routed to {target}"})
                            except Exception as e:
                                log_error("master_router", "ws_broadcast_analyze", e)
                            
                            if HAS_MQTT: telemetry_client.publish_state("ROUTING", "analyze", f"Routed to {target}")
                        elif node_name == "execute":
                            res = state_data.get('execution_result', 'Done')
                            _emit("speaking", f"SYSTEM_REPLY:<i>[✅ EXECUTION]: {res}</i>")
                            
                            try:
                                from core.ws_server import manager
                                manager.broadcast({"type": "status", "node": "execute", "message": res})
                            except Exception as e:
                                log_error("master_router", "ws_broadcast_execute", e)
                            
                            if HAS_MQTT: telemetry_client.publish_state("EXECUTION", "execute", res)
                        elif node_name == "memory":
                            _emit("speaking", "SYSTEM_REPLY:<i>[🗄️ MEMORY]: Task securely logged to SQLite vault.</i>")
                            
                            try:
                                from core.ws_server import manager
                                manager.broadcast({"type": "status", "node": "memory", "message": "Task logged to Knowledge Graph."})
                            except Exception as e:
                                log_error("master_router", "ws_broadcast_memory", e)
                            
                            if HAS_MQTT: telemetry_client.publish_state("MEMORY", "commit", "Logged to vault")
            except concurrent.futures.TimeoutError:
                _emit("speaking", "SYSTEM_REPLY:<i>[⚠️ TIMEOUT]: Task aborted after 90 seconds.</i>")
                if HAS_MQTT: telemetry_client.publish_state("SYSTEM", "error", "Task timeout")
                from core.voice import speak
                speak("The task took too long and has been terminated.")
                        
        run_live_stream() # ⚡ AUDIT FIX: Execute synchronously to prevent the QThread from dying and segfaulting the ui_signal!
    else:
        print("[⚡ LANGGRAPH NOT INSTALLED]")
        
    _t_end = time.time()
    _t_total = _t_end - _t_start
    print(f"\n[⏱️ LATENCY METRICS]: Total request processing time: {_t_total:.2f} seconds.")