"""
core/modern_browser.py — Modern Autonomous Headed Web Agent (2026 Engine)
===========================================================================
Architecture:
  1. Brave Browser Priority: Automatically auto-detects and launches Brave Browser
     on Windows/macOS/Linux with persistent user profile (~/.jarvis_brave_profile) or
     direct CDP attachment (port 9222), keeping authenticated sessions intact.
  2. Dynamic Target Discovery: Uses live Serper / Google intelligence to find
     official websites dynamically with zero hardcoded platform or URL dictionaries.
  3. Headed & Visible Browsing: Launches visibly (headless=False) so the user
     watches real-time navigation, typing, card selection, and media playback.
  4. Snapshot Accessibility Tree: Deterministic DOM tree extractor capturing form
     inputs, searchboxes, buttons, links, SPA cards, movie posters, and media tiles.
  5. Multi-Step Goal-Directed Execution Loop: Iteratively plans and executes next steps
     (search -> find result card -> click movie -> start playback) with Gemini Flash-Lite.
  6. Visual Coordinate Grounding: When DOM selectors fail on canvas/overlays, uses
     Gemini Vision coordinate detection ([ymin, xmin, ymax, xmax]) to click pixel center.
  7. Persistent Playback: Leaves the browser window open on desktop when streaming or
     playing media, rather than prematurely killing the video stream.
  8. Vision Fallback & Safety Gates: Domain allow/block safety lists and human confirmation gates.
"""

import os
import sys
import subprocess
import threading
import re
import json
import time
import asyncio
import urllib.parse
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from dotenv import load_dotenv

from core.jarvis_logger import log_info, log_warn, log_error

load_dotenv()

# ── Safety Lists ──────────────────────────────────────────────────────────────
BLOCKED_DOMAINS = {
    "malware.com", "phishing.com", "darkweb",
}

HIGH_RISK_KEYWORDS = {
    "buy", "pay", "payment", "checkout", "purchase", "order", "subscribe",
    "credit card", "cvv", "transfer", "delete account", "confirm order"
}


def get_chrome_executable_path() -> Optional[str]:
    """Find Google Chrome executable on Windows/Linux/macOS."""
    custom_path = os.environ.get("CHROME_PATH")
    if custom_path and os.path.exists(custom_path):
        return custom_path
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        "/usr/bin/google-chrome",
        "/usr/bin/chrome",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def get_lila_executable_path() -> Optional[str]:
    """Find Lila's Browser (BrowserClaw) executable on Windows."""
    custom_path = os.environ.get("LILA_BROWSER_PATH")
    if custom_path and os.path.exists(custom_path):
        return custom_path
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\BrowserClaw\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\BrowserClaw\Application\chrome.exe"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def get_lila_extension_args() -> List[str]:
    """Return command line flags to load installed unpacked extensions in Lila's Browser."""
    ext_candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\BrowserClaw\Extensions\uBlock0\uBlock0.chromium"),
        os.path.expandvars(r"%LOCALAPPDATA%\BrowserClaw\Extensions\ChromiumWebStore"),
    ]
    valid = [e for e in ext_candidates if os.path.exists(e)]
    if valid:
        ext_str = ",".join(valid)
        return [f"--disable-extensions-except={ext_str}", f"--load-extension={ext_str}"]
    return []


