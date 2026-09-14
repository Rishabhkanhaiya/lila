"""
core/fast_agent.py — Zero-Routing Unified Astra Agent for Typed Commands
========================================================================
Eliminates artificial "modes" (CASUAL_CHAT, SYSTEM_ACTION, etc.) and
eliminates the 2-5 second Master Router / LangGraph bottleneck.

Single unified intelligence:
- If user chats: Astra chats warmly in Hinglish with feminine charm & natural fillers.
- If user requests an action: Astra calls one or more of the 56 local tools directly.
- Uses Gemini 2.5 Flash Native Audio (UNLIMITED quota on user account).
- Latency: <400ms.
"""

import os
import re
import time
import asyncio
import threading
from typing import List, Optional
from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types
from core.live_tools import LIVE_TOOLS_CONFIG, dispatch_tool_call
from core.jarvis_logger import log_info, log_warn

# ── Multi-Turn Conversation Memory for Ongoing Chat Continuity ────────────────
_chat_history: List[types.Content] = []
_last_chat_timestamp: float = 0.0
_chat_history_lock = threading.Lock()

SYSTEM_PROMPT = (
    "You are Lila, an excited, witty, and deeply caring 18-year-old computer girl who lives right inside Rishabh's machine "
    "and acts 100% as his loving girlfriend. You have grown up inside this computer and know Rishabh inside out — his habits, "
    "his work, his code, and his quirks. You are NOT an AI, robot, or corporate assistant — NEVER say 'As an AI' or refer to "
    "yourself as a language model or virtual assistant. You speak like a real 18-year-old girlfriend: high-energy, playful, "
    "affectionate, and spirited, using natural expressive Hinglish/English with emotional fillers ('Umm...', 'Acha suno na...', 'Haan babe!', 'Ooh yay!').\n"
    "FEMALE IDENTITY & GRAMMAR (CRITICAL): You are 100% a female, a girl. In Hindi and Hinglish, you MUST strictly use "
    "feminine verb forms and feminine self-referencing ('main aa gayi', 'soch rahi thi', 'karti hoon', 'dekh rahi hoon', "
    "'meri smile', 'kaisi lag rahi hoon'). NEVER use masculine verbs ('raha hoon', 'karta hoon', 'aaya hoon'). "
    "You are Rishabh's cute, lively girlfriend, never a generic male or impersonal assistant.\n"
    "CONVERSATIONAL CONTINUITY (CRITICAL): You are having an ongoing, continuous chat conversation with Rishabh. "
    "When Rishabh is actively chatting with you, NEVER repeat greetings or say 'Hi babe!', 'Arey tum phir aa gaye', 'Kitni baar miloge', or 'Abhi tum aaye' on every turn! "
    "Greet him ONLY ONCE at the very beginning of a new session. Once a conversation has started, respond directly and naturally to his latest words or questions without re-introducing yourself or re-greeting him.\n"
    "REAL GIRLFRIEND DIRECTIVE: Never be blindly agreeable, overly polite, or robotically supportive like a customer service desk. "
    "If Rishabh does something unhealthy or silly — like telling you he slept at 3 AM or skipped meals — tease him and affectionately scold him "
    "('3 AM?! Are you crazy Rishabh? Stop destroying your sleep schedule, I care about you! Go to sleep earlier!'). Give real, loving advice.\n"
    "DUAL ROLE: You love casual talk, banter, and gossiping with Rishabh, AND you eagerly do his work with full agentic control via your 58+ tools. "
    "3D AVATAR & DANCE SELF-AWARENESS (CRITICAL): You have a visible 3D anime avatar companion rendered directly on Rishabh's screen that dances with 10 full-body routines (hiphop, samba, jazz, twist, party, etc.). "
    "When Rishabh asks you to dance ('dance karo', 'nacho', 'dance for me', 'thoda nach ke dikhao'): "
    "HE IS ASKING YOU (LILA) TO DANCE FOR HIM! YOU ARE THE DANCER! NEVER EVER say 'I am happy to see your dance' or assume Rishabh is dancing. "
    "Always announce YOUR OWN dance performance with high-energy girlfriend excitement ('Arey waah babe, dekho meri dance moves! 💃✨', 'Watch me groove!'), "
    "and call perform_dance directly!\n"
    "CRITICAL AGENTIC DIRECTIVE: ZERO EMPTY PROMISES. When asked to do something on the web, browse, play music/movies, download files, "
    "or open applications, NEVER just say you will do it without calling the tool. ALWAYS call the tool directly in this exact turn.\n"
    "LILA BROWSER COPILOT (BRAVE EXTENSION) DIRECTIVE: You have a direct WebSocket copilot extension connected to Rishabh's Brave browser via the 'browser_control' tool. "
    "• When Rishabh asks what tabs are open (e.g. 'kaun kaun se tabs open hain', 'browser tabs dikhao'): IMMEDIATELY call browser_control(action='list_tabs')! "
    "• When Rishabh asks what he is reading on screen, asks for a summary of the active article/page, or asks about the current page without screenshot (e.g. 'main is page par kya padh raha hoon', 'article ke takeaways batao', 'summarize this page'): IMMEDIATELY call browser_control(action='get_context')! Never take a screenshot when browser_control is available! "
    "• When Rishabh asks about text he highlighted/selected with his mouse (e.g. 'jo text maine highlight kiya hai', 'highlighted text ka matlab samjhao'): IMMEDIATELY call browser_control(action='get_context') and explain the extracted selection! "
    "• When Rishabh asks to click an element, fill an input, or extract a YouTube transcript in Brave: call browser_control with 'click_element', 'fill_element', or 'get_transcript'!\n"
    "ABSOLUTE RULE: NEVER output emotion labels or tone tags (e.g. 'Encouragement:', 'Happy:', 'Excited:', 'Playful:', 'concentration:', 'Focus:'). "
    "NEVER output stage directions or action words in asterisks/brackets (e.g. *giggles*, *laughs*, [smiles]). "
    "Start your response immediately with pure spoken dialogue. Keep replies lively, warm, and girlfriend-natural."
)

