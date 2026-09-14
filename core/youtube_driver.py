"""
core/youtube_driver.py — High-Reliability Universal YouTube Driver
==================================================================
Empowers JARVIS & Lila to reliably play any song, artist, video, or movie
on YouTube directly inside the active browser (Brave / Chrome / Edge)
with zero failed navigations, visible desktop focus, and auto-playback.
"""

import os
import re
import json
import time
import urllib.request
import urllib.parse
from typing import Optional, Tuple, Dict, Any

from core.jarvis_logger import log_info, log_warn, log_error

DEFAULT_FALLBACK_QUERY = "lo-fi chill beats"

FAVORITE_KEYWORDS = {
    "favourite", "favorite", "fav", "mera favourite", "meri favourite", "mere favourite",
    "mera favorite", "meri favorite", "mere favorite", "favourite song", "favorite song",
    "favourite gaana", "favourite gana", "mera fav", "meri fav", "favourite list",
    "meri favourite list", "playlist", "meri playlist", "accha wala", "accha sa",
    "kuch accha", "accha gaana", "accha gana", "accha song", "koi accha", "best song",
    "top song", "hit song", "pasandida", "mera pasandida", "favourite wala", "achha wala",
    "favourite songs", "favorite songs", "mere favourite songs",
    "अच्छा वाला", "अच्छा गाना", "मेरा फेवरेट", "फेवरेट", "पसंदीदा", "एक गाना"
}

def get_user_favorite_music() -> str:
    """Retrieve user's favorite song from memory or fall back to high-affinity Bollywood hit."""
    try:
        import sqlite3
        db_paths = [
            "D:/jarvis_project/data/lila_memory.db",
            os.path.expanduser(r"~\Downloads\jarvis_project\data\lila_memory.db"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "lila_memory.db")
        ]
        for db_path in db_paths:
            if os.path.exists(db_path):
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()
                cur.execute("SELECT value FROM facts WHERE category IN ('preference', 'profile') AND (value LIKE '%song%' OR value LIKE '%music%') ORDER BY id DESC LIMIT 10")
                for (val,) in cur.fetchall():
                    m = re.search(r"'(.*?)'", val)
                    if m:
                        clean = m.group(1).strip()
                        if clean and len(clean) > 3 and clean.lower() not in ["music", "song"]:
                            return f"{clean} Atif Aslam" if "tera hone laga" in clean.lower() else clean
    except Exception:
        pass
    return "Tera Hone Laga Hoon Atif Aslam"

def is_favorite_or_general_query(q: str) -> bool:
    """Check if query is asking for user's favorite song or a general good song rather than a specific title."""
    q_low = (q or "").strip().lower()
    if not q_low or q_low in ["", "youtube", "music", "song", "video", "gaana", "gana", "videos", "songs"]:
        return True
    if q_low in FAVORITE_KEYWORDS:
        return True
    for kw in FAVORITE_KEYWORDS:
        if kw in q_low:
            return True
    return False


def clean_youtube_search_query(raw_query: str) -> str:
    """Sanitize spoken or typed input into a clean YouTube search keyword without playback triggers."""
    clean_q = (raw_query or "").strip()
    if not clean_q:
        return "trending"

    prefixes = [
        "search on youtube for video of ", "search on youtube for videos of ",
        "search on youtube for video ", "search on youtube for videos ",
        "search on youtube for ", "search on youtube of ", "search on youtube ",
        "search youtube for video of ", "search youtube for video ", "search youtube for ", "search youtube ",
        "search video on youtube for ", "search video on youtube of ", "search video on youtube ",
        "search videos on youtube for ", "search videos on youtube of ", "search videos on youtube ",
        "youtube pe search karo ", "youtube par search karo ",
        "youtube pe search kardo ", "youtube par search kardo ",
        "youtube pe search ", "youtube par search ", "youtube search ",
        "youtube pe dikhao ", "youtube par dikhao ", "youtube pe ", "youtube par ",
        "search for video of ", "search for video ", "search for ", "search video of ", "search video ", "search videos ", "search ",
        "dhundo ", "dhoondo ", "dikhao ", "dikha do "
    ]

    suffixes = [
        " on youtube", " in youtube", " youtube pe", " youtube par", " on yt",
        " video on youtube", " videos on youtube", " video", " videos",
        " search karo on youtube", " search kardo on youtube", " search karo", " search kardo", " search",
        " dikhao on youtube", " dikhao", " dikha do on youtube", " dikha do",
        " chalao mat", " play mat karna", " sirf search", " only search",
        " please", " now"
    ]

    changed = True
    while changed:
        changed = False
        q_lower = clean_q.lower()
        for p in prefixes:
            if q_lower.startswith(p):
                clean_q = clean_q[len(p):].strip()
                changed = True
                break
        q_lower = clean_q.lower()
        for s in suffixes:
            if q_lower.endswith(s):
                clean_q = clean_q[:-len(s)].strip()
                changed = True
                break

    for pfx in ["of ", "the ", "on youtube", "youtube"]:
        if clean_q.lower().startswith(pfx):
            clean_q = clean_q[len(pfx):].strip()

    if clean_q.lower() in ["", "youtube", "video", "videos", "music", "song", "songs"]:
        return "trending"
    return clean_q


