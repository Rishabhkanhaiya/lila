"""
core/antigravity_agent.py — Dedicated Google Antigravity (AGY) Engine
=====================================================================
Empowers Lila to act as an expert pair-programming manager for Google Antigravity:
  1. Multi-Project Navigation & Indexing (SIH, Jarvis, Offline-GPT, Flutter, etc.)
  2. Advanced Prompt Engineering Synthesis (Transforms casual voice instructions
     into high-specification, rigorous, architecture-anchored Antigravity prompts)
  3. Interactive Antigravity Execution (Focuses Antigravity window, injects prompt
     into Chat Canvas via clipboard/keystrokes, and tracks execution history)
"""

import os
import re
import sys
import time
import json
import difflib
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

import psutil
import pyperclip

try:
    import win32gui
    import win32con
    import win32process
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

from core.jarvis_logger import log_info, log_warn, log_error
from core.win_os_agent import force_foreground_window, attach_to_user_desktop, get_open_windows

logger = logging.getLogger("JARVIS.AntigravityAgent")

ANTIGRAVITY_EXE = os.path.expandvars(r"%LOCALAPPDATA%\Programs\antigravity\Antigravity.exe")
ANTIGRAVITY_APP_STORAGE = os.path.expandvars(r"%APPDATA%\Antigravity\app_storage.json")
ANTIGRAVITY_DATA_DIR = os.path.expandvars(r"%USERPROFILE%\.gemini\antigravity")
PROMPTS_LOG_PATH = os.path.join(ANTIGRAVITY_DATA_DIR, "lila_prompts_history.json")
AGENTAPI_BIN = os.path.expandvars(r"%LOCALAPPDATA%\Programs\antigravity\resources\bin\language_server.exe")
AGENTAPI_BAT = os.path.join(ANTIGRAVITY_DATA_DIR, "bin", "agentapi.bat")


# ─────────────────────────────────────────────────────────────────────────────
# 1. MULTI-PROJECT INDEXER & NAVIGATOR
# ─────────────────────────────────────────────────────────────────────────────

class AntigravityProjectManager:
    """Discovers, indexes, and manages workspaces/projects for Antigravity."""

    KNOWN_PROJECT_SEARCH_ROOTS = [
        os.path.expandvars(r"%USERPROFILE%\Downloads"),
        os.path.expandvars(r"%USERPROFILE%\flutterprojects"),
        os.path.expandvars(r"%USERPROFILE%\Desktop"),
        "D:\\\\",
    ]

    def __init__(self):
        self._cached_projects: Dict[str, Dict[str, Any]] = {}
        self._last_scan_time: float = 0.0

    def discover_projects(self, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        """Scans filesystem and Antigravity configurations for all developer projects."""
        now = time.time()
        if self._cached_projects and not force_refresh and (now - self._last_scan_time) < 180:
            return self._cached_projects

        projects: Dict[str, Dict[str, Any]] = {}

        # 1. Parse Antigravity app_storage.json for known workspaces
        if os.path.exists(ANTIGRAVITY_APP_STORAGE):
            try:
                with open(ANTIGRAVITY_APP_STORAGE, "r", encoding="utf-8", errors="ignore") as f:
                    storage = json.load(f)
                    aux_session = storage.get("aux-pane-session", "")
                    if isinstance(aux_session, str):
                        try:
                            aux_data = json.loads(aux_session)
                            convos = aux_data.get("conversationPanes", {})
                            for cid, cval in convos.items():
                                tabs = cval.get("tabs", [])
                                for tab in tabs:
                                    f_uri = tab.get("content", {}).get("fileView", {}).get("fileUri", "")
                                    if f_uri and f_uri.startswith("file:///"):
                                        raw_path = f_uri.replace("file:///", "").replace("%20", " ").replace("%3A", ":")
                                        p_obj = Path(raw_path)
                                        # Resolve to highest meaningful workspace directory
                                        for cand in [p_obj.parent, p_obj.parent.parent, p_obj.parent.parent.parent]:
                                            if cand.exists() and cand.is_dir():
                                                # Check if it has git or package.json or is a direct subfolder of Downloads/Desktop
                                                if (cand / ".git").exists() or (cand / "package.json").exists() or (cand / "requirements.txt").exists():
                                                    name_key = cand.name.lower().strip()
                                                    if name_key and name_key not in projects:
                                                        projects[name_key] = {
                                                            "name": cand.name,
                                                            "path": str(cand),
                                                            "tech": self._detect_tech(str(cand)),
                                                            "source": "Antigravity Session",
                                                        }
                                                    break
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"[AntigravityAgent] Could not parse app_storage.json: {e}")

        # 2. Filesystem search roots
        for base in self.KNOWN_PROJECT_SEARCH_ROOTS:
            if not os.path.exists(base):
                continue
            try:
                for item in os.listdir(base):
                    full_path = os.path.join(base, item)
                    if not os.path.isdir(full_path):
                        continue
                    if item.startswith(".") or item in ("venv", "node_modules", "$RECYCLE.BIN", "System Volume Information"):
                        continue

                    has_git = os.path.exists(os.path.join(full_path, ".git"))
                    has_pkg = os.path.exists(os.path.join(full_path, "package.json"))
                    has_py = os.path.exists(os.path.join(full_path, "requirements.txt")) or os.path.exists(os.path.join(full_path, "pyproject.toml"))
                    has_flutter = os.path.exists(os.path.join(full_path, "pubspec.yaml"))
                    has_cmake = os.path.exists(os.path.join(full_path, "CMakeLists.txt"))

                    is_project = has_git or has_pkg or has_py or has_flutter or has_cmake or \
                                 any(k in item.lower() for k in ("jarvis", "sih", "gpt", "bot", "app", "web", "flutter"))

                    if is_project:
                        name_key = item.lower().strip()
                        projects[name_key] = {
                            "name": item,
                            "path": full_path,
                            "tech": self._detect_tech(full_path),
                            "source": "Filesystem",
                        }
            except Exception as e:
                logger.warning(f"[AntigravityAgent] Error scanning {base}: {e}")

        # 3. Primary known workspaces
        primary = {
            "jarvis": {
                "name": "jarvis_project",
                "path": r"c:\Users\Rishabh_Joshi\Downloads\jarvis_project",
                "tech": "Python / FastVoice / Gemini Live",
                "source": "Primary Workspace",
            },
            "jarvis_d": {
                "name": "jarvis_project (D Drive)",
                "path": r"D:\jarvis_project",
                "tech": "Python / FastVoice / Gemini Live",
                "source": "Parity Mirror",
            },
            "sih": {
                "name": "sih",
                "path": r"c:\Users\Rishabh_Joshi\Downloads\sih",
                "tech": "Full-Stack SIH 2026 Platform",
                "source": "Hackathon Workspace",
            },
            "offline-gpt": {
                "name": "offline-gpt",
                "path": r"c:\Users\Rishabh_Joshi\Downloads\New folder\offline-gpt",
                "tech": "Local LLM / Ollama",
                "source": "Offline AI",
            },
            "jarvis_test": {
                "name": "Jarvis_Test",
                "path": r"C:\Users\Rishabh_Joshi\Desktop\Jarvis_Test",
                "tech": "General Codebase",
                "source": "Active Workspace",
            },
        }
        for k, v in primary.items():
            if os.path.exists(v["path"]):
                projects[k] = v

        self._cached_projects = projects
        self._last_scan_time = now
        return projects

    def _detect_tech(self, path: str) -> str:
        """Detects the primary technology stack of a project directory."""
        if os.path.exists(os.path.join(path, "pubspec.yaml")):
            return "Flutter / Dart"
        if os.path.exists(os.path.join(path, "package.json")):
            try:
                with open(os.path.join(path, "package.json"), "r", encoding="utf-8", errors="ignore") as f:
                    pkg = json.load(f)
                    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                    if "next" in deps:
                        return "Next.js / React"
                    if "vite" in deps:
                        return "Vite / React"
                    if "vue" in deps:
                        return "Vue.js"
                    return "Node.js"
            except Exception:
                return "JavaScript / TypeScript"
        if os.path.exists(os.path.join(path, "requirements.txt")) or os.path.exists(os.path.join(path, "pyproject.toml")):
            return "Python"
        if os.path.exists(os.path.join(path, "pom.xml")):
            return "Java / Maven"
        if os.path.exists(os.path.join(path, "Cargo.toml")):
            return "Rust"
        return "General Codebase"

    def find_project(self, query: str) -> Optional[Dict[str, Any]]:
        """Fuzzy-matches a query string to the best matching project."""
        if not query:
            return None
        q = query.strip().lower()
        projects = self.discover_projects()

        if q in projects:
            return projects[q]

        for k, p in projects.items():
            if q == p["name"].lower():
                return p

        for k, p in projects.items():
            if q in k or q in p["name"].lower() or q in p["path"].lower():
                return p

        keys = list(projects.keys())
        matches = difflib.get_close_matches(q, keys, n=1, cutoff=0.45)
        if matches:
            return projects[matches[0]]

        return None

    def list_projects(self) -> List[Dict[str, Any]]:
        """Returns a list of all indexed projects with details."""
        projects = self.discover_projects()
        result = []
        for k, p in sorted(projects.items(), key=lambda x: x[1]["name"]):
            result.append({
                "key": k,
                "name": p["name"],
                "path": p["path"],
                "tech": p["tech"],
                "source": p.get("source", "Indexed"),
            })
        return result


