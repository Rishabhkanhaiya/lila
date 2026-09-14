"""
core/mission_pipeline.py — Lila Autonomous Multi-Step Chaining Engine
======================================================================
Executes complex, compound missions across multiple tools sequentially in a single turn.

Key Capabilities:
  1. DAG Goal Decomposer: Decomposes compound voice commands into typed atomic steps with dependencies.
  2. Smart Context Pipe: Captures extracted code, file paths, and URLs from Step N and pipes into Step N+1.
  3. Execution & Self-Healing: Runs each tool, verifies intermediate disk/app artifacts, and retries if needed.
  4. Real-Time Telemetry: Broadcasts live progress (Step X/Y) to desktop 3D overlay (8765) and mobile companion (8766).
  5. Zero Dead-Air Optimistic Speech: Immediately speaks a natural girlfriend confirmation while pipeline runs.
"""

import os
import re
import time
import json
import logging
import threading
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from pathlib import Path

from core.jarvis_logger import log_info, log_warn, log_error

logger = logging.getLogger("JARVIS.MissionPipeline")


# ─────────────────────────────────────────────────────────────────────────────
# Data Structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PipelineStep:
    step_id: int
    title: str
    tool: str
    action: str
    params: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[int] = field(default_factory=list)
    verify_type: Optional[str] = None  # "file_exists", "non_empty", "window_exists"
    verify_target: Optional[str] = None
    output: Optional[str] = None
    status: str = "PENDING"  # "PENDING", "RUNNING", "DONE", "FAILED"
    elapsed: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 1. Context Pipe & Variable Binding
# ─────────────────────────────────────────────────────────────────────────────

class ContextPipe:
    """Stores step outputs and resolves {{step_X.key}} dynamic placeholders."""

    def __init__(self):
        self._store: Dict[int, Dict[str, Any]] = {}

    def record_step(self, step_id: int, result_text: str):
        extracted_code = ""
        # Extract fenced code blocks if present
        code_matches = re.findall(r"```(?:\w+)?\s*\n(.*?)```", result_text, re.DOTALL)
        if code_matches:
            extracted_code = "\n\n".join(code_matches).strip()
        else:
            extracted_code = result_text.strip()

        # Extract file path if present
        fp_match = re.search(r"([A-Za-z]:\\[^\s\"\'<>|\n]+|\b[\w\-./\\]+\.(?:jsx?|tsx?|py|html|css|json|md|txt)\b)", result_text)
        extracted_fp = fp_match.group(1).strip() if fp_match else ""

        # Extract URLs if present
        url_match = re.search(r"https?://[^\s\"\'<>]+", result_text)
        extracted_url = url_match.group(0).strip() if url_match else ""

        self._store[step_id] = {
            "output": result_text,
            "code": extracted_code or result_text,
            "filepath": extracted_fp,
            "file_path": extracted_fp,
            "url": extracted_url or "http://localhost:5173",
            "summary": result_text[:200]
        }

    def resolve(self, val: Any) -> Any:
        """Recursively replace {{step_X.key}} in string, list, or dict."""
        if isinstance(val, str):
            def _repl(m):
                s_id = int(m.group(1))
                key = m.group(2).lower()
                step_data = self._store.get(s_id, {})
                res = step_data.get(key)
                if res is not None:
                    return str(res)
                return step_data.get("output", m.group(0))

            return re.sub(r"\{\{step_(\d+)\.(\w+)\}\}", _repl, val)

        elif isinstance(val, dict):
            return {k: self.resolve(v) for k, v in val.items()}
        elif isinstance(val, list):
            return [self.resolve(item) for item in val]
        return val


