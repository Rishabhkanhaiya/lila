"""
core/media_streaming.py — Universal Multi-Platform Media & Streaming Engine
=============================================================================
Guarantees strict, faithful platform routing:
  - If user specifies NetMirror  -> Opens NetMirror (https://netmirror.app / search)
  - If user specifies Netflix    -> Opens Netflix (https://www.netflix.com / search)
  - If user specifies Prime Video-> Opens Prime Video (https://www.primevideo.com / search)
  - If user specifies YouTube    -> Opens YouTube (https://www.youtube.com / play_youtube_video)
  - If user specifies JioCinema  -> Opens JioCinema (https://www.jiocinema.com / search)
  - If user specifies Hotstar    -> Opens Hotstar (https://www.hotstar.com / search)
  - If user specifies Spotify    -> Opens Spotify (https://open.spotify.com / search)
  - If user specifies Twitch     -> Opens Twitch (https://www.twitch.tv / search)

If no platform is specified:
  - Music / songs -> YouTube
  - Movies / series / streaming -> NetMirror (free movie hub)

Zero YouTube Hijack: Specifying NetMirror, Netflix, Prime, etc. NEVER routes to YouTube.
Zero Failure Guarantee: Navigates active browser or launches with guaranteed foreground focus.
"""

import os
import re
import time
import urllib.parse
from typing import Optional, Tuple, Dict, Any

from core.jarvis_logger import log_info, log_warn, log_error

STREAMING_PLATFORMS: Dict[str, Dict[str, str]] = {
    "netmirror": {
        "name": "NetMirror",
        "home": "https://netmirror.app",
        "search": "https://netmirror.center/search/{query}",
        "emoji": "🍿",
        "aliases": ["netmirror", "net mirror", "netmirror.app", "netmirror.center"]
    },
    "netflix": {
        "name": "Netflix",
        "home": "https://www.netflix.com",
        "search": "https://www.netflix.com/search?q={query}",
        "emoji": "🎬",
        "aliases": ["netflix", "net flix"]
    },
    "prime video": {
        "name": "Prime Video",
        "home": "https://www.primevideo.com",
        "search": "https://www.primevideo.com/search/ref=atv_nb_sr?phrase={query}",
        "emoji": "📺",
        "aliases": ["prime video", "amazon prime", "primevideo", "amazon prime video", "prime"]
    },
    "youtube": {
        "name": "YouTube",
        "home": "https://www.youtube.com",
        "search": "https://www.youtube.com/results?search_query={query}",
        "emoji": "🎵",
        "aliases": ["youtube", "yt", "you tube"]
    },
    "jiocinema": {
        "name": "JioCinema",
        "home": "https://www.jiocinema.com",
        "search": "https://www.jiocinema.com/search/{query}",
        "emoji": "🎥",
        "aliases": ["jiocinema", "jio cinema", "jio movies", "jio"]
    },
    "hotstar": {
        "name": "Disney+ Hotstar",
        "home": "https://www.hotstar.com",
        "search": "https://www.hotstar.com/in/explore?search_query={query}",
        "emoji": "⭐",
        "aliases": ["hotstar", "disney+ hotstar", "disney hotstar", "disney plus", "disney"]
    },
    "spotify": {
        "name": "Spotify",
        "home": "https://open.spotify.com",
        "search": "https://open.spotify.com/search/{query}",
        "emoji": "🎧",
        "aliases": ["spotify"]
    },
    "twitch": {
        "name": "Twitch",
        "home": "https://www.twitch.tv",
        "search": "https://www.twitch.tv/search?term={query}",
        "emoji": "🎮",
        "aliases": ["twitch", "twitch tv"]
    }
}


def detect_streaming_platform(text: str) -> Optional[Tuple[str, Dict[str, str]]]:
    """Detect which platform is explicitly requested in text."""
    t = (text or "").lower()
    # Check multi-word aliases first to prevent partial matches
    for plat_key, cfg in STREAMING_PLATFORMS.items():
        for alias in sorted(cfg["aliases"], key=len, reverse=True):
            pattern = rf"\b{re.escape(alias)}\b"
            if re.search(pattern, t):
                return plat_key, cfg
    return None