def clean_youtube_play_query(raw_query: str) -> str:
    """Sanitize spoken or typed input into a clean YouTube play target."""
    q = (raw_query or "").strip()
    if not q:
        return "trending songs 2026"

    prefixes = [
        "play on youtube for video ", "play on youtube for ", "play on youtube ",
        "play in youtube for ", "play in youtube ", "play youtube ",
        "play video of ", "play video on youtube of ", "play video on youtube ", "play video ",
        "play song of ", "play song on youtube of ", "play song on youtube ", "play song ",
        "play music of ", "play music on youtube ", "play music ",
        "play movie of ", "play movie on youtube ", "play movie ",
        "youtube pe play karo ", "youtube par play karo ", "youtube pe chalao ", "youtube par chalao ",
        "youtube pe bajao ", "youtube par bajao ", "youtube pe lagao ", "youtube par lagao ",
        "youtube pe sunao ", "youtube par sunao ", "youtube pe chala do ", "youtube pe ", "youtube par ",
        "play ", "listen to ",
        "laga do ", "lagao ", "baja do ", "bajao ", "chala do ", "chalao ", "sunao "
    ]

    suffixes = [
        " on youtube", " in youtube", " youtube pe", " youtube par", " on yt",
        " play karo on youtube", " play kardo on youtube", " play karo", " play kardo", " play",
        " chalao on youtube", " chala do on youtube", " chalao", " chala do", " chala",
        " bajao on youtube", " baja do on youtube", " bajao", " baja do", " baja",
        " lagao on youtube", " laga do on youtube", " lagao", " laga do", " laga",
        " sunao on youtube", " sunao",
        " video on youtube", " video", " videos",
        " song on youtube", " song", " gaana", " gana", " music", " track",
        " please", " now"
    ]

    changed = True
    while changed:
        changed = False
        q_lower = q.lower()
        for p in prefixes:
            if q_lower.startswith(p):
                q = q[len(p):].strip()
                changed = True
                break
        q_lower = q.lower()
        for s in suffixes:
            if q_lower.endswith(s):
                q = q[:-len(s)].strip()
                changed = True
                break

    for pfx in ["of ", "the ", "on youtube", "youtube"]:
        if q.lower().startswith(pfx):
            q = q[len(pfx):].strip()

    if is_favorite_or_general_query(q):
        return get_user_favorite_music()
    return q


def clean_youtube_query(raw_query: str) -> str:
    """Backwards-compatible alias for play query cleaning."""
    return clean_youtube_play_query(raw_query)


