"""
core/netmirror_driver.py — Dedicated High-Speed NetMirror & Movie Streaming Driver
===================================================================================
Provides native, dedicated integration for NetMirror (https://netmirror.app / https://netmirror.center)
and official movie/series streaming platforms.

Guarantees:
  1. Zero YouTube Hijack: Movie/series queries never get accidentally routed to YouTube.
  2. Direct Official Navigation: Opens NetMirror homepage or direct search results immediately.
  3. Window Foregrounding: Ensures browser is visibly focused on screen for instant playback.
  4. Girlfriend Personality: Warm, excited replies in Lila's voice.
"""

import os
import re
import time
import urllib.parse
from typing import Optional

from core.jarvis_logger import log_info, log_warn, log_error

NETMIRROR_HOME = "https://netmirror.app"
NETMIRROR_CENTER = "https://netmirror.center"
NETMIRROR_SEARCH_URL = "https://netmirror.center/search/{query}"


def clean_netmirror_query(raw_query: str) -> str:
    """Extract clean movie/series title from spoken or typed NetMirror command."""
    q = (raw_query or "").strip()
    if not q:
        return ""

    prefixes = [
        "search on netmirror for movie of ", "search on netmirror for video of ",
        "search on netmirror for movie ", "search on netmirror for ", "search on netmirror ",
        "search netmirror for movie ", "search netmirror for ", "search netmirror ",
        "netmirror pe search karo movie ", "netmirror pe search karo ", "netmirror par search karo ",
        "netmirror pe search kardo ", "netmirror par search kardo ",
        "netmirror pe movie lagao ", "netmirror par movie lagao ",
        "netmirror pe lagao movie ", "netmirror par lagao movie ",
        "netmirror pe lagao ", "netmirror par lagao ",
        "netmirror pe movie laga ", "netmirror par movie laga ",
        "netmirror pe laga ", "netmirror par laga ",
        "netmirror pe chalao movie ", "netmirror par chalao movie ",
        "netmirror pe chalao ", "netmirror par chalao ",
        "netmirror pe chala movie ", "netmirror par chala movie ",
        "netmirror pe chala ", "netmirror par chala ",
        "netmirror pe dekhna hai ", "netmirror par dekhna hai ",
        "netmirror pe dekho ", "netmirror par dekho ",
        "netmirror pe ", "netmirror par ", "netmirror me ", "netmirror mein ",
        "play on netmirror ", "play movie on netmirror ", "watch on netmirror ",
        "stream on netmirror ", "watch movie on netmirror ", "play netmirror ",
        "watch netmirror ", "stream netmirror ", "open netmirror and search for ",
        "open netmirror and play ", "open netmirror and search ", "open netmirror and ",
        "open netmirror ", "launch netmirror ", "start netmirror ", "open ", "launch ", "start ",
        "netmirror kholo aur ", "netmirror open karo aur ",
        "play movie ", "watch movie ", "stream movie ", "play series ",
        "watch series ", "stream series ", "play ", "watch ", "stream "
    ]

    suffixes = [
        " on netmirror", " in netmirror", " netmirror pe", " netmirror par",
        " netmirror me", " netmirror mein", " movie on netmirror",
        " search on netmirror", " play on netmirror", " watch on netmirror",
        " movie dekhni hai", " movie lagao", " movie chalao", " movie laga", " movie chala", " movie",
        " series lagao", " series chalao", " series laga", " series chala", " series",
        " search karo", " search kardo", " chalao", " chala do", " chala", " lagao", " laga do", " laga",
        " kholo", " open karo", " open", " please", " plz", " babe", " lila"
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

    q = q.strip("'\".,!?")
    if q.lower() in ["netmirror", "net mirror", "movie", "movies", "series", "film", "cinema", "on netmirror", "open netmirror", "website", "site", ""]:
        return ""
    return q


def open_netmirror(query: Optional[str] = None, browser: Optional[str] = None) -> str:
    """
    Direct high-speed NetMirror launcher.
    If query is provided and contains a specific movie/series title:
      Navigates directly to NetMirror search results for that movie.
    If query is empty or just asking to open NetMirror:
      Navigates directly to NetMirror homepage (https://netmirror.app).
    """
    clean_q = clean_netmirror_query(query or "")
    log_info("netmirror", f"open_netmirror called with raw='{query}', clean='{clean_q}'")

    if clean_q:
        encoded = urllib.parse.quote_plus(clean_q.lower())
        target_url = NETMIRROR_SEARCH_URL.format(query=encoded)
        log_msg = f"Searching NetMirror for '{clean_q}' -> {target_url}"
        reply = f"Haan babe! Maine NetMirror par '{clean_q}' search karke open kar diya hai! 🍿 Screen par movie click karke enjoy karo!"
    else:
        target_url = NETMIRROR_HOME
        log_msg = f"Opening NetMirror homepage -> {target_url}"
        reply = "Haan babe! Maine NetMirror website screen par open kar di hai! 🎬🍿 Ab batao kaunsi movie ya series dekhni hai?"

    log_info("netmirror", log_msg)

    # 1. Primary: Use win_os_agent browser launcher or active navigation
    nav_ok = False
    try:
        from core.win_os_agent import launch_or_focus_browser
        nav_ok = launch_or_focus_browser(target_url)
    except Exception as ex:
        log_warn("netmirror", f"launch_or_focus_browser error: {ex}")

    # 2. Secondary: Launch directly in Chrome
    if not nav_ok:
        try:
            from core.omniforge import launch_in_chrome
            nav_ok = launch_in_chrome(target_url)
        except Exception as ex:
            log_warn("netmirror", f"launch_in_chrome error: {ex}")

    # 3. Tertiary: OS Shell fallback
    if not nav_ok:
        try:
            os.startfile(target_url)
            nav_ok = True
        except Exception:
            import webbrowser
            webbrowser.open_new(target_url)
            nav_ok = True

    # 4. Guarantee browser window is foregrounded on screen
    try:
        from core.win_os_agent import find_browser_window, force_foreground_window
        time.sleep(0.4)
        bw = find_browser_window()
        if bw and bw.get("hwnd"):
            force_foreground_window(bw["hwnd"])
    except Exception:
        pass

    return reply