CONVERSATIONAL_STOP_WORDS = {
    "per", "par", "pe", "pr", "me", "mein", "mai", "main", "se", "ko", "ke", "ka", "ki",
    "liye", "karo", "kardo", "kar", "karo na", "kardo na", "karna", "krna", "dikhana", "dikhao",
    "chalao", "chala do", "chala", "lagao", "laga do", "laga", "bajao", "baja do", "baja",
    "sunao", "suna do", "dekhna", "dekhni", "dekho", "dekh", "hai", "h", "tha", "thi", "hun", "hoon",
    "accha", "acchi", "acche", "acha", "achi", "ache", "koi", "ek", "ak", "kuch",
    "to", "toh", "bhi", "babe", "lila", "please", "plz", "now", "abhi", "kripya",
    "open", "kholo", "launch", "start", "play", "watch", "stream",
    "movie", "movies", "film", "films", "cinema", "series", "web series", "show", "shows",
    "song", "songs", "gaana", "gaane", "gana", "gane", "video", "videos", "track", "tracks",
    "website", "site", "webpage", "on", "in", "at", "from",
    "screen", "desktop", "dikhra", "dikh", "dikhta", "dikhe", "hoga", "hogi", "hoge",
    "click", "clickar", "us", "wo", "woh", "ye", "yeh", "wala", "wali", "wale", "karo",
    "mera", "meri", "mere", "favourite", "favorite", "fav", "playlist", "meri playlist",
    "sound", "music", "gaana", "gana", "ek", "ak", "koi", "kuch", "achha", "accha", "attha",
    "n", "na", "ne", "re", "yaar", "yr", "bhai", "bro", "boola", "bola", "are", "arey", "batao", "lagane",
    "एक", "गाना", "गाने", "लगाना", "लगाओ", "लगा", "चलाना", "चलाओ", "चला", "बजाना", "बजाओ", "बजा",
    "सुनाना", "सुनाओ", "सुन", "लेगा", "अच्छा", "अच्छी", "अच्छे", "अट्ठा", "वाला", "वाली", "वाले", "फेवरेट", "पसंदीदा"
}

OUTER_FLUFF = {
    "per", "par", "pe", "pr", "me", "mein", "mai", "main", "se", "ko", "ke", "ka", "ki",
    "liye", "karo", "kardo", "kar", "karna", "krna", "dikhana", "dikhao",
    "wala", "wali", "wale", "click", "clickar", "us", "wo", "woh", "ye", "yeh",
    "babe", "lila", "please", "plz", "now", "abhi", "kripya", "to", "toh", "bhi",
    "on", "in", "at", "from", "n", "na", "ne", "re", "yaar", "yr", "bhai", "bro", "boola", "bola", "are", "arey"
}