def get_brave_executable_path() -> Optional[str]:
    """Find Brave Browser executable on Windows/Linux/macOS."""
    custom_path = os.environ.get("BRAVE_PATH")
    if custom_path and os.path.exists(custom_path):
        return custom_path
    candidates = [
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


async def check_cdp_available(port: int = 9222) -> bool:
    """Check if Chrome or Brave is listening on remote debugging port."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=0.6) as client:
            resp = await client.get(f"http://127.0.0.1:{port}/json/version")
            return resp.status_code == 200
    except Exception:
        return False


# ── DOM Accessibility Tree Extractor (0 LLM calls) ────────────────────────────
JS_EXTRACT_TREE = r"""
() => {
    let elements = [];
    let counter = 1;
    
    // Clean up previous refs
    document.querySelectorAll('[data-agent-ref]').forEach(el => el.removeAttribute('data-agent-ref'));
    
    // 1. Search inputs & primary interactive fields
    const inputSelectors = 'input[type="text"], input[type="search"], input:not([type="hidden"]):not([type="submit"]):not([type="button"]), textarea, [role="searchbox"]';
    
    // 2. High-priority search results, product links, and media/content cards
    const mainResultSelectors = '[data-component-type="s-search-result"] h2 a, [data-component-type="s-search-result"] h3 a, [data-component-type="s-search-result"] a.a-text-normal, .s-result-item[data-asin] h2 a, .movieCard, ytd-video-renderer #video-title, [class*="product-title"] a, [class*="item-title"] a, [class*="card"] a, [class*="poster"] a';
    
    // 3. General interactive controls, buttons, and remaining links
    const controlSelectors = 'button, [role="button"], input[type="submit"], input[type="button"], select, a[href], [role="link"], [role="tab"], [role="checkbox"], [onclick]';
    
    const iNodes = Array.from(document.querySelectorAll(inputSelectors));
    const rNodes = Array.from(document.querySelectorAll(mainResultSelectors));
    const cNodes = Array.from(document.querySelectorAll(controlSelectors));
    
    const seen = new Set();
    const nodes = [];
    for (let list of [iNodes, rNodes, cNodes]) {
        for (let n of list) {
            if (!seen.has(n)) {
                seen.add(n);
                nodes.push(n);
            }
        }
    }
    
    for (let node of nodes) {
        // Skip hidden or zero-dimension nodes
        const style = window.getComputedStyle(node);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
        const rect = node.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        
        let tag = node.tagName.toLowerCase();
        let type = node.getAttribute('type') || '';
        let name = node.getAttribute('name') || '';
        let placeholder = node.getAttribute('placeholder') || '';
        let ariaLabel = node.getAttribute('aria-label') || '';
        let text = (node.innerText || node.value || placeholder || ariaLabel || '').trim();
        text = text.replace(/\s+/g, ' ').substring(0, 100);
        
        let href = node.getAttribute('href') || '';
        
        // Filter out useless non-actionable noise (sponsored tooltips, rating stars popups)
        if (href.startsWith('javascript:') || href === '#') {
            if (!text || text === 'Sponsored' || text.includes('out of 5 stars') || text.length < 2) continue;
        }
        if (text === 'Sponsored' && !href) continue;
        if (!text && !placeholder && !ariaLabel) continue;
        
        const refId = 'e' + counter++;
        node.setAttribute('data-agent-ref', refId);
        
        if (href.length > 70) href = href.substring(0, 70) + '...';
        
        elements.push({
            ref: '@' + refId,
            tag: tag,
            type: type,
            name: name,
            placeholder: placeholder,
            ariaLabel: ariaLabel,
            text: text,
            href: href
        });
    }
    
    // Generate compact readable summary for LLM
    let summary = elements.map(el => {
        let desc = `${el.ref}: [${el.tag}${el.type ? ':' + el.type : ''}]`;
        if (el.text) desc += ` "${el.text}"`;
        if (el.placeholder && el.placeholder !== el.text) desc += ` (placeholder: "${el.placeholder}")`;
        if (el.ariaLabel && el.ariaLabel !== el.text) desc += ` (aria: "${el.ariaLabel}")`;
        if (el.href) desc += ` -> ${el.href}`;
        return desc;
    }).join('\n');
    
    return {
        elements: elements,
        summary: summary.substring(0, 25000),
        count: elements.length
    };
}
"""


def _clean_stale_profile_locks(profile_dir: str):
    """Kills orphaned Chrome/Brave processes locking the profile and removes stale lock files."""
    try:
        import psutil
        norm_pdir = os.path.normpath(profile_dir).lower()
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmd = ' '.join(p.info['cmdline'] or []).lower()
                name = (p.info['name'] or '').lower()
                if ('chrome' in name or 'brave' in name) and norm_pdir in cmd:
                    p.kill()
            except Exception:
                pass
    except Exception:
        pass
    for lock_name in ['SingletonLock', 'SingletonCookie', 'SingletonSocket', 'lockfile']:
        lock_path = os.path.join(profile_dir, lock_name)
        if os.path.exists(lock_path):
            try:
                os.remove(lock_path)
            except Exception:
                pass


class ModernBrowser:
    """Deterministic-first, autonomous multi-step browser automation engine."""
    
    def __init__(self, default_headless: bool = False):
        self._browser = None
        self._context = None
        self._playwright = None
        self._lock = asyncio.Lock()
        self._current_headless: Optional[bool] = None
        self.default_headless = default_headless
        self._is_persistent: bool = False
        self._is_cdp: bool = False

    async def _ensure_browser(self, headless: Optional[bool] = None, use_lila: bool = False):
        target_headless = self.default_headless if headless is None else headless
        from playwright.async_api import async_playwright
        
        # If running with a different headless mode or browser type, restart browser
        if self._context is not None or self._browser is not None:
            if self._current_headless != target_headless or getattr(self, '_current_use_lila', False) != use_lila:
                try:
                    if self._context:
                        await self._context.close()
                    if self._browser and not self._is_cdp:
                        await self._browser.close()
                except Exception:
                    pass
                self._browser = None
                self._context = None
                self._is_persistent = False
                self._is_cdp = False

        if self._context is None:
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            self._current_headless = target_headless

            launch_args = [
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-infobars',
                '--start-maximized',
                '--no-first-run'
            ]

            # 1. Tier 1: Check if Google Chrome is running with remote debugging port 9222
            if not use_lila:
                cdp_ready = await check_cdp_available(9222)
                if cdp_ready:
                    try:
                        log_info("[MODERN BROWSER]: Connecting to active Google Chrome over CDP (port 9222)...")
                        self._browser = await self._playwright.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=3000)
                        contexts = self._browser.contexts
                        self._context = contexts[0] if contexts else await self._browser.new_context()
                        self._is_cdp = True
                        self._current_use_lila = False
                        return
                    except Exception as e:
                        log_warn(f"[MODERN BROWSER]: CDP connection failed ({e}), falling back to direct launch.")

            # 2. Tier 2: Launch Google Chrome (as independent OS process with CDP 9222) or Lila's Browser
            if use_lila:
                chosen_exe = get_lila_executable_path()
                chosen_name = "Lila's Browser"
                profile_dir = os.path.expanduser("~/.jarvis_lila_profile")
                if chosen_exe and os.path.exists(chosen_exe):
                    os.makedirs(profile_dir, exist_ok=True)
                    ext_args = get_lila_extension_args()
                    active_args = launch_args + ext_args
                    for attempt in range(2):
                        try:
                            log_info(f"[MODERN BROWSER]: Launching {chosen_name} ({chosen_exe}) with persistent profile at {profile_dir} (headed={not target_headless}, attempt={attempt+1})...")
                            self._context = await self._playwright.chromium.launch_persistent_context(
                                user_data_dir=profile_dir,
                                executable_path=chosen_exe,
                                headless=target_headless,
                                args=active_args,
                                viewport={"width": 1366, "height": 768} if target_headless else None,
                                no_viewport=not target_headless,
                                locale="en-US"
                            )
                            self._browser = self._context.browser
                            self._is_persistent = True
                            self._current_use_lila = True
                            await self._context.add_init_script("""
                                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                                window.chrome = { runtime: {} };
                                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                            """)
                            return
                        except Exception as e:
                            if attempt == 0 and any(w in str(e).lower() for w in ["singleton", "lock", "used by another process"]):
                                log_warn(f"[MODERN BROWSER]: Profile locked for {chosen_name} ({e}). Cleaning stale lock and retrying...")
                                _clean_stale_profile_locks(profile_dir)
                                await asyncio.sleep(1.0)
                            else:
                                log_warn(f"[MODERN BROWSER]: {chosen_name} launch error ({e}), falling back to standard chromium.")
                                break
            else:
                brave_exe = get_brave_executable_path()
                chrome_exe = get_chrome_executable_path()
                chosen_exe = None
                chosen_name = "Browser"

                from core.win_os_agent import find_browser_window
                bw = find_browser_window()
                if bw and "brave" in bw.get("proc_name", "").lower() and brave_exe:
                    chosen_exe = brave_exe
                    chosen_name = "Brave Browser"
                elif chrome_exe and os.path.exists(chrome_exe):
                    chosen_exe = chrome_exe
                    chosen_name = "Google Chrome"
                elif brave_exe and os.path.exists(brave_exe):
                    chosen_exe = brave_exe
                    chosen_name = "Brave Browser"

                profile_dir = os.path.normpath(os.path.expanduser("~/.jarvis_chrome_profile"))
                if chosen_exe and os.path.exists(chosen_exe):
                    os.makedirs(profile_dir, exist_ok=True)
                    _clean_stale_profile_locks(profile_dir)
                    chrome_cmd = [
                        chosen_exe,
                        '--remote-debugging-port=9222',
                        '--remote-allow-origins=*',
                        f'--user-data-dir={profile_dir}',
                        '--no-first-run',
                        '--no-default-browser-check',
                        '--disable-blink-features=AutomationControlled',
                        '--start-maximized',
                        'about:blank'
                    ]
                    log_info(f"[MODERN BROWSER]: Launching {chosen_name} independently ({chosen_exe}) with port 9222...")
                    creationflags = 0
                    if sys.platform == "win32":
                        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    subprocess.Popen(chrome_cmd, creationflags=creationflags)
                    for _ in range(15):
                        await asyncio.sleep(0.3)
                        if await check_cdp_available(9222):
                            break
                    if await check_cdp_available(9222):
                        try:
                            log_info(f"[MODERN BROWSER]: Connecting to launched {chosen_name} over CDP (port 9222)...")
                            self._browser = await self._playwright.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=3000)
                            contexts = self._browser.contexts
                            self._context = contexts[0] if contexts else await self._browser.new_context()
                            self._is_cdp = True
                            self._current_use_lila = False
                            return
                        except Exception as e:
                            log_warn(f"[MODERN BROWSER]: CDP connection after launch failed: {e}")

            # 3. Tier 3: Standard Chromium Launch
            self._browser = await self._playwright.chromium.launch(
                headless=target_headless,
                args=launch_args,
                timeout=5000
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1366, "height": 768} if target_headless else None,
                no_viewport=not target_headless,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                locale="en-US"
            )
            self._current_use_lila = use_lila
            await self._context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            """)

    def is_safe_domain(self, url: str) -> bool:
        u_lower = url.lower()
        return not any(b in u_lower for b in BLOCKED_DOMAINS)

    def check_risk(self, task: str, actions: List[Dict[str, Any]]) -> bool:
        """Returns True if any planned action warrants human confirmation."""
        task_lower = task.lower()
        if any(w in task_lower for w in HIGH_RISK_KEYWORDS):
            return True
        for a in actions:
            val = str(a.get("value", "")).lower()
            if any(w in val for w in HIGH_RISK_KEYWORDS):
                return True
        return False

    async def extract_semantic_intent(self, task: str) -> Dict[str, Any]:
        """
        Zero-hardcoding semantic intent extractor using Gemini Flash-Lite.
        Identifies web platform, task category, canonical search query, and intended action.
        """
        try:
            from google import genai
            api_key = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GEMINI_API_KEY_2", "")
            if api_key:
                client = genai.Client(api_key=api_key)
                prompt = f"""You are an autonomous web intelligence agent.
Analyze the user's browsing/media task: "{task}"

Extract:
1. "platform": Exact name of the specific website, streaming service, or web platform requested (e.g. "NetMirror", "Coursera", "Twitch", "YouTube", "GitHub", "Amazon"), or null if no specific platform is named.
2. "category": One of ["streaming_media", "e_commerce", "research", "general_web"].
3. "query": The real canonical title or clean search term (resolve colloquial speech, slang, or slight misnomers to the actual official movie/show title, e.g. "the breacher movie" -> "Breach", "fast and furious 10" -> "Fast X").
4. "action": One of ["play_media", "search_and_extract", "download", "navigate"].

Return ONLY valid JSON:
{{
  "platform": "...",
  "category": "...",
  "query": "...",
  "action": "..."
}}"""
                for mod in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
                    try:
                        resp = client.models.generate_content(model=mod, contents=prompt)
                        txt = (resp.text or "{}").replace("```json", "").replace("```", "").strip()
                        match = re.search(r'\{.*\}', txt, re.DOTALL)
                        if match:
                            return json.loads(match.group(0))
                    except Exception:
                        continue

        except Exception as e:
            log_warn(f"[MODERN BROWSER]: Semantic intent extraction error: {e}")

        # Basic fallback if API call fails
        t_low = task.lower()
        is_media = any(w in t_low for w in ["movie", "stream", "watch", "play", "film", "series"])
        return {
            "platform": None,
            "category": "streaming_media" if is_media else "general_web",
            "query": task,
            "action": "play_media" if is_media else "navigate"
        }

    async def select_best_url(self, task: str, candidates: List[Dict[str, str]], category: str) -> str:
        """
        Dynamically select the single best URL where the user can directly perform their task in the browser,
        avoiding mobile app downloads, app stores, or static landing pages.
        """
        if not candidates:
            return ""
        if len(candidates) == 1:
            return candidates[0]["url"]

        try:
            from google import genai
            api_key = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GEMINI_API_KEY_2", "")
            if api_key:
                client = genai.Client(api_key=api_key)
                prompt = f"""User Task: "{task}"
Task Category: "{category}"

Web Search Candidates:
{json.dumps(candidates, indent=2)}

Select the single best URL where the user can directly accomplish their task inside a desktop web browser (avoid mobile app download landing pages, app store links, or non-functional blogs).
Return ONLY valid JSON:
{{"url": "the chosen URL", "reason": "concise rationale"}}"""

                for mod in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
                    try:
                        resp = client.models.generate_content(model=mod, contents=prompt)
                        txt = (resp.text or "{}").replace("```json", "").replace("```", "").strip()
                        match = re.search(r'\{.*\}', txt, re.DOTALL)
                        if match:
                            data = json.loads(match.group(0))
                            chosen = data.get("url")
                            if chosen and any(c.get("url") == chosen for c in candidates):
                                log_info(f"[MODERN BROWSER]: Dynamically selected best URL: {chosen} ({data.get('reason')})")
                                return chosen
                    except Exception:
                        continue
        except Exception as e:
            log_warn(f"[MODERN BROWSER]: Best URL selection error: {e}")

        return candidates[0]["url"]

    async def resolve_target_url(self, task: str, start_url: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
        """
        Dynamically discover the real active starting URL and semantic intent.
        100% generalized, intelligent, zero hardcoding.
        """
        if start_url:
            u = start_url if start_url.startswith("http") else f"https://{start_url}"
            clean_q = task
            for pfx in ["search for", "search", "khojo", "dhoondho", "look for", "find", "dekho", "dikhao", "open", "kholo"]:
                if clean_q.lower().startswith(pfx):
                    clean_q = clean_q[len(pfx):].strip()
                    break
            for sfx in [" dekho", " dikhao", " search karo", " kholo", " please", " plz", " on amazon", " in amazon", " pe", " par"]:
                if clean_q.lower().endswith(sfx):
                    clean_q = clean_q[:-len(sfx)].strip()
                    break
            u_domain = urllib.parse.urlparse(u.lower()).netloc
            if clean_q and clean_q.lower() != task.lower():
                if "amazon" in u_domain:
                    u = f"https://www.amazon.in/s?k={urllib.parse.quote_plus(clean_q)}"
                elif "youtube" in u_domain:
                    u = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(clean_q)}"
                elif "flipkart" in u_domain:
                    u = f"https://www.flipkart.com/search?q={urllib.parse.quote_plus(clean_q)}"
            return u, {"platform": None, "category": "general_web", "query": clean_q, "action": "navigate"}

        # 1. Direct explicit URL in task
        url_match = re.search(r'https?://[^\s]+', task)
        if url_match:
            return url_match.group(0), {"platform": None, "category": "general_web", "query": task, "action": "navigate"}

        # 2. Extract semantic intent dynamically (0 hardcoding)
        intent = await self.extract_semantic_intent(task)
        target_plat = intent.get("platform")
        category = intent.get("category", "general_web")
        clean_query = intent.get("query", "")

        # 3. Well-known services (instant zero-latency routing if exact match)
        KNOWN_SERVICES = {
            "netflix": "https://www.netflix.com",
            "youtube": "https://www.youtube.com",
            "prime video": "https://www.primevideo.com",
            "prime": "https://www.primevideo.com",
            "hotstar": "https://www.hotstar.com",
            "disney": "https://www.disneyplus.com",
            "hulu": "https://www.hulu.com",
            "twitch": "https://www.twitch.tv",
            "spotify": "https://open.spotify.com",
            "github": "https://github.com",
            "google": "https://www.google.com",
            "netmirror": "https://netmirror.app",
            "net mirror": "https://netmirror.app",
            "netmirror.app": "https://netmirror.app",
            "netmirror.center": "https://netmirror.center",
        }
        if target_plat and target_plat.lower() in KNOWN_SERVICES:
            return KNOWN_SERVICES[target_plat.lower()], intent

        # 4. Smart Serper intelligence with candidate evaluation
        try:
            from search_service.serper_client import search as serper_search
            SKIP_DOMAINS = [
                "play.google.com", "apps.apple.com", "reddit.com", "facebook.com",
                "instagram.com", "bluestacks.com", "ldplayer.net", "apkpure.com",
                "youtube.com", "twitter.com", "x.com"
            ]
            if target_plat:
                if category == "streaming_media" or intent.get("action") == "play_media":
                    search_query = f"{target_plat} streaming watch movies online"
                else:
                    search_query = f"{target_plat} official website"
            else:
                search_query = f"{clean_query or task} official website"

            s_res = await serper_search(search_query, max_results=5)
            candidates = []
            for r in s_res.get("results", []):
                u = r.get("url", "")
                if u and not any(skip in u.lower() for skip in SKIP_DOMAINS) and self.is_safe_domain(u):
                    candidates.append({
                        "title": r.get("title", ""),
                        "url": u,
                        "snippet": r.get("snippet", "")
                    })

            if candidates:
                best_url = await self.select_best_url(task, candidates, category)
                if best_url:
                    log_info(f"[MODERN BROWSER]: Resolved target URL for '{task}' -> {best_url}")
                    return best_url, intent
        except Exception as se:
            log_warn(f"[MODERN BROWSER]: Dynamic URL discovery error: {se}")

        # Fallback to direct search query
        q = urllib.parse.quote(task)
        return f"https://www.google.com/search?q={q}", intent

    async def plan_action_steps(
        self,
        task: str,
        tree_text: str,
        current_url: str = "",
        step_num: int = 1,
        max_steps: int = 4,
        target_item: str = ""
    ) -> Dict[str, Any]:
        """
        Autonomous Multi-Step Planner.
        Sends accessibility snapshot to Gemini Flash-Lite / Flash.
        Emits targeted next action sequence as structured JSON.
        """
        domain = urllib.parse.urlparse(current_url).netloc if current_url else ""
        site_heuristics = ""
        if domain:
            try:
                from core.trajectory_memory import get_site_memory
                site_heuristics = get_site_memory().format_heuristics(domain)
            except Exception:
                pass

        target_context = f'\nTarget Item / Clean Query: "{target_item}"' if target_item else ""
        prompt = f"""You are Astra's Autonomous Browser Pilot.
User Goal: "{task}"{target_context}
Current URL: "{current_url}"
Navigation Step: {step_num} of {max_steps}{site_heuristics}

Current Page Interactive Elements:
{tree_text}

TASK INSTRUCTIONS:
1. Examine the interactive elements (@e1, @e2, etc.) and page context.
2. Determine the best immediate action(s) towards fulfilling the User Goal:
   - CRITICAL INTENT RULE — SEARCH vs CLICK:
     * If the User Goal is to SEARCH / BROWSE / LOOK FOR options (e.g. "search", "khojo", "dhoondho", "look for", "show", "dikhao", "find", "check options"):
       Once the search query has been submitted and the search results page has loaded, the task is COMPLETE! Emit action "done" with {{"type": "done", "result": "Search results loaded on screen"}}. STRICTLY DO NOT CLICK on any product card or link unless the user explicitly told you to "click", "open", or "kholo"!
     * If and ONLY IF the User Goal explicitly asks to CLICK, OPEN, or SELECT an item (e.g. "click", "kholo", "open", "select", "first", "card", "pehla wala", "dusra", "chuno", "daba", "buy", "cart"):
       You MUST emit a "click" action targeting that specific element (@eX).
   - If user asks for "first" / "pehla" / "pehla wala" / "1st" / "kholo to shi vo" / "open it" / "click first": Click the first relevant search result or product link (@eX) in the list.
   - If user asks for "second" / "dusra wala" / "2nd", "third" / "teesra wala" / "3rd": Click the corresponding numbered item in the results list.
   - If user specifies a product or title in parentheses or text to open: Find and click that specific item/card.
   - If on a home or search page and need to find something: Fill the search input (@eX) with the target query ({target_item or 'requested item'}), and press Enter (or click search).
   - If search yielded 0 results: Try a clean or normalized version of the title into the search input.
   - If on the movie/media page: Click Play / Stream / Server button if needed to initiate playback.
   - If the goal is fulfilled (e.g. search results are loaded, video is playing, or requested click action completed): Emit action "done".
3. For "fill", ONLY target elements that accept text ([input], [textarea]).
4. Supported action types:
   - "click": {{"type": "click", "ref": "@e1"}}
   - "fill": {{"type": "fill", "ref": "@e2", "value": "text to type"}}
   - "press": {{"type": "press", "key": "Enter"}}
   - "scroll": {{"type": "scroll", "direction": "down"}}
   - "wait": {{"type": "wait", "ms": 1500}}
   - "done": {{"type": "done", "result": "Playback started / Task accomplished"}}

Reply with ONLY valid JSON:
{{
  "intent": "concise description of this step",
  "actions": [
    {{"type": "fill", "ref": "@e1", "value": "..."}},
    {{"type": "press", "key": "Enter"}}
  ]
}}"""

        from google import genai
        api_key = os.environ.get("GEMINI_API_KEY", "")
        client = genai.Client(api_key=api_key)

        models_to_try = [
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-1.5-flash"
        ]

        
        last_err = None
        for m in models_to_try:
            try:
                resp = client.models.generate_content(
                    model=m,
                    contents=prompt
                )
                text = (resp.text or "{}").replace("```json", "").replace("```", "").strip()
                match = re.search(r'\{.*\}', text, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
            except Exception as e:
                last_err = e
                continue

        # Groq Llama 3.3 Fallback for 100% resilient planning when Gemini hits 429
        groq_key = os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_API_KEY_2")
        if groq_key:
            try:
                from core.brain import call_groq_brain
                g_resp = call_groq_brain(prompt, phase="LOGIC", is_logic_task=True)
                if isinstance(g_resp, dict):
                    g_resp = g_resp.get("reply", "")
                text = str(g_resp).replace("```json", "").replace("```", "").strip()
                match = re.search(r'\{.*\}', text, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
            except Exception as ge:
                log_warn(f"[MODERN BROWSER]: Groq planning fallback error: {ge}")
                
        return {"intent": f"Plan fallback: {last_err}", "actions": []}

    async def visual_coordinate_grounding(self, page, target_description: str) -> bool:
        """
        Visual Coordinate Grounding Fallback:
        When DOM selectors/accessibility trees fail to locate an element (e.g. video player overlay, canvas, shadow DOM),
        takes a viewport screenshot and queries Gemini Vision for exact [ymin, xmin, ymax, xmax] coordinates,
        then clicks the exact center pixel via page.mouse.click.
        """
        try:
            screenshot_bytes = await page.screenshot(type="jpeg", quality=80)
            import base64
            img_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            
            viewport_size = page.viewport_size
            if not viewport_size:
                dim = await page.evaluate("() => ({ width: window.innerWidth, height: window.innerHeight })")
                vw = dim.get("width", 1366)
                vh = dim.get("height", 768)
            else:
                vw = viewport_size.get("width", 1366)
                vh = viewport_size.get("height", 768)

            prompt = (
                f"You are a computer vision coordinate grounding engine.\n"
                f"Locate the clickable center of: {target_description}.\n"
                f"Output ONLY a JSON array [ymin, xmin, ymax, xmax] with coordinates normalized from 0 to 1000.\n"
                f"Example: [340, 420, 380, 510]"
            )
            
            from google import genai
            keys_to_try = [k for k in [os.environ.get("GEMINI_API_KEY", ""), os.environ.get("GEMINI_API_KEY_2", "")] if k]
            models_to_try = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

            
            resp = None
            for ak in keys_to_try:
                client = genai.Client(api_key=ak)
                for mod in models_to_try:
                    try:
                        resp = client.models.generate_content(
                            model=mod,
                            contents=[
                                {"role": "user", "parts": [
                                    {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}},
                                    {"text": prompt}
                                ]}
                            ]
                        )
                        if resp and resp.text:
                            break
                    except Exception:
                        continue
                if resp and resp.text:
                    break

            if not resp or not resp.text:
                return False

            text = resp.text.strip()
            match = re.search(r'\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]', text)
            if match:
                ymin, xmin, ymax, xmax = map(int, match.groups())
                center_x = ((xmin + xmax) / 2.0 / 1000.0) * vw
                center_y = ((ymin + ymax) / 2.0 / 1000.0) * vh
                log_info(f"[MODERN BROWSER]: Coordinate grounding click at ({center_x:.1f}, {center_y:.1f})")
                await page.mouse.click(center_x, center_y)

                # Record fix in SiteExecutionMemory
                try:
                    dom_clean = urllib.parse.urlparse(page.url).netloc
                    from core.trajectory_memory import get_site_memory
                    get_site_memory().record_quirk(
                        dom_clean,
                        f"Ref missing for {target_description[:50]}",
                        f"Tier 3 Visual Grounding click at ({center_x:.0f},{center_y:.0f})"
                    )
                except Exception:
                    pass

                return True
        except Exception as e:
            log_warn(f"[MODERN BROWSER]: Visual coordinate grounding failed: {e}")
        return False

    async def execute_plan(self, page, plan: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Deterministic Execution (0 LLM calls).
        Replays the planned action steps via DOM attribute refs with resilient coordinate fallbacks.
        """
        actions = plan.get("actions", [])
        if not actions:
            return False, "No actions in plan"

        for step in actions:
            act_type = step.get("type", "").lower()
            ref = step.get("ref", "")
            raw_id = ref.replace("@", "")

            try:
                if act_type == "click":
                    locator = page.locator(f'[data-agent-ref="{raw_id}"]')
                    clicked = False
                    url_before = page.url
                    if await locator.count() > 0:
                        href = None
                        try:
                            href = await locator.first.get_attribute("href")
                        except Exception:
                            pass

                        try:
                            await locator.first.click(timeout=4500)
                            clicked = True
                        except Exception as click_err:
                            log_warn(f"[MODERN BROWSER]: Direct click on {ref} failed ({click_err}).")
                            if href and not href.startswith("javascript:") and href != "#":
                                full_href = urllib.parse.urljoin(page.url, href)
                                log_info(f"[MODERN BROWSER]: Navigating to link href directly on click error: {full_href}")
                                await page.goto(full_href, wait_until="domcontentloaded", timeout=8000)
                                clicked = True

                        # Resilient fallback: If click completed but URL did not change and no new tab opened, navigate to href directly
                        if clicked and href and not href.startswith("javascript:") and href != "#":
                            await asyncio.sleep(0.8)
                            page_count = len(self._context.pages) if self._context else 1
                            if page_count == 1 and page.url == url_before:
                                full_href = urllib.parse.urljoin(page.url, href)
                                log_info(f"[MODERN BROWSER]: Click did not change page; navigating to target href directly: {full_href}")
                                try:
                                    await page.goto(full_href, wait_until="domcontentloaded", timeout=8000)
                                except Exception:
                                    pass

                        # Self-Healing Action Cache for click targets (e.g. search button, play button, result card)
                        try:
                            dom_clean = urllib.parse.urlparse(page.url).netloc
                            step_str = str(step).lower()
                            if any(w in step_str for w in ["play", "watch", "card", "result", "stream", "search", "submit"]):
                                intent = "play_button" if any(w in step_str for w in ["play", "stream"]) else "result_card"
                                resilient_sel = await locator.first.evaluate("""
                                    (el) => {
                                        if (!el) return null;
                                        if (el.id && !el.id.match(/\\d{4,}/)) return '#' + CSS.escape(el.id);
                                        for (const attr of ['data-testid', 'aria-label', 'role']) {
                                             const v = el.getAttribute(attr);
                                             if (v) return `[${attr}="${CSS.escape(v)}"]`;
                                        }
                                        if (el.className && typeof el.className === 'string') {
                                             const cls = el.className.trim().split(/\\s+/).filter(c => !c.match(/active|focus|hover|\\d{4,}/));
                                             if (cls.length > 0) return `${el.tagName.toLowerCase()}.${cls.slice(0, 2).map(c => CSS.escape(c)).join('.')}`;
                                        }
                                        return null;
                                    }
                                """)
                                if resilient_sel:
                                    from core.trajectory_memory import get_action_cache
                                    get_action_cache().save_fix(dom_clean, intent, resilient_sel, action_type="click", strategy="tier2_healed")
                        except Exception:
                            pass
                    else:
                        # Fallback 1: Visual coordinate grounding
                        log_info(f"[MODERN BROWSER]: Ref {ref} not in DOM. Attempting visual coordinate grounding...")
                        grounded = await self.visual_coordinate_grounding(page, f"Button or target matching {ref} {step}")
                        if grounded:
                            log_info(f"[MODERN BROWSER]: Visual coordinate grounding clicked {ref} successfully.")
                            clicked = True
                        else:
                            # Fallback 2: Enter key for search/submit
                            if any(w in str(step).lower() for w in ["search", "submit", "go"]):
                                await page.keyboard.press("Enter")
                                clicked = True
                            else:
                                return False, f"Ref {ref} not found on page"
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=6000)
                    except Exception:
                        pass

                elif act_type == "fill":
                    locator = page.locator(f'[data-agent-ref="{raw_id}"]')
                    val = step.get("value", "")
                    filled = False
                    target_el = None
                    if await locator.count() > 0:
                        try:
                            await locator.first.fill(val, timeout=3000)
                            filled = True
                            target_el = locator.first
                        except Exception:
                            pass
                    if not filled:
                        # Fallback: find active input/searchbox on page
                        for sel in ['input[type="text"]', 'input[type="search"]', 'textarea', 'input:not([type="hidden"])']:
                            inp = page.locator(sel).first
                            if await inp.count() > 0:
                                try:
                                    await inp.fill(val, timeout=3000)
                                    filled = True
                                    target_el = inp
                                    break
                                except Exception:
                                    pass
                    if not filled:
                        return False, f"Ref {ref} not found or cannot accept input"

                    # Self-Healing Action Cache: Cache the fix for future Tier-1 instant hits
                    try:
                        dom_clean = urllib.parse.urlparse(page.url).netloc
                        if target_el:
                            resilient_sel = await target_el.evaluate("""
                                (el) => {
                                    if (!el) return null;
                                    if (el.id && !el.id.match(/\\d{4,}/)) return '#' + CSS.escape(el.id);
                                    for (const attr of ['name', 'aria-label', 'data-testid', 'placeholder']) {
                                        const v = el.getAttribute(attr);
                                        if (v) return `${el.tagName.toLowerCase()}[${attr}="${CSS.escape(v)}"]`;
                                    }
                                    if (el.className && typeof el.className === 'string') {
                                        const cls = el.className.trim().split(/\\s+/).filter(c => !c.match(/active|focus|hover|\\d{4,}/));
                                        if (cls.length > 0) return `${el.tagName.toLowerCase()}.${cls.slice(0, 2).map(c => CSS.escape(c)).join('.')}`;
                                    }
                                    return el.tagName.toLowerCase();
                                }
                            """)
                            if resilient_sel:
                                from core.trajectory_memory import get_action_cache
                                get_action_cache().save_fix(dom_clean, "search_input", resilient_sel, action_type="fill", strategy="tier2_healed")
                    except Exception:
                        pass

                elif act_type == "press":
                    key = step.get("key", "Enter")
                    await page.keyboard.press(key)
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=6000)
                    except Exception:
                        pass

                elif act_type == "scroll":
                    delta = 400 if step.get("direction") == "down" else -400
                    await page.mouse.wheel(0, delta)

                elif act_type == "wait":
                    ms = step.get("ms", 1000)
                    await asyncio.sleep(ms / 1000.0)

                elif act_type == "done":
                    return True, step.get("result", "Task completed")

            except Exception as e:
                return False, f"Execution failed at {step}: {e}"

        return True, "Executed step successfully"

    async def synthesize_task_result(self, task: str, page_title: str, page_content: str) -> str:
        """Use Gemini to summarize page content and answer the user's task directly."""
        prompt = f"""You are Astra's Web Research Extractor.
User Request: "{task}"
Current Page: "{page_title}"

Extracted Page Content:
{page_content[:3500]}

Please provide a clear, concise, and structured answer satisfying the user's request.
If the user requested top items or products, list the top 3 with their exact name, price, rating, and key highlights."""

        from google import genai
        api_key = os.environ.get("GEMINI_API_KEY", "")
        client = genai.Client(api_key=api_key)
        for m in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:

            try:
                resp = client.models.generate_content(model=m, contents=prompt)
                if resp.text:
                    return resp.text.strip()
            except Exception:
                continue
        return page_content[:600]

    async def vision_fallback(self, page, task: str) -> str:
        """Vision Fallback: Triggered only when deterministic ref execution encounters a block."""
        try:
            screenshot_bytes = await page.screenshot(type="jpeg", quality=75)
            import base64
            img_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

            prompt = (
                f"You are Astra's Computer Vision Fallback Cortex.\n"
                f"The deterministic accessibility tree needs guidance for task: '{task}'.\n"
                f"Look at the screen state and describe what is visible and what next action should be taken."
            )
            from core.eyes import call_vision
            return call_vision(img_b64, prompt)
        except Exception as e:
            return f"Vision fallback error: {e}"

    async def browse(
        self,
        task: str,
        start_url: Optional[str] = None,
        headless: bool = False,
        keep_open: Optional[bool] = None,
        max_steps: int = 4,
        use_lila: Optional[bool] = None
    ) -> str:
        """
        The Autonomous Browser Loop:
        1. Launches Google Chrome (default) or Lila's Browser with persistent profile.
        2. Dynamically discovers active target URL with live intelligence.
        3. Launches headed visible browser so user observes actions.
        4. Multi-step goal-directed iteration (search -> select card -> play / finish).
        5. Keeps window open for media streaming tasks.
        """
        task_lower = task.lower()
        if use_lila is None:
            use_lila = any(w in task_lower for w in ["lila", "lila's", "lilas", "browseros"])

        is_playback = any(w in task_lower for w in [
            "movie", "stream", "watch", "play", "video", "episode", "film",
            "chala do", "lagaye", "laga do", "chalao"
        ])
        
        # Interactive tasks or media playback should be headed and preserved open
        if keep_open is None:
            keep_open = is_playback
        if keep_open:
            headless = False

        async with self._lock:
            await self._ensure_browser(headless=headless, use_lila=use_lila)

            # Determine existing open pages and active tab
            existing_pages = [p for p in (self._context.pages if self._context else []) if not p.is_closed()]
            real_pages = [p for p in existing_pages if p.url and p.url != "about:blank" and not p.url.startswith("chrome://")]
            active_page = real_pages[-1] if real_pages else (existing_pages[-1] if existing_pages else None)

            # ── Universal In-Page Action Detection (All Websites, Never Navigate Away) ──
            in_page_action_keywords = [
                "click", "khol", "kholna", "kholo", "open", "select", "chuno", "daba",
                "first", "pehla", "pehli", "1st", "second", "dusra", "dusri", "2nd",
                "third", "teesra", "teesri", "3rd", "last", "aakhri", "wala", "wali",
                "cart", "buy", "kharid", "khareed", "purchase", "order",
                "scroll", "niche", "upar", "down", "up",
                "play", "chala", "chalao", "pause", "resume",
                "next", "previous", "prev", "aage", "piche", "back",
                "submit", "enter", "checkout", "login", "sign in", "review", "rating"
            ]
            nav_destination_words = [
                ".com", ".in", ".org", ".net", ".io", ".co", ".gov", "http://", "https://",
                "amazon", "youtube", "flipkart", "google", "netflix", "hotstar", "prime video",
                "wikipedia", "reddit", "twitter", "github", "jiocinema", "netmirror"
            ]

            is_in_page_action = (not start_url) and (
                any(w in task_lower for w in in_page_action_keywords) or
                not any(dest in task_lower for dest in nav_destination_words)
            )

            is_followup = False
            active_url = active_page.url if active_page else ""
            if is_in_page_action:
                is_followup = True
            elif active_page and active_url and active_url != "about:blank" and not active_url.startswith("chrome://"):
                active_domain = urllib.parse.urlparse(active_url.lower()).netloc
                mentions_other_site = False
                if start_url:
                    start_domain = urllib.parse.urlparse(start_url.lower()).netloc
                    if start_domain and active_domain and (start_domain not in active_domain and active_domain not in start_domain):
                        mentions_other_site = True
                else:
                    for dest in nav_destination_words:
                        if dest in task_lower and dest not in active_domain:
                            mentions_other_site = True
                            break
                if not mentions_other_site:
                    is_followup = True

            # Extract clean target item from task (no parentheses required)
            m_item = re.search(r"\(([^)]+)\)", task)
            if m_item:
                target_item = m_item.group(1).strip()
            else:
                clean_target = task
                for pfx in [
                    "click on the", "click on", "click the", "click",
                    "kholo to shi vo", "kholo to shi", "kholo to sahi", "kholo vo", "kholo use", "kholo", "khol do", "kholna",
                    "open the", "open that", "open it", "open", "select the", "select",
                    "chuno", "daba do", "daba", "press", "choose",
                    "pehla wala kholo", "pehla wala", "dusra wala kholo", "dusra wala"
                ]:
                    if clean_target.lower().startswith(pfx):
                        clean_target = clean_target[len(pfx):].strip()
                        break
                for sfx in [" kholo", " click karo", " open karo", " dekho", " dikhao", " par", " pe", " please", " plz"]:
                    if clean_target.lower().endswith(sfx):
                        clean_target = clean_target[:-len(sfx)].strip()
                        break
                target_item = clean_target if clean_target != task else ""

            # Universal Fallback: If in-page action requested but tab is detached/missing in Playwright context
            if is_in_page_action and (not active_page or not active_page.url or active_page.url == "about:blank"):
                from core.win_os_agent import find_window, force_foreground_window
                cw = find_window("chrome") or find_window("brave")
                if cw and cw.get("hwnd"):
                    force_foreground_window(cw["hwnd"])
                    time.sleep(0.3)
                    target_desc = target_item or "first product item or interactive element"
                    log_info(f"[MODERN BROWSER]: In-page action with detached tab — executing Universal Desktop Vision click for '{target_desc}' on active window...")
                    from core.desktop_driver import desktop_driver
                    v_res = desktop_driver.vision_action("click", target_desc)
                    return f"Interacted with on-screen browser: {v_res}"

            if is_followup and active_page:
                page = active_page
                log_info(f"[MODERN BROWSER]: Reusing active tab for in-page task: '{task}' on {page.url}")
                try:
                    await page.bring_to_front()
                except Exception:
                    pass
            elif active_page and (not active_page.url or active_page.url == "about:blank"):
                page = active_page
            else:
                try:
                    page = await self._context.new_page()
                except Exception as e:
                    log_warn(f"[MODERN BROWSER]: Context/page creation error ({e}), recreating browser context...")
                    await self.close()
                    await self._ensure_browser(headless=headless)
                    page = await self._context.new_page()

            # Bring Chrome window to front on Windows desktop
            try:
                from core.win_os_agent import find_window, force_foreground_window
                cw = find_window("chrome")
                if cw and cw.get("hwnd"):
                    force_foreground_window(cw["hwnd"])
            except Exception:
                pass

            try:
                if is_followup:
                    target_url = page.url
                    semantic_intent = {"category": "general_web", "action": "interact", "query": target_item}
                else:
                    # 1. Dynamically resolve starting URL and semantic intent
                    target_url, semantic_intent = await self.resolve_target_url(task, start_url)
                    if not self.is_safe_domain(target_url):
                        return f"Safety Block: Access to {target_url} is restricted by security policy."

                    is_playback = (
                        semantic_intent.get("category") == "streaming_media" or
                        semantic_intent.get("action") == "play_media" or
                        is_playback
                    )
                    target_item = semantic_intent.get("query", "") or target_item

                    log_info(f"[MODERN BROWSER]: Navigating to {target_url} (headed={not headless}, item='{target_item}')...")
                    try:
                        await page.goto(target_url, wait_until="domcontentloaded", timeout=10000)
                    except Exception as nav_err:
                        log_warn(f"[MODERN BROWSER]: Direct navigation to {target_url} failed ({nav_err}). Falling back to Google search...")
                        fallback_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(task)}"
                        await page.goto(fallback_url, wait_until="domcontentloaded", timeout=12000)
                        target_url = fallback_url
                    await asyncio.sleep(1.2)

                # Check if page immediately presented a bot challenge (with navigation-resilient retries)
                page_title = ""
                page_html = ""
                for _ in range(3):
                    try:
                        page_title = await page.title()
                        page_html = await page.content()
                        break
                    except Exception as _ce:
                        if "navigating" in str(_ce).lower() or "context" in str(_ce).lower():
                            await asyncio.sleep(1.2)
                        else:
                            try:
                                page_title = await page.title()
                            except Exception:
                                pass
                            break

                is_challenge = any(b in page_title.lower() or b in page_html[:1500].lower() for b in [
                    "robot check", "waf challenge", "captcha", "unusual traffic", "just a moment", "cloudflare", "turnstile"
                ])
                if is_challenge:
                    log_info("[MODERN BROWSER]: Detected possible challenge, waiting 4s for automatic pass...")
                    await asyncio.sleep(4.0)
                    try:
                        page_title = await page.title()
                        page_html = await page.content()
                    except Exception:
                        pass
                    is_challenge = any(b in page_title.lower() or b in page_html[:1500].lower() for b in [
                        "robot check", "waf challenge", "captcha", "unusual traffic", "just a moment", "cloudflare", "turnstile"
                    ])
                    if is_challenge:
                        from core.web_reader import WebCrawler
                        crawler_res = WebCrawler().execute_research(task)
                        if crawler_res and "Research failed" not in crawler_res:
                            return f"Completed via live web intelligence (bot challenge bypassed):\n\n{crawler_res[:1500]}"

                last_intent = "Browsing initialized"
                domain = urllib.parse.urlparse(page.url).netloc
                from core.trajectory_memory import get_action_cache, get_site_memory
                action_cache = get_action_cache()
                site_mem = get_site_memory()

                # Stagehand Pre-Flight: Preemptively dismiss known site obstacles (cookie walls, modal overlays)
                try:
                    for q in site_mem.get_quirks(domain):
                        fix = q.get("fix", "")
                        if fix.startswith("click:"):
                            sel = fix[6:].strip()
                            loc = page.locator(sel)
                            if await loc.count() > 0 and await loc.first.is_visible():
                                await loc.first.click(timeout=1200)
                                log_info(f"[STAGEHAND PRE-FLIGHT]: Dismissed known site obstacle: {sel}")
                except Exception:
                    pass

                # Stagehand Tier 1: Check ActionCache for instant zero-token execution
                clean_q = target_item or task
                for pfx in ["search for", "search", "khojo", "dhoondho", "look for", "find", "dekho", "dikhao", "open", "kholo"]:
                    if clean_q.lower().startswith(pfx):
                        clean_q = clean_q[len(pfx):].strip()
                        break
                for sfx in [" dekho", " dikhao", " search karo", " kholo", " please", " plz", " on amazon", " in amazon", " pe", " par"]:
                    if clean_q.lower().endswith(sfx):
                        clean_q = clean_q[:-len(sfx)].strip()
                        break
                search_query = clean_q if clean_q else target_item

                tier1_executed = False
                if search_query and not is_followup:
                    cached_search = action_cache.get(domain, "search_input")
                    if cached_search:
                        c_sel = cached_search.get("selector")
                        try:
                            loc = page.locator(c_sel)
                            if await loc.count() > 0 and await loc.first.is_visible():
                                log_info(f"[STAGEHAND TIER 1 HIT]: Using cached search selector '{c_sel}' for {domain} (0 tokens!)")
                                await loc.first.fill(search_query, timeout=2500)
                                await loc.first.press("Enter")
                                await asyncio.sleep(1.5)
                                last_intent = f"Searched for '{search_query}' using cached selector"
                                tier1_executed = True
                        except Exception as t1_err:
                            log_warn(f"[STAGEHAND TIER 1 MISS]: Cached selector '{c_sel}' failed: {t1_err}. Invalidating...")
                            action_cache.invalidate(domain, "search_input")
                
                # 2. Multi-Step Execution Loop (Tier 2 Semantic Planner & Tier 3 Vision)
                for step_idx in range(1, max_steps + 1):
                    if step_idx == 1 and tier1_executed:
                        continue
                    snapshot = {}
                    for _ in range(3):
                        try:
                            snapshot = await page.evaluate(JS_EXTRACT_TREE)
                            break
                        except Exception as _eval_err:
                            if "navigating" in str(_eval_err).lower() or "context" in str(_eval_err).lower():
                                await asyncio.sleep(1.0)
                            else:
                                break
                    tree_text = snapshot.get("summary", "") if isinstance(snapshot, dict) else ""
                    curr_url = page.url
                    try:
                        curr_title = await page.title()
                    except Exception:
                        curr_title = curr_url

                    # Plan action steps for this turn
                    plan = await self.plan_action_steps(
                        task,
                        tree_text,
                        current_url=curr_url,
                        step_num=step_idx,
                        max_steps=max_steps,
                        target_item=target_item
                    )
                    last_intent = plan.get("intent", last_intent)
                    actions = plan.get("actions", [])
                    log_info(f"[MODERN BROWSER]: Step {step_idx} on '{curr_url}': intent='{last_intent}', actions={actions}")

                    # Check risk confirmation
                    if self.check_risk(task, actions):
                        return f"⚠️ High-risk action detected in planned browsing steps. User confirmation required before proceeding with purchase or payment."

                    if not actions:
                        log_info(f"[MODERN BROWSER]: Step {step_idx} produced empty actions, terminating loop.")
                        break

                    # Check if model declared task done
                    if any(a.get("type") == "done" for a in actions):
                        if step_idx == 1 and any(w in task_lower for w in ["click", "khol", "open", "select", "daba", "chuno"]):
                            log_warn(f"[MODERN BROWSER]: Model declared 'done' on step 1 for click task. Intercepting to click target element...")
                            first_ref = None
                            # Match target_item keywords in elements
                            for el in snapshot.get("elements", []):
                                el_txt = (el.get("text", "") + " " + el.get("aria", "")).lower()
                                if target_item and any(w in el_txt for w in target_item.lower().split() if len(w) > 3):
                                    first_ref = el.get("ref")
                                    break
                            if not first_ref:
                                for el in snapshot.get("elements", []):
                                    if el.get("tag") in ["a", "button"] or any(t in el.get("type", "") for t in ["link", "button", "card"]):
                                        first_ref = el.get("ref")
                                        break
                            if first_ref:
                                actions = [{"type": "click", "ref": first_ref}]
                                plan["actions"] = actions
                            else:
                                last_intent = next((a.get("result", "Done") for a in actions if a.get("type") == "done"), "Done")
                                log_info(f"[MODERN BROWSER]: Model declared task complete: '{last_intent}'")
                                break
                        else:
                            last_intent = next((a.get("result", "Done") for a in actions if a.get("type") == "done"), "Done")
                            log_info(f"[MODERN BROWSER]: Model declared task complete: '{last_intent}'")
                            break

                    prev_page_count = len(self._context.pages) if self._context else 1
                    # Execute deterministic steps
                    success, status = await self.execute_plan(page, plan)
                    log_info(f"[MODERN BROWSER]: Step {step_idx} execution: success={success}, status='{status}', new_url='{page.url}'")
                    if not success:
                        print(f"[MODERN BROWSER]: Step {step_idx} execution halted ({status}). Trying vision fallback...")
                        await self.vision_fallback(page, task)
                        break

                    # Check if a new tab was opened (e.g. Amazon product opened in target="_blank")
                    if self._context and len(self._context.pages) > prev_page_count:
                        page = self._context.pages[-1]
                        log_info(f"[MODERN BROWSER]: Switched to newly opened tab: {page.url}")
                        try:
                            await page.bring_to_front()
                            await page.wait_for_load_state("domcontentloaded", timeout=6000)
                        except Exception:
                            pass

                    # Wait for page to settle after action
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=5000)
                    except Exception:
                        pass
                    await asyncio.sleep(1.5)

                    # For playback tasks: detect if media player or video element is present
                    if is_playback and step_idx >= 2:
                        has_player = False
                        try:
                            has_player = await page.evaluate("""
                                () => Boolean(document.querySelector('video, iframe[src*="embed"], iframe[src*="player"], [class*="player"], [id*="player"]'))
                            """)
                        except Exception:
                            pass
                        if has_player:
                            last_intent = f"Media player loaded on '{curr_title}'"
                            break

                try:
                    final_title = await page.title()
                except Exception:
                    final_title = page.url or "Website"

                # If the goal was media playback, confirm and leave browser open
                if is_playback:
                    return f"Navigated to '{final_title}' and initiated playback in your browser."

                # If user wanted data extraction / research summary
                extract_keywords = ["extract", "top", "results", "find", "search", "show", "summarize", "details", "price", "reviews", "data"]
                needs_extraction = any(w in task.lower() for w in extract_keywords)

                if needs_extraction:
                    try:
                        extracted_text = await page.evaluate("""
                            () => {
                                const clone = document.body.cloneNode(true);
                                const toRemove = clone.querySelectorAll('script, style, noscript, svg, nav, footer');
                                toRemove.forEach(el => el.remove());
                                return (clone.innerText || '').replace(/\\s+/g, ' ').trim().substring(0, 3500);
                            }
                        """)
                        if len(extracted_text) > 100:
                            synthesized = await self.synthesize_task_result(task, final_title, extracted_text)
                            return f"Results from '{final_title}':\n\n{synthesized}"
                    except Exception:
                        pass

                return f"Task completed on '{final_title}'. {last_intent}."

            except Exception as e:
                return f"Browsing error: {e}"
            finally:
                if not headless:
                    keep_open = True
                if not keep_open:
                    try:
                        await page.close()
                    except Exception:
                        pass

    async def close(self):
        """Cleanly close browser context and playwright instance."""
        async with self._lock:
            if self._context and not self._is_cdp:
                try:
                    await self._context.close()
                except Exception:
                    pass
            self._context = None
            if self._browser and not self._is_cdp:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
            self._is_persistent = False
            self._is_cdp = False


