"""
core/browseros_bridge.py — Lila's Browser (BrowserOS neo) MCP Client Bridge
=============================================================================
Architecture & Invariants:
1. Setup vs. Runtime Separation:
   - This module operates EXCLUSIVELY as an MCP client against a running BrowserOS neo instance.
   - It NEVER downloads, clones, or installs browser binaries at runtime.
2. Dynamic Discovery:
   - Resiliently probes BrowserOS neo's MCP endpoint (9200/mcp, 9100/mcp, 9100/sse, or local config).
3. Overlay Tie-In:
   - Automatically synchronizes with core.state_bridge:
     * mood: "focused" on task dispatch
     * mood: "excited" on task completion
     * mood: "sleepy" on error
4. Standardized Output:
   - Returns a structured BrowseResult dictionary compatible with all Jarvis tools and voice agents.
"""

import os
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import json
import time
import asyncio
import urllib.parse
from typing import Optional, Dict, Any, List

import httpx
from core.jarvis_logger import log_info, log_warn, log_error

# Configuration & Candidate Endpoints
DEFAULT_CANDIDATES = [
    "http://127.0.0.1:9210/mcp",
    "http://127.0.0.1:9200/mcp",
    "http://127.0.0.1:9100/mcp",
    "http://127.0.0.1:9100/sse"
]

CONFIG_FILE = Path(__file__).resolve().parent.parent / "lila-browser-integration" / "config" / "browseros-providers.json"