def is_compound_mission(goal: str) -> bool:
    """Checks if a user request represents a multi-step compound mission requiring sequential chaining."""
    if not goal or len(goal.strip()) < 10:
        return False
    g_low = goal.strip().lower()

    # Explicit chaining conjunctions
    chaining_phrases = [
        "and then", "and write", "and save", "and open", "and preview", "and run",
        ", save it to", ", write it to", ", create", "then open", "then preview", "then run",
        "aur likho", "aur banao", "aur save karo", "aur open karo", "aur run karo"
    ]
    if any(p in g_low for p in chaining_phrases):
        return True

    # Multi-action pattern: research/search + write/save/create + open/preview/run
    has_research = any(w in g_low for w in ["research", "search", "find", "dhundho", "khojo", "summarize", "summary", "look for", "scrape"])
    has_write = any(w in g_low for w in ["write", "create", "save", "banao", "likho", "generate"])
    has_action = any(w in g_low for w in ["open", "preview", "browser", "chrome", "kholo", "run", "launch"])
    if has_research and (has_write or has_action):
        if has_write and has_action:
            return True
        if has_write and any(ext in g_low for ext in [".md", ".py", ".jsx", ".tsx", ".html", ".txt", ".json"]):
            return True

    # Antigravity multi-step tasks
    if "antigravity" in g_low and any(w in g_low for w in ["task", "execute", "run", "fix", "feature", "build"]):
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# 2. Plan Decomposer (Fast Heuristic Rules + Gemini 2.5 Flash Structured Plan)
# ─────────────────────────────────────────────────────────────────────────────