# ─────────────────────────────────────────────────────────────────────────────
# 2. PROMPT ENGINEERING SYNTHESIZER
# ─────────────────────────────────────────────────────────────────────────────

class AntigravityPromptSynthesizer:
    """
    Applies battle-tested prompt engineering techniques to turn raw user
    voice requests into production-grade, high-specification prompts for
    Google Antigravity autonomous agents.

    Provides 6 specialized prompting strategies:
      - 'feature': Deep Feature Implementation (/goal)
      - 'debug': Root-Cause Forensic Debugging (/goal)
      - 'perf': Performance & Latency Optimization (/goal)
      - 'refactor': Zero-Regression Architecture Refactoring
      - 'grill_me': Interactive Requirements Alignment (/grill-me)
      - 'swarm': Multi-Agent Concurrent Collaboration (/teamwork-preview)
    """

    STRATEGY_TEMPLATES: Dict[str, Dict[str, Any]] = {
        "feature": {
            "name": "Feature Implementation",
            "persona": (
                "You are operating as a Principal Systems Architect & Senior Staff Engineer on Google Antigravity. "
                "Your mandate is to solve this task with 100% production-quality implementation, airtight logic, "
                "and concrete verification. Exercise deep reasoning, maintain existing code conventions, and never cut corners."
            ),
            "default_slash": "/goal",
            "best_for": "Building net-new subsystems, API endpoints, UI features, or core architectural capabilities.",
            "guidelines": [
                "Ensure all functions and modules have robust error handling, typed signatures, and clear docstrings.",
                "Preserve existing architecture and interfaces; do NOT perform unnecessary breaking refactors.",
                "Guarantee responsive, non-blocking execution for long-running or I/O-bound tasks.",
            ],
        },
        "debug": {
            "name": "Root-Cause Forensic Debugging",
            "persona": (
                "You are operating as a Senior Staff Reliability & Systems Debugging Engineer on Google Antigravity. "
                "Your mandate is to uncover the root cause through forensic log analysis, eliminate the race condition or defect, "
                "and implement a permanent, airtight fix with zero regressions."
            ),
            "default_slash": "/goal",
            "best_for": "Diagnosing intermittent glitches, audio breaking, state synchronization bugs, or crashes.",
            "guidelines": [
                "Inspect historical logs, stack traces, and transcript steps to construct a complete problem trace chain.",
                "Isolate the root cause before editing code; avoid superficial patches or blind trial-and-error.",
                "Execute reproducer tests to prove the defect before applying code modifications.",
                "Run the entire regression test suite after the fix to verify zero side-effects.",
            ],
        },
        "perf": {
            "name": "Performance & Latency Optimization",
            "persona": (
                "You are operating as a Principal Performance Architect on Google Antigravity. "
                "Your objective is high-throughput, sub-frame latency optimization and minimal CPU/memory footprint."
            ),
            "default_slash": "/goal",
            "best_for": "Optimizing real-time screen capture, audio streaming buffers, WebSocket pipelines, and background workers.",
            "guidelines": [
                "Measure and record baseline latency and resource consumption before making changes.",
                "Eliminate polling loops, synchronous I/O on async threads, and unnecessary memory allocations.",
                "Maintain defensive bounds on buffers and scheduling queues to prevent bufferbloat or starvation.",
                "Execute post-optimization benchmark assertions demonstrating measurable latency and CPU improvements.",
            ],
        },
        "refactor": {
            "name": "Architectural Refactoring & Modularity",
            "persona": (
                "You are operating as a Lead Codebase Architect on Google Antigravity. "
                "Your mandate is to decouple subsystems, eliminate technical debt, and preserve 100% backward compatibility."
            ),
            "default_slash": "",
            "best_for": "Decoupling tangled modules, introducing clean observer/event patterns, and standardizing APIs.",
            "guidelines": [
                "Preserve all existing public method signatures and return types; do NOT break dependent callers.",
                "Favor composition and loose coupling (e.g. event emitters, observers, typed protocols).",
                "Keep file changes focused and surgical; avoid cosmetic restyling of unrelated lines.",
                "Verify that all downstream callers in the codebase continue to pass unit tests without modification.",
            ],
        },
        "grill_me": {
            "name": "Interactive Design Alignment (/grill-me)",
            "persona": (
                "You are operating as an Executive Technical Product Manager & Systems Architect on Google Antigravity. "
                "Your objective is to thoroughly interview the operator, surface hidden edge cases, and align on design before coding."
            ),
            "default_slash": "/grill-me",
            "best_for": "Vague, ambiguous, or high-risk feature requests requiring user architectural decisions.",
            "guidelines": [
                "Do NOT start modifying code immediately.",
                "Ask structured, high-value questions one at a time to clarify technical trade-offs, security, and fallbacks.",
                "Synthesize user answers into an implementation_plan.md artifact with clear decision records.",
                "Wait for explicit user approval on the plan before transitioning to implementation mode.",
            ],
        },
        "swarm": {
            "name": "Multi-Agent Swarm Collaboration (/teamwork-preview)",
            "persona": (
                "You are operating as a Swarm Mission Director & Distributed Agent Coordinator on Google Antigravity. "
                "Decompose complex, multi-faceted missions into specialized subagents executing concurrently."
            ),
            "default_slash": "/teamwork-preview",
            "best_for": "Large multi-module tasks requiring simultaneous security auditing, test writing, and benchmarking.",
            "guidelines": [
                "Decompose the task into independent, modular work streams with clear responsibilities.",
                "Invoke specialized subagents (e.g. Security Auditor, QA Automation, Performance Profiler).",
                "Ensure subagents use isolated workspaces (branch or share mode) to prevent file conflicts.",
                "Consolidate all subagent outcomes and verification results into a unified walkthrough.md artifact.",
            ],
        },
    }

    @classmethod
    def normalize_strategy(cls, strategy: Optional[str]) -> str:
        """Maps aliases and user inputs to valid strategy keys."""
        if not strategy:
            return "feature"
        s = strategy.strip().lower()
        if s in cls.STRATEGY_TEMPLATES:
            return s
        if any(k in s for k in ("bug", "fix", "debug", "issue", "error", "problem", "fault")):
            return "debug"
        if any(k in s for k in ("perf", "speed", "latency", "fast", "optimiz")):
            return "perf"
        if any(k in s for k in ("refactor", "clean", "decouple", "modul")):
            return "refactor"
        if any(k in s for k in ("grill", "interview", "ask", "clarify", "align", "plan")):
            return "grill_me"
        if any(k in s for k in ("swarm", "team", "multi", "parallel", "concurrent")):
            return "swarm"
        return "feature"

    @classmethod
    def list_strategies(cls) -> List[Dict[str, Any]]:
        """Returns all available prompting strategies with metadata."""
        return [
            {
                "key": key,
                "strategy_key": key,
                "name": data["name"],
                "description": data["best_for"],
                "best_for": data["best_for"],
                "default_slash": data["default_slash"],
                "persona": data["persona"],
                "guidelines": data["guidelines"],
            }
            for key, data in cls.STRATEGY_TEMPLATES.items()
        ]

    @classmethod
    def get_strategy_guide(cls, strategy_name: Optional[str] = None) -> str:
        """Returns comprehensive strategy guide in markdown."""
        if strategy_name and strategy_name.lower() != "all":
            strat_key = cls.normalize_strategy(strategy_name)
            data = cls.STRATEGY_TEMPLATES[strat_key]
            lines = [
                f"# Antigravity Prompting Strategy: {data['name']}",
                f"- **Strategy Key**: `{strat_key}`",
                f"- **Default Command**: `{data['default_slash'] or 'None (Standard)'}`",
                f"- **Best Suited For**: {data['best_for']}",
                "",
                "## Operational Persona",
                f"> {data['persona']}",
                "",
                "## Core Guidelines",
            ]
            for g in data["guidelines"]:
                lines.append(f"- {g}")
            return "\n".join(lines)

        # Check for full master guide file
        doc_candidates = [
            Path(__file__).resolve().parent.parent / "docs" / "antigravity_prompting_guide.md",
            Path(r"C:\Users\Rishabh_Joshi\Downloads\jarvis_project\docs\antigravity_prompting_guide.md"),
        ]
        for p in doc_candidates:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        return f.read()
                except Exception:
                    pass

        lines = [
            "# Google Antigravity Prompting Strategies Catalog",
            "Available High-Specification Architecture Anchoring (HSAA) strategies for Lila:",
            "",
        ]
        for key, data in cls.STRATEGY_TEMPLATES.items():
            lines.append(f"### `{key}`: {data['name']}")
            lines.append(f"- **Slash Command**: `{data['default_slash'] or 'Standard'}`")
            lines.append(f"- **Best For**: {data['best_for']}")
            lines.append(f"- **Persona**: *{data['persona'][:120]}...*")
            lines.append("")
        return "\n".join(lines)

    @classmethod
    def synthesize_prompt(
        cls,
        raw_request: str,
        project: Optional[Dict[str, Any]] = None,
        strategy: str = "feature",
        technical_specs: Optional[str] = None,
        slash_command: Optional[str] = None,
        urgency: Optional[str] = None,
        negative_constraints: Optional[List[str]] = None,
        verification_steps: Optional[List[str]] = None,
        context_files: Optional[List[str]] = None,
    ) -> str:
        """
        Synthesizes a structured Antigravity prompt incorporating:
          - Strategy-selected Role & Persona definition
          - Environment & Project Anchoring
          - Concrete Functional Requirements & Technical Specifications
          - Negative Constraints (no placeholders, strict parity, no fallbacks)
          - Verification Protocol (test commands, logs, assertions)
          - Targeted Slash Commands (/goal, /grill-me, /browser, /schedule, /teamwork-preview)
        """
        raw = (raw_request or "").strip()
        strat_key = cls.normalize_strategy(strategy)
        strat = cls.STRATEGY_TEMPLATES[strat_key]

        proj_name = project.get("name", "Active Workspace") if project else "Active Workspace"
        proj_path = project.get("path", "") if project else ""
        proj_tech = project.get("tech", "Full Stack") if project else "Full Stack"

        # Determine appropriate slash command
        cmd_prefix = ""
        if slash_command:
            cmd = slash_command.strip()
            cmd_prefix = f"/{cmd.lstrip('/')} "
        elif strat["default_slash"]:
            cmd_prefix = f"{strat['default_slash']} "
        else:
            raw_lower = raw.lower()
            if any(k in raw_lower for k in ("overnight", "deep refactor", "long running", "dont stop until", "complete build", "entire feature")):
                cmd_prefix = "/goal "
            elif any(k in raw_lower for k in ("think carefully", "deep thinking", "complex architecture", "strategic", "multiple perspectives")):
                cmd_prefix = "/owl "
            elif any(k in raw_lower for k in ("website", "browse", "scrape", "web interaction", "youtube", "browser")):
                cmd_prefix = "/browser "
            elif any(k in raw_lower for k in ("daily", "hourly", "cron", "schedule", "remind me in", "recurring")):
                cmd_prefix = "/schedule "

        lines = []
        lines.append(f"{cmd_prefix}# Task: {raw}")
        lines.append("")
        lines.append("## Role & Operational Persona")
        lines.append(strat["persona"])
        lines.append("")
        lines.append("## Workspace & Environment Context")
        lines.append(f"- **Active Project**: `{proj_name}`")
        if proj_path:
            lines.append(f"- **Project Root**: `{proj_path}`")
        lines.append(f"- **Tech Stack**: {proj_tech}")
        lines.append("- **Host Operating System**: Windows 11 (PowerShell terminal syntax: use forward slashes or escaped backslashes, avoid bash-only constructs).")
        lines.append("- **Python Virtualenv**: Active system/project virtualenv.")
        if context_files:
            lines.append(f"- **Relevant Files**: {', '.join(f'`{f}`' for f in context_files)}")
        lines.append("")
        lines.append("## Technical Specifications & Functional Requirements")
        lines.append(f"1. **Core Objective**: Implement the following user requirement precisely:")
        lines.append(f"   > \"{raw}\"")

        if technical_specs:
            lines.append("2. **Detailed Specifications Provided by User**:")
            for spec_line in technical_specs.strip().split("\n"):
                spec_line = spec_line.strip()
                if spec_line:
                    lines.append(f"   - {spec_line}")
        else:
            lines.append(f"2. **Architectural Guidelines ({strat['name']})**:")
            for g in strat["guidelines"]:
                lines.append(f"   - {g}")

        lines.append("")
        lines.append("## Strict Negative Constraints (Zero Toleration)")
        default_constraints = [
            "**NO Placeholder / Mock Code**: Every function must be fully implemented. Never use `pass`, `TODO: implement later`, or mock dummy return values.",
            "**NO Third-Party Fallbacks**: If the system specifies native audio, native APIs, or specific frameworks, maintain 100% purity — do not introduce unapproved fallback libraries.",
            "**Preserve Documentation Integrity**: Preserve all existing comments and docstrings unrelated to this change.",
            "**Parity Guarantee**: If modifying files in `jarvis_project`, ensure changes are reflected across both C: and D: drives with 100% SHA-256 hash parity.",
        ]
        if negative_constraints:
            for nc in negative_constraints:
                default_constraints.append(nc)
        for nc in default_constraints:
            lines.append(f"- {nc}")

        lines.append("")
        lines.append("## Verification & Quality Assurance Protocol")
        lines.append("Before concluding this task, you MUST perform concrete automated or command-line verification:")
        if verification_steps:
            for idx, step in enumerate(verification_steps, 1):
                lines.append(f"{idx}. {step}")
        else:
            lines.append("1. Run relevant unit tests or verification scripts using terminal commands.")
            lines.append("2. Inspect exit codes, logs, and output to guarantee zero uncaught exceptions or regressions.")
            lines.append("3. Summarize all files modified and validation results clearly in your response.")

        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 3. INTERACTIVE ANTIGRAVITY EXECUTOR