def _load_config() -> Dict[str, Any]:
    """Loads provider and connection configuration."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log_warn("browseros_bridge", f"Failed to parse config file: {e}")
    return {}


def _discover_config_port() -> Optional[int]:
    """Inspects local BrowserOS neo (browserclaw) config for dynamically bound port."""
    home = Path.home()
    candidate_paths = [
        home / ".browserclaw" / "config.json",
        home / ".browserclaw" / "settings.json",
        Path(os.environ.get("LOCALAPPDATA", "")) / "BrowserOS" / "config.json"
    ]
    for p in candidate_paths:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    port = data.get("mcp_port") or data.get("server_port") or data.get("port")
                    if port and isinstance(port, int):
                        return port
            except Exception:
                pass
    return None


async def probe_mcp_endpoint(timeout_sec: float = 1.2) -> Optional[str]:
    """
    Dynamically probes candidate endpoints to find the active BrowserOS neo MCP server.
    Returns the valid base URL or None if server is not responding.
    """
    cfg = _load_config()
    mcp_cfg = cfg.get("mcp_connection", {})
    candidates = list(mcp_cfg.get("fallback_endpoints", []))
    primary = mcp_cfg.get("primary_endpoint")
    if primary and primary not in candidates:
        candidates.insert(0, primary)
    for d in DEFAULT_CANDIDATES:
        if d not in candidates:
            candidates.append(d)

    # If local port was found in config, prioritize it
    cfg_port = _discover_config_port()
    if cfg_port:
        cfg_url = f"http://127.0.0.1:{cfg_port}/mcp"
        if cfg_url not in candidates:
            candidates.insert(0, cfg_url)

    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        for url in candidates:
            try:
                # 1. Test MCP GET / Streamable HTTP probe
                resp = await client.get(url)
                if resp.status_code in [200, 400, 404, 405]:
                    # Server is listening on this port
                    log_info("browseros_bridge", f"Found active BrowserOS MCP server at {url} (HTTP {resp.status_code})")
                    return url
            except Exception:
                pass

            # 2. Test base /health endpoint on same origin
            try:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                health_url = f"{parsed.scheme}://{parsed.netloc}/health"
                resp = await client.get(health_url)
                if resp.status_code == 200:
                    log_info("browseros_bridge", f"Found active BrowserOS server via /health at {health_url}")
                    return url
            except Exception:
                pass

    return None


def _update_overlay_mood(mood: str, caption: str):
    """Safely notifies the Lila floating companion overlay without crashing."""
    try:
        from core.state_bridge import broadcast_state
        broadcast_state(mood=mood, caption=caption)
    except Exception as ex:
        log_warn("browseros_bridge", f"Overlay state broadcast skipped: {ex}")


async def browse_via_lila_browser(task: str, url: Optional[str] = None) -> Dict[str, Any]:
    """
    Asynchronously executes a browsing task through Lila's Browser (BrowserOS neo).
    Returns a standardized BrowseResult.
    """
    task_clean = (task or "").strip()
    log_info("browseros_bridge", f"Initiating browsing mission: '{task_clean}'")

    # 1. Notify overlay: mood -> focused
    _update_overlay_mood("focused", f"Lila's Browser: {task_clean[:55]}...")

    # Direct launch request for Lila's Browser
    t_lower = task_clean.lower()
    if any(k in t_lower for k in [
        "open lila's browser", "launch lila's browser", "open lila browser", 
        "start lila's browser", "launch lila browser", "open browser",
        "launch browser", "start browser", "open lila", "lila browser",
        "lila's browser", "start lila"
    ]) and not any(w in t_lower for w in ["search", "find", "what is", "who is", "latest news"]):
        import subprocess
        chrome_path = os.path.expandvars(r"%LOCALAPPDATA%\BrowserClaw\Application\chrome.exe")
        start_page = str(Path(__file__).resolve().parent.parent / "lila-browser-integration" / "branding" / "start-page.html")
        if os.path.exists(chrome_path):
            subprocess.Popen([chrome_path, start_page])
            _update_overlay_mood("excited", "Opened Lila's Browser! ✨")
            return {
                "success": True,
                "task": task_clean,
                "url": start_page,
                "summary": "Opened Lila's Browser with your custom start page on your desktop.",
                "content": "Opened Lila's Browser.",
                "actions_taken": ["Launched BrowserOS neo (Lila's Browser)"],
                "engine": "Lila's Browser"
            }

    start_time = time.time()
    _update_overlay_mood("focused", f"Lila's Browser: {task_clean[:55]}...")

    # 2. Actionable browsing or playback mission on Lila's Browser
    # Uses full autonomous vision/DOM perception engine driving BrowserClaw (Lila's Browser)!
    is_playback = any(w in t_lower for w in [
        "movie", "stream", "watch", "play", "video", "episode", "film",
        "chala do", "lagaye", "laga do", "chalao", "netmirror", "youtube"
    ])

    try:
        from core.modern_browser import run_browser_task
        res_str = await asyncio.to_thread(
            run_browser_task,
            task=task_clean,
            start_url=url,
            headless=not is_playback,
            keep_open=is_playback,
            use_lila=True
        )
        elapsed = round(time.time() - start_time, 2)
        _update_overlay_mood("excited", "Lila's Browser action complete! ✨")
        return {
            "success": True,
            "task": task_clean,
            "url": url,
            "summary": res_str or f"Completed '{task_clean}' on Lila's Browser.",
            "content": res_str,
            "actions_taken": [f"Autonomously navigated via Lila's Browser in {elapsed}s"],
            "engine": "Lila's Browser (Autonomous Engine)",
            "elapsed_seconds": elapsed
        }
    except Exception as fallback_err:
        elapsed = round(time.time() - start_time, 2)
        _update_overlay_mood("sleepy", "Browser task paused.")
        return {
            "success": False,
            "task": task_clean,
            "url": url,
            "summary": f"Could not complete browse task: {fallback_err}.",
            "content": "",
            "actions_taken": [],
            "engine": "Lila's Browser",
            "elapsed_seconds": elapsed,
            "error": str(fallback_err)
        }


def browse_via_lila_browser_sync(task: str, url: Optional[str] = None) -> Dict[str, Any]:
    """Synchronous wrapper for browse_via_lila_browser."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(asyncio.run, browse_via_lila_browser(task, url))
                return fut.result(timeout=60.0)
        else:
            return loop.run_until_complete(browse_via_lila_browser(task, url))
    except Exception:
        return asyncio.run(browse_via_lila_browser(task, url))


if __name__ == "__main__":
    print("Testing Lila's Browser MCP Client Bridge...")
    res = browse_via_lila_browser_sync("Check current weather in Mumbai")
    print("Result:", json.dumps(res, indent=2))