class PlanDecomposer:
    """Decomposes compound missions into structured, executable steps."""

    @staticmethod
    def decompose(goal: str) -> List[PipelineStep]:
        goal_clean = goal.strip()
        g_low = goal_clean.lower()

        # ── Fast Rule-Based Decompositions (<5ms) for Common Patterns ────────
        # Pattern 1: Web Research / Summarization -> Write to File -> Open Preview
        if any(w in g_low for w in ["research", "search", "find", "dhundho", "khojo", "summarize", "summary", "look for", "scrape"]) and \
           any(w in g_low for w in ["write", "create", "save", "banao", "likho", "generate"]) and \
           any(w in g_low for w in ["open", "preview", "browser", "chrome", "kholo", "run", "launch"]):

            # Extract target file or default to appropriate component name
            fname = "sample_component.jsx"
            fn_m = re.search(r"([A-Za-z0-9_\-\\]+\.(?:html|jsx?|tsx?|py|css|md|txt))", goal_clean)
            if fn_m:
                fname = fn_m.group(1)
            elif "html" in g_low or "web" in g_low:
                fname = "index.html"
            elif "python" in g_low or ".py" in g_low:
                fname = "app.py"
            elif "md" in g_low or "markdown" in g_low or "report" in g_low:
                fname = "report.md"

            # Clean search query by stripping write/save target and open/preview clauses
            clean_query = re.sub(r'(?:,\s*)?(?:save|write|create|banao|likho)\s+(?:it\s+)?(?:to|in)?\s*[\w\-./\\]+', '', goal_clean, flags=re.IGNORECASE)
            clean_query = re.sub(r'(?:,\s*)?(?:and\s+)?(?:open|preview|run|kholo)\s+(?:the\s+)?(?:file|preview|browser|it)?', '', clean_query, flags=re.IGNORECASE).strip()
            for p in ['search for ', 'search ', 'research ', 'find ', 'summarize ']:
                if clean_query.lower().startswith(p):
                    clean_query = clean_query[len(p):].strip()
            search_query = f"{clean_query} clean full code snippet or summary" if clean_query else f"{goal_clean} clean full code snippet or summary"

            return [
                PipelineStep(
                    step_id=1,
                    title="Research & Extract Implementation Specs",
                    tool="web_agent",
                    action="search",
                    params={"query": search_query},
                    verify_type="non_empty"
                ),
                PipelineStep(
                    step_id=2,
                    title=f"Write Content to {fname}",
                    tool="file_manager",
                    action="write",
                    params={"target": fname, "content": "{{step_1.code}}"},
                    depends_on=[1],
                    verify_type="file_exists",
                    verify_target=fname
                ),
                PipelineStep(
                    step_id=3,
                    title="Open Live Preview on Screen",
                    tool="file_manager" if not fname.endswith(".py") else "code_engineer",
                    action="open" if not fname.endswith(".py") else "run",
                    params={"target": fname, "filepath": fname},
                    depends_on=[2]
                ),
            ]

        # Pattern 2: Antigravity Coding Task -> Verify Artifact Walkthrough
        if "antigravity" in g_low or ("task" in g_low and any(w in g_low for w in ["code", "project", "repo", "fix", "feature"])):
            return [
                PipelineStep(
                    step_id=1,
                    title="Execute Autonomous Coding in Antigravity",
                    tool="antigravity",
                    action="execute",
                    params={"prompt": goal_clean},
                    verify_type="non_empty"
                ),
                PipelineStep(
                    step_id=2,
                    title="Inspect & Open Walkthrough Artifact on Screen",
                    tool="screen_action",
                    action="click",
                    params={"target": "walkthrough"},
                    depends_on=[1]
                ),
            ]

        # ── Gemini Structured DAG Planner for Arbitrary Missions (Multi-Key, Multi-Model) ────
        try:
            from google import genai
            from google.genai import types

            try:
                from dotenv import load_dotenv
                load_dotenv()
            except Exception:
                pass

            keys = []
            for k in ["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GOOGLE_API_KEY"]:
                val = os.environ.get(k)
                if val and len(val.strip()) > 10 and val.strip() not in keys:
                    keys.append(val.strip())

            if not keys:
                raise ValueError("Missing GEMINI_API_KEY")

            prompt = (
                "You are the Master Mission Planner for JARVIS/Lila.\n"
                "Decompose the following user command into a clean, sequential execution pipeline of 2 to 4 atomic steps.\n"
                f"User Goal: \"{goal_clean}\"\n\n"
                "Available Polymorphic Tools:\n"
                "- web_agent (action: 'search', 'browse', 'scrape', 'research', 'download', params: query, url)\n"
                "- code_engineer (action: 'edit', 'create', 'run', 'query', 'open_workspace', params: filepath, content)\n"
                "- file_manager (action: 'read', 'write', 'open', 'list', 'create_doc', params: target, content)\n"
                "- window_manager (action: 'open', 'switch', 'close', 'minimize', 'maximize', params: app_name)\n"
                "- screen_action (action: 'click', 'type', 'press_keys', 'read', params: target)\n"
                "- antigravity (action: 'execute', 'navigate', 'list', params: prompt)\n"
                "- system_control (action: 'shell', 'system_info', params: command)\n"
                "- media_player (action: 'play_youtube', 'volume', 'control', params: query)\n\n"
                "Return ONLY a JSON array of step objects. Format:\n"
                "[\n"
                "  {\n"
                "    \"step_id\": 1,\n"
                "    \"title\": \"Short step description\",\n"
                "    \"tool\": \"tool_name\",\n"
                "    \"action\": \"action_name\",\n"
                "    \"params\": {\"param_name\": \"value or {{step_1.code}}\"},\n"
                "    \"depends_on\": [],\n"
                "    \"verify_type\": \"file_exists\" | \"non_empty\" | null,\n"
                "    \"verify_target\": \"filename.ext\" | null\n"
                "  }\n"
                "]"
            )

            models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
            last_err = None
            for key in keys:
                for mod in models:
                    try:
                        client = genai.Client(api_key=key)
                        resp = client.models.generate_content(
                            model=mod,
                            contents=prompt,
                            config=types.GenerateContentConfig(
                                response_mime_type="application/json",
                                temperature=0.1
                            )
                        )
                        raw_text = resp.text.strip()
                        data = json.loads(raw_text)
                        if isinstance(data, list) and len(data) > 0:
                            steps = []
                            for s in data:
                                steps.append(PipelineStep(
                                    step_id=int(s.get("step_id", len(steps) + 1)),
                                    title=str(s.get("title", f"Step {len(steps) + 1}")),
                                    tool=str(s.get("tool", "web_agent")),
                                    action=str(s.get("action", "search")),
                                    params=dict(s.get("params", {})),
                                    depends_on=[int(d) for d in s.get("depends_on", [])],
                                    verify_type=s.get("verify_type"),
                                    verify_target=s.get("verify_target")
                                ))
                            return steps
                    except Exception as me:
                        last_err = me
                        continue

            if last_err:
                raise last_err

        except Exception as e:
            logger.warning(f"[PlanDecomposer] LLM decomposition error: {e}. Using intelligent fallback.")

        # Default 2-step fallback
        return [
            PipelineStep(
                step_id=1,
                title="Execute Core Objective",
                tool="web_agent" if "search" in g_low else "file_manager",
                action="search" if "search" in g_low else "open",
                params={"query": goal_clean, "target": goal_clean}
            ),
            PipelineStep(
                step_id=2,
                title="Verify & Focus Result on Screen",
                tool="screen_action",
                action="read",
                params={},
                depends_on=[1]
            )
        ]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Real-Time Telemetry (Desktop Overlay & Mobile UI Broadcaster)
# ─────────────────────────────────────────────────────────────────────────────

