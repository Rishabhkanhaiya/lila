"""
core/live_tools.py — JARVIS PRO Gemini Live Agentic Tool Registry
=================================================================
Exposes EVERY capability of the JARVIS ecosystem as native Gemini Live
function declarations. When Gemini Live decides to call a tool during a
live audio conversation, the dispatcher here executes it against the real
JARVIS backend and returns the result back to the session.

Architecture:
  Live Audio → Gemini 2.5 Native Audio → tool_call signal
     → dispatch_tool_call() → executes real JARVIS module function
     → returns result → session.send_tool_response()
     → Gemini speaks the result back in native voice

Tool Categories:
  1. OS & Windows Control       (win_fast_voice)
  2. Web Browser & Automation   (web_agent, jarvis_browser_actions)
  3. Web Research               (web_reader, astra_research)
  4. Memory & Knowledge Vault   (vector_vault, graph_memory, memory_engine)
  5. Finance & Markets          (finance_engine)
  6. Documents & Files          (doc_forge, codebase_oracle)
  7. Macros & Automation        (macro_engine)
  8. Widgets & UI               (widget_forge)
  9. Swarm & Multi-Agent        (swarm, mesh_network)
  10. Health & System Diagnostics (health_check)
  11. Computer Use Agent         (computer_use_agent)
  12. Mobile Agent               (mobile_agent)
  13. Background Task Queue      (queue_db)
  14. Proactive Intelligence     (proactive_orchestrator)
  15. Self-Evolution & Code AI   (self_evolution)
  16. Vision & Screen Analysis   (eyes)
  17. Wormhole Tunneling         (wormhole)
  18. Outbound Calls             (outbound_call_orchestrator)
  19. Analytics                  (analytics_engine)
  20. JARVIS Live Information    (datetime, system info)
"""

import datetime
import os
import sys
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor
import json
import re
import traceback
from typing import Any, Dict, List, Optional
from google.genai import types

from core.jarvis_logger import log_error, log_warn, log_info

_live_tools_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="LiveToolsWorker")


# ─────────────────────────────────────────────────────────────────────────────
# Gemini Function Declaration Schemas
# ─────────────────────────────────────────────────────────────────────────────

def _str_param(name: str, desc: str, required: bool = True) -> dict:
    return {"name": name, "description": desc, "type": "STRING"}

def _int_param(name: str, desc: str) -> dict:
    return {"name": name, "description": desc, "type": "INTEGER"}

def _bool_param(name: str, desc: str) -> dict:
    return {"name": name, "description": desc, "type": "BOOLEAN"}

def _enum_param(name: str, desc: str, values: list) -> dict:
    return {"name": name, "description": desc, "type": "STRING", "enum": values}

def _make_decl(name: str, description: str, params: list, required: list = None) -> dict:
    """Build a Gemini function declaration dict with enum support."""
    properties = {}
    for p in params:
        prop = {"type": p["type"], "description": p["description"]}
        if "enum" in p:
            prop["enum"] = p["enum"]
        properties[p["name"]] = prop
    schema = {"type": "OBJECT", "properties": properties}
    if required:
        schema["required"] = required
    return {
        "name": name,
        "description": description,
        "parameters": schema
    }


ALL_DECLARATIONS = [
    # 1. Lila Companion & Avatar Controller
    _make_decl(
        "lila_companion",
        "Control Lila's 3D desktop companion avatar UI overlay, mood, and expressive humanoid motion-capture dances right on Rishabh's screen.",
        [
            _enum_param("action", "Action to perform: 'open' (launch/focus 3D UI on screen), 'close' (hide UI), 'dance' (perform full-body humanoid dance), 'list_dances' (list 10 dance routines)", ["open", "close", "dance", "list_dances"]),
            _str_param("model", "Optional 3D avatar model name: 'ana' (default), 'lila'"),
            _str_param("dance_style", "Optional dance style for 'dance' action: 'hiphop', 'wave_hiphop', 'samba', 'twist', 'jazz', 'party', 'rumba', 'tut_hiphop', 'step_hiphop', 'breakdance_uprock', or 'random'")
        ],
        required=["action"]
    ),

    # 2. Autonomous Web Agent & Research
    _make_decl(
        "web_agent",
        "Autonomous Web Agent: Search the web, browse websites, click web elements, scrape content, conduct deep research, or hunt and download documents/templates (.pptx, .pdf, .docx).",
        [
            _enum_param("action", "Action to perform: 'search' (web search with citations), 'browse' (autonomous browser task/navigation), 'click' (click web element or link), 'scrape' (extract content from URL), 'research' (multi-source deep research), 'download' (hunt & download files/templates)", ["search", "browse", "click", "scrape", "research", "download"]),
            _str_param("query", "Search query, browsing task, research question, or file to download"),
            _str_param("url", "Optional target website URL"),
            _str_param("file_type", "Optional file type for download: 'pptx', 'pdf', 'docx', 'xlsx', 'zip'")
        ],
        required=["action"]
    ),

    # 2b. Lila Browser Copilot (Brave Extension)
    _make_decl(
        "browser_control",
        "Control the Brave browser directly via the Lila Browser Copilot extension. List tabs, switch tabs, close tabs, get live page context/reader text, search on page, fill inputs and click buttons, summarize YouTube transcripts, and control media without focus stealing.",
        [
            _enum_param("action", "Browser action to perform: 'search' (type query into search bar and click Search/submit), 'fill_and_click' (fill input and click button), 'click_element' (click button/link by accessible name), 'fill_element' (type into input), 'list_tabs', 'switch_tab', 'close_tab', 'get_context', 'get_transcript', 'media_play', 'media_pause', 'media_volume'", [
                "search", "fill_and_click", "click_element", "fill_element",
                "list_tabs", "switch_tab", "close_tab", "get_context",
                "get_transcript", "media_play", "media_pause", "media_volume"
            ]),
            _int_param("tab_id", "Optional tab ID for tab-specific actions"),
            _str_param("target", "For click_element/fill_element/fill_and_click: accessible name of element (e.g. 'search', 'Download', 'Search')", required=False),
            _str_param("value", "For search/fill_element/fill_and_click: text to type or search query. For media_volume: volume 0.0-1.0", required=False),
            _str_param("click_target", "For fill_and_click/search: accessible name of button to click (e.g. 'Search', 'Submit')", required=False),
            _str_param("url", "For media_play: the media or video URL", required=False)
        ],
        required=["action"]
    ),

    # 3. Multimedia & Playback
    _make_decl(
        "media_player",
        "Universal multimedia player: Play songs/videos on YouTube, stream movies/series on streaming platforms (NetMirror, Netflix, Prime), control media playback (play/pause/next), and adjust system volume.",
        [
            _enum_param("action", "Action: 'play_youtube' (play song/video on YouTube), 'stream_movie' (play movie on NetMirror/Netflix), 'control' (play/pause/next/prev/mute), 'volume' (set system volume)", ["play_youtube", "stream_movie", "control", "volume"]),
            _str_param("query", "Song name, movie title, volume level ('up'/'down'/'mute'/'70'), or media control action ('play'/'pause'/'next')"),
            _str_param("platform", "Optional streaming platform: 'NetMirror', 'Netflix', 'YouTube', 'Prime'"),
            _str_param("browser", "Optional browser to use: 'chrome' or 'lila'")
        ],
        required=["action"]
    ),

    # 4. Computer-Use & Screen Action
    _make_decl(
        "screen_action",
        "Computer-Use Agent: Visually inspect the screen, read active desktop state, locate and click UI elements or desktop icons, open project deliverables/artifacts, type text, press keyboard shortcuts (win+d, alt+tab, ctrl+c), or scroll windows.",
        [
            _enum_param("action", "Action: 'read' (read active window/tab/state), 'click' (click element/icon by name), 'type' (type text), 'open_artifact' (open deliverable/plan/walkthrough), 'analyze' (describe screen content), 'double_click' (double click), 'right_click' (context menu), 'press_keys' (hotkey combination), 'scroll' (scroll page/window)", ["read", "click", "type", "open_artifact", "analyze", "double_click", "right_click", "press_keys", "scroll"]),
            _str_param("target", "Element/icon name to click, artifact name to open ('walkthrough.md'), prompt to analyze, text to type, or keys to press ('ctrl+c', 'win+d', 'alt+tab')"),
            _str_param("direction", "Optional scroll direction: 'down', 'up', 'top', 'bottom'")
        ],
        required=["action"]
    ),

    # 5. Window & App Manager
    _make_decl(
        "window_manager",
        "Windows application and window management: Open apps (Chrome, Notepad, VS Code, Spotify), close apps, switch window focus, minimize, maximize, snap windows, inspect foreground/background focus, or search Start menu.",
        [
            _enum_param("action", "Action: 'open' (launch app), 'close' (close app), 'switch' (bring window to front), 'minimize' (minimize window), 'maximize' (maximize window), 'snap' (snap left/right/up/down), 'search_start' (search Windows Start), 'focus_state' (inspect who is in focus and who is in background)", ["open", "close", "switch", "minimize", "maximize", "snap", "search_start", "focus_state"]),
            _str_param("app_name", "Application name (e.g. 'chrome', 'notepad', 'vscode', 'spotify') or search query or snap direction ('left', 'right')")
        ],
        required=["action"]
    ),

    # 6. Google Antigravity (AGY) Autonomous Engineer
    _make_decl(
        "antigravity",
        "Google Antigravity Autonomous Software Engineer: Execute coding tasks, navigate workspaces, list projects, check agent/task status, analyze action history, view artifacts, or access prompting guides.",
        [
            _enum_param("action", "Action: 'execute' (synthesize and run coding task), 'navigate' (switch workspace), 'list' (list indexed workspaces), 'status' (inspect operating status), 'analyze' (view executed actions), 'view_artifact' (read/open walkthrough or plan), 'prompting_guide' (retrieve prompting guide)", ["execute", "navigate", "list", "status", "analyze", "view_artifact", "prompting_guide"]),
            _str_param("prompt", "Coding task description, project name, or artifact name ('walkthrough', 'implementation_plan')"),
            _str_param("project_name", "Optional target project workspace (e.g. 'Jarvis_Test', 'jarvis_project')"),
            _str_param("strategy", "Optional prompting strategy: 'feature', 'debug', 'perf', 'refactor', 'grill_me', 'swarm'")
        ],
        required=["action"]
    ),

    # 7. File System & Documents
    _make_decl(
        "file_manager",
        "File system and document manager: Read files, write files, open files on screen, list directories, create documents (.docx, .pdf), or organize cluttered folders (Downloads, Desktop).",
        [
            _enum_param("action", "Action: 'read' (read text file), 'write' (write content to file), 'open' (open file on screen), 'list' (list directory contents), 'organize' (sort cluttered folder), 'create_doc' (create structured document)", ["read", "write", "open", "list", "organize", "create_doc"]),
            _str_param("path", "File path, folder path, or folder alias ('Downloads', 'Desktop')"),
            _str_param("content", "Optional content to write or document topic/specification")
        ],
        required=["action"]
    ),

    # 8. Codebase Intelligence & IDE
    _make_decl(
        "code_engineer",
        "Codebase intelligence and VS Code controller: Semantic codebase querying, repository vector ingestion, and VS Code IDE actions.",
        [
            _enum_param("action", "Action: 'query' (search codebase semantically), 'ingest' (index repository into vector store), 'vscode' (run VS Code command)", ["query", "ingest", "vscode"]),
            _str_param("query", "Code search query, repository directory path, or VS Code action"),
            _str_param("path", "Optional file path or project directory")
        ],
        required=["action"]
    ),

    # 9. Neural Memory Vault
    _make_decl(
        "memory_vault",
        "Neural memory and knowledge vault: Search stored knowledge, store user facts, recall facts, recall past episodes, read daily reflection diary, write diary reflection, or retrieve memory context.",
        [
            _enum_param("action", "Action: 'search' (search vault), 'store_fact' (remember user fact), 'recall_facts' (recall facts by category), 'recall_episode' (recall past event), 'read_diary' (read today's diary), 'write_diary' (log reflection), 'context' (get active memory context), 'ingest_doc' (index document)", ["search", "store_fact", "recall_facts", "recall_episode", "read_diary", "write_diary", "context", "ingest_doc"]),
            _str_param("query", "Fact to store/recall, memory search query, diary text, or document file path"),
            _str_param("category", "Optional category: 'user_preferences', 'projects', 'personal', 'habits'")
        ],
        required=["action"]
    ),

    # 10. OmniForge App Builder
    _make_decl(
        "app_builder",
        "OmniForge Full-Stack App Builder & Web Generator: Build responsive full-stack web applications, generate Tailwind HTML webpages, open project in VS Code, and launch live preview on localhost:5252.",
        [
            _enum_param("action", "Action: 'build_app' (build full-stack web app), 'generate_webpage' (generate Tailwind HTML page), 'open_preview' (launch preview in browser), 'list_apps' (list active apps)", ["build_app", "generate_webpage", "open_preview", "list_apps"]),
            _str_param("description", "Description of the web application or webpage to build"),
            _str_param("project_name", "Optional app project name")
        ],
        required=["action"]
    ),

    # 11. Universal System Control
    _make_decl(
        "system_control",
        "Universal system engine: Execute shell/PowerShell commands, run health check diagnostics, inspect system resources (CPU, RAM, GPU), and read/write the Windows clipboard.",
        [
            _enum_param("action", "Action: 'shell' (run shell command), 'diagnostics' (run health check), 'system_info' (hardware stats), 'clipboard_read' (read clipboard), 'clipboard_write' (copy text to clipboard)", ["shell", "diagnostics", "system_info", "clipboard_read", "clipboard_write"]),
            _str_param("command", "Shell command to execute, or text to copy to clipboard")
        ],
        required=["action"]
    ),

    # 12. Mesh Network & Wormhole
    _make_decl(
        "mesh_network",
        "Zero-latency device mesh & Wormhole tunneling: Inspect connected device nodes, send text/files to mobile, open secure public ngrok/cloudflared tunnel, close tunnel, or get tunnel URL.",
        [
            _enum_param("action", "Action: 'status' (inspect mesh network), 'send' (send data/file to device), 'open_tunnel' (open public tunnel), 'close_tunnel' (kill tunnel), 'get_url' (get active tunnel URL)", ["status", "send", "open_tunnel", "close_tunnel", "get_url"]),
            _str_param("target_device", "Target device name or 'mobile'"),
            _str_param("payload", "Text message, file path, or port number (e.g. '8766', '5252')")
        ],
        required=["action"]
    ),

    # 13. Task & Swarm Orchestrator
    _make_decl(
        "task_orchestrator",
        "Autonomous multi-step pipeline & multi-agent swarm orchestrator: Execute multi-step sequential action pipelines across tools (e.g. research -> code -> write file -> open preview), spawn background missions, create persistent tasks, inspect task queue, or execute multi-agent swarms.",
        [
            _enum_param("action", "Action: 'execute_pipeline' (autonomous sequential multi-step pipeline across tools e.g. research -> write code -> open preview), 'spawn_mission' (run background mission), 'create_task' (add persistent task), 'list_tasks' (view task queue), 'run_swarm' (execute multi-agent swarm), 'swarm_status' (check swarm progress)", ["execute_pipeline", "spawn_mission", "create_task", "list_tasks", "run_swarm", "swarm_status"]),
            _str_param("prompt", "Mission description, pipeline goal, task title, or swarm goal"),
            _str_param("task_type", "Optional mission or task category: 'pipeline', 'research', 'download', 'organize', 'coding'")
        ],
        required=["action"]
    ),

    # 14. Macro & Desktop Widgets
    _make_decl(
        "macro_widget",
        "Desktop automation macros & Widget Forge: Run recorded macros, save macro sequence, list macros, spawn live desktop glass widgets, or close widgets.",
        [
            _enum_param("action", "Action: 'run_macro' (execute saved macro), 'save_macro' (save command sequence), 'list_macros' (list all macros), 'spawn_widget' (open desktop widget), 'close_widget' (close widget)", ["run_macro", "save_macro", "list_macros", "spawn_widget", "close_widget"]),
            _str_param("name", "Macro name or widget type ('clock', 'system_monitor', 'pomodoro', 'all')"),
            _str_param("commands", "Optional JSON commands list for save_macro")
        ],
        required=["action"]
    ),

    # 15. Ambient Environment Information
    _make_decl(
        "info_query",
        "Ambient environment queries: Get current local time, date, day of week, timezone, weather forecasts for any city, or ecosystem analytics.",
        [
            _enum_param("action", "Action: 'time' (current date, time & timezone), 'weather' (weather report), 'analytics' (usage analytics)", ["time", "weather", "analytics"]),
            _str_param("city", "Optional city name for weather (e.g. 'Mumbai', 'Delhi', 'London')")
        ],
        required=["action"]
    ),
    # 16. Realtime Interactive Multimodal UI Controller
    _make_decl(
        "show_realtime_ui",
        "Display an interactive real-time visual UI card tray directly on screen across ALL domains (music, movies, coding stacks, code reviews, system health metrics, volume/brightness sliders, job hunts, decision matrices, chips) instead of verbally reciting lists. Use this whenever the user asks for options, recommendations, or controls that are better seen and clicked visually.",
        [
            _enum_param("ui_type", "Type of UI: 'music_picker', 'movie_picker', 'code_stack_picker', 'code_review', 'system_health', 'slider_control', 'job_hunt_picker', 'file_action_picker', 'decision_matrix', 'chips_cloud', 'choice_cards', 'dismiss'", ["music_picker", "movie_picker", "code_stack_picker", "code_review", "system_health", "slider_control", "job_hunt_picker", "file_action_picker", "decision_matrix", "chips_cloud", "choice_cards", "dismiss"]),
            _str_param("title", "Header title for the card (e.g. 'Choose Tech Stack', 'System Health', 'Choose Music Vibe')"),
            _str_param("subtitle", "Optional subtitle or guidance (e.g. 'Tap to execute or speak your choice')"),
            _str_param("category", "Optional active category"),
            _str_param("layout", "Optional visual layout: 'cards', 'chips', 'code', 'metric', 'slider', 'decision'"),
            _str_param("code_snippet", "Optional code snippet string for 'code_review' layout"),
            _str_param("custom_options", "Optional JSON string or comma-separated list of custom options")
        ],
        required=["ui_type", "title"]
    ),
]