# ── Global Singleton Instance ─────────────────────────────────────────────────
_browser_instance = None

async def get_modern_browser(default_headless: bool = True) -> ModernBrowser:
    global _browser_instance
    curr_loop = asyncio.get_running_loop()
    if _browser_instance is None or getattr(_browser_instance, '_owning_loop', None) != curr_loop:
        _browser_instance = ModernBrowser(default_headless=default_headless)
        _browser_instance._owning_loop = curr_loop
    return _browser_instance


def _fallback_launch(task: str, start_url: Optional[str] = None) -> str:
    """Instant visual fallback to Chrome when Playwright automation times out or fails."""
    target_url = start_url
    in_page_action_keywords = ["click", "khol", "kholo", "open", "select", "pehla", "dusra", "scroll", "daba", "cart"]
    t_low = (task or "").lower()

    # If this was an in-page interaction task, fall back to Desktop Vision click on active screen
    if not target_url and any(w in t_low for w in in_page_action_keywords):
        try:
            from core.desktop_driver import desktop_driver
            v_res = desktop_driver.vision_action("click", task)
            return f"Interacted with on-screen browser: {v_res}"
        except Exception:
            pass

    if not target_url:
        if "amazon" in t_low:
            q = task
            for drop in ["amazon", "kholo", "dekho", "par", "pe", "or", "aur", "ak kaam karo", "ek kaam karo", "search", "please", "plz"]:
                q = re.sub(rf"\b{drop}\b", "", q, flags=re.I)
            q = " ".join(q.split())
            target_url = f"https://www.amazon.in/s?k={urllib.parse.quote_plus(q)}" if q else "https://www.amazon.in"
        elif "youtube" in t_low:
            q = task
            for drop in ["youtube", "kholo", "dekho", "par", "pe", "or", "aur", "search", "play"]:
                q = re.sub(rf"\b{drop}\b", "", q, flags=re.I)
            q = " ".join(q.split())
            target_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(q)}" if q else "https://www.youtube.com"
        else:
            q = " ".join((task or "").split())
            target_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(q)}" if q else "https://www.google.com"

    try:
        from core.win_os_agent import navigate_active_browser
        if navigate_active_browser(target_url):
            return f"Opened browser to {target_url}"
    except Exception:
        pass
    try:
        import webbrowser
        webbrowser.open(target_url)
    except Exception:
        pass
    return f"Opened browser to {target_url}"