class PipelineTelemetry:
    """Broadcasts live step progress to Desktop Companion Overlay (8765) and Mobile (8766)."""

    @staticmethod
    def broadcast_progress(mission: str, step: PipelineStep, current_idx: int, total_steps: int, status: str = "RUNNING"):
        payload = {
            "type": "pipeline_progress",
            "mission": mission[:80],
            "step_id": step.step_id,
            "step_index": current_idx,
            "total_steps": total_steps,
            "title": step.title,
            "tool": f"{step.tool}.{step.action}",
            "status": status,
            "timestamp": time.time()
        }

        # 1. Broadcast to Desktop Companion Overlay (WebSocket 8765)
        try:
            from core.state_bridge import broadcast_state
            step_badge = f"Step {current_idx}/{total_steps}: {step.title}"
            mood = "excited" if status == "DONE" else "focused"
            broadcast_state(mood=mood, caption=step_badge)
        except Exception:
            pass

        # 2. Broadcast to Mobile Companion Server (8766/8767)
        try:
            from core.remote_mobile_server import broadcast_to_mobile
            broadcast_to_mobile(payload)
        except Exception:
            pass

        # 3. Broadcast to Agent Event Bus (JARVIS live feed)
        try:
            from core.agent_events import emit
            emit("pipeline", "step_progress", payload)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# 4. Pipeline Execution Engine
# ─────────────────────────────────────────────────────────────────────────────