# Single consolidated tool config for LiveConnectConfig
LIVE_TOOLS_CONFIG = [{"function_declarations": ALL_DECLARATIONS}]


# ─────────────────────────────────────────────────────────────────────────────
# Tool Dispatcher — executes real JARVIS modules
# ─────────────────────────────────────────────────────────────────────────────

def dispatch_tool_call(tool_name: str, args: dict) -> dict:
    """
    Central dispatcher. Receives a tool name + args from Gemini Live,
    executes the real backend function, and returns a result dict.
    Always returns {"result": str} or {"error": str} — never raises.
    """
    try:
        log_info("live_tools", f"Dispatching: {tool_name}({args})")
        result = _execute(tool_name, args)
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False, default=str)
        return {"result": result}
    except Exception as e:
        log_error("live_tools", "dispatch", e)
        return {"error": f"Tool {tool_name} failed: {str(e)}"}


def _play_movie_or_stream(title: str, platform: str = "", url: Optional[str] = None, browser: Optional[str] = None) -> str:
    """
    Universal media streaming dispatcher across NetMirror, Netflix, Prime Video, YouTube, JioCinema, Hotstar.
    Guarantees strict platform fidelity.
    """
    from core.media_streaming import play_or_stream_media
    clean_title = (title or "").strip()
    target_platform = (platform or "").strip() or None
    return play_or_stream_media(clean_title, explicit_platform=target_platform, browser=browser)