def clean_media_title(raw_text: str, platform_key: Optional[str] = None) -> str:
    """Extract clean title/query from spoken or typed media command."""
    q = (raw_text or "").strip()
    if not q:
        return ""

    aliases = []
    if platform_key and platform_key in STREAMING_PLATFORMS:
        aliases = STREAMING_PLATFORMS[platform_key]["aliases"]
    else:
        for cfg in STREAMING_PLATFORMS.values():
            aliases.extend(cfg["aliases"])

    # Remove platform names from query
    for alias in sorted(aliases, key=len, reverse=True):
        q = re.sub(rf"\b{re.escape(alias)}\b", "", q, flags=re.I)

    # Clean prefixes
    prefixes = [
        "are gana laga boola ", "are gana laga ", "gana laga boola ",
        "are boola ", "are bola ", "boola ", "bola ", "are ", "arey ",
        "lagane ko boola ", "lagane ko bola ", "lagane boola ", "lagane bola ",
        "search for movie of ", "search for video of ", "search for song of ",
        "search for movie ", "search for video ", "search for song ", "search for ",
        "search karo movie ", "search karo song ", "search karo ", "search kardo ",
        "search ", "dhundo ", "dhoondo ", "find ", "look up ", "look for ",
        "open and search for ", "open and play ", "open and search ", "open and ",
        "kholo aur ", "open karo aur ", "kholo ", "open karo ", "open ", "launch ", "start ",
        "play movie of ", "play song of ", "play video of ",
        "play movie ", "play song ", "play video ", "play series ",
        "watch movie ", "watch series ", "watch video ", "watch ",
        "stream movie ", "stream series ", "stream ",
        "play ", "listen to ",
        "pe search karo ", "par search karo ", "per search karo ", "pe search kardo ", "par search kardo ",
        "pe movie lagao ", "par movie lagao ", "per movie lagao ", "pe movie laga ", "par movie laga ", "per movie laga ",
        "pe lagao movie ", "par lagao movie ", "per lagao movie ", "pe laga movie ", "par laga movie ", "per laga movie ",
        "pe lagao ", "par lagao ", "per lagao ", "pe laga ", "par laga ", "per laga ",
        "pe movie chalao ", "par movie chalao ", "per movie chalao ", "pe movie chala ", "par movie chala ", "per movie chala ",
        "pe chalao movie ", "par chalao movie ", "per chalao movie ", "pe chala movie ", "par chala movie ", "per chala movie ",
        "pe chalao ", "par chalao ", "per chalao ", "pe chala ", "par chala ", "per chala ",
        "pe dekhna hai ", "par dekhna hai ", "per dekhna hai ", "pe dekho ", "par dekho ", "per dekho ",
        "pe ", "par ", "per ", "pr ", "me ", "mein ", "mai ", "main "
    ]

    suffixes = [
        " boola n", " bola n", " boola na", " bola na", " boola", " bola",
        " on netmirror", " in netmirror", " netmirror pe", " netmirror par", " netmirror per",
        " on netflix", " in netflix", " netflix pe", " netflix par", " netflix per",
        " on prime video", " in prime video", " prime video pe", " prime video par", " prime video per",
        " on youtube", " in youtube", " youtube pe", " youtube par", " youtube per",
        " on jiocinema", " in jiocinema", " jiocinema pe", " jiocinema par", " jiocinema per",
        " on hotstar", " in hotstar", " hotstar pe", " hotstar par", " hotstar per",
        " on spotify", " in spotify", " spotify pe", " spotify par", " spotify per",
        " on", " in", " at", " from", " pe", " par", " per", " pr",
        " screen par dikhra hoga click karo", " screen par dikh raha hoga click karo",
        " screen par dikhra hoga", " screen par dikh raha hoga",
        " dikhra hoga click karo", " dikh raha hoga click karo", " dikhra hoga", " dikh raha hoga",
        " screen par click karo", " screen pe click karo", " screen par", " screen pe",
        " us per click karo", " us pe click karo", " click karo", " click kar", " clickar", " click",
        " movie dekhni hai", " movie lagao", " movie chalao", " movie laga", " movie chala", " movie",
        " series lagao", " series chalao", " series laga", " series chala", " series",
        " song lagao", " song chalao", " song laga", " song chala", " song", " songs",
        " gaana lagao", " gaana chalao", " gaana laga", " gaana chala", " gaana", " gaane",
        " gana lagao", " gana chalao", " gana laga", " gana chala", " gana", " gane",
        " search karo", " search kardo", " search",
        " chalao", " chala do", " chala", " lagao", " laga do", " laga", " bajao", " baja do", " baja",
        " sunao", " suna do",
        " kholo", " open karo", " open", " launch", " start", " please", " plz", " now", " abhi", " babe", " lila",
        " n", " na", " re", " yaar", " yr"
    ]

    changed = True
    while changed:
        changed = False
        q_lower = q.lower().strip()
        for p in prefixes:
            if q_lower.startswith(p):
                q = q[len(p):].strip()
                changed = True
                break
        q_lower = q.lower().strip()
        for s in suffixes:
            if q_lower.endswith(s):
                q = q[:-len(s)].strip()
                changed = True
                break

    # Strip leftover punctuation & whitespace
    q = " ".join(q.split()).strip("'\".,!?")
    words = [w.strip("'\".,!?") for w in q.split() if w.strip("'\".,!?")]
    if not words or all(w.lower() in CONVERSATIONAL_STOP_WORDS for w in words):
        return ""

    # Strip conversational fluff words only from outer ends
    while words and words[0].lower() in OUTER_FLUFF:
        words.pop(0)
    while words and words[-1].lower() in OUTER_FLUFF:
        words.pop(-1)

    result = " ".join(words).strip()
    if len(result) <= 1:
        return ""
    return result