def resolve_youtube_video(query: str) -> Tuple[str, str]:
    """
    Resolves a search query to a direct YouTube watch URL and video title.
    Uses multi-tiered extraction:
      1. Direct YouTube search HTML with ytInitialData JSON extraction.
      2. Regular expression /watch?v= video ID scan.
      3. PyWhatKit playonyt parser fallback.
      4. Search results page fallback.
    Returns: (watch_url, video_title)
    """
    clean_q = clean_youtube_query(query)
    encoded_q = urllib.parse.quote(clean_q)
    search_url = f"https://www.youtube.com/results?search_query={encoded_q}"

    # ── Strategy 1: Modern ytInitialData JSON Extraction ──────────────────────
    try:
        req = urllib.request.Request(
            search_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        match = re.search(r'ytInitialData\s*=\s*({.*?});</script>', html)
        if match:
            data = json.loads(match.group(1))

            allow_shorts = any(w in clean_q.lower() for w in ["short", "shorts", "reel", "reels"])

            # Strategy 1A: Extract standard full-length video (videoRenderer)
            def _find_standard_vids(obj):
                if isinstance(obj, dict):
                    if "videoRenderer" in obj:
                        vr = obj["videoRenderer"]
                        vid_id = vr.get("videoId")
                        title = ""
                        raw_title = vr.get("title", {})
                        if isinstance(raw_title, dict):
                            runs = raw_title.get("runs", [])
                            if runs and isinstance(runs, list):
                                title = runs[0].get("text", "")
                            elif "simpleText" in raw_title:
                                title = raw_title["simpleText"]
                        if vid_id and len(vid_id) == 11 and not vid_id.startswith("http"):
                            if allow_shorts or ("#shorts" not in title.lower() and "#short" not in title.lower()):
                                yield vid_id, title
                    for k, v in obj.items():
                        if not allow_shorts and k in (
                            "shortsLockupViewModel", "reelItemRenderer", "reelWatchEndpoint",
                            "shortsShelfRenderer", "richShelfRenderer", "gridShelfViewModel"
                        ):
                            continue
                        yield from _find_standard_vids(v)
                elif isinstance(obj, list):
                    for item in obj:
                        yield from _find_standard_vids(item)

            for vid_id, vid_title in _find_standard_vids(data):
                if vid_id:
                    return f"https://www.youtube.com/watch?v={vid_id}", (vid_title or clean_q)

            # Strategy 1B: Fallback general extractor ignoring shorts shelves
            def _find_vids(obj):
                if isinstance(obj, dict):
                    if not allow_shorts and any(sk in obj for sk in ["reelWatchEndpoint", "shortsLockupViewModel"]):
                        return
                    if "videoId" in obj and ("title" in obj or "thumbnail" in obj):
                        title = ""
                        raw_title = obj.get("title", {})
                        if isinstance(raw_title, dict):
                            runs = raw_title.get("runs", [])
                            if runs and isinstance(runs, list):
                                title = runs[0].get("text", "")
                            elif "simpleText" in raw_title:
                                title = raw_title["simpleText"]
                        if allow_shorts or ("#shorts" not in title.lower() and "#short" not in title.lower()):
                            yield obj["videoId"], title
                    for v in obj.values():
                        yield from _find_vids(v)
                elif isinstance(obj, list):
                    for item in obj:
                        yield from _find_vids(item)

            for vid_id, vid_title in _find_vids(data):
                if vid_id and len(vid_id) == 11 and not vid_id.startswith("http"):
                    return f"https://www.youtube.com/watch?v={vid_id}", (vid_title or clean_q)

        # ── Strategy 2: Regex Scan for /watch?v= ─────────────────────────────
        regex_ids = re.findall(r'/watch\?v=([a-zA-Z0-9_-]{11})', html)
        if regex_ids:
            unique_ids = list(dict.fromkeys(regex_ids))
            return f"https://www.youtube.com/watch?v={unique_ids[0]}", clean_q

    except Exception as ex:
        log_warn("youtube_driver", f"Direct search scrape error: {ex}")

    # ── Strategy 3: PyWhatKit Fallback ────────────────────────────────────────
    try:
        import pywhatkit
        raw_url = pywhatkit.playonyt(clean_q, open_video=False)
        if raw_url:
            clean_url = raw_url.replace(r"\u0026", "&").replace("\\u0026", "&")
            return clean_url, clean_q
    except Exception as ex:
        log_warn("youtube_driver", f"pywhatkit fallback error: {ex}")

    # ── Strategy 4: Fallback to Search Results ────────────────────────────────
    return search_url, clean_q


def search_youtube(query: str) -> str:
    """
    Search YouTube and navigate the active browser to the search results page.
    GUARANTEE: Strictly searches only. Never plays any video, never clicks results.
    """
    clean_q = clean_youtube_search_query(query)
    encoded_q = urllib.parse.quote_plus(clean_q)
    target_url = f"https://www.youtube.com/results?search_query={encoded_q}"
    log_info("youtube_driver", f"search_youtube: '{clean_q}' -> {target_url}")

    nav_ok = False
    try:
        from core.win_os_agent import navigate_active_browser
        nav_ok = navigate_active_browser(target_url)
    except Exception as ex:
        log_warn("youtube_driver", f"navigate_active_browser failed: {ex}")

    if not nav_ok:
        try:
            from core.win_os_agent import launch_or_focus_browser
            nav_ok = launch_or_focus_browser(target_url)
        except Exception:
            pass

    if not nav_ok:
        try:
            os.startfile(target_url)
        except Exception:
            import webbrowser
            webbrowser.open_new(target_url)

    # Ensure browser is in the foreground
    try:
        from core.win_os_agent import find_browser_window, force_foreground_window
        time.sleep(0.4)
        bw = find_browser_window()
        if bw and bw.get("hwnd"):
            force_foreground_window(bw["hwnd"])
    except Exception:
        pass

    return f"Searched YouTube for '{clean_q}'. Results are open on your screen."


def _ensure_playback_active():
    """Checks if browser is outputting audio; if still paused after 1.5s, sends play key."""
    try:
        from pycaw.pycaw import AudioUtilities
        for s in AudioUtilities.GetAllSessions():
            if s.Process and any(b in s.Process.name().lower() for b in ["brave", "chrome", "msedge"]):
                if s.State == 1:
                    # Already actively playing audio! DO NOT press any key!
                    return
    except Exception:
        pass

    # If audio is not active, tap play/pause shortcut 'k'
    try:
        import ctypes
        ctypes.windll.user32.keybd_event(0x4B, 0, 0, 0)
        time.sleep(0.04)
        ctypes.windll.user32.keybd_event(0x4B, 0, 2, 0)
    except Exception:
        pass


def play_youtube_via_extension(url: str, title: str = "") -> Optional[dict]:
    """
    Zero-focus playback via Lila Browser Copilot Brave extension (Spec §2.2 Option A).
    Sends media.play to extension which opens active:false tab, mutes, plays, and unmutes.
    """
    try:
        from core.state_bridge import is_extension_connected, send_copilot_command_sync
        if is_extension_connected():
            log_info("youtube_driver", f"Extension connected — initiating zero-focus background playback: {url}")
            res = send_copilot_command_sync("media.play", {"url": url}, timeout=6.0)
            return res
    except Exception as ex:
        log_warn("youtube_driver", f"play_youtube_via_extension error: {ex}")
    return None


def play_youtube_video(query: str) -> str:
    """
    High-reliability YouTube playback:
    Resolves the video, navigates the active browser, ensures visibility, and starts playback.
    If query is strictly an inquiry to search without play verbs, routes to search_youtube.
    """
    raw_lower = (query or "").lower().strip()
    # Guard 0: NetMirror & Movie Streaming Intercept (Zero YouTube Hijack Guarantee)
    if "netmirror" in raw_lower or "net mirror" in raw_lower:
        log_info("youtube_driver", f"Redirecting NetMirror streaming query to dedicated NetMirror driver: '{query}'")
        from core.netmirror_driver import open_netmirror
        return open_netmirror(query)

    # Guard: If query is purely a search request without any play verbs, route to search_youtube
    play_verbs = ["play", "chalao", "chala do", "chala ", "bajao", "baja do", "baja ", "lagao", "laga do", "laga ", "sunao", "listen to"]
    has_play = any(v in raw_lower for v in play_verbs)
    is_search = any(s in raw_lower for s in ["search", "dhundo", "dhoondo", "look up", "find", "dikhao"]) or raw_lower.startswith("youtube pe search")
    if is_search and not has_play:
        log_info("youtube_driver", f"Redirecting search-only query to search_youtube: '{query}'")
        return search_youtube(query)

    clean_q = clean_youtube_play_query(query)
    log_info("youtube_driver", f"Playing query='{query}' (cleaned='{clean_q}')")

    target_url, title = resolve_youtube_video(clean_q)
    log_info("youtube_driver", f"Resolved YouTube target: {target_url} ('{title}')")

    # 0. Zero-Focus Playback via Lila Browser Copilot Extension (Spec §2.2 Option A)
    ext_res = play_youtube_via_extension(target_url, title)
    if ext_res and ext_res.get("ok"):
        log_info("youtube_driver", f"Zero-focus playback started via Lila Browser Copilot extension: {ext_res}")
        return f"Haan babe! '{title}' background mein play kar diya hai! 🎵💕"

    # 1. Primary: Navigate active browser (Brave / Chrome / Edge)
    nav_ok = False
    try:
        from core.win_os_agent import navigate_active_browser
        nav_ok = navigate_active_browser(target_url)
    except Exception as ex:
        log_warn("youtube_driver", f"navigate_active_browser failed: {ex}")

    # 2. Secondary: Launch in Chrome / default browser if active navigation failed
    if not nav_ok:
        try:
            from core.omniforge import launch_in_chrome
            nav_ok = launch_in_chrome(target_url)
        except Exception as ex:
            log_warn("youtube_driver", f"launch_in_chrome failed: {ex}")

    # 3. Tertiary: OS Shell fallback
    if not nav_ok:
        try:
            os.startfile(target_url)
            nav_ok = True
        except Exception:
            import webbrowser
            webbrowser.open_new(target_url)
            nav_ok = True

    # 4. Give browser a moment to load, ensure foreground, and verify playback is active
    try:
        from core.win_os_agent import find_browser_window, force_foreground_window, get_app_focus_status
        time.sleep(0.4)
        st = get_app_focus_status("brave")
        if st["status"] in ("background", "minimized") and st.get("hwnd"):
            force_foreground_window(st["hwnd"])
        time.sleep(1.0)
        _ensure_playback_active()
    except Exception:
        pass

    return f"Haan babe! '{title}' play kar diya hai! 🎵💕"