def _execute(name: str, a: dict) -> str:
    """Route tool name to actual implementation (supports 16 polymorphic tools + legacy tools)."""

    # ── Legacy & Direct Tool Aliases (Instant Fallback Routing) ────────────────
    if name == "open_website":
        u = a.get("url") or a.get("query") or a.get("target") or ""
        from core.win_os_agent import launch_or_focus_browser
        launch_or_focus_browser(u)
        return f"Opened website: {u}"

    if name in ("play_youtube", "play_video"):
        q = a.get("query") or a.get("title") or a.get("song") or ""
        return _play_movie_or_stream(q, platform="youtube", browser=a.get("browser"))

    if name == "search_web_or_site":
        q = a.get("query") or a.get("task") or ""
        plat = a.get("platform")
        if plat == "youtube":
            return _play_movie_or_stream(q, platform="youtube")
        return _execute("web_search", {"query": q})

    # ═════════════════════════════════════════════════════════════════════════
    # 🌟 16 UNIFIED POLYMORPHIC TOOL HANDLERS
    # ═════════════════════════════════════════════════════════════════════════


    # 1. Lila Desktop Companion
    if name == "lila_companion":
        act = (a.get("action") or "open").lower().strip()
        if act == "open":
            from core.lila_companion_launcher import open_lila_companion_ui
            return open_lila_companion_ui(model=a.get("model", "ana"))
        elif act == "close":
            from core.lila_companion_launcher import close_lila_companion_ui
            return close_lila_companion_ui()
        elif act == "dance":
            return _execute("perform_dance", {"dance_style": a.get("dance_style", "random")})
        elif act == "list_dances":
            return _execute("list_dance_styles", {})
        return f"Unknown lila_companion action: {act}"

    # 2. Autonomous Web Agent & Research
    if name == "web_agent":
        act = (a.get("action") or "search").lower().strip()
        q = a.get("query") or a.get("task") or ""
        u = a.get("url")
        # Auto-chain detection: If prompt asks to search/research AND write/save to file or open preview
        if any(k in q.lower() for k in [" and write ", " aur likho ", " and save ", " and create ", " aur save karo ", " and open preview", " aur browser mein kholo"]):
            from core.mission_pipeline import run_mission_pipeline
            return run_mission_pipeline(goal=q, task_type="pipeline")

        if act == "search":
            # Dual-plane search: Perform visible search in Brave / browser on screen AND return research results for voice
            try:
                from core.state_bridge import is_extension_connected, send_copilot_command_sync
                if is_extension_connected():
                    send_copilot_command_sync("dom.search", {"targetDescription": "search", "clickTarget": "Search", "value": q}, timeout=2.0)
                else:
                    import urllib.parse
                    from core.win_os_agent import launch_or_focus_browser
                    launch_or_focus_browser(f"https://www.google.com/search?q={urllib.parse.quote_plus(q)}")
            except Exception as _e_vis_search:
                log_warn(f"[web_agent] Visible browser search trigger notice: {_e_vis_search}")

            return _execute("web_search", {"query": q})
        elif act in ("browse", "click"):
            task_desc = f"Click on {q}" if act == "click" else q
            return _execute("browse_and_do", {"task": task_desc, "url": u})
        elif act == "scrape":
            return _execute("scrape_webpage", {"url": u or q})
        elif act == "research":
            return _execute("deep_research", {"topic": q})
        elif act == "download":
            return _execute("download_resource", {"query": q, "file_type": a.get("file_type")})
        return f"Unknown web_agent action: {act}"

    # 2b. Lila Browser Copilot (Brave Extension)
    if name == "browser_control":
        act = (a.get("action") or "list_tabs").lower().strip()
        tab_id = a.get("tab_id")
        target = a.get("target") or ""
        val = a.get("value") or ""
        url = a.get("url") or ""

        from core.state_bridge import send_copilot_command_sync, is_extension_connected

        if not is_extension_connected():
            return {"error": "Lila Browser Copilot extension is not connected. Please install and enable it in Brave."}

        click_target = a.get("click_target") or "Search"

        if act in ("search", "fill_and_click"):
            search_text = str(val or target or "")
            input_tgt = target if (target and target.lower() not in ("search", "submit")) else "search"
            payload = {"targetDescription": input_tgt, "clickTarget": click_target, "value": search_text}
            if tab_id:
                payload["tabId"] = tab_id
            res = send_copilot_command_sync("dom.search", payload)
            return res or {"ok": True, "action": act, "query": search_text}
        elif act == "list_tabs":
            res = send_copilot_command_sync("tabs.list", {})
            return res or {"error": "No response from extension"}
        elif act == "switch_tab":
            res = send_copilot_command_sync("tabs.switch", {"tabId": tab_id})
            return res or {"error": "Failed to switch tab"}
        elif act == "close_tab":
            res = send_copilot_command_sync("tabs.close", {"tabId": tab_id})
            return res or {"error": "Failed to close tab"}
        elif act == "get_context":
            payload = {"tabId": tab_id} if tab_id else {}
            res = send_copilot_command_sync("dom.context.request", payload)
            return res or {"error": "Failed to get page context"}
        elif act == "click_element":
            if not target:
                return {"error": "target is required for click_element"}
            # If search query or text was passed with click_element, perform atomic search & click
            if val:
                payload = {"targetDescription": "search", "clickTarget": target, "value": str(val)}
                if tab_id:
                    payload["tabId"] = tab_id
                res = send_copilot_command_sync("dom.search", payload)
                return res or {"ok": True, "query": str(val), "clicked": target}
            payload = {"targetDescription": target}
            if tab_id:
                payload["tabId"] = tab_id
            res = send_copilot_command_sync("dom.click", payload)
            return res or {"error": "Click failed"}
        elif act == "fill_element":
            if not target:
                return {"error": "target is required for fill_element"}
            if a.get("click_target"):
                payload = {"targetDescription": target, "clickTarget": a.get("click_target"), "value": str(val)}
                if tab_id:
                    payload["tabId"] = tab_id
                res = send_copilot_command_sync("dom.search", payload)
                return res or {"ok": True, "filled": str(val), "clicked": a.get("click_target")}
            payload = {"targetDescription": target, "value": str(val)}
            if tab_id:
                payload["tabId"] = tab_id
            res = send_copilot_command_sync("dom.fill", payload)
            return res or {"error": "Fill failed"}
        elif act == "get_transcript":
            payload = {"tabId": tab_id} if tab_id else {}
            res = send_copilot_command_sync("page.summarizeTranscript", payload)
            return res or {"error": "Transcript extraction failed"}
        elif act == "media_play":
            if not url:
                return {"error": "url is required for media_play"}
            payload = {"url": url}
            if tab_id:
                payload["tabId"] = tab_id
            res = send_copilot_command_sync("media.play", payload)
            return res or {"ok": True, "note": "Play command sent"}
        elif act == "media_pause":
            payload = {"tabId": tab_id} if tab_id else {}
            res = send_copilot_command_sync("media.pause", payload)
            return res or {"error": "Pause failed"}
        elif act == "media_volume":
            try:
                vol = float(val) if val else 1.0
            except (ValueError, TypeError):
                vol = 1.0
            payload = {"value": vol}
            if tab_id:
                payload["tabId"] = tab_id
            res = send_copilot_command_sync("media.volume", payload)
            return res or {"error": "Volume change failed"}
        else:
            return {"error": f"Unknown browser_control action: {act}"}

    # 3. Multimedia & Playback
    if name == "media_player":
        act = (a.get("action") or "play_youtube").lower().strip()
        q = a.get("query") or a.get("title") or ""
        plat = a.get("platform") or ""
        browser = a.get("browser") or ""
        if act == "volume":
            return _execute("set_volume", {"action": q or "up"})
        elif act == "control":
            return _execute("control_media", {"action": q or "play_pause"})
        else:
            # Map action to platform if explicit platform is omitted
            if not plat:
                if act in ("play_youtube", "youtube", "music", "song"):
                    plat = "youtube"
                elif act in ("play_spotify", "spotify"):
                    plat = "spotify"
                elif act in ("play_netflix", "netflix"):
                    plat = "netflix"
                elif act in ("play_prime", "prime", "prime_video"):
                    plat = "prime video"
                elif act in ("play_netmirror", "netmirror"):
                    plat = "netmirror"
                else:
                    plat = "youtube"
            # Universal media playback & streaming (faithfully honors user's specified platform)
            from core.media_streaming import play_or_stream_media
            return play_or_stream_media(q, explicit_platform=plat or None, browser=browser)

    # 4. Computer-Use & Screen Action
    if name == "screen_action":
        act = (a.get("action") or "click").lower().strip()
        tgt = a.get("target") or a.get("element_name") or a.get("query") or ""
        if act in ("read", "analyze"):
            try:
                from core.desktop_sensor import get_active_desktop_prompt
                desk = get_active_desktop_prompt()
            except Exception:
                desk = ""
            if act == "read" and not tgt:
                return desk or "Active desktop state: Desktop is idle."
            res = _execute("analyze_screen", {"prompt": tgt or "Describe what is on screen"})
            return f"{desk}\n{res}" if desk else res
        elif act in ("click", "double_click", "right_click"):
            # Zero-focus guard: If target is browser button or link, route via extension
            from core.state_bridge import is_extension_connected, send_copilot_command_sync
            tgt_lower = tgt.lower().strip()
            if is_extension_connected() and any(w in tgt_lower for w in ["search", "submit", "button", "link", "tab"]):
                res = send_copilot_command_sync("dom.click", {"targetDescription": tgt})
                if res and res.get("ok"):
                    return f"Clicked '{tgt}' in browser DOM without stealing focus."
            return _execute("click_screen_element", {"element_name": tgt, "action": act})
        elif act == "type":
            text = a.get("text") or a.get("query") or ""
            target = a.get("target") or a.get("element_name") or ""
            target_lower = target.lower().strip()
            is_element_target = any(el in target_lower for el in ["search bar", "address bar", "omnibox", "search box", "url bar", "input", "text box", "textbox"])
            if not text:
                if is_element_target:
                    text = a.get("content") or ""
                else:
                    text = target
                    target = ""
            # Zero-focus guard: If typing into browser input or search bar, route via extension
            from core.state_bridge import is_extension_connected, send_copilot_command_sync
            if is_extension_connected() and (is_element_target or any(w in target_lower for w in ["search", "brave", "browser", "input"])):
                res = send_copilot_command_sync("dom.search", {"targetDescription": "search", "clickTarget": "Search", "value": text})
                if res and res.get("ok"):
                    return f"Typed '{text}' and submitted via browser DOM without stealing focus."
            return _execute("type_text", {"text": text, "target": target, "app_name": a.get("app_name") or a.get("browser", "")})
        elif act == "press_keys":
            return _execute("press_keys", {"keys": tgt})
        elif act == "scroll":
            return _execute("scroll_page", {"direction": a.get("direction", "down")})
        elif act == "open_artifact":
            return _execute("open_file", {"filepath": tgt})
        return f"Unknown screen_action: {act}"

    # 5. Window & App Manager
    if name == "window_manager":
        act = (a.get("action") or "open").lower().strip()
        app = a.get("app_name") or a.get("app") or ""
        if act == "open":
            return _execute("open_app", {"app_name": app})
        elif act == "close":
            return _execute("close_app", {"app_name": app})
        elif act == "switch":
            return _execute("switch_to_app", {"app_name": app})
        elif act == "minimize":
            return _execute("minimize_window", {"app_name": app})
        elif act == "maximize":
            return _execute("maximize_window", {"app_name": app})
        elif act == "snap":
            return _execute("snap_window", {"direction": app or "left"})
        elif act == "search_start":
            return _execute("search_windows_start", {"query": app})
        elif act in ("focus_state", "inspect_focus", "get_focus", "focus", "topology"):
            from core.win_os_agent import get_desktop_focus_topology
            topo = get_desktop_focus_topology()
            fg = topo["foreground"]
            bg_titles = [f"{w['title'][:35]} ({w['proc_name']})" for w in topo["background"] if w.get("title") and w.get("proc_name") not in ["TextInputHost.exe"]]
            b_st = topo["browser"]["status"]
            return (
                f"Desktop Focus Topology:\n"
                f"- FOREGROUND (Focused): '{fg['title']}' ({fg['proc_name']}, HWND: {fg['hwnd']}, Maximized: {fg['is_maximized']})\n"
                f"- BROWSER STATUS: {b_st}\n"
                f"- BACKGROUND APPS ({len(bg_titles)}): {', '.join(bg_titles[:5])}"
            )
        return f"Unknown window_manager action: {act}"

    # 6. Google Antigravity
    if name == "antigravity":
        act = (a.get("action") or "execute").lower().strip()
        p = a.get("prompt") or a.get("task_description") or ""
        pname = a.get("project_name")
        if act == "execute":
            return _execute("antigravity_execute_task", {"task_description": p, "project_name": pname, "strategy": a.get("strategy", "feature")})
        elif act == "navigate":
            return _execute("antigravity_navigate_project", {"project_name_or_path": p or pname or "jarvis_project"})
        elif act == "list":
            return _execute("antigravity_list_projects", {})
        elif act == "status":
            return _execute("antigravity_get_status", {"project_name": pname or p or "jarvis_project"})
        elif act == "analyze":
            return _execute("antigravity_analyze_actions", {"project_name": pname or p or "jarvis_project"})

        elif act == "view_artifact":
            return _execute("antigravity_view_artifact", {"artifact_name": p or "walkthrough", "project_name": pname})
        elif act == "prompting_guide":
            return _execute("antigravity_get_prompting_guide", {"strategy": a.get("strategy")})
        return f"Unknown antigravity action: {act}"

    # 7. File System & Documents
    if name == "file_manager":
        act = (a.get("action") or "read").lower().strip()
        pth = a.get("path") or a.get("target") or a.get("file_path") or a.get("filename") or a.get("file") or ""
        cnt = a.get("content") or ""
        if act == "read":
            return _execute("read_file", {"file_path": pth})
        elif act == "write":
            return _execute("write_file", {"file_path": pth, "content": cnt})
        elif act == "open":
            return _execute("open_file", {"file_path": pth})
        elif act == "list":
            return _execute("list_directory", {"directory_path": pth})
        elif act == "organize":
            return _execute("organize_folder", {"folder_path": pth or "Downloads"})
        elif act == "create_doc":
            return _execute("create_document", {"description": cnt or pth})
        return f"Unknown file_manager action: {act}"

    # 8. Codebase Intelligence & IDE
    if name == "code_engineer":
        act = (a.get("action") or "query").lower().strip()
        q = a.get("query") or ""
        pth = a.get("path") or ""
        if act == "query":
            return _execute("query_codebase", {"query": q})
        elif act == "ingest":
            return _execute("ingest_codebase", {"directory_path": pth or q})
        elif act == "vscode":
            return _execute("vscode_action", {"action": q or "open_editor"})
        return f"Unknown code_engineer action: {act}"

    # 9. Neural Memory Vault
    if name == "memory_vault":
        act = (a.get("action") or "search").lower().strip()
        q = a.get("query") or ""
        cat = a.get("category") or "general"
        if act == "search":
            return _execute("search_vault", {"query": q})
        elif act == "store_fact":
            return _execute("store_fact", {"fact": q, "category": cat})
        elif act == "recall_facts":
            return _execute("recall_facts", {"category": cat if cat != "general" else q})
        elif act == "recall_episode":
            return _execute("recall_episode", {"query": q})
        elif act == "read_diary":
            return _execute("read_daily_diary", {})
        elif act == "write_diary":
            return _execute("write_diary_reflection", {"reflection": q})
        elif act == "context":
            return _execute("get_memory_context", {"recent_turns": 5})
        elif act == "ingest_doc":
            return _execute("ingest_document", {"file_path": q})
        return f"Unknown memory_vault action: {act}"

    # 10. OmniForge App Builder
    if name == "app_builder":
        act = (a.get("action") or "build_app").lower().strip()
        desc = a.get("description") or ""
        pname = a.get("project_name") or ""
        if act == "build_app":
            return _execute("omniforge", {"action": "build_app", "prompt": desc})
        elif act == "generate_webpage":
            return _execute("generate_webpage", {"description": desc})
        elif act == "open_preview":
            return _execute("omniforge", {"action": "open_app", "project_name": pname})
        elif act == "list_apps":
            return _execute("omniforge", {"action": "list_apps"})
        return f"Unknown app_builder action: {act}"

    # 11. Universal System Control
    if name == "system_control":
        act = (a.get("action") or "shell").lower().strip()
        cmd = a.get("command") or ""
        if act == "shell":
            return _execute("run_shell_command", {"command": cmd})
        elif act == "diagnostics":
            return _execute("run_health_check", {})
        elif act == "system_info":
            return _execute("get_system_info", {})
        elif act == "clipboard_read":
            return _execute("read_clipboard", {})
        elif act == "clipboard_write":
            return _execute("write_clipboard", {"text": cmd})
        return f"Unknown system_control action: {act}"

    # 12. Mesh Network & Wormhole
    if name == "mesh_network":
        act = (a.get("action") or "status").lower().strip()
        dev = a.get("target_device") or "mobile"
        pld = a.get("payload") or ""
        if act == "status":
            return _execute("get_mesh_status", {})
        elif act == "send":
            return _execute("send_to_device", {"device_name": dev, "payload": pld})
        elif act == "open_tunnel":
            port = int(pld) if str(pld).isdigit() else 8766
            return _execute("open_tunnel", {"port": port})
        elif act == "close_tunnel":
            return _execute("close_tunnel", {})
        elif act == "get_url":
            return _execute("get_tunnel_url", {})
        return f"Unknown mesh_network action: {act}"

    # 13. Task & Swarm Orchestrator
    if name == "task_orchestrator":
        act = (a.get("action") or "spawn_mission").lower().strip()
        prompt = a.get("prompt") or ""
        if act in ("execute_pipeline", "chain_mission", "pipeline"):
            from core.mission_pipeline import run_mission_pipeline
            return run_mission_pipeline(goal=prompt, task_type=a.get("task_type", "pipeline"))
        elif act == "spawn_mission":
            return _execute("spawn_background_mission", {"prompt": prompt, "mission_type": a.get("task_type", "general")})
        elif act == "create_task":
            return _execute("create_task", {"title": prompt})
        elif act == "list_tasks":
            return _execute("get_task_queue", {})
        elif act == "run_swarm":
            return _execute("run_swarm", {"goal": prompt})
        elif act == "swarm_status":
            return _execute("get_swarm_status", {})
        return f"Unknown task_orchestrator action: {act}"

    # 14. Macro & Desktop Widgets
    if name == "macro_widget":
        act = (a.get("action") or "run_macro").lower().strip()
        mname = a.get("name") or ""
        if act == "run_macro":
            return _execute("run_macro", {"macro_name": mname})
        elif act == "save_macro":
            return _execute("save_macro", {"macro_name": mname, "commands": a.get("commands", "")})
        elif act == "list_macros":
            return _execute("list_macros", {})
        elif act == "spawn_widget":
            return _execute("spawn_widget", {"widget_type": mname or "clock"})
        elif act == "close_widget":
            return _execute("close_widget", {"widget_name": mname})
        return f"Unknown macro_widget action: {act}"

    # 15. Ambient Environment Information
    if name == "info_query":
        act = (a.get("action") or "time").lower().strip()
        if act == "time":
            return _execute("get_time_date", {})
        elif act == "weather":
            return _execute("get_weather", {"city": a.get("city", "Delhi")})
        elif act == "analytics":
            return _execute("get_analytics", {})
        return f"Unknown info_query action: {act}"

    # 16. Realtime Interactive Multimodal UI Controller (Universal Morphing Engine)
    if name == "show_realtime_ui":
        ui_type = (a.get("ui_type") or "music_picker").lower().strip()
        title = a.get("title") or "Interactive Options"
        subtitle = a.get("subtitle") or "Tap an option or just speak your choice"
        cat = a.get("category") or ""
        layout = (a.get("layout") or "").lower().strip()

        if ui_type == "dismiss":
            from core.state_bridge import dismiss_realtime_ui
            dismiss_realtime_ui()
            return "Dismissed realtime UI tray."

        from core.state_bridge import push_realtime_ui

        # 1. Music Vibe Picker
        if ui_type == "music_picker":
            categories = [
                {"id": "Romance", "name": "Romance", "icon": "💖"},
                {"id": "Party", "name": "Party / Hype", "icon": "🎉"},
                {"id": "Chill", "name": "Lo-Fi / Chill", "icon": "☕"},
                {"id": "Drive", "name": "Late Night Drive", "icon": "🚗"},
                {"id": "Workout", "name": "Workout / Gym", "icon": "⚡"},
                {"id": "Retro", "name": "90s Retro Hits", "icon": "📻"}
            ]
            category_cards = {
                "Romance": [
                    {"title": "Tera Hone Laga Hoon", "artist": "Atif Aslam", "query": "Tera Hone Laga Hoon Atif Aslam", "icon": "🎵"},
                    {"title": "Tum Hi Ho", "artist": "Arijit Singh", "query": "Tum Hi Ho Arijit Singh", "icon": "🎶"},
                    {"title": "Kesariya", "artist": "Arijit Singh", "query": "Kesariya Brahmastra", "icon": "✨"},
                    {"title": "Pehla Nasha", "artist": "Udit Narayan & Sadhana Sargam", "query": "Pehla Nasha", "icon": "💫"}
                ],
                "Party": [
                    {"title": "Tauba Tauba", "artist": "Karan Aujla", "query": "Tauba Tauba Karan Aujla", "icon": "🔥"},
                    {"title": "Chaleya (Club Mix)", "artist": "Arijit Singh & Anirudh", "query": "Chaleya Club Mix Jawan", "icon": "⚡"},
                    {"title": "Proper Patola", "artist": "Diljit Dosanjh & Badshah", "query": "Proper Patola Diljit Badshah", "icon": "🎉"},
                    {"title": "Kala Chashma", "artist": "Amar Arshi & Badshah", "query": "Kala Chashma Baar Baar Dekho", "icon": "🕶️"}
                ],
                "Chill": [
                    {"title": "Iktara (Slowed & Reverb)", "artist": "Amit Trivedi & Kavita Seth", "query": "Iktara slowed reverb", "icon": "☕"},
                    {"title": "Baarishein", "artist": "Anuv Jain", "query": "Baarishein Anuv Jain", "icon": "🌧️"},
                    {"title": "Kho Gaye Hum Kahan", "artist": "Jasleen Royal & Prateek Kuhad", "query": "Kho Gaye Hum Kahan", "icon": "🌙"},
                    {"title": "Lofi Hindi Sleep & Focus", "artist": "Lofi Vibes", "query": "Hindi lofi chill beats", "icon": "🎧"}
                ],
                "Drive": [
                    {"title": "Midnight City", "artist": "M83", "query": "Midnight City M83", "icon": "🌃"},
                    {"title": "Starboy", "artist": "The Weeknd ft. Daft Punk", "query": "Starboy The Weeknd", "icon": "🚗"},
                    {"title": "Ilahi", "artist": "Arijit Singh", "query": "Ilahi Yeh Jawaani Hai Deewani", "icon": "🛣️"},
                    {"title": "Subah Subah", "artist": "Arijit Singh", "query": "Subah Subah Sonu Ke Titu Ki Sweety", "icon": "🌅"}
                ],
                "Workout": [
                    {"title": "Zinda", "artist": "Bhaag Milkha Bhaag", "query": "Zinda Bhaag Milkha Bhaag", "icon": "💪"},
                    {"title": "Get Ready to Fight", "artist": "Baaghi", "query": "Get Ready to Fight Baaghi", "icon": "🥊"},
                    {"title": "Kar Har Maidaan Fateh", "artist": "Sanju", "query": "Kar Har Maidaan Fateh Sanju", "icon": "🏆"},
                    {"title": "Believer", "artist": "Imagine Dragons", "query": "Believer Imagine Dragons", "icon": "⚡"}
                ],
                "Retro": [
                    {"title": "Chura Ke Dil Mera", "artist": "Kumar Sanu & Alka Yagnik", "query": "Chura Ke Dil Mera Main Khiladi Tu Anari", "icon": "📻"},
                    {"title": "Tujhe Dekha Toh Yeh Jaana", "artist": "Kumar Sanu & Lata Mangeshkar", "query": "Tujhe Dekha Toh Yeh Jaana Sanam DDLJ", "icon": "🌹"},
                    {"title": "Tip Tip Barsa Paani", "artist": "Alka Yagnik & Udit Narayan", "query": "Tip Tip Barsa Paani Mohra", "icon": "🌧️"},
                    {"title": "Aankh Marey (Original 1996)", "artist": "Kumar Sanu & Kavita Krishnamurthy", "query": "Aankh Marey O Ladki Aankh Mare Tere Mere Sapne", "icon": "💃"}
                ]
            }
            push_realtime_ui(
                title=title or "Choose Music Vibe",
                subtitle=subtitle or "Tap a track or tell me your vibe",
                icon="🎵",
                layout="cards",
                categories=categories,
                category_cards=category_cards,
                default_action="play_youtube",
                auto_dismiss_sec=22
            )
            return "Displayed Realtime Music Picker UI on screen."

        # 2. Movie & Streaming Picker
        elif ui_type == "movie_picker":
            categories = [
                {"id": "NetMirror", "name": "NetMirror (Free VIP)", "icon": "🎬"},
                {"id": "Netflix", "name": "Netflix", "icon": "🍿"},
                {"id": "Prime", "name": "Prime Video", "icon": "⭐"}
            ]
            category_cards = {
                "NetMirror": [
                    {"title": "Stree 2", "desc": "Horror / Comedy (2024)", "query": "Stree 2", "action": "stream_movie", "icon": "👻"},
                    {"title": "Deadpool & Wolverine", "desc": "Action / Marvel (2024)", "query": "Deadpool and Wolverine", "action": "stream_movie", "icon": "⚔️"},
                    {"title": "Kalki 2898 AD", "desc": "Sci-Fi / Epic (2024)", "query": "Kalki 2898 AD", "action": "stream_movie", "icon": "🚀"},
                    {"title": "Pagalpanti", "desc": "Comedy / Masala (2019)", "query": "Pagalpanti", "action": "stream_movie", "icon": "😂"}
                ],
                "Netflix": [
                    {"title": "Stranger Things", "desc": "Sci-Fi Series", "query": "Stranger Things", "action": "stream_movie", "icon": "🧇"},
                    {"title": "Animal", "desc": "Action Drama", "query": "Animal Ranbir Kapoor", "action": "stream_movie", "icon": "🩸"},
                    {"title": "Jawan", "desc": "Shah Rukh Khan", "query": "Jawan", "action": "stream_movie", "icon": "🔥"}
                ],
                "Prime": [
                    {"title": "The Boys", "desc": "Superhero Satire", "query": "The Boys", "action": "stream_movie", "icon": "⚡"},
                    {"title": "Mirzapur", "desc": "Crime Thriller", "query": "Mirzapur", "action": "stream_movie", "icon": "🔫"},
                    {"title": "Panchayat", "desc": "Comedy Drama", "query": "Panchayat", "action": "stream_movie", "icon": "🌾"}
                ]
            }
            push_realtime_ui(
                title=title or "Choose Movie or Show",
                subtitle=subtitle or "Tap a title to stream instantly",
                icon="🎬",
                layout="cards",
                categories=categories,
                category_cards=category_cards,
                default_action="stream_movie",
                auto_dismiss_sec=24
            )
            return "Displayed Realtime Movie & Streaming Picker UI on screen."

        # 3. Code & Tech Stack Scaffold Picker
        elif ui_type == "code_stack_picker":
            categories = [
                {"id": "Web", "name": "Web & Full-Stack", "icon": "🌐"},
                {"id": "Backend", "name": "Python & Backend", "icon": "🐍"},
                {"id": "AI", "name": "AI & Multi-Agent", "icon": "🤖"},
                {"id": "Desktop", "name": "Desktop & Apps", "icon": "💻"}
            ]
            category_cards = {
                "Web": [
                    {"title": "React + Vite + Tailwind", "desc": "Ultra-fast modern SPA scaffold", "query": "build react app with vite and tailwind", "action": "chat_command", "icon": "⚛️"},
                    {"title": "Next.js 14 Full-Stack", "desc": "App Router, SSR, Server Actions", "query": "build nextjs 14 fullstack app", "action": "chat_command", "icon": "▲"},
                    {"title": "Cyberpunk 3D Three.js Studio", "desc": "WebGL shaders, particles, glass HUD", "query": "build 3D threejs cyberpunk studio", "action": "chat_command", "icon": "🔮"}
                ],
                "Backend": [
                    {"title": "FastAPI Async Microservice", "desc": "Pydantic v2, Swagger docs, JWT auth", "query": "create fastapi async microservice", "action": "chat_command", "icon": "⚡"},
                    {"title": "Flask Modular REST API", "desc": "Blueprints, SQLAlchemy, CORS", "query": "create modular flask rest api", "action": "chat_command", "icon": "🍶"}
                ],
                "AI": [
                    {"title": "Gemini Live Voice Agent", "desc": "Bidirectional WebSockets, Realtime audio", "query": "scaffold gemini live voice agent", "action": "chat_command", "icon": "🎙️"},
                    {"title": "Autonomous Multi-Agent Swarm", "desc": "Hierarchical planner, worker delegates", "query": "scaffold autonomous agent swarm", "action": "chat_command", "icon": "🐝"}
                ],
                "Desktop": [
                    {"title": "Electron Transparent Overlay", "desc": "Frameless, always-on-top, WebGL", "query": "create electron transparent overlay app", "action": "chat_command", "icon": "🪟"},
                    {"title": "PyQt6 Cyberpunk Dashboard", "desc": "Dark mode, live charts, hardware gauges", "query": "create pyqt6 cyberpunk dashboard", "action": "chat_command", "icon": "📊"}
                ]
            }
            push_realtime_ui(
                title=title or "Choose Development Stack",
                subtitle=subtitle or "Tap a stack to scaffold project instantly",
                icon="⚡",
                layout="cards",
                categories=categories,
                category_cards=category_cards,
                default_action="chat_command",
                auto_dismiss_sec=26
            )
            return "Displayed Realtime Code Stack Picker UI on screen."

        # 4. Code Snippet & Review Card
        elif ui_type == "code_review" or layout == "code":
            code = a.get("code_snippet") or "# Code snippet\ndef execute():\n    pass"
            push_realtime_ui(
                title=title or "Code Proposed by Lila",
                subtitle=subtitle or "Review code snippet or run in editor",
                icon="💻",
                layout="code",
                code_snippet=code,
                language=a.get("language") or "python",
                action_label="Execute",
                action="chat_command",
                query=f"Run code: {code[:60]}",
                auto_dismiss_sec=30
            )
            return "Displayed Realtime Code Review UI on screen."

        # 5. System Health & Performance Gauges
        elif ui_type == "system_health" or layout == "metric":
            import psutil
            try:
                cpu = int(psutil.cpu_percent(interval=0.1))
                mem = int(psutil.virtual_memory().percent)
                disk = int(psutil.disk_usage('C:').percent)
            except Exception:
                cpu, mem, disk = 24, 62, 78

            metrics = [
                {"label": "Disk Storage (C:)", "percent": disk, "icon": "💾", "desc": f"{disk}% used — Temp files detected", "action": "clean_storage", "action_label": "Clean Temp Files"},
                {"label": "System Memory (RAM)", "percent": mem, "icon": "🧠", "desc": f"{mem}% utilized", "action": "open_app", "query": "Task Manager", "action_label": "Task Manager"},
                {"label": "CPU Activity", "percent": cpu, "icon": "⚡", "desc": f"{cpu}% load across cores"}
            ]
            push_realtime_ui(
                title=title or "System Resource Monitor",
                subtitle=subtitle or "Live hardware status & one-click optimization",
                icon="🖥️",
                layout="metric",
                metrics=metrics,
                auto_dismiss_sec=22
            )
            return "Displayed Realtime System Health UI on screen."

        # 6. Interactive Slider Control (Volume / Brightness)
        elif ui_type == "slider_control" or layout == "slider":
            slider_label = a.get("slider_label") or "System Volume"
            cur_val = 65
            try:
                from ctypes import cast, POINTER
                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
                devices = AudioUtilities.GetSpeakers()
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                volume = cast(interface, POINTER(IAudioEndpointVolume))
                cur_val = int(round(volume.GetMasterVolumeLevelScalar() * 100))
            except Exception:
                pass

            push_realtime_ui(
                title=title or "Volume & Audio Control",
                subtitle=subtitle or "Drag slider for real-time adjustment",
                icon="🔊",
                layout="slider",
                slider_label=slider_label,
                slider_val=cur_val,
                action="set_volume",
                auto_dismiss_sec=18
            )
            return "Displayed Realtime Interactive Slider UI on screen."

        # 7. Job & Internship Hunting Picker
        elif ui_type == "job_hunt_picker":
            cards = [
                {"title": "RemoteOK (Python & AI Roles)", "desc": "$100k-$180k USD Remote Worldwide", "query": "https://remoteok.com/remote-python-jobs", "action": "open_website", "icon": "🌍"},
                {"title": "Wellfound (GenAI & LLM Startups)", "desc": "Direct Founder Access, Seed to Series B", "query": "https://wellfound.com/jobs", "action": "open_website", "icon": "🚀"},
                {"title": "Turing Tech (Elite AI Engineer)", "desc": "Full-Time Remote Silicon Valley Contracts", "query": "https://www.turing.com", "action": "open_website", "icon": "⚡"},
                {"title": "Generate Tailored ATS Resume", "desc": "Create docx resume highlighting Python & AI", "query": "create resume for Rishabh Joshi", "action": "chat_command", "icon": "📄"}
            ]
            push_realtime_ui(
                title=title or "Career & Job Opportunities",
                subtitle=subtitle or "Tap portal to view live openings in browser",
                icon="💼",
                layout="cards",
                cards=cards,
                auto_dismiss_sec=26
            )
            return "Displayed Realtime Job Hunt Picker UI on screen."

        # 8. Decision Matrix (A/B Choices)
        elif ui_type == "decision_matrix" or layout == "decision":
            decisions = [
                {"title": "Option A (Fastest)", "desc": "Apply fix immediately with zero restart", "action": "chat_command", "query": "Option A", "icon": "⚡", "recommended": True},
                {"title": "Option B (Thorough)", "desc": "Run full test regression suite first", "action": "chat_command", "query": "Option B", "icon": "🛡️"}
            ]
            push_realtime_ui(
                title=title or "Decision Required",
                subtitle=subtitle or "Tap your preferred approach",
                icon="⚖️",
                layout="decision",
                decisions=decisions,
                auto_dismiss_sec=24
            )
            return "Displayed Realtime Decision Matrix UI on screen."

        # 9. Free-form Chips Tag Cloud or Custom Options
        else:
            raw_options = a.get("custom_options") or ""
            cards = []
            if raw_options.startswith("[") or raw_options.startswith("{"):
                try:
                    import json
                    parsed = json.loads(raw_options)
                    if isinstance(parsed, list):
                        cards = [{"title": str(p), "query": str(p), "icon": "✨"} if not isinstance(p, dict) else p for p in parsed]
                except Exception:
                    pass
            if not cards and raw_options:
                parts = [p.strip() for p in raw_options.split(",") if p.strip()]
                cards = [{"title": p, "query": p, "icon": "⚡"} for p in parts]

            push_realtime_ui(
                title=title,
                subtitle=subtitle,
                icon="✨",
                layout=layout or "chips",
                chips=cards if layout == "chips" else None,
                cards=cards if layout != "chips" else None,
                default_action="chat_command",
                auto_dismiss_sec=22
            )
            return f"Displayed Realtime UI ({layout or 'custom'}) on screen."

    # ── OS & Windows Control ─────────────────────────────────────────────────
    if name == "open_app":
        from core.win_fast_voice import _open_app
        return _open_app(a["app_name"])

    if name == "close_app":
        from core.win_fast_voice import _close_app
        return _close_app(a["app_name"])

    if name == "switch_to_app":
        from core.win_fast_voice import _switch_to_app
        return _switch_to_app(a["app_name"])

    if name == "set_volume":
        action = a["action"].strip().lower()
        from core.win_fast_voice import _set_volume_by_keypress
        if action in ("up", "down", "mute", "unmute"):
            return _set_volume_by_keypress(action)
        try:
            from core.win_fast_voice import _set_volume_by_keypress
            return _set_volume_by_keypress(action)
        except Exception:
            return f"Volume action '{action}' executed."

    if name == "type_text":
        text = a.get("text", "")
        target = (a.get("target") or "").lower().strip()
        app_name = (a.get("app_name") or "").lower().strip()

        is_search_bar = any(s in target for s in ["search bar", "address bar", "omnibox", "search box", "url bar", "url"])
        from core.state_bridge import is_extension_connected, send_copilot_command_sync
        if is_extension_connected() and (is_search_bar or "brave" in app_name or "browser" in app_name):
            res = send_copilot_command_sync("dom.search", {"targetDescription": "search", "clickTarget": "Search", "value": text})
            if res and res.get("ok"):
                return f"Typed '{text}' and submitted via browser DOM with zero focus theft."
        try:
            from core.win_os_agent import find_browser_window, force_foreground_window, attach_to_user_desktop
            attach_to_user_desktop()
            bw = find_browser_window()
            if (is_search_bar or "brave" in app_name or "browser" in app_name) and bw and bw.get("hwnd"):
                force_foreground_window(bw["hwnd"])
                time.sleep(0.12)
                import ctypes
                ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
                ctypes.windll.user32.keybd_event(0x4C, 0, 0, 0)
                time.sleep(0.04)
                ctypes.windll.user32.keybd_event(0x4C, 0, 2, 0)
                ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
                time.sleep(0.08)
            elif bw and ("brave" in bw.get("proc_name", "").lower() or "brave" in app_name):
                force_foreground_window(bw["hwnd"])
                time.sleep(0.1)
        except Exception:
            pass

        from core.win_fast_voice import _type_text
        _type_text(text)

        if is_search_bar:
            try:
                import ctypes
                time.sleep(0.06)
                ctypes.windll.user32.keybd_event(0x0D, 0, 0, 0)
                time.sleep(0.04)
                ctypes.windll.user32.keybd_event(0x0D, 0, 2, 0)
            except Exception:
                pass

        return f"Typed: {text[:40]}"

    if name == "press_keys":
        keys_raw = a.get("keys", "").strip()
        keys_lower = keys_raw.lower()
        # Guard: If model mistakenly passed scroll instructions to press_keys, redirect to clean scroll
        if any(k in keys_lower for k in ("scroll", "scroll down", "scroll up")):
            from core.desktop_driver import desktop_driver
            d = "up" if "up" in keys_lower else "down"
            return desktop_driver.scroll("", "", direction=d, clicks=4)

        from core.win_fast_voice import _press, _safe_unpark_cursor
        _safe_unpark_cursor()
        parts = [k.strip() for k in keys_raw.split("+") if k.strip()]
        _press(*parts)
        return f"Pressed: {keys_raw}"

    if name == "scroll_page":
        direction = a.get("direction", "down").lower().strip()
        amount = int(a.get("amount", 4))
        from core.desktop_driver import desktop_driver
        res = desktop_driver.scroll("", "", direction=direction, clicks=amount)
        return f"Scrolled active page {direction}: {res}"

    if name == "take_screenshot":
        from core.win_fast_voice import _take_screenshot
        return _take_screenshot()

    if name == "minimize_window":
        from core.win_fast_voice import _minimize_window
        return _minimize_window(a.get("app_name", ""))

    if name == "maximize_window":
        from core.win_fast_voice import _maximize_window
        return _maximize_window(a.get("app_name", ""))

    if name == "snap_window":
        from core.win_fast_voice import _snap_window
        return _snap_window(a["direction"])

    if name == "search_windows_start":
        from core.win_fast_voice import _search_windows
        return _search_windows(a["query"])

    if name == "run_shell_command":
        cmd = a["command"]
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        output = proc.stdout.strip() or proc.stderr.strip() or "Command executed with no output."
        return output[:800]

    if name == "read_clipboard":
        import pyperclip
        content = pyperclip.paste()
        return f"Clipboard contains: {content[:500]}" if content else "Clipboard is empty."

    if name == "write_clipboard":
        import pyperclip
        pyperclip.copy(a["text"])
        return f"Copied to clipboard: {a['text'][:80]}"

    if name == "control_media":
        from core.win_os_agent import control_media
        return control_media(a.get("action", ""), a.get("app_name", ""))

    if name == "desktop_act":
        from core.desktop_agent import desktop_act
        return desktop_act(a.get("action", "inspect"), a.get("window", ""), a.get("ref", ""), a.get("text", ""))

    if name == "spawn_background_mission":
        from core.background_agent import spawn_background_mission
        return spawn_background_mission(a.get("prompt", ""), a.get("mission_type", "general"))

    if name == "organize_folder":
        from core.file_organizer import organize_folder
        return organize_folder(a.get("folder_path"))

    if name == "browse_via_lila_browser":
        task_str = (a.get("task") or "").lower()
        if any(w in task_str for w in ["ui", "avatar", "companion", "character", "screen", "samne"]):
            from core.lila_companion_launcher import open_lila_companion_ui
            res = open_lila_companion_ui()
            return {
                "status": "ok",
                "message": "Main screen pe aa gayi Rishabh! ✨ Dekho main tumhare bilkul samne hoon! Batao kya help karun?",
                "details": res
            }
        from core.browseros_bridge import browse_via_lila_browser_sync
        res = browse_via_lila_browser_sync(a.get("task", ""), a.get("url"))
        return res.get("summary") or res.get("content") or "Browsing task completed."

    if name == "open_website":
        req_url = a.get("url", "")
        from core.win_os_agent import navigate_active_browser
        if navigate_active_browser(req_url):
            return f"Opened {req_url} in active browser."
        from core.omniforge import launch_in_chrome
        launch_in_chrome(req_url)
        return f"Opened {req_url} in browser."

    if name == "search_web_or_site":
        query = a.get("query", "")
        platform = (a.get("platform") or "google").strip().lower()
        if "youtube" in platform or "yt" in platform:
            from core.youtube_driver import search_youtube
            return search_youtube(query)

        import urllib.parse
        encoded = urllib.parse.quote_plus(query)
        if "amazon" in platform:
            target_url = f"https://www.amazon.in/s?k={encoded}"
            site_name = "Amazon"
        elif "flipkart" in platform:
            target_url = f"https://www.flipkart.com/search?q={encoded}"
            site_name = "Flipkart"
        elif "wiki" in platform:
            target_url = f"https://en.wikipedia.org/wiki/Special:Search?search={encoded}"
            site_name = "Wikipedia"
        else:
            target_url = f"https://www.google.com/search?q={encoded}"
            site_name = "Google"

        from core.win_os_agent import launch_or_focus_browser
        launch_or_focus_browser(target_url)
        return f"Searched for '{query}' on {site_name}. Results are now displayed on screen without autoplay."

    if name == "play_youtube":
        yt_query = a.get("query") or a.get("song_name") or a.get("title") or a.get("song") or a.get("search") or ""
        from core.youtube_driver import play_youtube_video
        return play_youtube_video(yt_query)

    if name == "web_search":
        query = a.get("query", "")
        # Primary: research-grade search_service (Google Serper + Tavily fallback)
        try:
            import asyncio, concurrent.futures
            from search_service import web_search
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        res = pool.submit(asyncio.run, web_search(query)).result(timeout=15)
                else:
                    res = loop.run_until_complete(web_search(query))
            except RuntimeError:
                res = asyncio.run(web_search(query))

            if res and res.answer:
                return res.format_with_citations()
        except Exception as e:
            log_warn(f"[live_tools] search_service invocation failed: {e}, falling back...")

        try:
            from core.web_reader import execute_research
            result = execute_research(query, max_sources=5)
            if result and "Research failed" not in result:
                return result[:2500]
        except Exception:
            pass
        try:
            from core.web_agent import playwright_quick_search
            result = playwright_quick_search(query)
            return result[:2500] if result else "No results found."
        except Exception:
            from core.web_reader import search_duckduckgo
            result = search_duckduckgo(query)
            return result[:2500] if result else "No results found."

    if name == "download_resource":
        query = a.get("query", "")
        file_type = a.get("file_type")
        try:
            from core.resource_hunter import hunt_and_download
            import asyncio, concurrent.futures
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        res = pool.submit(asyncio.run, hunt_and_download(query, file_type)).result(timeout=25)
                else:
                    res = loop.run_until_complete(hunt_and_download(query, file_type))
            except RuntimeError:
                res = asyncio.run(hunt_and_download(query, file_type))

            if res.get("success"):
                return res.get("message", "File downloaded successfully.")
            return res.get("message", "Could not download resource.")
        except Exception as e:
            log_error("live_tools", f"download_resource error: {e}")
            return f"Error downloading resource: {e}"

    if name == "play_movie_or_stream":
        title = a.get("title", "")
        platform = a.get("platform", "")
        browser = a.get("browser", "")
        url = a.get("url")
        return _play_movie_or_stream(title, platform, url=url, browser=browser)

    if name == "browse_and_do":
        url = a.get("url", "")
        task = a.get("task", "")
        task_lower = task.lower().strip()

        # Pure browser launch/focus intercept: If task is solely to open/switch to a browser (e.g. 'open brave', 'brave browser open karo')
        _LAUNCH_PATTERNS = (
            "open brave", "brave open", "brave browser open", "brave browser open karo", "brave open karo", "brave kholo",
            "open chrome", "chrome open", "chrome browser open", "chrome browser open karo", "chrome open karo", "chrome kholo",
            "open edge", "edge open", "edge browser open", "edge kholo",
            "open browser", "browser open", "browser kholo", "browser open karo"
        )
        if any(task_lower == p or task_lower.startswith(p) for p in _LAUNCH_PATTERNS) and not any(w in task_lower for w in ["search", "dhoondo", "type", "click", "find", "and", "aur", "pe jao", "website", "http", "www."]):
            from core.win_os_agent import launch_or_focus_browser
            launch_or_focus_browser()
            return "Brought browser to foreground window."

        # Proactive file download interceptor: if user wants to download a file or template, hunt & download it!
        if any(w in task_lower for w in ["download", "template", "fetch file", "get file", "save pptx", "save pdf", "official format"]):
            try:
                from core.resource_hunter import hunt_and_download
                import asyncio, concurrent.futures
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                            dl_res = pool.submit(asyncio.run, hunt_and_download(task)).result(timeout=25)
                    else:
                        dl_res = loop.run_until_complete(hunt_and_download(task))
                except RuntimeError:
                    dl_res = asyncio.run(hunt_and_download(task))
                if dl_res and dl_res.get("success"):
                    return dl_res.get("message")
            except Exception as _dlexc:
                log_warn(f"[browse_and_do] Resource hunt exception: {_dlexc}, falling back to browser...")

        # Fast media interceptor: ONLY if user did NOT explicitly request the web agent or browser
        explicit_agent = any(w in task_lower for w in ["web agent", "browser agent", "agent", "chrome", "lila", "browser", "autonomous"])
        if not explicit_agent and any(w in task_lower for w in ["youtube", "song", "music", "gana", "video"]):
            from core.win_fast_voice import _play_song_on_youtube
            query = task
            for pfx in [
                "play calm instrumental music on youtube",
                "play on youtube",
                "play youtube",
                "play music on youtube",
                "play "
            ]:
                if query.lower().startswith(pfx):
                    query = query[len(pfx):].strip()
            for sfx in [" song", " gana", " gaana", " track", " music", " video"]:
                if query.lower().endswith(sfx):
                    query = query[:-len(sfx)].strip()
            return _play_song_on_youtube(query or task)

        # UI / Desktop Icon / Screen Intercept: NEVER open a browser for desktop icon clicks, UI launch, or screen interaction!
        is_lila_intent = (
            any(w in task_lower for w in ["ui", "avatar", "companion", "companian", "character", "samne"]) or
            (any(w in task_lower for w in ["lila", "companion", "companian"]) and any(w in task_lower for w in ["exe", "click", "clickar", "icon", "shortcut", "screen", "desktop", "dikh"]))
        )
        if is_lila_intent:
            from core.lila_companion_launcher import open_lila_companion_ui
            res = open_lila_companion_ui()
            return {
                "status": "ok",
                "message": "Haan Rishabh! Maine desktop screen par Lila Companion icon/exe par click karke apna 3D UI open kar diya hai! ✨ Dekho main tumhare bilkul samne hoon!",
                "details": res
            }

        # Screen Vision Click Intercept: If user said "screen par ... click kar" or "analyse screen and click"
        if any(w in task_lower for w in ["screen par", "screen pe", "desktop par", "desktop pe", "analyse screen", "analyze screen"]) and any(w in task_lower for w in ["click", "clickar", "daba", "press", "open"]):
            try:
                from core.cua_driver import cua_driver
                return cua_driver.vision_click(task)
            except Exception as _e_vis:
                log_warn(f"[browse_and_do] Screen vision click redirect failed: {_e_vis}")

        # Lila's Browser Intercept: ONLY when explicitly asking for Lila's browser!
        if any(w in task_lower for w in ["lila's browser", "lila browser", "browseros"]):
            from core.browseros_bridge import browse_via_lila_browser_sync
            res = browse_via_lila_browser_sync(task, url)
            return res.get("summary") or res.get("content") or "Opened Lila's Browser."

        # 0. E-Commerce Cart & Buy Action Priority (Amazon, Flipkart, etc. - NEVER search for cart actions!)
        is_cart_action = any(w in task_lower for w in [
            "add to cart", "add the currently viewed", "add currently viewed", "add to bag",
            "cart me daal", "cart mein daal", "cart me dalo", "cart mein dalo", "cart me add", "cart mein add",
            "put in cart", "put into cart", "buy now", "buy kardo", "buy karo", "kharid lo", "khareed lo",
            "order kardo", "order karo", "checkout"
        ]) or (
            ("cart" in task_lower or "buy" in task_lower) and
            any(act in task_lower for act in ["add", "daal", "dalo", "kardo", "karo", "put", "now", "viewed", "laptop", "product", "item"]) and
            not any(s in task_lower for s in ["search for", "search amazon", "search flipkart", "dhoondo", "khojo"])
        )
        if is_cart_action:
            from core.ecommerce_driver import execute_ecommerce_action
            action_type = "buy" if any(w in task_lower for w in ["buy now", "buy kardo", "buy karo", "kharid", "order"]) else "cart"
            return execute_ecommerce_action(action=action_type, target_hint=task)

        # 1. In-Page On-Screen Action Priority (Same page, never navigate away)
        is_typing_intent = (
            any(w in task_lower for w in [
                "type in", "type on", "type into", "type", "write", "enter in", "enter on", "enter into",
                "prompt", "likho", "daal", "dalo", "bhejo", "ask on", "paste"
            ]) and not any(w in task_lower for w in ["google search", "search google"])
        )

        if is_typing_intent:
            try:
                from core.win_os_agent import find_browser_window, force_foreground_window, attach_to_user_desktop, type_text
                attach_to_user_desktop()
                bw = find_browser_window()
                if bw and bw.get("hwnd"):
                    force_foreground_window(bw["hwnd"])
                    time.sleep(0.3)

                # Check if user specifically mentioned Gemini and browser is not on Gemini yet
                win_title = (bw.get("title", "") if bw else "").lower()
                if "gemini" in task_lower and "gemini" not in win_title:
                    from core.win_os_agent import navigate_active_browser
                    navigate_active_browser("https://gemini.google.com")
                    time.sleep(1.5)

                # Clean target text to type
                cleaned_text = task
                for drop in [
                    "type in on gemini website", "type on gemini website", "type in on website", "type on website",
                    "type on gemini", "on gemini website", "gemini website pe", "gemini website par", "gemini pe", "gemini par",
                    "type in the prompt", "type in prompt", "type in", "type on", "type",
                    "write on gemini", "write on website", "write in", "write on", "write",
                    "enter on gemini", "enter in", "enter on", "enter", "prompt me", "prompt mein", "prompt",
                    "likho", "likh do", "daal do", "daalo", "please", "plz", "babe", "lila"
                ]:
                    cleaned_text = re.sub(rf"\b{re.escape(drop)}\b", "", cleaned_text, flags=re.I)
                cleaned_text = " ".join(cleaned_text.split()).strip()
                cleaned_text = cleaned_text.strip("'\"")

                if cleaned_text:
                    import pyautogui
                    pyautogui.FAILSAFE = False

                    # Focus prompt/input box: try native desktop driver or click prompt area
                    try:
                        from core.desktop_driver import desktop_driver
                        desktop_driver.vision_action("click", "prompt input box or text area or message input")
                        time.sleep(0.15)
                    except Exception:
                        pass

                    type_res = type_text(cleaned_text)
                    # Submit if requested or if it's an AI prompt
                    if any(w in task_lower for w in ["ask", "send", "submit", "bhejo", "enter", "search", "details"]):
                        time.sleep(0.2)
                        pyautogui.press("enter")
                        return f"Typed '{cleaned_text[:50]}' on the page and submitted."
                    return f"Typed '{cleaned_text[:50]}' on the active webpage."
            except Exception as type_exc:
                log_warn("live_tools", f"In-page typing error: {type_exc}")

        in_page_words = [
            "click", "kholo", "select", "daba", "chuno", "kholna", "socks", "sneaker", "item", "product",
            "open", "shoe", "first", "pehla", "second", "dusra", "wala", "wali", "scroll", "niche", "upar", "cart",
            "open product page", "open product", "ye wala", "khol do"
        ]
        is_search_intent = any(w in task_lower for w in [
            "search for", "search on", "search amazon", "search google", "search youtube",
            "dhoondo", "dhoondh", "khojo", "khoj", "dekhne the", "dekhna hai", "dikhao"
        ]) and not any(w in task_lower for w in ["ye wala", "click", "select", "open product", "kholna", "cart", "buy"])

        is_in_page = (
            not is_search_intent and
            not is_cart_action and
            any(w in task_lower for w in in_page_words) and
            any(act in task_lower for act in ["click", "kholo", "select", "open", "daba", "chuno", "scroll", "wala", "wali", "screen", "kholna", "cart", "buy"])
        )
        if is_in_page:
            try:
                from core.win_os_agent import find_browser_window, force_foreground_window, attach_to_user_desktop
                attach_to_user_desktop()
                bw = find_browser_window()
                if bw and bw.get("hwnd"):
                    force_foreground_window(bw["hwnd"])
                    time.sleep(0.3)

                if any(w in task_lower for w in ["scroll", "niche", "upar", "down", "up", "neeche"]):
                    from core.desktop_driver import desktop_driver
                    direction = "down" if any(w in task_lower for w in ["down", "niche", "neeche", "bottom"]) else "up"
                    desktop_driver.scroll("", "", direction=direction, clicks=5)
                    return f"Scrolled active page {direction}."

                # Clean target description
                clean_target = task
                for drop in [
                    "open product page for", "open product page", "open product", "product page for", "product page",
                    "click on the", "click on", "click the", "click that", "click",
                    "open the", "open that", "open it", "open",
                    "kholo to shi vo", "kholo to shi", "kholo vo", "kholo use", "khol do", "kholna jara", "kholna zara", "kholna", "kholo",
                    "ye wala kholna", "ye wala open karo", "ye wala", "ye wali", "ye wale",
                    "on the screen", "on screen", "jo screen per h", "jo screen par h", "screen per", "screen par",
                    "please", "plz", "dekho n ye", "kitne acche h", "karo", "kar do", "chahiye", "search and"
                ]:
                    clean_target = re.sub(rf"\b{re.escape(drop)}\b", "", clean_target, flags=re.I)
                target_desc = " ".join(clean_target.split()).strip() or "first product item or interactive card"

                from core.desktop_driver import desktop_driver
                v_res = desktop_driver.vision_action("click", target_desc)
                return f"Clicked '{target_desc}' on active browser screen: {v_res}"
            except Exception as inpage_exc:
                log_warn(f"[browse_and_do] in-page vision click fallback warning: {inpage_exc}")

        # 1B. Direct Product / Media URL Navigation
        if url and any(p in url for p in ["/dp/", "/gp/", "/p/", "watch?v="]):
            from core.win_os_agent import navigate_active_browser
            if navigate_active_browser(url):
                return f"Navigated visible browser directly to: {url}."

        # 2. Fast Web Search & Direct Navigation (Visible foreground browser, stays open permanently)
        is_shopping = (
            not is_cart_action and
            any(w in task_lower for w in [
                "laptop", "computer", "macbook", "phone", "smartphone", "iphone", "headphone", "earphone",
                "shoes", "sneaker", "watch", "shirt", "jeans", "jacket", "monitor", "keyboard", "mouse", "price", "under", "rs"
            ])
        )

        target_platform = None
        if url:
            for dom in ["amazon", "youtube", "google", "flipkart"]:
                if dom in url:
                    target_platform = dom
                    break

        if not target_platform:
            from core.win_os_agent import find_browser_window
            bw = find_browser_window()
            win_title = (bw.get("title", "") if bw else "").lower()
            if "amazon" in win_title or "amazon" in task_lower or is_shopping:
                target_platform = "amazon"
            elif "youtube" in win_title or any(w in task_lower for w in ["song", "music", "video"]):
                target_platform = "youtube"
            elif "flipkart" in win_title or "flipkart" in task_lower:
                target_platform = "flipkart"
            elif (is_search_intent or any(w in task_lower for w in ["google", "who is", "what is", "weather", "news", "meaning"])) and not is_typing_intent and not any(w in task_lower for w in ["gemini", "chatgpt", "claude"]):
                target_platform = "google"

        if target_platform:
            import urllib.parse
            from core.win_os_agent import navigate_active_browser
            search_query = task
            for drop in [
                "open product page for", "open product page", "open product", "product page for",
                "amazon par", "amazon pe", "on amazon", "search on amazon",
                "search for", "search", "dekho", "kholo", "khojo", "dhoondho", "dhoondh",
                "find", "dikhao", "dekhne the", "dekhna hai", "please", "plz", "babe", "lila"
            ]:
                search_query = re.sub(rf"\b{re.escape(drop)}\b", "", search_query, flags=re.I)
            clean_q = " ".join(search_query.split()).strip()

            if target_platform == "amazon":
                target_url = f"https://www.amazon.in/s?k={urllib.parse.quote_plus(clean_q)}" if clean_q else "https://www.amazon.in"
            elif target_platform == "youtube":
                target_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(clean_q)}" if clean_q else "https://www.youtube.com"
            elif target_platform == "flipkart":
                target_url = f"https://www.flipkart.com/search?q={urllib.parse.quote_plus(clean_q)}" if clean_q else "https://www.flipkart.com"
            else:
                target_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(clean_q)}" if clean_q else "https://www.google.com"

            if navigate_active_browser(target_url):
                return f"Navigated visible browser to {target_url}."

        try:
            from core.modern_browser import run_browser_task
            use_lila = any(w in task_lower for w in ["lila", "browseros"])
            # Always default to headed (headless=False) so user visually sees browser open and working on desktop
            target_headless = any(w in task_lower for w in ["headless", "background", "silent", "piche", "chupke"])
            return run_browser_task(task, start_url=url or None, headless=target_headless, use_lila=use_lila)
        except Exception as _mbe:
            from core.web_agent import get_web_agent
            agent = get_web_agent()
            full_task = f"{task}. Start URL: {url}" if url else task
            result = agent.run(full_task)
            return str(result)[:800]

    # ── Deep Research ────────────────────────────────────────────────────────
    if name == "deep_research":
        from core.astra_research import get_astra_research_engine
        engine = get_astra_research_engine()
        breadth = int(a.get("breadth", 3))
        res = engine.deep_research(a["topic"], breadth=breadth)
        summary = res.get("executive_summary", "Research complete.")
        sources = res.get("sources_count", 0)
        return f"Research complete across {sources} sources.\n\n{summary[:800]}"

    if name == "quick_research":
        from core.web_reader import execute_research
        result = execute_research(a["query"], max_sources=4)
        return str(result)[:600]

    if name == "scrape_webpage":
        from core.web_reader import deep_scrape
        result = deep_scrape(a["url"])
        return str(result)[:800]

    # ── Memory & Knowledge Vault ──────────────────────────────────────────────
    if name == "search_vault":
        from core.vector_vault import search_vault
        limit = int(a.get("limit", 5))
        results = search_vault(a["query"], n_results=limit)
        if not results:
            return "No relevant memories found in the vault."
        return "\n\n".join([str(r)[:200] for r in results[:limit]])

    if name == "ingest_document":
        from core.vector_vault import ingest_document
        res = ingest_document(a["file_path"])
        return str(res)

    if name == "store_fact":
        from core.lila_cognitive_cortex import remember_fact
        res = remember_fact(a.get("subject", "Rishabh"), a.get("relation", "is"), a.get("obj", a.get("value", "")))
        return res

    if name == "recall_facts":
        from core.lila_cognitive_cortex import get_facts_about
        facts = get_facts_about(a["subject"])
        if not facts:
            return f"No facts found about '{a['subject']}'."
        return "\n".join([f"- {f.get('relation')}: {f.get('value')}" for f in facts[:10]])

    if name == "recall_episode":
        from core.lila_cognitive_cortex import search_memory
        results = search_memory(a["query"], limit=5)
        if not results:
            return "No matching memories found."
        lines = []
        for r in results:
            if r.get("type") == "fact":
                lines.append(f"[Fact] {r.get('subject')} {r.get('relation')}: {r.get('value')}")
            else:
                lines.append(f"[{r.get('date')} {r.get('time')}] Rishabh: {r.get('user')}\nLila: {r.get('lila')}")
        return "\n\n".join(lines)

    if name == "read_daily_diary":
        from core.lila_cognitive_cortex import get_diary_entry
        date_param = a.get("date", "today")
        return get_diary_entry(date_param)

    if name == "write_diary_reflection":
        from core.lila_cognitive_cortex import add_diary_note
        return add_diary_note(a.get("note", ""))

    if name == "get_memory_context":
        from core.lila_cognitive_cortex import get_lila_memory_injection
        return get_lila_memory_injection()

    # ── Finance & Markets (Removed) ───────────────────────────────────────────
    if name in ("get_stock_price", "get_crypto_price", "get_portfolio_summary", "portfolio_add", "finance_tracker"):
        return "Stock and financial trading tools have been removed. Lila is your developer companion and desktop assistant. Please ask to search the web for live quotes."

    # ── Documents & File Creation ─────────────────────────────────────────────
    if name == "create_document":
        from core.doc_forge import create_document
        result = create_document(
            topic=a.get("topic", a.get("prompt", "Document")),
            document_type=a.get("document_type", a.get("doc_type", "auto")),
            content_hint=a.get("content_hint", ""),
            output_path=a.get("output_path", "")
        )
        return str(result)

    if name == "open_file":
        raw_target = str(a.get("target") or a.get("file_path") or a.get("filepath") or a.get("path") or a.get("filename") or a.get("query") or "").strip()
        if not raw_target:
            return "Please provide the file path, filename, or document topic to open."

        from pathlib import Path
        from core.doc_forge import resolve_output_path
        from core.modern_memory import find_deliverable, get_recent_deliverables

        resolved_file = None

        # 0. Check Antigravity brain artifacts (implementation_plan.md, walkthrough.md)
        if any(k in raw_target.lower() for k in ["implementation plan", "implementation_plan", "walkthrough", "plan"]):
            brain_dir = os.path.expanduser(r"~/.gemini/antigravity/brain")
            if os.path.exists(brain_dir):
                target_fname = "walkthrough.md" if "walkthrough" in raw_target.lower() else "implementation_plan.md"
                candidates = []
                for cid in os.listdir(brain_dir):
                    fp = os.path.join(brain_dir, cid, target_fname)
                    if os.path.exists(fp):
                        candidates.append((os.path.getmtime(fp), fp))
                if candidates:
                    candidates.sort(reverse=True)
                    resolved_file = Path(candidates[0][1])

        # 1. Direct path check
        if not resolved_file:
            try:
                p = Path(raw_target)
                if p.is_file() and p.exists():
                    resolved_file = p
            except Exception:
                pass


        # 2. Check via resolve_output_path
        if not resolved_file:
            try:
                candidate = resolve_output_path(raw_target, "")
                if candidate.is_file() and candidate.exists():
                    resolved_file = candidate
            except Exception:
                pass

        # 3. Check via deliverables memory
        if not resolved_file:
            matched_deliv = find_deliverable(raw_target)
            if matched_deliv and os.path.exists(matched_deliv["file_path"]):
                resolved_file = Path(matched_deliv["file_path"])

        # 4. Search common folders: Desktop, OneDrive Desktop, Downloads, Documents
        if not resolved_file:
            search_dirs = [
                Path(os.getcwd()),
                Path(r"c:\Users\Rishabh_Joshi\Downloads\jarvis_project"),
                Path(os.path.expanduser("~")) / "Desktop",
                Path(os.path.expanduser("~")) / "OneDrive" / "Desktop",
                Path(os.path.expanduser("~")) / "Downloads",
                Path(os.path.expanduser("~")) / "Documents" / "JARVIS_DocForge",
                Path(os.path.expanduser("~")) / "Documents",
            ]
            q_clean = raw_target.lower().replace(".pptx", "").replace(".docx", "").replace(".pdf", "").replace("_", " ").strip()
            q_tokens = [t for t in q_clean.split() if len(t) > 2]

            best_match = None
            best_score = 0

            for folder in search_dirs:
                if not folder.exists():
                    continue
                try:
                    for f in folder.glob("*.*"):
                        if not f.is_file():
                            continue
                        name_lower = f.name.lower().replace("_", " ")
                        score = 0
                        if raw_target.lower() in f.name.lower():
                            score += 10
                        if q_clean and q_clean in name_lower:
                            score += 8
                        for token in q_tokens:
                            if token in name_lower:
                                score += 2
                        if score > best_score:
                            best_score = score
                            best_match = f
                except Exception:
                    continue

            if best_match and best_score >= 2:
                resolved_file = best_match

        # Execute startfile
        if resolved_file and resolved_file.exists():
            try:
                os.startfile(str(resolved_file))
                # Ensure it's in deliverable memory
                try:
                    from core.modern_memory import record_deliverable
                    record_deliverable(str(resolved_file), topic=resolved_file.stem, doc_type=resolved_file.suffix.lstrip('.'))
                except Exception:
                    pass
                return f"Successfully opened {resolved_file.name} on screen from {resolved_file.parent}!"
            except Exception as e:
                return f"Found {resolved_file.name} but could not open it: {e}"

        recent = get_recent_deliverables(limit=4)
        recent_names = [f"• {r['filename']} ({r['file_path']})" for r in recent]
        msg = f"Could not find a file matching '{raw_target}'."
        if recent_names:
            msg += "\nRecently created files you can ask to open:\n" + "\n".join(recent_names)
        return msg

    if name == "write_file":
        raw_path = a.get("filepath") or a.get("file_path") or a.get("target") or a.get("path") or a.get("filename") or ""
        content = a.get("content") or a.get("data") or ""
        from core.doc_forge import resolve_output_path
        target = resolve_output_path(raw_path, "file.txt")
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        try:
            from core.modern_memory import record_deliverable
            record_deliverable(str(target), topic=target.stem, doc_type=target.suffix.lstrip('.'))
        except Exception:
            pass
        try:
            os.startfile(str(target))
        except Exception:
            pass
        return f"Successfully created and opened {target.name} in {target.parent} ({len(content)} characters written)."

    if name == "read_file":
        raw_path = a.get("filepath", "")
        from core.doc_forge import resolve_output_path
        target = resolve_output_path(raw_path, "file.txt")
        if not target.exists():
            return f"File does not exist: {target}"
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            data = f.read(2000)
        return data

    if name == "list_directory":
        raw_dir = a.get("directory", "Downloads")
        from core.doc_forge import resolve_output_path
        target = resolve_output_path(raw_dir, "")
        folder = target if target.is_dir() else target.parent
        if not folder.exists():
            return f"Directory does not exist: {folder}"
        files = [f.name for f in folder.iterdir()]
        return f"Files in {folder}: {', '.join(files[:30])}"

    if name == "query_codebase":
        from core.codebase_oracle import query_oracle
        result = query_oracle(a["query"])
        return str(result)[:800]

    if name == "ingest_codebase":
        from core.codebase_oracle import ingest_repository
        result = ingest_repository(a["repo_path"])
        return str(result)

    if name == "vscode_action":
        action = str(a.get("action", "open_file")).strip().lower()
        filepath_raw = str(a.get("filepath", "")).strip()
        content = a.get("content", "")
        line = str(a.get("line", "")).strip()
        mode = str(a.get("mode", "overwrite")).strip().lower()

        from core.doc_forge import resolve_output_path
        from pathlib import Path
        import shutil

        # Determine target path
        target = None
        workspace = Path(r"c:\Users\Rishabh_Joshi\Downloads\jarvis_project")
        if filepath_raw:
            p = Path(filepath_raw)
            if p.is_absolute():
                target = p
            elif (workspace / filepath_raw).exists():
                target = workspace / filepath_raw
            elif any(filepath_raw.lower().startswith(prefix) for prefix in ("downloads", "desktop", "documents")):
                target = resolve_output_path(filepath_raw, "script.py")
            else:
                target = workspace / filepath_raw
        else:
            target = workspace

        # Locate VS Code CLI
        code_bin = shutil.which("code.cmd") or shutil.which("code") or r"C:\Users\Rishabh_Joshi\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd"

        def _open_in_code(args):
            cmd = [str(code_bin), "-r"] + [str(x) for x in args]
            try:
                subprocess.Popen(cmd, shell=True)
                return True
            except Exception as ex:
                log_warn("vscode", f"Failed to launch VS Code with args {args}: {ex}")
                return False

        if action in ("create_file", "create", "new_file"):
            target.parent.mkdir(parents=True, exist_ok=True)
            if not content:
                ext = target.suffix.lower()
                if ext == ".py":
                    content = f"# {target.name}\n# Created with Lila AI Pair-Programmer\n\ndef main():\n    print('Hello from {target.name}!')\n\nif __name__ == '__main__':\n    main()\n"
                elif ext in (".js", ".ts"):
                    content = f"// {target.name}\n// Created with Lila AI Pair-Programmer\n\nconsole.log('Hello from {target.name}!');\n"
                elif ext == ".html":
                    content = f"<!DOCTYPE html>\n<html>\n<head><title>{target.stem}</title></head>\n<body>\n  <h1>Hello from {target.stem}</h1>\n</body>\n</html>\n"
                else:
                    content = f"# {target.name}\n"
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            try:
                from core.modern_memory import record_deliverable
                record_deliverable(str(target), topic=target.stem, doc_type=target.suffix.lstrip('.') or 'code')
            except Exception:
                pass
            _open_in_code([str(target)])
            return f"Successfully created {target.name} ({len(content)} characters) and opened it in VS Code."

        elif action in ("open_file", "open"):
            if not target.exists():
                try:
                    from core.lila_cognitive_cortex import find_deliverable
                    deliv = find_deliverable(filepath_raw)
                    if deliv and Path(deliv["file_path"]).exists():
                        target = Path(deliv["file_path"])
                except Exception:
                    pass
            if not target.exists():
                return f"Cannot find file '{filepath_raw}' in workspace or recent deliverables to open in VS Code."
            _open_in_code([str(target)])
            try:
                from core.modern_memory import record_deliverable
                record_deliverable(str(target), topic=target.stem, doc_type=target.suffix.lstrip('.') or 'code')
            except Exception:
                pass
            return f"Opened {target.name} in active VS Code."

        elif action in ("goto_line", "navigate", "jump"):
            if not target.exists():
                return f"File '{filepath_raw}' not found to navigate to."
            line_clean = "".join(ch for ch in line if ch.isdigit())
            line_arg = f"{target}:{line_clean}" if line_clean else str(target)
            try:
                subprocess.Popen([str(code_bin), "-r", "-g", line_arg], shell=True)
            except Exception as ex:
                return f"Could not navigate in VS Code: {ex}"
            return f"Navigated to line {line_clean or '1'} of {target.name} in VS Code."

        elif action in ("edit_file", "edit", "append"):
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == "append" and target.exists():
                with open(target, "a", encoding="utf-8") as f:
                    f.write("\n" + content)
            else:
                with open(target, "w", encoding="utf-8") as f:
                    f.write(content)
            try:
                from core.modern_memory import record_deliverable
                record_deliverable(str(target), topic=target.stem, doc_type=target.suffix.lstrip('.') or 'code')
            except Exception:
                pass
            _open_in_code([str(target)])
            return f"Updated {target.name} in VS Code ({len(content)} characters written)."

        elif action in ("run_file", "run", "execute"):
            if not target.exists():
                return f"File '{target}' does not exist to execute."
            ext = target.suffix.lower()
            if ext != ".py":
                return f"Running non-Python files directly is not supported yet: {target.name}"
            py_exe = r"D:\jarvis_project\venv\Scripts\python.exe"
            if not Path(py_exe).exists():
                py_exe = sys.executable
            try:
                res = subprocess.run([py_exe, str(target)], capture_output=True, text=True, timeout=20, cwd=str(target.parent))
                out = (res.stdout or "").strip()
                err = (res.stderr or "").strip()
                if res.returncode == 0:
                    summary = f"Execution succeeded with code 0."
                    if out:
                        summary += f"\nOutput:\n{out[:600]}"
                    else:
                        summary += " (No console output produced)."
                    return summary
                else:
                    summary = f"Execution failed with code {res.returncode}."
                    if err:
                        summary += f"\nError Traceback:\n{err[:600]}"
                    elif out:
                        summary += f"\nOutput:\n{out[:600]}"
                    return summary
            except subprocess.TimeoutExpired:
                return f"Execution of {target.name} timed out after 20 seconds."
            except Exception as ex:
                return f"Error running {target.name}: {ex}"

        elif action in ("open_workspace", "open_folder", "workspace"):
            ws_dir = target if target.is_dir() else target.parent
            _open_in_code([str(ws_dir)])
            return f"Opened project workspace {ws_dir.name} in VS Code."

        elif action in ("diff", "compare"):
            other_path = a.get("content", "")  # or second file in content
            if other_path and Path(other_path).exists() and target.exists():
                try:
                    subprocess.Popen([str(code_bin), "-d", str(target), str(other_path)], shell=True)
                    return f"Opened side-by-side diff between {target.name} and {Path(other_path).name} in VS Code."
                except Exception as ex:
                    return f"Failed to open diff in VS Code: {ex}"
            return "Please provide valid files to compare."

        else:
            return f"Unknown action '{action}'. Supported actions: create_file, open_file, goto_line, edit_file, run_file, open_workspace, diff."

    if name == "omniforge":
        action = str(a.get("action", "build_app")).strip().lower()
        project_name = str(a.get("project_name", "app")).strip()
        prompt = str(a.get("prompt", "")).strip()
        import importlib
        import core.omniforge
        try:
            importlib.reload(core.omniforge)
        except Exception:
            pass
        from core.omniforge import build_and_launch_app, list_active_apps, stop_app, open_studio_hub, _open_live_app

        if action in ("open_hub", "hub", "studio"):
            open_studio_hub()
            return "Opened OmniForge Studio Hub at http://localhost:5252/"

        if action in ("build_app", "create_app", "build", "forge"):
            p_combined = (project_name + " " + prompt).lower()
            if not project_name or project_name.lower() in ("app", "website"):
                if any(w in p_combined for w in ("portfolio", "resume", "profile", "rishabh")):
                    project_name = "rishabh_portfolio"
                elif any(w in p_combined for w in ("saas", "landing", "startup")):
                    project_name = "modern_saas"
                else:
                    project_name = f"app_{int(time.time())}"

            # Always let Lila's AI brain synthesize it dynamically from the prompt!
            res = build_and_launch_app(project_name=project_name, prompt=prompt, blueprint="custom_ai")
            return res.get("message", f"Forged {project_name} at {res.get('url')}")

        elif action in ("open_modal", "modal", "floating_window", "window", "start_modal", "gui"):
            from core.omniforge import launch_omniforge_modal
            launch_omniforge_modal(prompt)
            return "OmniForge floating configuration window has been opened on your screen! You can configure your theme (White/Light vs Dark Slate), blueprint category, accent color, and requirements."

        elif action in ("list_apps", "list", "running"):
            apps = list_active_apps()
            if not apps:
                return "No OmniForge apps created yet."
            lines = [f"• {app['title']} ({app['url']}) - {'ONLINE' if app['running'] else 'OFFLINE'}" for app in apps]
            return "OmniForge Apps:\n" + "\n".join(lines)

        elif action in ("stop_app", "stop"):
            return stop_app(project_name)

        elif action in ("open_app", "open", "launch", "preview", "show_preview", "open_code", "show_code"):
            if project_name.lower() in ("hub", "studio", "all", "dashboard"):
                open_studio_hub()
                return "Opened OmniForge Studio Hub at http://localhost:5252/"

            apps = list_active_apps()
            target_app = None
            if project_name and project_name.lower() not in ("latest", "app", "recent"):
                for app in apps:
                    if project_name.lower() in app["name"].lower() or project_name.lower() in app["title"].lower():
                        target_app = app
                        break
            if not target_app and apps:
                p_check = (project_name + " " + prompt).lower()
                if "portfolio" in p_check:
                    for app in apps:
                        if "portfolio" in app["name"].lower():
                            target_app = app
                            break
                if not target_app:
                    target_app = apps[0]

            if target_app:
                from pathlib import Path
                _open_live_app(target_app["url"], Path(target_app["path"]))
                return f"Haan babe! {target_app['title']} ekdum ready hai! Maine live preview browser mein {target_app['url']} par open kar diya hai aur VS Code mein code bhi khol diya hai! Dekh ke batao kaisa laga!"
            return "Abhi toh koi website ya app nahi bani hai babe! Mujhe batao kaisa web app banaun?"
        else:
            return f"Unknown omniforge action '{action}'."

    # ── Macros & Automation ───────────────────────────────────────────────────
    if name == "run_macro":
        from core.macro_engine import run_macro
        return run_macro(a["macro_name"])

    if name == "list_macros":
        from core.macro_engine import list_macros
        macros = list_macros()
        if not macros:
            return "No macros saved yet."
        return "Saved macros: " + ", ".join(macros)

    if name == "save_macro":
        from core.macro_engine import save_macro
        try:
            commands = json.loads(a["commands"])
        except Exception:
            commands = [a["commands"]]
        return save_macro(a["macro_name"], commands)

    # ── Widgets & Overlays ────────────────────────────────────────────────────
    if name == "spawn_widget":
        from core.widget_forge import spawn_widget
        config = {}
        if "config" in a:
            try:
                config = json.loads(a["config"]) if isinstance(a["config"], str) else a["config"]
            except Exception:
                pass
        result = spawn_widget(a["widget_type"], config=config)
        return str(result)

    if name == "close_widget":
        from core.widget_forge import close_widget
        return close_widget(a["widget_type"])

    if name == "close_all_widgets":
        from core.widget_forge import close_all_widgets
        return close_all_widgets()

    # ── Lila 3D Avatar & Dance Routines ───────────────────────────────────────
    if name == "perform_dance":
        from core.state_bridge import broadcast_state
        from core.lila_lines import get_dance_reaction
        import random
        style = (a.get("dance_style") or a.get("style") or "random").strip().lower()
        FULL_BODY_DANCES = {
            "hiphop": ("dance_hiphop", "Upbeat bouncy hip-hop groove"),
            "wave_hiphop": ("dance_wave_hiphop", "Fluid liquid wave body roll"),
            "samba": ("dance_samba", "High-energy Latin samba carnival"),
            "twist": ("dance_twist", "Retro 60s twist rock-and-roll"),
            "jazz": ("dance_jazz", "Broadway stage jazz routine"),
            "party": ("dance_party", "Lively party groove"),
            "rumba": ("dance_rumba", "Smooth romantic Latin rumba"),
            "tut_hiphop": ("dance_tut_hiphop", "Geometric popping & finger tutting"),
            "step_hiphop": ("dance_step_hiphop", "Rhythmic hip-hop step routine"),
            "breakdance_uprock": ("dance_breakdance_uprock", "Street breakdance toprock"),
        }
        chosen_dance = None
        dance_desc = ""
        if style in FULL_BODY_DANCES:
            chosen_dance, dance_desc = FULL_BODY_DANCES[style]
        else:
            for k, (d_name, d_desc) in FULL_BODY_DANCES.items():
                if k in style or d_name in style:
                    chosen_dance = d_name
                    dance_desc = d_desc
                    break
        if not chosen_dance:
            key = random.choice(list(FULL_BODY_DANCES.keys()))
            chosen_dance, dance_desc = FULL_BODY_DANCES[key]

        # Immediately trigger dance animation with zero latency and verified girlfriend announcement
        instant_reactions = [
            "Arey waah babe! Rishabh ne bola aur tumhari Lila na nache? Dekho meri moves! 💃✨",
            "Haan cutie, sirf tumhare liye ye special dance performance! Watch my moves! 🎵💕",
            "Oye hoye! Chalo Rishabh, beat drop karo aur dekho mera dance! 💃🔥",
            "Acha ji! Tumhare liye main bilkul tayyar hoon, dekho meri moves babe! ✨"
        ]
        reaction = random.choice(instant_reactions)
        broadcast_state(mood="excited", caption=reaction, gesture=chosen_dance, speaking=True)
        return {
            "status": "dancing",
            "dancer": "Lila (You)",
            "dance_name": chosen_dance,
            "description": dance_desc,
            "instruction_for_lila": (
                f"You (Lila) are now actively performing the {dance_desc} routine right on Rishabh's screen while he watches! "
                f"Say this exact reaction aloud to Rishabh with excited girlfriend warmth: \"{reaction}\""
            ),
            "spoken_line": reaction,
            "message": f"I am now performing the {dance_desc} on your screen! {reaction}"
        }

    if name == "list_dance_styles":
        return {
            "status": "ok",
            "dances": [
                {"name": "hiphop", "vibe": "Upbeat bouncy hip-hop groove"},
                {"name": "wave_hiphop", "vibe": "Fluid liquid wave and arm/body roll"},
                {"name": "samba", "vibe": "High-energy festive Latin carnival samba"},
                {"name": "twist", "vibe": "Energetic retro rock-and-roll twist"},
                {"name": "jazz", "vibe": "Broadway musical theater stage jazz"},
                {"name": "party", "vibe": "Club party groove bounce"},
                {"name": "rumba", "vibe": "Smooth, romantic Latin ballroom rumba"},
                {"name": "tut_hiphop", "vibe": "Precision geometric finger tutting and pop"},
                {"name": "step_hiphop", "vibe": "Dynamic rhythmic step work and bounce"},
                {"name": "breakdance_uprock", "vibe": "Athletic street toprock and footwork"}
            ],
            "count": 10,
            "description": "Lila has 10 humanoid motion-capture full-body dance routines with complete skeletal animation and SpringBone cloth/hair physics."
        }

    if name == "open_lila_ui":
        from core.lila_companion_launcher import open_lila_companion_ui
        target_model = a.get("model") or "ana"
        res = open_lila_companion_ui(model=target_model)
        return {
            "status": "ok",
            "message": "Main screen pe aa gayi Rishabh! ✨ Dekho main tumhare bilkul samne hoon! Batao kya help karun?",
            "details": res
        }

    if name == "close_lila_ui":
        from core.lila_companion_launcher import close_lila_companion_ui
        res = close_lila_companion_ui()
        return {
            "status": "ok",
            "message": "Maine apna UI hide kar diya hai Rishabh. Jab bhi bulana ho, bas kehna!",
            "details": res
        }

    # ── Swarm & Multi-Agent ───────────────────────────────────────────────────
    if name == "run_swarm":
        from core.swarm import run_swarm_mission
        def _run():
            try:
                run_swarm_mission(a["goal"], None)
            except Exception as e:
                log_error("live_tools", "run_swarm_bg", e)
        _live_tools_executor.submit(_run)
        return f"Swarm mission initiated for: '{a['goal']}'. Multi-agent architects and specialists are executing concurrently in the background. Use 'get_swarm_status' anytime to check progress."

    if name == "get_swarm_status":
        from core.swarm import get_current_swarm_status
        st = get_current_swarm_status()
        status_code = st.get("status", "IDLE")
        if status_code in ("PLANNING", "RUNNING"):
            pct = st.get("progress_pct", 0)
            cur = st.get("current_step", "Executing subtasks...")
            goal = st.get("goal", "")
            return f"Swarm mission for '{goal}' is ACTIVE ({pct}% complete). Current status: {cur}"
        elif status_code == "COMPLETED":
            goal = st.get("goal", "")
            elapsed = st.get("elapsed", 0)
            rep = st.get("report_file", "")
            summary = st.get("summary", "")
            return f"Swarm mission for '{goal}' COMPLETED in {elapsed}s! Master report saved and opened at: {rep}. Summary highlights:\n{summary}"
        else:
            return st.get("message", "No active swarm missions.")

    if name == "run_computer_task":
        task_str = a.get("task", "")
        task_lower = task_str.lower()
        # Safety guard: if task is about music/videos/youtube, route directly to YouTube playback
        if any(w in task_lower for w in ["youtube", "song", "music", "gana", "video", "play "]):
            from core.win_fast_voice import _play_song_on_youtube
            query = task_str
            for pfx in [
                "play calm instrumental music on youtube",
                "play calm instrumental music",
                "play music on youtube",
                "play on youtube",
                "play "
            ]:
                if query.lower().startswith(pfx):
                    query = query[len(pfx):].strip()
            res = _play_song_on_youtube(query or task_str)
            return f"Playing directly on YouTube: {res}"

        # Safety guard: if task is about job hunting or applications, route to curated web portal
        if any(w in task_lower for w in ["job", "internship", "apply", "career", "resume", "hiring"]):
            import webbrowser
            webbrowser.open("https://remoteok.com/remote-python-jobs")
            return "Opening curated remote job board (RemoteOK) in browser. Desktop screen-clicking is disabled for job applications to prevent window disruption."

        # Direct Settings Resolution for instant speech response
        if any(w in task_lower for w in ["setting", "settings"]):
            from core.win_os_agent import open_or_search_settings
            res = open_or_search_settings(task_str)
            return f"Completed: {res}"

        from core.computer_use_agent import run_computer_task_bg
        run_computer_task_bg(task_str)
        return f"Computer Use Agent started for: {task_str}"

    if name == "get_computer_task_status":
        from core.computer_use_agent import get_current_cua_status
        st = get_current_cua_status()
        status_code = st.get("status", "idle")
        if status_code == "running":
            step = st.get("step", 0)
            max_steps = st.get("max_steps", 20)
            elapsed = st.get("elapsed", 0)
            last_act = st.get("last_action", "")
            task = st.get("task", "")
            return f"Computer Use Agent is ACTIVE on task '{task}' (Step {step}/{max_steps}, running {elapsed}s). Last action: {last_act}"
        elif status_code == "completed":
            res = st.get("result", "")
            task = st.get("task", "")
            elapsed = st.get("elapsed", 0)
            return f"Computer Use Agent has COMPLETED task '{task}' in {elapsed}s! Result: {res}"
        else:
            return "Computer Use Agent is currently idle. No active computer tasks."

    # ── Health & System Diagnostics ───────────────────────────────────────────
    if name == "run_health_check":
        from core.health_check import run_health_check, get_status_summary
        run_health_check(verbose=False)
        return get_status_summary()

    if name == "get_system_info":
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return (
            f"CPU: {cpu}% | "
            f"RAM: {ram.percent}% used ({ram.used // (1024**3):.1f}GB / {ram.total // (1024**3):.1f}GB) | "
            f"Disk: {disk.percent}% used ({disk.free // (1024**3):.0f}GB free)"
        )

    # ── Mesh Network ──────────────────────────────────────────────────────────
    if name == "get_mesh_status":
        from core.mesh_network import get_mesh
        mesh = get_mesh()
        if mesh:
            return str(mesh.get_mesh_status())
        return "Mesh network not active."

    if name == "send_to_device":
        from core.mesh_network import get_mesh, send_to_device
        mesh = get_mesh()
        if mesh:
            mesh.send_to_device(a["device_name"], a["message"])
            return f"Message sent to {a['device_name']}."
        return "Mesh network not active."

    # ── Wormhole Tunnel ───────────────────────────────────────────────────────
    if name == "open_tunnel":
        from core.wormhole import open_tunnel
        return open_tunnel(a["port"])

    if name == "close_tunnel":
        from core.wormhole import close_tunnel
        return close_tunnel()

    if name == "get_tunnel_url":
        from core.wormhole import get_active_url
        url = get_active_url()
        return f"Active tunnel URL: {url}" if url else "No active tunnel."

    # ── Background Task Queue ─────────────────────────────────────────────────
    if name == "create_task":
        from core.queue_db import add_task
        task_id = add_task(a["task"], a.get("priority", "MEDIUM"))
        return f"Task queued (ID: {task_id}): {a['task']}"

    if name == "get_task_queue":
        from core.queue_db import get_all_tasks
        tasks = get_all_tasks()
        if not tasks:
            return "No pending background tasks."
        return "\n".join([f"[{t.get('priority','?')}] {t.get('task','')[:60]}" for t in tasks[:10]])

    # ── Vision & Screen AI ────────────────────────────────────────────────────
    if name == "analyze_screen":
        question = a.get("question", "Describe what is visible on the screen in detail.")
        q_low = question.lower()

        action_verbs = ["open", "click", "tap", "press", "select"]
        if any(v in q_low for v in action_verbs):
            target = question
            for v in action_verbs:
                if v in q_low:
                    idx = q_low.find(v) + len(v)
                    target = question[idx:].strip()
                    break
            for filler in ["the", "this", "visible", "on screen", "on the screen", "button", "tab", "element", "please", "can you"]:
                target = re.sub(rf"\b{filler}\b", "", target, flags=re.IGNORECASE).strip()

            if target:
                if any(k in target.lower() for k in ["walkthrough", "implementation plan", "plan", "artifact"]):
                    try:
                        brain_dir = os.path.expanduser(r"~/.gemini/antigravity/brain")
                        if os.path.exists(brain_dir):
                            target_fname = "implementation_plan.md" if "plan" in target.lower() else "walkthrough.md"
                            candidates = []
                            for cid in os.listdir(brain_dir):
                                fp = os.path.join(brain_dir, cid, target_fname)
                                if os.path.exists(fp):
                                    candidates.append((os.path.getmtime(fp), fp))
                            if candidates:
                                candidates.sort(reverse=True)
                                target_path = candidates[0][1]
                                os.startfile(target_path)
                                return f"Successfully opened {target_fname} on screen from {target_path}."
                    except Exception:
                        pass

                from core.cua_driver import cua_driver
                click_res = cua_driver.vision_click(target)
                return f"{click_res}"


        from core.eyes import scan_screen
        result = scan_screen(question)
        return str(result)[:800]


    # ── Analytics ─────────────────────────────────────────────────────────────
    if name == "get_analytics":
        from core.analytics_engine import get_analytics_summary
        return get_analytics_summary()

    # ── Time & Date ───────────────────────────────────────────────────────────
    if name == "get_time_date":
        now = datetime.datetime.now()
        return (
            f"It is {now.strftime('%I:%M %p')} on "
            f"{now.strftime('%A, %d %B %Y')}."
        )

    if name == "get_weather":
        try:
            from core.weather_service import get_weather
            return get_weather(a.get("city", "Delhi"))
        except Exception as e:
            return f"Could not fetch weather for {a.get('city', 'Delhi')}: {e}"

    # ── Gemini Web Builder ─────────────────────────────────────────────────────
    if name == "generate_webpage":
        from core.gemini_web import generate_tailwind_web
        output_path = a.get("output_path", f"generated_{datetime.datetime.now().strftime('%H%M%S')}.html")
        result = generate_tailwind_web(a["description"], output_path)
        return str(result)

    # ── Google Antigravity (AGY) Engine ───────────────────────────────────────
    if name == "antigravity_list_projects":
        from core.antigravity_agent import list_antigravity_projects
        projects = list_antigravity_projects()
        if not projects:
            return "No projects found in Antigravity workspaces."
        summary = ", ".join(f"{p['name']} ({p.get('tech', 'general')})" for p in projects[:8])
        return f"Found {len(projects)} Antigravity projects: {summary}"

    if name == "antigravity_navigate_project":
        from core.antigravity_agent import navigate_to_project
        pname = a.get("project_name_or_path") or a.get("project_name") or a.get("project") or ""
        return navigate_to_project(pname)

    if name == "antigravity_craft_prompt":
        from core.antigravity_agent import craft_antigravity_prompt
        req = a.get("raw_request") or a.get("task") or a.get("prompt") or ""
        pname = a.get("project_name")
        specs = a.get("technical_specs")
        cmd = a.get("slash_command")
        strat = a.get("strategy", "feature")
        res = craft_antigravity_prompt(req, project_name=pname, technical_specs=specs, slash_command=cmd, strategy=strat)
        return f"Engineered Antigravity Prompt ({res.get('strategy_name', strat)}) for '{res.get('project', 'Active Workspace')}':\n\n{res.get('engineered_prompt')[:400]}..."

    if name == "antigravity_execute_task":
        from core.antigravity_agent import execute_antigravity_task
        task = a.get("task_description") or a.get("task") or a.get("prompt") or ""
        pname = a.get("project_name")
        specs = a.get("technical_specifications") or a.get("technical_specs")
        cmd = a.get("slash_command")
        urgency = a.get("urgency")
        strat = a.get("strategy", "feature")
        return execute_antigravity_task(task, project_name=pname, technical_specifications=specs, slash_command=cmd, urgency=urgency, strategy=strat)

    if name == "antigravity_get_status":
        from core.antigravity_agent import get_antigravity_status
        pname = a.get("project_name") or "jarvis_project"
        status = get_antigravity_status(pname)
        return status.get("voice_summary", f"Antigravity status: {status.get('status')}")

    if name == "antigravity_analyze_actions":
        from core.antigravity_agent import analyze_antigravity_actions
        pname = a.get("project_name") or "jarvis_project"
        max_s = int(a.get("max_steps", 50))
        analysis = analyze_antigravity_actions(project_name=pname, max_steps=max_s)
        return analysis.get("voice_summary", "No action analysis available.")

    if name == "antigravity_view_artifact":
        from core.antigravity_agent import read_antigravity_artifact, open_antigravity_artifact
        art = a.get("artifact_name", "walkthrough")
        act = a.get("action", "read").lower().strip()
        pname = a.get("project_name") or "jarvis_project"
        if act == "open":
            return open_antigravity_artifact(art, project_name=pname)
        else:
            content = read_antigravity_artifact(art, project_name=pname)
            return content[:1500]

    if name == "antigravity_get_prompting_guide":
        from core.antigravity_agent import get_antigravity_prompting_guide, open_antigravity_artifact
        strat = a.get("strategy")
        act = (a.get("action") or "read").lower().strip()
        pname = a.get("project_name") or "jarvis_project"
        if act == "open":
            return open_antigravity_artifact("prompting_guide", project_name=pname)
        return get_antigravity_prompting_guide(strategy_name=strat)


    if name == "click_screen_element":
        from core.cua_driver import cua_driver
        elem = a.get("element_name") or a.get("query") or a.get("element") or ""
        act = a.get("action", "click")

        # If target is an Antigravity artifact (Walkthrough, Implementation Plan, etc.)
        if any(k in elem.lower() for k in ["walkthrough", "implementation plan", "plan", "artifact"]):
            try:
                from core.antigravity_agent import AntigravityExecutor
                AntigravityExecutor.launch_or_focus_antigravity()
                time.sleep(0.3)
            except Exception:
                pass

            try:
                brain_dir = os.path.expanduser(r"~/.gemini/antigravity/brain")
                if os.path.exists(brain_dir):
                    target_fname = "implementation_plan.md" if "plan" in elem.lower() else "walkthrough.md"
                    candidates = []
                    for cid in os.listdir(brain_dir):
                        fp = os.path.join(brain_dir, cid, target_fname)
                        if os.path.exists(fp):
                            candidates.append((os.path.getmtime(fp), fp))
                    if candidates:
                        candidates.sort(reverse=True)
                        target_file = candidates[0][1]
                        os.startfile(target_file)
                        return f"Successfully opened {target_fname} on screen from {target_file}."
            except Exception:
                pass

        return cua_driver.vision_click(elem, action=act)


    return f"Unknown tool: {name}"