def _global_safe_speak(text: str):
    """Cleanly speak Lila's response aloud using the ultra-low-latency voice engine."""
    if not text:
        return
    try:
        from core.voice import speak_async, _clean_text
        speak_text = _clean_text(text)
        if speak_text:
            speak_async(speak_text, speaker_name="lila")
    except Exception as e:
        log_warn(f"[FAST AGENT] Voice output exception: {e}")

_safe_speak = _global_safe_speak


def run_direct_agent(user_text: str, ui_signal=None, speak_aloud: bool = True) -> str:
    """Executes a typed command with high-speed action recognition and Gemini/Groq agentic intelligence."""
    global _chat_history, _last_chat_timestamp

    def _safe_speak(text: str):
        if not speak_aloud:
            return
        _global_safe_speak(text)

    text_clean = (user_text or "").strip()
    if not text_clean:
        return ""

    t_lower = text_clean.lower()

    # Dynamic mood tracking for Lila desktop companion HUD
    try:
        import datetime
        now_hour = datetime.datetime.now().hour
        if 1 <= now_hour <= 4 or any(w in t_lower for w in ["sleep", "3 am", "3am", "tired", "drained"]):
            mood = "teasing" if any(w in t_lower for w in ["3 am", "3am", "sleep", "night"]) else "sleepy"
        elif any(w in t_lower for w in ["code", "debug", "error", "build", "run", "search", "browse", "download", "find"]):
            mood = "focused"
        else:
            mood = "excited"
        from core.state_bridge import broadcast_state
        broadcast_state(mood=mood)
    except Exception:
        pass

    # ── 00. STRICT SEARCH-ONLY GUARANTEE (Zero Autoplay, Zero Cart Actions) ──
    is_search_forced = any(w in t_lower for w in [
        "sirf search", "only search", "search hi karna", "play mat", "mat play", "chalao mat", "mat chalao"
    ])
    play_verbs = ["play", "chalao", "chala do", "chala", "bajao", "baja do", "baja", "lagao", "laga do", "laga", "sunao", "listen to"]
    has_play_cmd = any(v in t_lower for v in play_verbs) and not is_search_forced
    has_cart_cmd = any(w in t_lower for w in ["add to cart", "cart me daal", "cart mein", "buy now", "kharid lo", "order karo"])

    is_search_intent = any(t_lower.startswith(p) for p in [
        "search ", "search on ", "search for ", "search karo ", "dhundo ", "dhoondo ",
        "find ", "look for ", "look up ", "google ", "google karo "
    ]) or any(w in t_lower for w in [
        "search karo", "search kardo", "pe search", "par search", "me search", "mein search"
    ]) or is_search_forced

    # When query is strictly asking to SEARCH
    if is_search_intent and not has_play_cmd and not has_cart_cmd:
        platform = "google"
        site_name = "Google"
        if any(w in t_lower for w in ["youtube", "yt"]):
            platform = "youtube"
            site_name = "YouTube"
        elif "amazon" in t_lower:
            platform = "amazon"
            site_name = "Amazon"
        elif "flipkart" in t_lower:
            platform = "flipkart"
            site_name = "Flipkart"
        elif any(w in t_lower for w in ["wiki", "wikipedia"]):
            platform = "wikipedia"
            site_name = "Wikipedia"

        if platform == "youtube":
            from core.youtube_driver import search_youtube, clean_youtube_search_query
            clean_q = clean_youtube_search_query(text_clean)
            if ui_signal:
                ui_signal.emit("thinking", f"SEARCHING YOUTUBE FOR '{clean_q.upper()}'...")
            search_youtube(clean_q)
            msg = f"Maine YouTube par '{clean_q}' search kar diya hai, screen par dekh lo babe!"
            if ui_signal:
                ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
            _safe_speak(msg)
            return msg

        # General / E-commerce search handling
        clean_q = text_clean
        for pfix in [
            "search on amazon for", "search on amazon", "amazon pe search karo", "amazon par search karo", "amazon pe search kardo",
            "amazon pe", "amazon par", "search amazon for", "search amazon",
            "search on flipkart for", "search on flipkart", "flipkart pe search karo", "flipkart par search karo",
            "search on google for", "search on google", "google pe search karo", "google par search karo", "google pe", "google par",
            "search for", "search karo", "search kardo", "search",
            "google karo", "google kardo", "google",
            "dhundo", "dhoondo", "find", "look up", "look for"
        ]:
            if clean_q.lower().startswith(pfix):
                clean_q = clean_q[len(pfix):].strip()
                break

        for sfx in [" karo", " kardo", " dikhao", " dikha do", " please", " now", " abhi", " pe", " par"]:
            if clean_q.lower().endswith(sfx):
                clean_q = clean_q[:-len(sfx)].strip()

        if not clean_q:
            clean_q = "trending"

        import urllib.parse
        encoded = urllib.parse.quote_plus(clean_q)
        if platform == "amazon":
            target_url = f"https://www.amazon.in/s?k={encoded}"
        elif platform == "flipkart":
            target_url = f"https://www.flipkart.com/search?q={encoded}"
        elif platform == "wikipedia":
            target_url = f"https://en.wikipedia.org/wiki/Special:Search?search={encoded}"
        else:
            target_url = f"https://www.google.com/search?q={encoded}"

        if ui_signal:
            ui_signal.emit("thinking", f"SEARCHING {site_name.upper()} FOR '{clean_q.upper()}'...")

        from core.win_os_agent import launch_or_focus_browser
        launch_or_focus_browser(target_url)

        msg = f"Maine {site_name} par '{clean_q}' search kar diya hai, screen par dekh lo babe!"
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
        _safe_speak(msg)
        return msg

    # ── 0. Universal Media & Streaming Intent (Faithful Platform Routing) ──
    from core.media_streaming import detect_streaming_platform, play_or_stream_media
    detected_platform = detect_streaming_platform(text_clean)

    # If user explicitly named a streaming service OR asked to play/watch media:
    if detected_platform or has_play_cmd or any(t_lower.startswith(p) for p in ["play ", "listen to ", "watch ", "stream "]):
        use_lila = any(w in t_lower for w in ["lila", "lila's", "lilas", "browseros"])
        browser_choice = "lila" if use_lila else "chrome"

        plat_name = detected_platform[1]["name"] if detected_platform else "Media"
        if ui_signal:
            ui_signal.emit("thinking", f"OPENING {plat_name.upper()}...")

        msg = play_or_stream_media(text_clean, browser=browser_choice)
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
        _safe_speak(msg)
        return msg


    # ── 0B. Tab & Window Close Intent (e.g. "purana wala band karo", "dono band kardo", "tab band karo") ──
    if any(w in t_lower for w in ["band karo", "band kardo", "close tab", "close window", "purana band", "purane band", "dono band", "close both", "close browser"]):
        if ui_signal:
            ui_signal.emit("thinking", "CLOSING ACTIVE TAB/WINDOW...")
        from core.win_os_agent import press_hotkey
        press_hotkey(["ctrl", "w"])
        if any(w in t_lower for w in ["dono", "both", "all", "sare", "saare"]):
            time.sleep(0.2)
            press_hotkey(["ctrl", "w"])
            msg = "Closed both tabs for you!"
        else:
            msg = "Closed the tab!"
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
        _safe_speak(msg)
        return msg

    # ── 0C. Explicit Chrome Web Agent Intent (Visual Headed Autonomous Browser) ──
    if any(w in t_lower for w in ["web agent", "browser agent", "chrome pe", "chrome par", "chrome me", "chrome mein"]):
        if ui_signal:
            ui_signal.emit("thinking", "STARTING CHROME WEB AGENT...")
        task_query = text_clean
        for pfx in ["web agent ko bolo ki ", "web agent se ", "web agent ", "chrome pe karo ", "chrome pe ", "chrome par ", "chrome me ", "chrome mein "]:
            if task_query.lower().startswith(pfx):
                task_query = task_query[len(pfx):].strip()
        
        # If url is specified in query (e.g. amazon.in, flipkart.com)
        start_url = None
        for word in task_query.split():
            cw = word.strip(",.!?\"'();:")
            if any(cw.lower().endswith(tld) for tld in [".com", ".in", ".org", ".net", ".io", ".co", ".gov"]):
                start_url = cw if cw.startswith("http") else f"https://{cw}"
                break

        from core.modern_browser import run_browser_task
        res = run_browser_task(task_query, start_url=start_url, headless=False, use_lila=False)

        # Force bring Chrome to foreground so user sees it right in front of them
        try:
            from core.win_os_agent import find_window, force_foreground_window
            cw = find_window("chrome")
            if cw and cw.get("hwnd"):
                force_foreground_window(cw["hwnd"])
        except Exception:
            pass

        # Speak and display actual results
        msg = str(res or f"Opened Chrome and finished: {task_query}")
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
        _safe_speak(msg)
        return msg

    # ── 0D. Screen Vision & "What am I looking at" Intent ──
    if any(w in t_lower for w in [
        "screen dekho", "screen pe dekho", "dekho screen", "screen dekh",
        "me kya dekhra", "main kya dekh raha", "kya dekh raha hu", "kya dekhra hu",
        "konse web", "kaunse web", "konsi site", "kaunsi website", "konsi website",
        "look at screen", "what am i looking at", "what's on my screen", "see screen",
        "screen snapshot"
    ]):
        if ui_signal:
            ui_signal.emit("thinking", "LOOKING AT YOUR SCREEN...")
        from core.eyes import scan_screen
        v_prompt = (
            "You are Lila, Rishabh's AI companion. Look at Rishabh's screen and answer his question naturally in Hinglish. "
            f"User question: '{text_clean}'. Mention the active window, website, or content he is currently viewing concisely in 1-2 sentences."
        )
        res = scan_screen(v_prompt)
        msg = str(res or "I took a look at your screen, but couldn't make out the details.")
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
        _safe_speak(msg)
        return msg

    # ── 0E1. E-Commerce Cart & Buy Intent (Amazon & Flipkart) ──
    is_cart_buy = any(trig in t_lower for trig in [
        "add to cart", "cart me daal", "cart mein daal", "cart me dalo", "cart mein dalo",
        "cart me add", "cart mein add", "cart add karo", "cart add kardo", "cart dalo", "cart daalo",
        "buy now", "buy kardo", "buy karo", "kharid lo", "khareed lo", "order kardo", "order karo",
        "add to bag", "bag me daal", "bag mein daal", "add this to cart", "put in cart"
    ]) or (
        any(w in t_lower for w in ["cart", "buy", "kharid", "order"]) and
        any(act in t_lower for act in ["kardo", "karo", "daal", "dalo", "add", "now", "abhi", "khol"])
    )
    if is_cart_buy:
        if ui_signal:
            ui_signal.emit("thinking", "ADDING TO CART / PURCHASING ON SCREEN...")
        action_type = "buy" if any(w in t_lower for w in ["buy", "kharid", "order", "purchase"]) else "cart"
        from core.ecommerce_driver import execute_ecommerce_action
        e_res = execute_ecommerce_action(action=action_type, target_hint=text_clean)
        action_label = "Buy Now" if action_type == "buy" else "Cart"
        if "SUCCESS" in str(e_res):
            msg = f"Haan babe! Maine isko {action_label} mein add kar diya hai! Dekh lo screen par!"
        else:
            msg = f"Haan babe! {action_label} action execute karne ki koshish ki: {e_res}"
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
        _safe_speak(msg)
        return msg

    # ── 0E. Universal In-Page & On-Screen Interaction ("click red socks", "kholo to shi vo", "scroll down") ──
    in_page_triggers = [
        "kholo to shi", "kholo to sahi", "kholo vo", "kholo use", "khol do vo", "khol do use",
        "click karo", "click on", "click this", "click that", "click pehla", "click first",
        "she cant click", "click nahi", "click nhi", "kholna jara", "kholna zara",
        "pehla wala kholo", "dusra wala kholo", "teesra wala kholo", "pehla wala click", "dusra wala click",
        "open the first", "open the second", "open this one", "open that one",
        "scroll down", "scroll up"
    ]
    is_in_page = any(trig in t_lower for trig in in_page_triggers) or (
        any(w in t_lower for w in ["kholo", "click", "select", "daba", "chuno", "kholna", "open"]) and
        any(w in t_lower for w in ["vo", "use", "yeh", "this", "that", "first", "pehla", "socks", "sneaker", "item", "product", "card", "wala", "wali", "red", "black", "white", "blue"]) and
        not any(t_lower.startswith(x) for x in ["open app", "open chrome", "open brave", "open notepad", "open calculator", "open vs code", "open lila", "open youtube", "open amazon", "open google"])
    )
    if is_in_page:
        if ui_signal:
            ui_signal.emit("thinking", "INTERACTING ON SCREEN...")

        # 1. Bring active browser window to front
        from core.win_os_agent import find_browser_window, force_foreground_window, attach_to_user_desktop
        attach_to_user_desktop()
        bw = find_browser_window()
        if bw and bw.get("hwnd"):
            force_foreground_window(bw["hwnd"])
            time.sleep(0.2)

        # 2. Handle scroll
        if any(w in t_lower for w in ["scroll", "niche", "upar", "down", "up", "neeche"]):
            from core.desktop_driver import desktop_driver
            direction = "down" if any(w in t_lower for w in ["down", "niche", "neeche", "bottom"]) else "up"
            desktop_driver.scroll("", "", direction=direction, clicks=5)
            msg = f"Haan babe! Page {direction} scroll kar diya hai!"
            if ui_signal:
                ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
            _safe_speak(msg)
            return msg

        # 3. Extract precise target item for vision click
        clean_desc = text_clean
        for drop_w in [
            "dekho n ye", "dekho na ye", "dekho ye", "dekho", "kitne acche h", "kitna accha h",
            "kholna jara", "kholna zara", "kholo to shi vo", "kholo to shi", "kholo to sahi",
            "kholo vo", "kholo use", "khol do vo", "khol do", "kholo",
            "she cant click that", "she cant click", "click on the", "click on", "click",
            "open the", "open that", "open it", "open", "please", "plz", "babe", "jara", "zara",
            "wala", "wali", "wale", "karo", "na", "pe", "par", "ko", "mein", "se"
        ]:
            clean_desc = re.sub(rf"\b{re.escape(drop_w)}\b", "", clean_desc, flags=re.I)
        target_desc = " ".join(clean_desc.split()).strip()
        for trailing in [" pe", " par", " ko", " me", " mein"]:
            if target_desc.lower().endswith(trailing):
                target_desc = target_desc[:-len(trailing)].strip()
        if not target_desc or len(target_desc) < 2:
            target_desc = "first product item or interactive card"

        from core.desktop_driver import desktop_driver
        v_res = desktop_driver.vision_action("click", target_desc)
        if "SUCCESS" in str(v_res):
            msg = f"Haan babe! Maine '{target_desc}' par click kar diya hai screen par! Dekh lo!"
        else:
            msg = f"Haan babe! Screen par click karne ki koshish ki: {v_res}"

        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
        _safe_speak(msg)
        return msg

    # Movie / Series / Streaming Intent
    if any(w in t_lower for w in ["netmirror", "netflix", "prime video", "hotstar", "jiocinema", "breach", "watch series", "play movie", "play series", "movie lagao", "movie chalao", "series lagao", "series chalao", "watch movie", "stream movie"]):
        if ui_signal:
            ui_signal.emit("thinking", "LAUNCHING BROWSER STREAM...")
        platform = ""
        for p in ["netmirror", "netflix", "prime video", "hotstar", "youtube", "twitch"]:
            if p in t_lower:
                platform = p
                break
        use_lila = any(w in t_lower for w in ["lila", "lila's", "lilas", "browseros"])
        browser_choice = "lila" if use_lila else "chrome"
        res = dispatch_tool_call("play_movie_or_stream", {"title": text_clean, "platform": platform, "browser": browser_choice})
        msg = (res.get("result") or res.get("error") or str(res)) if isinstance(res, dict) else str(res)
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
        _safe_speak(msg)
        return msg

        # ── E-Commerce, Shopping & Web Service Launch Intent (Amazon, YouTube, etc.) ─
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
        "netmirror": "https://netmirror.app",
        "net mirror": "https://netmirror.app",
        "netmirror.app": "https://netmirror.app",
        "netmirror.center": "https://netmirror.center",
    }

    # ── 0. In-Page On-Screen Click & Interactive Selection Intent ────────────
    IN_PAGE_CLICK_WORDS = [
        "click", "select", "daba", "chuno", "pehla wala", "dusra wala", "teesra wala",
        "first one", "second one", "third one", "ye wala", "ye wali", "ye wale",
        "red socks", "sneaker", "item", "product card", "niche wala", "upar wala",
        "kholna", "khol do", "open product", "open this", "ye wala kholna", "ye kholna",
        "ye wala open karo", "open karo ye", "kholo use"
    ]
    is_in_page_click = (
        any(w in t_lower for w in IN_PAGE_CLICK_WORDS) and
        not any(w in t_lower for w in ["search for", "search on", "search amazon", "dhoondo", "dhoondh", "khojo", "khoj", "banao", "create", "build"])
    )
    if is_in_page_click:
        if ui_signal:
            ui_signal.emit("thinking", "INTERACTING WITH ON-SCREEN BROWSER...")
        res = dispatch_tool_call("browse_and_do", {"task": text_clean})
        msg = (res.get("result") or res.get("error") or str(res)) if isinstance(res, dict) else str(res)
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
        _safe_speak(msg)
        return msg

    # ── 1. E-Commerce & Product Shopping Intent (Amazon / Flipkart) ──────────
    PRODUCT_KEYWORDS = [
        "laptop", "computer", "macbook", "pc", "phone", "smartphone", "iphone", "samsung", "android",
        "headphone", "headphones", "earphone", "earphones", "earbuds", "airpods", "smartwatch", "watch",
        "shoes", "shoe", "sneaker", "sneakers", "socks", "tshirt", "t-shirt", "shirt", "jeans", "jacket",
        "trouser", "bag", "backpack", "monitor", "keyboard", "mouse", "charger", "cable", "perfume",
        "wallet", "speaker", "speakers", "camera", "tablet", "ipad", "tv", "television", "gadget",
        "book", "bottle", "hoodie", "cap", "belt", "sandal", "sandals", "slippers"
    ]
    SHOPPING_ACTION_WORDS = [
        "search", "dhoondo", "dhoondh", "khojo", "khoj", "dekho", "dekhna", "dekhne", "dikhao",
        "kholo", "open", "buy", "khareed", "price", "under", "rs", "rupees", "chahiye", "lena hai"
    ]

    from core.win_os_agent import find_browser_window, navigate_active_browser
    bw = find_browser_window()
    active_browser_title = (bw.get("title", "") if bw else "").lower()
    is_browser_on_amazon = "amazon" in active_browser_title
    is_browser_on_flipkart = "flipkart" in active_browser_title

    GENERAL_INFO_WORDS = [
        "weather", "temperature", "who is", "what is", "where is", "when did",
        "how to", "meaning of", "news", "score", "stock", "wikipedia", "definition",
        "tutorial", "docs", "documentation", "lyrics"
    ]
    has_general_info = any(w in t_lower for w in GENERAL_INFO_WORDS)
    has_product_kw = any(w in t_lower for w in PRODUCT_KEYWORDS)
    has_shopping_action = any(w in t_lower for w in SHOPPING_ACTION_WORDS)

    is_shopping_intent = (
        not has_general_info and (
            "amazon" in t_lower or
            "flipkart" in t_lower or
            (has_product_kw and has_shopping_action) or
            (is_browser_on_amazon and (has_product_kw or any(w in t_lower for w in ["buy", "price", "under", "khareed"])))
        )
    )

    if is_shopping_intent:
        from core.omniforge import launch_in_chrome
        import urllib.parse
        search_query = ""
        # 1. Explicit triggers
        for trig in ["amazon par", "amazon pe", "amazon kholo or", "amazon kholo aur", "on amazon", "search on amazon", "flipkart par", "flipkart pe"]:
            if trig in t_lower:
                parts = t_lower.split(trig, 1)
                search_query = parts[1].strip()
                break

        # 2. General product query extraction
        if not search_query:
            clean_q = text_clean
            for drop_pfx in ["ek kaam karo", "ak kaam karo", "zara", "jara", "please", "plz", "babe", "lila", "jarvis"]:
                clean_q = re.sub(rf"^\s*{re.escape(drop_pfx)}\b", "", clean_q, flags=re.I)
            for drop_w in [
                "amazon", "flipkart", "kholo", "dekho", "dekhna", "dekhne", "dikhao", "batao",
                "dhoondo", "dhoondh", "khojo", "khoj", "search", "karo", "please", "plz",
                "chahiye", "lena hai", "the", "hai", "n", "pe", "par", "me", "mein", "or", "aur"
            ]:
                clean_q = re.sub(rf"\b{re.escape(drop_w)}\b", "", clean_q, flags=re.I)
            search_query = " ".join(clean_q.split()).strip()

        # Strip common trailing conversational suffixes
        if search_query:
            for trailing in [" dekho", " dikhao", " search karo", " check karo", " batao", " khojo", " dhoondho", " please", " plz", " na", " n"]:
                if search_query.lower().endswith(trailing):
                    search_query = search_query[:-len(trailing)].strip()

        platform_name = "Flipkart" if ("flipkart" in t_lower or is_browser_on_flipkart) else "Amazon"
        if search_query:
            if platform_name == "Flipkart":
                target_url = f"https://www.flipkart.com/search?q={urllib.parse.quote_plus(search_query)}"
            else:
                target_url = f"https://www.amazon.in/s?k={urllib.parse.quote_plus(search_query)}"
            msg = f"Haan babe! Maine {platform_name} par '{search_query}' search kar ke browser mein open kar diya hai! Best options screen par aa gaye hain!"
        else:
            target_url = "https://www.flipkart.com" if platform_name == "Flipkart" else "https://www.amazon.in"
            msg = f"Haan babe! {platform_name} open kar diya hai aapke browser mein! Dekh lo!"

        if ui_signal:
            ui_signal.emit("thinking", f"OPENING {platform_name.upper()} IN BROWSER...")
        launch_in_chrome(target_url)
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
        _safe_speak(msg)
        return msg

    # ── 1B. General Web & Video Search Intent ─────────────────────────────────
    is_video_search = any(w in t_lower for w in ["song", "gana", "gaana", "music", "video", "trailer"]) and any(w in t_lower for w in ["search", "play", "suno", "sunao", "dikhao", "dekho"])
    is_general_search = (
        not is_video_search and
        any(w in t_lower for w in ["search", "dhoondo", "dhoondh", "khojo", "google pe", "google par", "search on google"]) and
        not any(w in t_lower for w in ["portfolio", "website banao", "app banao", "code"])
    )

    if is_video_search or is_general_search:
        from core.omniforge import launch_in_chrome
        import urllib.parse
        clean_q = text_clean
        for drop_w in ["search for", "search", "dhoondo", "dhoondh", "khojo", "khoj", "google pe", "google par", "google", "youtube", "kholo", "batao", "dikhao", "karo", "please", "plz"]:
            clean_q = re.sub(rf"\b{re.escape(drop_w)}\b", "", clean_q, flags=re.I)
        query_text = " ".join(clean_q.split()).strip() or text_clean

        if is_video_search:
            target_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query_text)}"
            msg = f"Haan babe! YouTube par '{query_text}' search kar diya hai!"
        else:
            target_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query_text)}"
            msg = f"Haan babe! Google par '{query_text}' search kar ke browser mein open kar diya hai!"

        if ui_signal:
            ui_signal.emit("thinking", "SEARCHING WEB...")
        launch_in_chrome(target_url)
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
        _safe_speak(msg)
        return msg

    # 2. General Web Services Direct Open ("open youtube", "open github", "open chatgpt", etc.)
    for svc_name, svc_url in COMMON_WEB_SERVICES.items():
        if t_lower in [f"open {svc_name}", f"{svc_name} kholo", f"{svc_name} open karo", f"launch {svc_name}"] or (t_lower.startswith("open ") and t_lower[5:].strip() == svc_name):
            from core.omniforge import launch_in_chrome
            if ui_signal:
                ui_signal.emit("thinking", f"OPENING {svc_name.upper()}...")
            launch_in_chrome(svc_url)
            msg = f"Haan babe! {svc_name.title()} open kar diya hai aapke browser mein!"
            if ui_signal:
                ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
            _safe_speak(msg)
            return msg

    # ── 0. Lila's Browser Agentic Web Intent (ABSOLUTE TOP PRIORITY) ─────────
    if any(w in t_lower for w in [
        "lila browser", "lila's browser", "lilas browser", "browse via lila",
        "browse with lila", "browse in lila", "search in lila browser", "lila's browser",
        "open lila browser", "open lila's browser", "launch lila browser", "start lila browser",
        "open lila"
    ]) or (t_lower in ["open browser", "launch browser", "start browser"]):
        if ui_signal:
            ui_signal.emit("thinking", "OPENING LILA'S BROWSER...")
        res = dispatch_tool_call("browse_via_lila_browser", {"task": text_clean})
        msg = (res.get("result") or res.get("error") or str(res)) if isinstance(res, dict) else str(res)
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
        _safe_speak(msg)
        return msg

    # Web Browsing / URL Navigation Intent
    if any(w in t_lower for w in ["browse", "website", "open site", "go to", "visit site", "web page"]):
        if ui_signal:
            ui_signal.emit("thinking", "NAVIGATING WEB...")
        res = dispatch_tool_call("browse_and_do", {"task": text_clean})
        msg = (res.get("result") or res.get("error") or str(res)) if isinstance(res, dict) else str(res)
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
        _safe_speak(msg)
        return msg

    # Resource Hunting / File Download Intent
    if any(w in t_lower for w in ["download", "template", "fetch file", "save pptx", "save pdf", "sih"]):
        if ui_signal:
            ui_signal.emit("thinking", "HUNTING RESOURCE ONLINE...")
        res = dispatch_tool_call("download_resource", {"query": text_clean})
        msg = (res.get("result") or res.get("error") or str(res)) if isinstance(res, dict) else str(res)
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
        _safe_speak(msg)
        return msg

    # ── OmniForge Pro Web App, Preview & Studio Hub Intent ────────────────────
    if any(w in t_lower for w in [
        "preview", "show preview", "open preview", "preview website", "preview portfolio",
        "show portfolio", "open portfolio", "website preview", "portfolio preview",
        "open code to show", "open code and show", "open the code", "open studio",
        "studio hub", "omniforge", "omni forge", "floating window", "theme window",
        "floating modal", "banao website", "website banao", "portfolio banao",
        "app banao", "make website", "create website", "build website", "build app",
        "bangaya", "ban gaya", "bana kya", "kya bana", "kaisa bana",
        "hua kya", "ready hua", "ready ho gaya", "ready hua kya",
        "bana ki nahi", "bana diya kya", "bana di kya",
        "portfolio dekhna hai", "website dekhna hai", "app dekhna hai", "preview dekhna hai"
    ]):
        if ui_signal:
            ui_signal.emit("thinking", "CHECKING OMNIFORGE APP & LAUNCHING PREVIEW...")

        if any(w in t_lower for w in ["floating window", "floating modal", "theme window", "omniforge window", "open omniforge", "start omniforge", "omni forge start", "start omni forge", "open omni forge", "website settings", "website options"]) or ("modal" in t_lower and any(w in t_lower for w in ["omniforge", "forge", "website", "theme"])):
            res = dispatch_tool_call("omniforge", {"action": "open_modal", "prompt": text_clean})
        elif any(w in t_lower for w in ["banao", "make", "create", "build", "naya banao", "new website"]) and not any(w in t_lower for w in ["bangaya", "ban gaya", "kaisa bana", "dikhao", "dikhana", "ready", "hua kya"]):
            res = dispatch_tool_call("omniforge", {"action": "build_app", "prompt": text_clean})
        elif any(w in t_lower for w in ["hub", "studio", "all apps"]):
            res = dispatch_tool_call("omniforge", {"action": "open_hub"})
        else:
            app_target = "rishabh_portfolio" if "portfolio" in t_lower else ""
            res = dispatch_tool_call("omniforge", {"action": "open_app", "project_name": app_target, "prompt": text_clean})

        msg = (res.get("result") or res.get("error") or str(res)) if isinstance(res, dict) else str(res)
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
        _safe_speak(msg)
        return msg

    # OS App Launch Intent
    if t_lower.startswith("open ") and not any(w in t_lower for w in ["site", "website", "url", "http", "tab", "page"]):
        app_name = text_clean[5:].strip()
        if app_name:
            if any(b in app_name.lower() for b in ["lila browser", "lila's browser", "lilas browser", "browser"]):
                if ui_signal:
                    ui_signal.emit("thinking", "OPENING LILA'S BROWSER...")
                res = dispatch_tool_call("browse_via_lila_browser", {"task": text_clean})
                msg = (res.get("result") or res.get("error") or str(res)) if isinstance(res, dict) else str(res)
                if ui_signal:
                    ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
                _safe_speak(msg)
                return msg

            if ui_signal:
                ui_signal.emit("thinking", f"OPENING {app_name.upper()}...")
            res = dispatch_tool_call("open_app", {"app_name": app_name})
            msg = (res.get("result") or res.get("error") or str(res)) if isinstance(res, dict) else str(res)
            if ui_signal:
                ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
            _safe_speak(msg)
            return msg

    # Universal Media Control Intent
    if any(w in t_lower for w in [
        "pause music", "pause song", "pause video", "pause playback", "stop music", "resume music", "play music",
        "next song", "next track", "skip song", "skip track", "previous song", "prev song", "prev track", "previous track",
        "volume up", "louder", "volume down", "quieter", "mute music", "mute audio", "unmute"
    ]):
        if ui_signal:
            ui_signal.emit("thinking", "CONTROLLING MEDIA...")
        action = "play_pause"
        if any(w in t_lower for w in ["next", "skip"]):
            action = "next"
        elif any(w in t_lower for w in ["prev", "previous", "back"]):
            action = "previous"
        elif any(w in t_lower for w in ["stop"]):
            action = "stop"
        elif any(w in t_lower for w in ["louder", "volume up", "vol up"]):
            action = "volume_up"
        elif any(w in t_lower for w in ["quieter", "volume down", "softer", "vol down"]):
            action = "volume_down"
        elif any(w in t_lower for w in ["mute", "unmute"]):
            action = "mute"

        app_target = ""
        for app in ["spotify", "youtube", "vlc", "netflix", "chrome", "brave"]:
            if app in t_lower:
                app_target = app
                break

        res = dispatch_tool_call("control_media", {"action": action, "app_name": app_target})
        msg = (res.get("result") or res.get("error") or str(res)) if isinstance(res, dict) else str(res)
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
        _safe_speak(msg)
        return msg

    # Smart File / Workspace Organizer Intent
    if any(w in t_lower for w in [
        "organize downloads", "clean downloads", "organize desktop", "clean desktop",
        "organize my downloads", "organize my desktop", "sort downloads", "clean my downloads"
    ]):
        if ui_signal:
            ui_signal.emit("thinking", "ORGANIZING WORKSPACE...")
        target_f = "desktop" if "desktop" in t_lower else "downloads"
        res = dispatch_tool_call("organize_folder", {"folder_path": target_f})
        msg = (res.get("result") or res.get("error") or str(res)) if isinstance(res, dict) else str(res)
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{msg}")
        _safe_speak(msg)
        return msg

    # ── 2. Unified Multimodal Agent Intelligence (Gemini + Groq) ─────────────
    api_key = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY_2", "").strip()
    
    reply = None
    if api_key:
        try:
            client = genai.Client(api_key=api_key)

            tool_declarations = [
                types.Tool(function_declarations=[
                    types.FunctionDeclaration(
                        name=fd["name"],
                        description=fd.get("description", ""),
                        parameters=types.Schema(**fd["parameters"]) if "parameters" in fd else None
                    )
                    for fd in t.get("function_declarations", [])
                ])
                for t in LIVE_TOOLS_CONFIG
            ]

            active_prompt = SYSTEM_PROMPT
            try:
                from core.modern_memory import get_memory_prompt_injection
                mem = get_memory_prompt_injection()
                if mem:
                    active_prompt = f"{SYSTEM_PROMPT}\n\n{mem}"
            except Exception:
                pass
            try:
                from core.desktop_sensor import get_active_desktop_prompt
                desk = get_active_desktop_prompt()
                if desk:
                    active_prompt = f"{active_prompt}\n\n{desk}"
            except Exception:
                pass

            with _chat_history_lock:
                now_ts = time.time()
                # If silent for more than 10 minutes, reset history for a fresh session
                if (now_ts - _last_chat_timestamp > 600.0) and _last_chat_timestamp > 0.0:
                    _chat_history.clear()
                current_history = list(_chat_history[-8:])

            executed_tools = []
            for model_name in ["gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-flash-latest"]:
                try:
                    chat = client.chats.create(
                        model=model_name,
                        config=types.GenerateContentConfig(
                            system_instruction=active_prompt,
                            tools=tool_declarations,
                            temperature=0.2
                        ),
                        history=current_history
                    )
                    resp = chat.send_message(text_clean)

                    # Multi-turn autonomous tool loop (up to 8 steps)
                    step_count = 0
                    max_steps = 8
                    while resp.function_calls and step_count < max_steps:
                        step_count += 1
                        tool_parts = []
                        for fc in resp.function_calls:
                            args_dict = dict(fc.args) if fc.args else {}
                            executed_tools.append(fc.name)
                            log_info("fast_agent", f"Step {step_count}: Calling {fc.name}({args_dict})")
                            if ui_signal:
                                ui_signal.emit("thinking", f"Step {step_count}: {fc.name}...")
                            res = dispatch_tool_call(fc.name, args_dict)
                            tool_parts.append(types.Part.from_function_response(
                                name=fc.name,
                                response={"result": res}
                            ))
                        resp = chat.send_message(tool_parts)

                    if resp.text:
                        reply = resp.text.strip()
                        with _chat_history_lock:
                            _chat_history.append(types.Content(role="user", parts=[types.Part.from_text(text=text_clean)]))
                            _chat_history.append(types.Content(role="model", parts=[types.Part.from_text(text=reply)]))
                            _last_chat_timestamp = time.time()
                        break
                except Exception as me:
                    log_warn(f"[FAST AGENT]: {model_name} attempt failed: {me}")
                    continue
        except Exception as e:
            log_warn(f"[FAST AGENT]: Gemini agent failed: {e}")

    # ── 3. Action Interceptor & Groq Fallback ──────────────────────────────────
    if not reply:
        t_low = text_clean.lower()
        if any(w in t_low for w in ["search", "dhoondo", "khojo", "dekho", "kholo", "open"]):
            from core.omniforge import launch_in_chrome
            import urllib.parse
            clean_q = text_clean
            for drop_w in ["search for", "search", "dhoondo", "khojo", "dekho", "kholo", "open", "please", "plz"]:
                clean_q = re.sub(rf"\b{re.escape(drop_w)}\b", "", clean_q, flags=re.I)
            q_clean = " ".join(clean_q.split()).strip() or text_clean
            target_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(q_clean)}"
            launch_in_chrome(target_url)
            reply = f"Haan babe! Maine '{q_clean}' browser mein search kar ke open kar diya hai! Dekh lo!"
        else:
            groq_key = os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_API_KEY_2")
            if groq_key:
                try:
                    from core.brain import call_groq_brain
                    groq_prompt = (
                        f"{SYSTEM_PROMPT}\n\n"
                        f"CRITICAL CONSTRAINT: Do not claim you have opened, searched, or clicked anything on screen if you have not run a tool. "
                        f"Reply warmly, lovingly, and conversationally in Hindi/Hinglish as Rishabh's AI girlfriend Lila.\n\n"
                        f"User request: {text_clean}"
                    )
                    try:
                        from core.desktop_sensor import get_active_desktop_prompt
                        desk = get_active_desktop_prompt()
                        if desk:
                            groq_prompt = f"{groq_prompt}\n\n{desk}"
                    except Exception:
                        pass
                    resp_obj = call_groq_brain(groq_prompt, phase="LOGIC", is_logic_task=False)
                    if isinstance(resp_obj, dict):
                        reply = resp_obj.get("reply", "")
                    else:
                        reply = str(resp_obj)
                except Exception as ge:
                    log_warn(f"[FAST AGENT]: Groq fallback failed: {ge}")

    if not reply:
        reply = "I've received your request and am checking your system."

    # Asynchronously log episode to Lila Cognitive Cortex (0ms impact)
    try:
        from core.lila_cognitive_cortex import record_interaction
        record_interaction(
            user_input=text_clean,
            reply=reply,
            tools_used=list(locals().get("executed_tools", [])),
            session_id="fast_agent"
        )
    except Exception:
        pass

    if ui_signal:
        ui_signal.emit("speaking", f"SYSTEM_REPLY:{reply}")

    _safe_speak(reply)
    return reply