# ─────────────────────────────────────────────────────────────────────────────

class AntigravityExecutor:
    """Interacts with Antigravity desktop window to foreground, switch projects, and execute prompts."""

    @staticmethod
    def find_antigravity_window() -> Optional[Dict[str, Any]]:
        """Finds any open top-level Antigravity IDE window."""
        attach_to_user_desktop()
        windows = get_open_windows()
        for win in windows:
            if win.get("proc_name", "").lower() == "antigravity.exe":
                return win
            t_low = win.get("title", "").lower()
            if "antigravity" in t_low or "dance animation system" in t_low:
                return win
        return None

    @staticmethod
    def launch_or_focus_antigravity(project_path: Optional[str] = None) -> bool:
        """
        Brings Antigravity to the foreground window.
        If a project_path is specified, always spawns the EXE with that path so
        Electron switches workspace even if the IDE is already open.
        If no project_path, just focuses the existing window.
        """
        attach_to_user_desktop()
        ag_win = AntigravityExecutor.find_antigravity_window()

        # If we have a specific project path to switch to, always launch/pass it
        # (Antigravity Electron handles "already open" gracefully and switches workspace)
        if project_path and os.path.exists(project_path) and os.path.exists(ANTIGRAVITY_EXE):
            try:
                subprocess.Popen([ANTIGRAVITY_EXE, project_path], shell=False)
                for _ in range(16):
                    time.sleep(0.25)
                    w = AntigravityExecutor.find_antigravity_window()
                    if w and w.get("hwnd"):
                        force_foreground_window(w["hwnd"])
                        return True
            except Exception as e:
                logger.error(f"[AntigravityAgent] Failed to launch Antigravity with project_path: {e}")
            # Fall through to focus existing window
            if ag_win and ag_win.get("hwnd"):
                force_foreground_window(ag_win["hwnd"])
                return True
            return False

        # No project_path: just focus existing window or launch bare
        if ag_win and ag_win.get("hwnd"):
            force_foreground_window(ag_win["hwnd"])
            return True

        if os.path.exists(ANTIGRAVITY_EXE):
            try:
                subprocess.Popen([ANTIGRAVITY_EXE], shell=False)
                for _ in range(12):
                    time.sleep(0.25)
                    w = AntigravityExecutor.find_antigravity_window()
                    if w and w.get("hwnd"):
                        force_foreground_window(w["hwnd"])
                        return True
            except Exception as e:
                logger.error(f"[AntigravityAgent] Failed to launch Antigravity: {e}")
                return False

        return ag_win is not None

    # Cached active conversation ID — shared across all calls in this process
    _active_conversation_id: Optional[str] = None

    @classmethod
    def execute_via_agentapi(
        cls,
        engineered_prompt: str,
        task_title: str = "",
        conversation_id: Optional[str] = None,
        project_name: Optional[str] = None,
        force_new: bool = False,
    ) -> Optional[str]:

        """
        Executes a prompt via Google Antigravity's native agentapi CLI.

        Strategy (same-chat reuse first):
          1. If conversation_id is provided or cached, attempt agentapi send-message.
          2. If no conversation exists, resolve via AntigravityStatusMonitor.
          3. If send-message succeeds → return existing conversation_id.
          4. If send-message fails or force_new=True → fallback to new-conversation.
          5. Cache the resulting conversation_id for subsequent calls.

        Returns conversation_id string if successful, None otherwise.
        """
        exe = None
        if os.path.exists(AGENTAPI_BIN):
            exe = AGENTAPI_BIN
        elif os.path.exists(AGENTAPI_BAT):
            exe = AGENTAPI_BAT

        if not exe:
            return None

        clean_title = (task_title or "Task Execution")[:60].replace('"', "'")

        # Build the agentapi prefix based on exe type
        api_prefix = [exe, "agentapi"] if exe.endswith(".exe") else [exe]

        # ── 1. Resolve active conversation ID ────────────────────────────────
        active_cid = conversation_id or cls._active_conversation_id
        if not active_cid and not force_new:
            try:
                convo_info = _status_monitor.resolve_conversation(project_name)
                if convo_info:
                    candidate_cid = convo_info[0]
                    # Validate the conversation still exists in Antigravity
                    val_cmd = api_prefix + ["get-conversation-metadata", candidate_cid]
                    val_res = subprocess.run(val_cmd, capture_output=True, text=True, timeout=10)
                    if val_res.returncode == 0:
                        active_cid = candidate_cid
                        logger.info(f"[AntigravityAgent] Resolved active conversation: {active_cid}")
                    else:
                        logger.info(f"[AntigravityAgent] Conversation {candidate_cid} no longer valid, will create new")
            except Exception as e:
                logger.warning(f"[AntigravityAgent] Could not resolve conversation: {e}")

        # ── 2. Attempt send-message (same-chat reuse) ─────────────────────────
        if active_cid and not force_new:
            try:
                send_cmd = api_prefix + ["send-message", f"--title={clean_title}", active_cid, engineered_prompt]
                res = subprocess.run(send_cmd, capture_output=True, text=True, timeout=25)
                if res.returncode == 0:
                    cls._active_conversation_id = active_cid
                    logger.info(f"[AntigravityAgent] send-message succeeded → conversation {active_cid}")
                    return active_cid
                else:
                    logger.warning(
                        f"[AntigravityAgent] send-message failed (code {res.returncode}): {res.stderr[:200]}"
                        " — falling back to new-conversation"
                    )
            except Exception as e:
                logger.warning(f"[AntigravityAgent] send-message exception: {e} — falling back to new-conversation")

        # ── 3. Fallback: create a new conversation ─────────────────────────────
        try:
            new_cmd = api_prefix + ["new-conversation", f"--title={clean_title}", engineered_prompt]
            res = subprocess.run(new_cmd, capture_output=True, text=True, timeout=20)
            if res.returncode == 0:
                try:
                    data = json.loads(res.stdout)
                    cid = data.get("response", {}).get("newConversation", {}).get("conversationId", "")
                    if cid:
                        cls._active_conversation_id = cid
                        logger.info(f"[AntigravityAgent] new-conversation created: {cid}")
                        return cid
                except Exception:
                    pass
                logger.info(f"[AntigravityAgent] new-conversation executed (code 0): {res.stdout[:100]}")
                return "active"
            else:
                logger.warning(f"[AntigravityAgent] new-conversation failed (code {res.returncode}): {res.stderr}")
        except Exception as e:
            logger.error(f"[AntigravityAgent] agentapi execution failed: {e}")

        return None




    @staticmethod
    def inject_prompt(engineered_prompt: str, task_title: str = "", project_name: Optional[str] = None, force_new: bool = False) -> bool:
        """
        Executes an engineered prompt directly in Antigravity.
        Priority:
          1. Copies prompt to Windows clipboard for user review and safety.
          2. Uses native agentapi CLI (same-chat reuse via send-message, fallback new-conversation).
          3. Focuses Antigravity window so user sees progress live.
          4. Fallback: Uses UI mouse click + paste if agentapi is unavailable.
        """
        if not engineered_prompt:
            return False

        # 1. Always copy to clipboard for user safety
        try:
            pyperclip.copy(engineered_prompt)
        except Exception as e:
            logger.warn(f"[AntigravityAgent] Clipboard copy warning: {e}")

        # 2. Try native Antigravity agentapi CLI first (same-chat reuse preferred)
        conv_id = AntigravityExecutor.execute_via_agentapi(engineered_prompt, task_title, project_name=project_name, force_new=force_new)

        # 3. Always bring Antigravity window to the foreground
        ag_win = AntigravityExecutor.find_antigravity_window()
        if not ag_win:
            AntigravityExecutor.launch_or_focus_antigravity()
            time.sleep(0.5)
            ag_win = AntigravityExecutor.find_antigravity_window()

        if ag_win and ag_win.get("hwnd"):
            force_foreground_window(ag_win["hwnd"])

        if conv_id:
            return True

        # 4. Fallback to UI clicking & pasting if agentapi did not succeed
        if ag_win and ag_win.get("hwnd"):
            try:
                rect = ag_win.get("rect")
                if rect:
                    left, top, right, bottom = rect
                    cx = left + int((right - left) * 0.45)
                    cy = max(top + 50, bottom - 80)

                    import ctypes
                    user32 = ctypes.windll.user32
                    user32.SetCursorPos(cx, cy)
                    time.sleep(0.05)
                    user32.mouse_event(0x0002, 0, 0, 0, 0)
                    time.sleep(0.05)
                    user32.mouse_event(0x0004, 0, 0, 0, 0)
                    time.sleep(0.1)

                VK_CONTROL = 0x11
                VK_V = 0x56
                VK_RETURN = 0x0D

                user32.keybd_event(VK_CONTROL, 0, 0, 0)
                user32.keybd_event(VK_V, 0, 0, 0)
                time.sleep(0.06)
                user32.keybd_event(VK_V, 0, 2, 0)
                user32.keybd_event(VK_CONTROL, 0, 2, 0)
                time.sleep(0.2)

                user32.keybd_event(VK_RETURN, 0, 0, 0)
                time.sleep(0.06)
                user32.keybd_event(VK_RETURN, 0, 2, 0)
                return True
            except Exception as e:
                logger.error(f"[AntigravityAgent] UI injection failed: {e}")

        return False


    @staticmethod
    def record_prompt_history(raw_request: str, engineered_prompt: str, project_name: str, executed: bool):
        """Saves a persistent record of the dispatched prompt for audit and review."""
        try:
            history = []
            if os.path.exists(PROMPTS_LOG_PATH):
                try:
                    with open(PROMPTS_LOG_PATH, "r", encoding="utf-8") as f:
                        history = json.load(f)
                except Exception:
                    history = []

            entry = {
                "timestamp": datetime.now().isoformat(),
                "raw_request": raw_request,
                "project": project_name,
                "executed": executed,
                "engineered_prompt": engineered_prompt,
            }
            history.append(entry)
            if len(history) > 100:
                history = history[-100:]

            os.makedirs(os.path.dirname(PROMPTS_LOG_PATH), exist_ok=True)
            with open(PROMPTS_LOG_PATH, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            logger.warning(f"[AntigravityAgent] Failed to record prompt history: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 4. TRANSCRIPT PARSING & STREAMING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _clean_task_title(raw: str) -> str:
    """Extracts a clean, human-readable task title from raw prompt content."""
    t = raw.replace("<USER_REQUEST>", "").replace("</USER_REQUEST>", "").strip()
    lines = [l.strip() for l in t.splitlines() if l.strip()]
    for l in lines:
        cleaned = re.sub(r"^/[a-zA-Z_\-]+\s*", "", l).strip()
        if cleaned.lower().startswith("# task:"):
            return cleaned[7:].strip()
        if cleaned.lower().startswith("task:"):
            return cleaned[5:].strip()
    # Filter out markdown formatting / system metadata tags
    for l in lines:
        if not l.startswith("<") and not l.startswith("#") and not l.startswith("```"):
            return l[:140]
    return lines[0][:140] if lines else "General Antigravity Task"


def _read_recent_transcript_steps(filepath: Path, max_bytes: int = 1048576) -> List[Dict[str, Any]]:
    """
    Safely reads recent JSON-line steps from transcript.jsonl up to max_bytes from the tail.
    Handles mid-line split chunk boundaries gracefully and parses all valid JSON step objects.
    """
    if not filepath.exists():
        return []
    try:
        with open(filepath, "rb") as f:
            f.seek(0, os.SEEK_END)
            sz = f.tell()
            read_len = min(max_bytes, sz)
            f.seek(sz - read_len)
            data = f.read(read_len)

        lines = data.split(b"\n")
        # If we didn't read from byte 0, skip the first split chunk as it may be a partial line
        start_idx = 1 if read_len < sz else 0
        parsed = []
        for l in lines[start_idx:]:
            s = l.strip().decode("utf-8", errors="ignore")
            if s and s.startswith("{") and s.endswith("}"):
                try:
                    parsed.append(json.loads(s))
                except Exception:
                    pass
        return parsed
    except Exception as e:
        logger.error(f"[AntigravityAgent] Failed to read recent transcript steps from {filepath}: {e}")
        return []


def _tail_jsonl(filepath: Path, n_lines: int = 30) -> List[Dict[str, Any]]:
    """Reads the last n_lines from a JSONL file."""
    steps = _read_recent_transcript_steps(filepath, max_bytes=524288)
    return steps[-n_lines:]


def _read_task_steps(filepath: Path, max_steps: int = 60) -> List[Dict[str, Any]]:
    """
    Reads steps for the most relevant task from transcript.jsonl.
    Inspects recent conversation turns to identify the active or most recently completed task
    that performed actions, guaranteeing that file modifications and verification commands are captured.
    """
    steps = _read_recent_transcript_steps(filepath, max_bytes=1048576)
    if not steps:
        return []

    # Partition into turns delimited by USER_INPUT
    turns: List[List[Dict[str, Any]]] = []
    current_turn: List[Dict[str, Any]] = []
    for st in steps:
        if st.get("type") == "USER_INPUT":
            if current_turn:
                turns.append(current_turn)
            current_turn = [st]
        else:
            if current_turn:
                current_turn.append(st)
    if current_turn:
        turns.append(current_turn)

    if not turns:
        return steps[-max_steps:]

    latest_turn = turns[-1]
    latest_tc_count = sum(len(st.get("tool_calls", [])) for st in latest_turn)

    # If latest turn has active tool calls or is the only turn, inspect it
    if latest_tc_count >= 2 or len(turns) == 1:
        chosen_turn = latest_turn
    else:
        # Search backward for the most recent completed turn that executed tools
        chosen_turn = None
        for prev_turn in reversed(turns[:-1]):
            prev_tc = sum(len(st.get("tool_calls", [])) for st in prev_turn)
            if prev_tc > 0:
                chosen_turn = prev_turn
                break
        if not chosen_turn:
            chosen_turn = latest_turn

    if len(chosen_turn) <= max_steps:
        return chosen_turn
    else:
        # Preserve user prompt at index 0 and capture the trailing execution steps
        return [chosen_turn[0]] + chosen_turn[-(max_steps - 1):]


# ─────────────────────────────────────────────────────────────────────────────
# 5. ANTIGRAVITY STATUS MONITOR
# ─────────────────────────────────────────────────────────────────────────────

class AntigravityStatusMonitor:
    """
    Monitors the real-time operational status of Google Antigravity (AGY):
    - Process liveness, CPU%, memory usage, window focus
    - Active conversation ID and step counts
    - Agent lifecycle state (RUNNING, WAITING_FOR_USER, IDLE, STOPPED)
    - Active task description and project context (e.g. Jarvis_Test)
    """

    def __init__(self):
        self.brain_dir = Path(os.path.expandvars(r"%USERPROFILE%\.gemini\antigravity\brain"))

    def get_process_info(self) -> Dict[str, Any]:
        """Detects if Antigravity is running and captures process metrics."""
        proc_info = {
            "running": False,
            "pid": None,
            "cpu_percent": 0.0,
            "memory_mb": 0.0,
            "window_title": "",
            "hwnd": None,
            "is_foreground": False,
        }
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                name = proc.info['name'] or ''
                if 'antigravity' in name.lower():
                    proc_info["running"] = True
                    proc_info["pid"] = proc.info['pid']
                    try:
                        p = psutil.Process(proc.info['pid'])
                        proc_info["cpu_percent"] = round(p.cpu_percent(interval=0.01), 1)
                        proc_info["memory_mb"] = round(p.memory_info().rss / (1024 * 1024), 1)
                    except Exception:
                        pass
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        win = AntigravityExecutor.find_antigravity_window()
        if win:
            proc_info["window_title"] = win.get("title", "")
            proc_info["hwnd"] = win.get("hwnd")
            if not proc_info["running"] and win.get("hwnd"):
                proc_info["running"] = True
                if HAS_WIN32:
                    try:
                        _, win_pid = win32process.GetWindowThreadProcessId(win["hwnd"])
                        if win_pid:
                            proc_info["pid"] = win_pid
                            p = psutil.Process(win_pid)
                            proc_info["cpu_percent"] = round(p.cpu_percent(interval=0.01), 1)
                            proc_info["memory_mb"] = round(p.memory_info().rss / (1024 * 1024), 1)
                    except Exception:
                        pass

            if HAS_WIN32 and win.get("hwnd"):
                try:
                    fg_hwnd = win32gui.GetForegroundWindow()
                    proc_info["is_foreground"] = (fg_hwnd == win["hwnd"])
                except Exception:
                    pass

        return proc_info

    def resolve_conversation(self, project_name: Optional[str] = None) -> Optional[Tuple[str, Path, Path]]:
        """
        Resolves the most relevant conversation ID, brain directory, and transcript path.
        If project_name is provided, performs multi-source scoring across artifacts,
        transcript head (workspace & prompt), and transcript tail to identify the exact session.
        Returns: (conversation_id, brain_path, transcript_path) or None.
        """
        if not self.brain_dir.exists():
            return None

        candidates = []
        for d in self.brain_dir.iterdir():
            if d.is_dir() and len(d.name) > 20:
                tpath = d / ".system_generated" / "logs" / "transcript.jsonl"
                if tpath.exists():
                    try:
                        mtime = tpath.stat().st_mtime
                        candidates.append((mtime, d.name, d, tpath))
                    except Exception:
                        continue

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)

        if not project_name:
            latest = candidates[0]
            return (latest[1], latest[2], latest[3])

        p_norm = project_name.lower().strip()
        tokens = {p_norm, p_norm.replace("_", " "), p_norm.replace(" ", "_"), p_norm.replace("-", "_")}
        if "jarvis" in p_norm:
            tokens.update({"jarvis", "jarvis_test", "jarvis test", "jarvis_project", "desktop/jarvis_test", "downloads/jarvis_project"})

        scored_candidates = []
        for mtime, cid, cpath, tpath in candidates[:50]:
            score = 0
            # 1. Check artifacts (walkthrough.md, implementation_plan.md)
            for art_name in ("walkthrough.md", "implementation_plan.md"):
                art_file = cpath / art_name
                if art_file.exists():
                    try:
                        with open(art_file, "r", encoding="utf-8", errors="ignore") as af:
                            art_head = af.read(8192).lower()
                            if any(tok in art_head for tok in tokens):
                                score += 60
                                break
                    except Exception:
                        pass

            # 2. Check transcript HEAD (first 128 KB) and TAIL (last 512 KB)
            try:
                with open(tpath, "rb") as f:
                    head_data = f.read(131072).decode("utf-8", errors="ignore").lower()
                    if any(tok in head_data for tok in tokens):
                        score += 50

                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    tail_len = min(524288, size)
                    f.seek(size - tail_len)
                    tail_data = f.read(tail_len).decode("utf-8", errors="ignore").lower()
                    if any(tok in tail_data for tok in tokens):
                        score += 40
            except Exception:
                pass

            if score > 0:
                scored_candidates.append((score, mtime, cid, cpath, tpath))

        if scored_candidates:
            scored_candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
            best = scored_candidates[0]
            return (best[2], best[3], best[4])

        latest = candidates[0]
        return (latest[1], latest[2], latest[3])

    def get_system_status(self, project_name: Optional[str] = None) -> Dict[str, Any]:
        """Returns comprehensive status dictionary for Antigravity."""
        proc = self.get_process_info()
        convo_info = self.resolve_conversation(project_name)

        status: Dict[str, Any] = {
            "status": "STOPPED" if not proc["running"] else "IDLE",
            "process": proc,
            "project_context": project_name or "Active Workspace",
            "conversation_id": None,
            "total_steps": 0,
            "last_step_type": None,
            "last_step_time": None,
            "active_task": None,
            "artifacts_available": {},
            "voice_summary": "",
        }

        if not convo_info:
            status["voice_summary"] = (
                "Google Antigravity is not currently running and no recent conversations were found."
                if not proc["running"]
                else "Antigravity is open but has no active conversations."
            )
            return status

        cid, cdir, tpath = convo_info
        status["conversation_id"] = cid

        for art in ("walkthrough.md", "implementation_plan.md"):
            ap = cdir / art
            if ap.exists():
                status["artifacts_available"][art] = {
                    "path": str(ap),
                    "size_bytes": ap.stat().st_size,
                    "updated_at": datetime.fromtimestamp(ap.stat().st_mtime).isoformat(),
                }

        steps = _read_recent_transcript_steps(tpath, max_bytes=524288)
        last_step = steps[-1] if steps else {}
        if steps:
            status["total_steps"] = last_step.get("step_index", len(steps))
            status["last_step_type"] = last_step.get("type")
            status["last_step_time"] = last_step.get("created_at")

            for st in reversed(steps):
                if st.get("type") == "USER_INPUT":
                    content = st.get("content", "")
                    clean_task = _clean_task_title(content)
                    if clean_task:
                        status["active_task"] = clean_task
                        break

        if not status["active_task"] and os.path.exists(PROMPTS_LOG_PATH):
            try:
                with open(PROMPTS_LOG_PATH, "r", encoding="utf-8") as f:
                    history = json.load(f)
                    if history:
                        status["active_task"] = history[-1].get("raw_request", "")[:120]
            except Exception:
                pass

        # Robust lifecycle state evaluation
        if not proc["running"]:
            status["status"] = "STOPPED"
        else:
            if steps:
                last_type = last_step.get("type")
                last_state = last_step.get("status")
                has_tool_calls = bool(last_step.get("tool_calls"))

                recent_has_tools = any(bool(s.get("tool_calls")) for s in steps[-3:])
                recent_generic_running = any(s.get("type") == "GENERIC" and s.get("status") == "RUNNING" for s in steps[-3:])

                if has_tool_calls or last_state == "RUNNING" or recent_has_tools or recent_generic_running:
                    status["status"] = "RUNNING"
                elif last_type == "USER_INPUT":
                    status["status"] = "RUNNING"
                elif last_type == "PLANNER_RESPONSE":
                    status["status"] = "WAITING_FOR_USER"
                else:
                    status["status"] = "IDLE"
            else:
                status["status"] = "IDLE"

        task_str = f"on task '{status['active_task']}'" if status['active_task'] else ""
        proj_str = f"in {project_name}" if project_name else ""
        if status["status"] == "RUNNING":
            status["voice_summary"] = f"Antigravity is actively executing {proj_str} {task_str}. Memory is {proc['memory_mb']} megabytes with {status['total_steps']} steps recorded."
        elif status["status"] == "WAITING_FOR_USER":
            status["voice_summary"] = f"Antigravity is waiting for user response {proj_str} {task_str}. {len(status['artifacts_available'])} artifacts are available."
        elif status["status"] == "IDLE":
            status["voice_summary"] = f"Antigravity is idle {proj_str}. Total steps executed in session: {status['total_steps']}."
        else:
            status["voice_summary"] = f"Antigravity process is currently stopped. Last recorded session was {cid[:8]}."

        return status


# ─────────────────────────────────────────────────────────────────────────────
# 6. ANTIGRAVITY ACTION ANALYZER
# ─────────────────────────────────────────────────────────────────────────────

class AntigravityActionAnalyzer:
    """
    Parses and summarizes completed actions, tool calls, and modifications
    executed by Google Antigravity autonomous agents.
    """

    def __init__(self, monitor: Optional[AntigravityStatusMonitor] = None):
        self.monitor = monitor or AntigravityStatusMonitor()

    def analyze_completed_actions(
        self,
        project_name: Optional[str] = None,
        conversation_id: Optional[str] = None,
        max_steps: int = 50,
    ) -> Dict[str, Any]:
        """
        Analyzes the latest executed task's actions in Antigravity.
        Returns a structured dictionary of tools used, files modified,
        commands run, verification outcomes, and a spoken narrative summary.
        """
        if conversation_id:
            cdir = self.monitor.brain_dir / conversation_id
            tpath = cdir / ".system_generated" / "logs" / "transcript.jsonl"
            if not tpath.exists():
                return {"error": f"Conversation '{conversation_id}' transcript not found."}
            cid = conversation_id
        else:
            convo = self.monitor.resolve_conversation(project_name)
            if not convo:
                return {"error": "No Antigravity conversations found."}
            cid, cdir, tpath = convo

        steps = _read_task_steps(tpath, max_steps=max_steps)
        if not steps:
            return {
                "conversation_id": cid,
                "project": project_name or "Active Workspace",
                "message": "No executed actions found in transcript.",
                "voice_summary": "No executed actions found in the current Antigravity conversation.",
            }

        tools_count: Dict[str, int] = {}
        files_modified: List[str] = []
        files_viewed: List[str] = []
        commands_executed: List[Dict[str, Any]] = []
        subagents_spawned: List[str] = []
        task_objective = ""
        task_start_time = None
        task_end_time = None

        for st in steps:
            if st.get("type") == "USER_INPUT" and not task_objective:
                task_objective = _clean_task_title(st.get("content", ""))
                task_start_time = st.get("created_at")

            for tc in st.get("tool_calls", []):
                tname = tc.get("name", "unknown")
                tools_count[tname] = tools_count.get(tname, 0) + 1
                args = tc.get("args", {})

                if tname in ("replace_file_content", "multi_replace_file_content", "write_to_file", "edit_file"):
                    raw_tf = (
                        args.get("TargetFile")
                        or args.get("target_file")
                        or args.get("filepath")
                        or args.get("path")
                        or args.get("file_path")
                        or ""
                    )
                    tf = str(raw_tf).strip("\"' ")
                    if tf and tf not in files_modified:
                        files_modified.append(tf)

                elif tname in ("view_file", "read_file"):
                    raw_tf = args.get("AbsolutePath") or args.get("path") or args.get("filepath") or ""
                    tf = str(raw_tf).strip("\"' ")
                    if tf and tf not in files_viewed:
                        files_viewed.append(tf)

                elif tname == "run_command":
                    raw_cmd = args.get("CommandLine") or args.get("command") or ""
                    cmd = str(raw_cmd).strip("\"' ")
                    summary = (args.get("toolSummary") or "").strip("\"' ")
                    if cmd:
                        commands_executed.append({
                            "command": cmd[:120],
                            "summary": summary,
                        })

                elif tname in ("invoke_subagent", "define_subagent"):
                    subagents = args.get("Subagents", [])
                    for sub in subagents:
                        role = sub.get("Role", "Subagent")
                        subagents_spawned.append(role)

            if st.get("created_at"):
                task_end_time = st.get("created_at")

        walkthrough_path = cdir / "walkthrough.md"
        walkthrough_summary = ""
        walkthrough_files: List[str] = []
        if walkthrough_path.exists():
            try:
                with open(walkthrough_path, "r", encoding="utf-8", errors="ignore") as f:
                    wt_text = f.read(6000)
                    lines = wt_text.splitlines()
                    for idx, line in enumerate(lines):
                        sline = line.strip()
                        if any(sline.startswith(h) for h in ("## Executive Summary", "## Summary", "# Walkthrough", "### Summary", "## 1. Summary")):
                            collected = []
                            for nxt in lines[idx + 1:idx + 8]:
                                nxt_s = nxt.strip()
                                if nxt_s and not nxt_s.startswith("#") and not nxt_s.startswith("```") and not nxt_s.startswith("---"):
                                    collected.append(nxt_s)
                                    if len(" ".join(collected)) > 260:
                                        break
                            if collected:
                                walkthrough_summary = " ".join(collected)
                                break
                    if not files_modified:
                        f_matches = re.findall(r"\[`?([a-zA-Z0-9_\-\.\/\\\:]+\.[a-zA-Z0-9]+)`?\]\(file:///", wt_text)
                        for fm in f_matches:
                            if fm not in walkthrough_files:
                                walkthrough_files.append(fm)
            except Exception:
                pass

        effective_files = files_modified or walkthrough_files
        mod_count = len(effective_files)
        cmd_count = len(commands_executed)
        proj_label = f"in {project_name}" if project_name else ""

        if mod_count > 0 or cmd_count > 0:
            voice_summary = (
                f"Antigravity executed {len(steps)} steps {proj_label} for task '{task_objective[:60]}'. "
                f"Modified {mod_count} files and ran {cmd_count} verification commands. "
            )
            if effective_files:
                basenames = [os.path.basename(f.strip("\"' ")) for f in effective_files[:3]]
                voice_summary += f"Updated {', '.join(basenames)}. "
            if walkthrough_summary:
                voice_summary += f"Summary: {walkthrough_summary[:150]}."
        elif walkthrough_summary:
            voice_summary = (
                f"Antigravity recently completed tasks {proj_label}. "
                f"Walkthrough summary: {walkthrough_summary[:180]}."
            )
        else:
            voice_summary = f"Antigravity executed {len(steps)} steps {proj_label} on task '{task_objective[:60]}'."

        return {
            "conversation_id": cid,
            "project": project_name or "Active Workspace",
            "task_objective": task_objective or "General Pair-Programming Task",
            "task_start_time": task_start_time,
            "task_end_time": task_end_time,
            "total_steps_analyzed": len(steps),
            "tools_invoked": tools_count,
            "files_modified": effective_files,
            "files_viewed": files_viewed[:10],
            "commands_executed": commands_executed[:10],
            "subagents_spawned": subagents_spawned,
            "has_walkthrough": walkthrough_path.exists(),
            "has_implementation_plan": (cdir / "implementation_plan.md").exists(),
            "walkthrough_snippet": walkthrough_summary,
            "voice_summary": voice_summary.strip(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 7. ANTIGRAVITY ARTIFACT MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class AntigravityArtifactManager:
    """Manages reading, rendering, and opening Antigravity brain artifacts."""

    def __init__(self, monitor: Optional[AntigravityStatusMonitor] = None):
        self.monitor = monitor or AntigravityStatusMonitor()

    def get_artifact(
        self,
        artifact_name: str = "walkthrough",
        conversation_id: Optional[str] = None,
        project_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Reads artifact markdown content and metadata."""
        if conversation_id:
            cdir = self.monitor.brain_dir / conversation_id
        else:
            convo = self.monitor.resolve_conversation(project_name)
            if not convo:
                return None
            _, cdir, _ = convo

        art_low = artifact_name.lower()
        if "prompt" in art_low or "guide" in art_low:
            fname = "antigravity_prompting_guide.md"
            apath = cdir / fname
            if not apath.exists():
                fallback_path = Path("D:/jarvis_project/docs/antigravity_prompting_guide.md")
                if fallback_path.exists():
                    apath = fallback_path
        elif "plan" in art_low:
            fname = "implementation_plan.md"
            apath = cdir / fname
        else:
            fname = "walkthrough.md"
            apath = cdir / fname

        if not apath.exists():
            return None

        try:
            with open(apath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            return {
                "artifact_name": fname,
                "file_path": str(apath),
                "size_bytes": apath.stat().st_size,
                "updated_at": datetime.fromtimestamp(apath.stat().st_mtime).isoformat(),
                "content": content,
            }
        except Exception as e:
            logger.error(f"[AntigravityAgent] Failed to read artifact {apath}: {e}")
            return None

    def open_artifact_on_screen(
        self,
        artifact_name: str = "walkthrough",
        conversation_id: Optional[str] = None,
        project_name: Optional[str] = None,
    ) -> str:
        """Opens artifact in default system editor and brings Antigravity/editor forward."""
        data = self.get_artifact(artifact_name, conversation_id, project_name)
        if not data:
            return f"Artifact '{artifact_name}' not found for project '{project_name or 'current'}'."

        fp = data["file_path"]
        try:
            os.startfile(fp)
            return f"Successfully opened {data['artifact_name']} on screen from {fp}."
        except Exception as e:
            return f"Error opening artifact {fp}: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# 8. UNIFIED CONVENIENCE API (DISPATCHED BY LIVE TOOLS & FAST VOICE)
# ─────────────────────────────────────────────────────────────────────────────

_manager = AntigravityProjectManager()
_synthesizer = AntigravityPromptSynthesizer()
_executor = AntigravityExecutor()
_status_monitor = AntigravityStatusMonitor()
_action_analyzer = AntigravityActionAnalyzer(_status_monitor)
_artifact_manager = AntigravityArtifactManager(_status_monitor)


def list_antigravity_projects() -> List[Dict[str, Any]]:
    """Returns all projects indexed for Antigravity."""
    return _manager.list_projects()


def navigate_to_project(project_name_or_path: str) -> str:
    """Switches to or opens a project in Antigravity."""
    proj = _manager.find_project(project_name_or_path)
    target_path = proj["path"] if proj else project_name_or_path
    target_name = proj["name"] if proj else os.path.basename(project_name_or_path)

    if not os.path.exists(target_path):
        return f"Could not find project '{project_name_or_path}'. Available projects: {', '.join(p['name'] for p in _manager.list_projects()[:6])}."

    success = _executor.launch_or_focus_antigravity(target_path)
    if success:
        return f"Switched Antigravity workspace to '{target_name}' at {target_path}."
    else:
        return f"Failed to switch Antigravity to '{target_name}'."


def craft_antigravity_prompt(
    raw_request: str,
    project_name: Optional[str] = None,
    technical_specs: Optional[str] = None,
    slash_command: Optional[str] = None,
    strategy: str = "feature",
    context_files: Optional[List[str]] = None,
    negative_constraints: Optional[List[str]] = None,
    verification_steps: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Synthesizes an engineered prompt without executing it immediately."""
    proj = _manager.find_project(project_name) if project_name else None
    prompt = _synthesizer.synthesize_prompt(
        raw_request=raw_request,
        project=proj,
        technical_specs=technical_specs,
        slash_command=slash_command,
        strategy=strategy,
        context_files=context_files,
        negative_constraints=negative_constraints,
        verification_steps=verification_steps,
    )
    strat_key = _synthesizer.normalize_strategy(strategy)
    strat = _synthesizer.STRATEGY_TEMPLATES[strat_key]
    return {
        "project": proj["name"] if proj else "Active Workspace",
        "strategy": strat_key,
        "strategy_name": strat["name"],
        "engineered_prompt": prompt,
        "techniques_applied": [
            f"Role & Persona Definition ({strat['persona'].split('.')[0]})",
            "Environment & Workspace Anchoring (Windows 11, PowerShell, virtualenv)",
            f"Technical Specifications & Architectural Guidelines ({strat['name']})",
            "Strict Negative Constraints (No placeholders, strict parity, no fallbacks)",
            "Automated Verification Protocol (test commands, logs)",
            f"Antigravity Slash Command Selection ({strat.get('default_slash') or 'Dynamic'})",
        ]
    }


def execute_antigravity_task(
    task_description: str,
    project_name: Optional[str] = None,
    technical_specifications: Optional[str] = None,
    slash_command: Optional[str] = None,
    urgency: Optional[str] = None,
    strategy: str = "feature",
    context_files: Optional[List[str]] = None,
    negative_constraints: Optional[List[str]] = None,
    verification_steps: Optional[List[str]] = None,
) -> str:
    """
    Synthesizes an engineered prompt with full technical specifications and
    executes it directly into Antigravity.
    """
    proj = _manager.find_project(project_name) if project_name else None
    p_name = proj["name"] if proj else "Active Workspace"
    p_path = proj["path"] if proj else None

    # 1. Synthesize engineered prompt
    engineered_prompt = _synthesizer.synthesize_prompt(
        raw_request=task_description,
        project=proj,
        technical_specs=technical_specifications,
        slash_command=slash_command,
        urgency=urgency,
        strategy=strategy,
        context_files=context_files,
        negative_constraints=negative_constraints,
        verification_steps=verification_steps,
    )

    # 2. Switch/focus project if specified
    if p_path:
        _executor.launch_or_focus_antigravity(p_path)
    else:
        _executor.launch_or_focus_antigravity()

    # 3. Inject prompt into Antigravity (same-chat reuse first, new-conversation fallback)
    task_title = task_description[:50]
    success = _executor.inject_prompt(engineered_prompt, task_title=task_title, project_name=p_name)


    # 4. Record history
    _executor.record_prompt_history(
        raw_request=task_description,
        engineered_prompt=engineered_prompt,
        project_name=p_name,
        executed=success,
    )

    if success:
        return (
            f"Successfully launched task '{task_title}' (strategy: {strategy}) into Google Antigravity for '{p_name}'. "
            f"Antigravity window has been foregrounded and the agent is actively executing."
        )
    else:
        return (
            f"Formulated engineered prompt for '{p_name}' and copied to clipboard, "
            f"but could not inject into Antigravity."
        )


def list_antigravity_prompt_strategies() -> List[Dict[str, Any]]:
    """Returns catalog of high-specification Antigravity prompting strategies."""
    return _synthesizer.list_strategies()


def get_antigravity_prompting_guide(strategy_name: Optional[str] = None) -> str:
    """Returns markdown documentation guide for Antigravity prompting strategies."""
    return _synthesizer.get_strategy_guide(strategy_name=strategy_name)


def get_antigravity_status(project_name: Optional[str] = None) -> Dict[str, Any]:
    """Returns comprehensive real-time operational status for Google Antigravity."""
    return _status_monitor.get_system_status(project_name=project_name)


def analyze_antigravity_actions(
    project_name: Optional[str] = None,
    conversation_id: Optional[str] = None,
    max_steps: int = 50,
) -> Dict[str, Any]:
    """Analyzes completed actions, tool calls, and files modified for a project or conversation."""
    return _action_analyzer.analyze_completed_actions(
        project_name=project_name,
        conversation_id=conversation_id,
        max_steps=max_steps,
    )


def get_antigravity_status_voice_summary(project_name: Optional[str] = None) -> str:
    """Returns a natural, concise spoken summary of Antigravity runtime status."""
    status = _status_monitor.get_system_status(project_name=project_name)
    return status.get("voice_summary", "Antigravity status unavailable.")


def get_antigravity_actions_voice_summary(project_name: Optional[str] = None) -> str:
    """Returns a natural spoken summary of executed actions, modified files, and outcomes."""
    analysis = _action_analyzer.analyze_completed_actions(project_name=project_name)
    if "voice_summary" in analysis and analysis["voice_summary"]:
        return analysis["voice_summary"]
    status = _status_monitor.get_system_status(project_name=project_name)
    return status.get("voice_summary", "Antigravity action analysis unavailable.")


def get_antigravity_voice_summary(project_name: Optional[str] = None, mode: str = "auto") -> str:
    """Returns a natural, concise spoken summary for voice responses."""
    if mode == "status":
        return get_antigravity_status_voice_summary(project_name)
    elif mode == "actions":
        return get_antigravity_actions_voice_summary(project_name)

    # Auto mode: if Antigravity is actively RUNNING, report status & active task.
    status = _status_monitor.get_system_status(project_name=project_name)
    if status.get("status") == "RUNNING":
        return status.get("voice_summary", "")

    # Otherwise report executed action summary
    analysis = _action_analyzer.analyze_completed_actions(project_name=project_name)
    if analysis.get("voice_summary"):
        return analysis["voice_summary"]
    return status.get("voice_summary", "Antigravity status unavailable.")


def read_antigravity_artifact(
    artifact_name: str = "walkthrough",
    project_name: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> str:
    """Reads markdown content of walkthrough.md or implementation_plan.md."""
    data = _artifact_manager.get_artifact(
        artifact_name=artifact_name,
        conversation_id=conversation_id,
        project_name=project_name,
    )
    if not data:
        return f"Artifact '{artifact_name}' not found for project '{project_name or 'current'}'."
    return data["content"]


def open_antigravity_artifact(
    artifact_name: str = "walkthrough",
    project_name: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> str:
    """Opens artifact in system default editor and brings window forward."""
    return _artifact_manager.open_artifact_on_screen(
        artifact_name=artifact_name,
        conversation_id=conversation_id,
        project_name=project_name,
    )