class MissionPipeline:
    """The Autonomous Sequential Action Chaining Pipeline Engine."""

    def __init__(self, goal: str, task_type: str = "general"):
        self.goal = goal
        self.task_type = task_type
        self.pipe = ContextPipe()
        self.steps: List[PipelineStep] = []
        self.started_at = 0.0
        self.completed_at = 0.0

    def execute(self) -> Dict[str, Any]:
        from core.live_tools import dispatch_tool_call

        self.started_at = time.time()
        logger.info(f"[MissionPipeline] Starting multi-step chaining for: '{self.goal}'")

        # 1. Decompose Goal
        self.steps = PlanDecomposer.decompose(self.goal)
        total = len(self.steps)

        # 2. Optimistic Voice Acknowledgment if multi-step (>1 step)
        if total > 1:
            self._speak_optimistic_ack(total)

        executed_steps = []
        pipeline_success = True

        # 3. Sequential Execution Loop
        for idx, step in enumerate(self.steps, 1):
            step.status = "RUNNING"
            t_step_start = time.time()
            PipelineTelemetry.broadcast_progress(self.goal, step, idx, total, "RUNNING")

            # Resolve placeholders from predecessor step outputs
            resolved_params = self.pipe.resolve(step.params)
            # Ensure action parameter is set
            if "action" not in resolved_params:
                resolved_params["action"] = step.action

            logger.info(f"[MissionPipeline] Step {idx}/{total} [{step.tool}]: {step.title} | Params: {resolved_params}")

            # Dispatch tool call to live registry
            tool_resp = dispatch_tool_call(step.tool, resolved_params)
            raw_result = tool_resp.get("result") or tool_resp.get("error") or str(tool_resp)
            step.output = raw_result
            step.elapsed = time.time() - t_step_start

            # 4. Verification Check
            verified = self._verify_step(step, resolved_params)
            if not verified:
                # 1-Shot Self-Healing Retry
                logger.warn(f"[MissionPipeline] Step {idx} verification failed. Attempting self-healing retry...")
                retry_resp = self._self_heal_retry(step, resolved_params)
                if retry_resp:
                    step.output = retry_resp
                    verified = self._verify_step(step, resolved_params)

            if verified:
                step.status = "DONE"
                self.pipe.record_step(step.step_id, step.output)
                PipelineTelemetry.broadcast_progress(self.goal, step, idx, total, "DONE")
            else:
                step.status = "FAILED"
                pipeline_success = False
                PipelineTelemetry.broadcast_progress(self.goal, step, idx, total, "FAILED")
                logger.error(f"[MissionPipeline] Step {idx} failed: {step.output}")
                # Continue if non-blocking or break if strictly dependent
                break

            executed_steps.append({
                "step": idx,
                "title": step.title,
                "tool": f"{step.tool}.{step.action}",
                "status": step.status,
                "elapsed": round(step.elapsed, 2),
                "summary": (step.output or "")[:250]
            })

        self.completed_at = time.time()
        total_duration = round(self.completed_at - self.started_at, 2)

        # 5. Build Consolidated Result Synthesis
        summary_lines = [
            f"Autonomous Mission Pipeline {'COMPLETED' if pipeline_success else 'PARTIALLY FINISHED'} in {total_duration}s:",
            f"Goal: \"{self.goal}\"",
        ]
        for s in executed_steps:
            mark = "✅" if s["status"] == "DONE" else "❌"
            summary_lines.append(f"{mark} Step {s['step']}: {s['title']} ({s['elapsed']}s)")

        # Extract final delivery artifact or URL if available
        last_step = self.steps[-1] if self.steps else None
        if last_step and last_step.output:
            summary_lines.append(f"\nFinal Result:\n{last_step.output[:600]}")

        final_synthesis = "\n".join(summary_lines)
        logger.info(f"[MissionPipeline] Pipeline finished in {total_duration}s. Success: {pipeline_success}")

        # Final telemetry broadcast
        try:
            from core.state_bridge import broadcast_state
            broadcast_state(mood="excited" if pipeline_success else "idle", caption=f"Pipeline Completed in {total_duration}s! ✨")
        except Exception:
            pass

        return {
            "success": pipeline_success,
            "goal": self.goal,
            "duration": total_duration,
            "total_steps": total,
            "executed_steps": executed_steps,
            "synthesis": final_synthesis,
            "result": final_synthesis
        }

    def _verify_step(self, step: PipelineStep, params: Dict[str, Any]) -> bool:
        """Verifies step outcome on physical system."""
        if step.verify_type == "file_exists":
            target = step.verify_target or params.get("target") or params.get("filepath") or params.get("file_path") or ""
            if target:
                p = Path(target)
                if not p.is_absolute():
                    p = Path.cwd() / p
                if p.exists() and p.stat().st_size > 0:
                    return True
                # Search downloads or workspace
                dw = Path(os.path.expanduser("~/Downloads")) / Path(target).name
                if dw.exists() and dw.stat().st_size > 0:
                    return True
                try:
                    from core.doc_forge import resolve_output_path
                    res_p = resolve_output_path(target, "")
                    if res_p.exists() and res_p.stat().st_size > 0:
                        return True
                except Exception:
                    pass
                return False

        elif step.verify_type == "non_empty":
            return bool(step.output and len(step.output.strip()) > 10 and "failed" not in step.output.lower()[:30])

        return True

    def _self_heal_retry(self, step: PipelineStep, params: Dict[str, Any]) -> Optional[str]:
        """Performs a 1-shot self-healing recovery if verification failed."""
        from core.live_tools import dispatch_tool_call
        try:
            if step.verify_type == "file_exists":
                target = step.verify_target or params.get("target") or "output.txt"
                content = params.get("content") or self.pipe._store.get(1, {}).get("code", "")
                if content:
                    logger.info(f"[MissionPipeline:SelfHeal] Rewriting {target} via file_manager...")
                    res = dispatch_tool_call("file_manager", {"action": "write", "target": target, "content": content})
                    return res.get("result")
        except Exception as e:
            logger.warn(f"[MissionPipeline:SelfHeal] Error during retry: {e}")
        return None

    def _speak_optimistic_ack(self, total_steps: int):
        """Speaks a brief 1-second girlfriend acknowledgment so user has zero dead-air."""
        def _ack():
            try:
                from core.fast_agent import _global_safe_speak
                # Keep it natural, lively, and warm in Hinglish
                g_low = self.goal.lower()
                if "research" in g_low or "search" in g_low:
                    ack_text = "Haan babe, research karke code likh rahi hoon aur preview open kar rahi hoon!"
                elif "antigravity" in g_low or "code" in g_low:
                    ack_text = "Haan babe, autonomous task execute karke walkthrough open kar rahi hoon!"
                else:
                    ack_text = f"Haan babe! {total_steps} steps mein execute kar rahi hoon, wait karo!"
                _global_safe_speak(ack_text)
            except Exception:
                pass

        threading.Thread(target=_ack, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# 5. Public Convenience API
# ─────────────────────────────────────────────────────────────────────────────

def run_mission_pipeline(goal: str, task_type: str = "general") -> str:
    """Entry point for live tool dispatcher and agent cores."""
    if not goal or not goal.strip():
        return "Please provide a mission goal or compound task to execute."

    pipeline = MissionPipeline(goal.strip(), task_type=task_type)
    res = pipeline.execute()
    return res.get("synthesis") or res.get("result") or str(res)
