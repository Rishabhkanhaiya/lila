"""
background_agent.py — Asynchronous Sub-Agent Mission Manager with Proactive Voice Callback
========================================================================================
Enables Lila/JARVIS to spawn non-blocking background missions (deep research, file downloads,
batch operations, code tasks), continue conversation seamlessly in Gemini Live voice thread,
and proactively speak aloud upon mission completion using 100% pure Aoede voice.
"""

import time
import uuid
import threading
import traceback
from typing import Dict, Any, Optional

from core.jarvis_logger import log_info, log_warn, log_error
from core.state_bridge import broadcast_state


class BackgroundMissionManager:
    """Manages non-blocking autonomous sub-agent missions."""

    def __init__(self):
        self._active_missions: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def spawn(self, prompt: str, mission_type: str = "general") -> str:
        """
        Spawns a background mission thread.
        Returns immediate conversational confirmation to unblock user interaction.
        """
        mission_id = f"mission_{uuid.uuid4().hex[:6]}"
        with self._lock:
            self._active_missions[mission_id] = {
                "id": mission_id,
                "prompt": prompt,
                "type": mission_type,
                "status": "running",
                "start_time": time.time(),
                "result": None,
                "error": None
            }

        worker = threading.Thread(
            target=self._run_mission,
            args=(mission_id, prompt, mission_type),
            daemon=True,
            name=f"SubAgent-{mission_id}"
        )
        worker.start()
        log_info("background_agent", f"Spawned background mission {mission_id}: '{prompt[:80]}'")
        
        # Companion-style immediate confirmation
        return (
            f"I've started that in the background for you, Rishabh! (Mission ID: {mission_id}). "
            f"I'll let you know the second it's done. What else can I help you with?"
        )

    def _run_mission(self, mission_id: str, prompt: str, mission_type: str):
        """Worker execution loop for the background mission."""
        log_info("background_agent", f"Mission {mission_id} execution started.")
        try:
            broadcast_state(mood="focused", caption=f"Background mission: {prompt[:40]}")
            result_summary = ""

            # Route by mission type or general agent
            p_low = prompt.lower()
            if "research" in mission_type or any(w in p_low for w in ["research", "investigate", "analyze"]):
                try:
                    from core.web_reader import execute_research
                    raw_res = execute_research(prompt, max_sources=4)
                    result_summary = raw_res[:800] if raw_res else "Research concluded."
                except Exception:
                    from search_service import quick_search
                    result_summary = quick_search(prompt)[:600]

            elif "download" in mission_type or "download" in p_low:
                from core.live_tools import dispatch_tool_call
                res = dispatch_tool_call("download_resource", {"query": prompt})
                result_summary = res.get("result") or res.get("error") or str(res)

            elif any(w in p_low for w in ["organize", "clean folder", "sort files"]):
                from core.file_organizer import organize_folder
                result_summary = organize_folder()

            else:
                # General autonomous agent reasoning
                from core.fast_agent import run_direct_agent
                result_summary = run_direct_agent(prompt)

            with self._lock:
                if mission_id in self._active_missions:
                    self._active_missions[mission_id]["status"] = "completed"
                    self._active_missions[mission_id]["result"] = result_summary

            log_info("background_agent", f"Mission {mission_id} finished successfully: {result_summary[:100]}")
            self._notify_completion(mission_id, prompt, result_summary, is_error=False)

        except Exception as e:
            err_msg = str(e)
            log_error("background_agent", f"Mission {mission_id} failed", e)
            with self._lock:
                if mission_id in self._active_missions:
                    self._active_missions[mission_id]["status"] = "failed"
                    self._active_missions[mission_id]["error"] = err_msg
            self._notify_completion(mission_id, prompt, err_msg, is_error=True)

    def _notify_completion(self, mission_id: str, prompt: str, outcome: str, is_error: bool = False):
        """Proactively speaks the completion summary aloud in pure Aoede voice and updates UI."""
        try:
            clean_prompt = prompt[:50].strip()
            if is_error:
                spoken_msg = f"Rishabh, a quick update on your background mission to {clean_prompt}. It ran into an issue: {outcome[:120]}."
            else:
                spoken_msg = f"Hey Rishabh! Your background mission to {clean_prompt} is finished. {outcome[:150]}."

            broadcast_state(
                mood="excited",
                caption=f"Mission {mission_id} finished: {outcome[:60]}"
            )

            # Proactive Aoede voice announcement
            from core.voice import speak
            speak(spoken_msg)

        except Exception as ne:
            log_warn(f"[background_agent]: Notification callback error: {ne}")

    def list_active(self) -> Dict[str, Any]:
        """Returns status of all tracked missions."""
        with self._lock:
            return {
                mid: {
                    "prompt": m["prompt"][:60],
                    "status": m["status"],
                    "elapsed_sec": int(time.time() - m["start_time"])
                }
                for mid, m in self._active_missions.items()
            }


# Global singleton
_mission_manager = None

def get_mission_manager() -> BackgroundMissionManager:
    global _mission_manager
    if _mission_manager is None:
        _mission_manager = BackgroundMissionManager()
    return _mission_manager


def spawn_background_mission(prompt: str, mission_type: str = "general") -> str:
    """
    Public tool entry point for Gemini Live & Fast Agent.
    """
    return get_mission_manager().spawn(prompt, mission_type=mission_type)