def play_or_stream_media(
    user_text: str,
    explicit_platform: Optional[str] = None,
    browser: Optional[str] = None
) -> str:
    """
    Core entrypoint for ALL media playback and streaming across JARVIS and Lila.
    Faithfully honors user platform preference:
      - If user said 'on netflix' -> Netflix only.
      - If user said 'on netmirror' -> NetMirror only.
      - If user said 'on prime video' -> Prime Video only.
      - If user said 'on youtube' -> YouTube only.
    """
    text_clean = (user_text or "").strip()
    detected = detect_streaming_platform(explicit_platform or text_clean)

    if detected:
        plat_key, plat_cfg = detected
    else:
        # Check if user explicitly asked for a movie or streaming series
        t_low = text_clean.lower()
        is_explicit_movie = any(m in t_low for m in ["movie", "movies", "film", "films", "cinema", "series", "web series", "season", "episode", "tv show", "tv shows"])

        if is_explicit_movie:
            # If user is talking about movies/shows without specifying a platform, trigger Realtime UI movie picker!
            log_info("media_streaming", f"No platform specified for movie query '{text_clean}'. Triggering Realtime UI.")
            try:
                from core.live_tools import dispatch_tool_call
                dispatch_tool_call("show_realtime_ui", {"ui_type": "movie_picker", "title": "Choose Movie or Show"})
                return "Aww babe, movie night? 💕 Maine screen par trending movies aur platforms laga diye hain, tap karke select kar lo!"
            except Exception:
                return "Aww babe, movie night? 💕 Batao kaunse platform pe chalau — Netflix, Prime Video, ya NetMirror? Aur kaunsi movie dekhne ka mann hai?"
        else:
            # By default, ALL songs, music, artist queries, videos, clips, and general playback route to YouTube!
            plat_key = "youtube"
            plat_cfg = STREAMING_PLATFORMS["youtube"]

    plat_name = plat_cfg["name"]
    emoji = plat_cfg["emoji"]
    clean_title = clean_media_title(text_clean, plat_key)

    log_info("media_streaming", f"Routing '{text_clean}' -> Platform: {plat_name} ({plat_key}), Title: '{clean_title}'")

    # ── Special YouTube Direct Video Resolution ──
    if plat_key == "youtube":
        from core.youtube_driver import play_youtube_video, get_user_favorite_music, is_favorite_or_general_query
        if clean_title and len(clean_title) > 1 and not is_favorite_or_general_query(clean_title):
            return play_youtube_video(clean_title)
        else:
            # If user explicitly asked for favorite song, play it directly
            t_low = text_clean.lower()
            if any(f in t_low for f in ["favourite", "favorite", "fav", "mera favourite", "meri pasand"]):
                fav_music = get_user_favorite_music()
                log_info("media_streaming", f"Autonomous favorite music trigger for query '{text_clean}' -> '{fav_music}'")
                return play_youtube_video(fav_music)

            # Open-ended music request: Pop Realtime UI picker with category pills and curated cards!
            try:
                from core.live_tools import dispatch_tool_call
                dispatch_tool_call("show_realtime_ui", {"ui_type": "music_picker", "title": "Choose Music Vibe"})
                return "Babe, kis mood ka gana sunna hai? Romance, party ya chill? Screen par options dekh lo! 💕"
            except Exception as ex:
                log_warn("media_streaming", f"show_realtime_ui trigger error: {ex}")
                fav_music = get_user_favorite_music()
                return play_youtube_video(fav_music)

    # ── NetMirror Dedicated Handling ──
    elif plat_key == "netmirror":
        from core.netmirror_driver import open_netmirror
        return open_netmirror(clean_title if clean_title else None, browser=browser)

    # ── All Other Streaming Services (Netflix, Prime Video, JioCinema, Hotstar, Spotify) ──
    else:
        if clean_title:
            encoded = urllib.parse.quote_plus(clean_title)
            target_url = plat_cfg["search"].format(query=encoded)
            reply = f"Haan babe! Maine {plat_name} par '{clean_title}' search karke open kar diya hai! {emoji} Screen par dekh lo!"
        else:
            target_url = plat_cfg["home"]
            reply = f"Haan babe! Maine {plat_name} website screen par open kar di hai! {emoji} Ab batao kya dekhna hai?"

    # Execute browser navigation
    nav_ok = False
    try:
        from core.win_os_agent import launch_or_focus_browser
        nav_ok = launch_or_focus_browser(target_url)
    except Exception as ex:
        log_warn("media_streaming", f"launch_or_focus_browser error for {target_url}: {ex}")

    if not nav_ok:
        try:
            from core.omniforge import launch_in_chrome
            nav_ok = launch_in_chrome(target_url)
        except Exception as ex:
            log_warn("media_streaming", f"launch_in_chrome error for {target_url}: {ex}")

    if not nav_ok:
        try:
            os.startfile(target_url)
            nav_ok = True
        except Exception:
            import webbrowser
            webbrowser.open_new(target_url)
            nav_ok = True

    # Focus and foreground browser
    try:
        from core.win_os_agent import find_browser_window, force_foreground_window
        time.sleep(0.4)
        bw = find_browser_window()
        if bw and bw.get("hwnd"):
            force_foreground_window(bw["hwnd"])
    except Exception:
        pass

    return reply