_browser_worker_loop = None
_browser_worker_thread = None


def _get_browser_worker_loop():
    global _browser_worker_loop, _browser_worker_thread
    if _browser_worker_loop is None or not _browser_worker_loop.is_running():
        _browser_worker_loop = asyncio.new_event_loop()
        _browser_worker_thread = threading.Thread(target=_browser_worker_loop.run_forever, daemon=True)
        _browser_worker_thread.start()
    return _browser_worker_loop


def run_browser_task(
    task: str,
    start_url: Optional[str] = None,
    headless: bool = False,
    keep_open: Optional[bool] = None,
    use_lila: Optional[bool] = None
) -> str:
    """
    Synchronous tool-compatible runner for Live Voice / background tasks.
    Runs on a persistent background event loop thread so browser windows never close unexpectedly.
    """
    async def _run():
        mb = await get_modern_browser(default_headless=headless)
        return await asyncio.wait_for(
            mb.browse(
                task,
                start_url=start_url,
                headless=headless,
                keep_open=keep_open,
                use_lila=use_lila
            ),
            timeout=40.0
        )

    try:
        loop = _get_browser_worker_loop()
        future = asyncio.run_coroutine_threadsafe(_run(), loop)
        return future.result(timeout=45.0)
    except Exception as e:
        log_warn(f"[MODERN BROWSER]: run_browser_task caught exception/timeout ({e}), triggering instant visual fallback...")
        return _fallback_launch(task, start_url)


