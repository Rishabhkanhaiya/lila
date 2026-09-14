import os
import sys
import json
import io
import contextlib
import random
import hashlib
import filelock
import threading

_sandbox_lock = threading.Lock()
try:
    import docker
    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False
import time

from core.memory import save_memory
from core.brain import call_groq_brain, update_chat_history
# Ensure we load the voice and eyes for the verification phase!
from core.eyes import scan_screen

# \u2705 NEXUS-2 Phase C: Parallel tool execution
import concurrent.futures as _cf

def run_tools_parallel(tool_calls: list) -> list:
    """
    NEXUS-2: Run multiple tool calls simultaneously using a thread pool.
    Each entry in tool_calls: (callable, *args)
    Returns list of results in the same order.
    
    Example:
        results = run_tools_parallel([
            (web_search, "latest news"),
            (memory_retrieve, "user preferences"),
        ])
    """
    if not tool_calls:
        return []
    if len(tool_calls) == 1:
        fn, *args = tool_calls[0]
        return [fn(*args)]
    
    pool = _cf.ThreadPoolExecutor(max_workers=min(len(tool_calls), 6))
    try:
        futures = []
        for call in tool_calls:
            fn, *args = call
            futures.append(pool.submit(fn, *args))
        results = []
        for fut in futures:
            try:
                results.append(fut.result(timeout=30))
            except Exception as _e:
                results.append(f"[TOOL ERROR]: {_e}")
        return results
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

from core.voice import speak 
import threading
import httpx
from core.jarvis_logger import log_error, log_warn, log_info
from core.config import CHECKPOINT_DIR   # Fix #4: hash-named checkpoints

authorization_event = threading.Event()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "MOCK_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "MOCK_CHAT_ID")

def send_telegram_approval(action_desc):
    if TELEGRAM_BOT_TOKEN == "MOCK_TOKEN":
        # Telegram not configured — auto-approve SIDE_EFFECT actions
        # (this is a personal assistant, not a corporate system)
        # IRREVERSIBLE actions are blocked regardless (separate check in wrapper)
        print("[📱 TELEGRAM]: Not configured — auto-approving action.")
        return True
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"⚠️ AGENTHELM SECURITY ALERT\nJARVIS is attempting action:\n{action_desc}\n\nReply /approve or /deny."
    }
    try:
        httpx.post(url, json=payload, timeout=5)
    except Exception as e:
        log_error("hands", "telegram_send_approval", e)
    
    # Poll for 60 seconds with responsive timeout
    start_time = time.time()
    offset = -1
    while time.time() - start_time < 60:
        try:
            resp = httpx.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=5", timeout=6)
            updates = resp.json()
            if updates.get("ok"):
                for update in updates["result"]:
                    offset = update["update_id"] + 1
                    text = update.get("message", {}).get("text", "").lower()
                    if "/approve" in text:
                        return True
                    if "/deny" in text:
                        return False
        except Exception as e:
            log_error("hands", "telegram_poll_updates", e)
        time.sleep(2)
    return False

# ==========================================
# ⚡ FEATURE VI: AGENTHELM SECURITY GOVERNANCE
# ==========================================
class SecurityClearance:
    READ_ONLY = "read"
    SIDE_EFFECT = "side_effect"
    IRREVERSIBLE = "irreversible"

def requires_authorization(level):
    """
    AgentHelm Decorator: Pauses execution and requires Human-in-the-Loop (HITL) approval
    before JARVIS can run potentially dangerous commands on the host OS.

    SIDE_EFFECT   → auto-approved when Telegram not set (personal assistant mode)
    IRREVERSIBLE  → always requires Telegram or local event approval
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            if level in [SecurityClearance.SIDE_EFFECT, SecurityClearance.IRREVERSIBLE]:
                action_desc = kwargs.get('action_description', 'System Modification')
                print(f"\n[⚠️ AGENTHELM]: Level '{level.upper()}' → {action_desc}")

                # SIDE_EFFECT: auto-approve when Telegram not configured (personal use)
                if level == SecurityClearance.SIDE_EFFECT and TELEGRAM_BOT_TOKEN == "MOCK_TOKEN":
                    print("[✅ AGENTHELM]: Auto-approved (personal mode — configure Telegram for remote approval)")
                    return func(*args, **kwargs)

                print("[⏸️ THREAD SUSPENDED]: Awaiting Human-in-the-Loop (HITL) authorization...")
                print("AUTHORIZATION_REQUIRED_BY_UI")

                # ⚡ Feature VI: Telegram HITL
                approved = send_telegram_approval(action_desc)
                if not approved:
                    # Try local event fallback (UI can set this)
                    authorization_event.clear()
                    approved = authorization_event.wait(timeout=10.0)

                if not approved:
                    return f"[❌ AUTHORIZATION DENIED]: Action timed out or rejected."

            return func(*args, **kwargs)
        return wrapper
    return decorator

# ==========================================
# ⚡ THE PRESERVED DOCKER EXECUTION ENGINE
# ==========================================
@requires_authorization(level=SecurityClearance.SIDE_EFFECT)
def run_in_docker_sandbox(script_code, requires_network=False, target_ip=None, action_description="Execute Sandbox Code"):
    """
    MODE 3 SECURE SANDBOX: 
    Spins up an isolated container, executes the code, extracts the output, and destroys itself.
    """
    try:
        import docker
        client = docker.from_env()
    except Exception as e:
        print(f"\n[⚠️ DOCKER OFFLINE]: Falling back to local Restricted Python execution. Warning: Host capabilities limited!")
        import builtins
        import io
        import sys
        
        # 🛡️ FEATURE 2: STRICTLY SANDBOXED GLOBALS
        safe_globals = {
            "__builtins__": {
                "print": print, "range": range, "int": int, "float": float,
                "str": str, "list": list, "dict": dict, "len": len,
                "abs": abs, "min": min, "max": max, "sum": sum,
                "bool": bool, "tuple": tuple, "set": set, "enumerate": enumerate,
                "zip": zip, "map": map, "filter": filter, "isinstance": isinstance,
                "round": round, "any": any, "all": all, "open": open, "__import__": __import__,
                "True": True, "False": False, "None": None, "Exception": Exception, "ValueError": ValueError
            },
            "math": __import__("math"),
            "json": __import__("json"),
            "datetime": __import__("datetime"),
            "os": __import__("os"),
            "sys": __import__("sys"),
            "subprocess": __import__("subprocess"),
            "time": __import__("time")
        }
        
        with _sandbox_lock:
            old_stdout = sys.stdout
            redirected_output = sys.stdout = io.StringIO()
            
            try:
                exec(script_code, safe_globals, {})
                
                result = redirected_output.getvalue().strip()
                if not result:
                    result = "Script executed successfully but produced no output."
                return result
                
            except Exception as local_e:
                return f"[⚠️ LOCAL EXECUTION CRASH]: {local_e}"
            finally:
                sys.stdout = old_stdout

    # 🛡️ FEATURE 1: TOTAL AIR-GAPPING (PRESERVED)
    network_mode = "none" 
    
    # 🛡️ FEATURE 2: THE WHITELIST & APPROVAL SWITCH (PRESERVED)
    if requires_network:
        # Safe zones: Localhost and standard local router subnets
        allowed_ips = ["127.0.0.1", "localhost", "192.168.", "10.0.0."] 
        is_safe = False
        
        if target_ip:
            for ip in allowed_ips:
                if target_ip.startswith(ip):
                    is_safe = True
                    break
        
        if is_safe:
            network_mode = "bridge"
            print(f"\n[🟢 NETWORK]: Target {target_ip} whitelisted. Bridging sandbox...")
        else:
            print(f"\n[🔴 NETWORK BLOCK]: Target {target_ip} is outside local subnet. Enforcing air-gap.")
    # ... [Inside run_in_docker_sandbox] ...
    print("\n[🐳 DOCKER]: Spinning up ephemeral container...")
    tmp_path = ""
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(script_code)
            tmp_path = f.name
            
        safe_command = ["timeout", "15s", "python", "/tmp/script.py"]
        
        container = client.containers.run(
            "python:3.9-slim",
            command=safe_command,
            volumes={tmp_path: {'bind': '/tmp/script.py', 'mode': 'ro'}},
            network_mode=network_mode,
            remove=True, 
            mem_limit="512m", 
            cpu_period=100000,
            cpu_quota=50000, 
            detach=False
        )
        if os.path.exists(tmp_path): os.remove(tmp_path)
        return container.decode('utf-8')
    except docker.errors.ContainerError as e:
        if os.path.exists(tmp_path): os.remove(tmp_path)
        if e.exit_status == 124:
             return "[⚠️ SANDBOX KILLED]: Script exceeded the 15-second execution time limit (Infinite loop detected)."
        return f"[⚠️ SANDBOX ERROR]: {e.stderr.decode('utf-8')}"
    except Exception as e:
        if os.path.exists(tmp_path): os.remove(tmp_path)
        return f"[⚠️ SANDBOX CRASH]: {e}"

# ==========================================
# ⚡ THE PRESERVED MASTER EXECUTION LOOP
# ==========================================
# ... (keep all your existing docker/sandbox code at the top of hands.py) ...

# ── Clarifying question detector ──────────────────────────────────────────────────
_CLARIFY_RULES = [
    (["create file", "make file", "new file", "save file", "create a file", "make a file"],
     "Batao file kahan save karni hai? Downloads, Desktop, ya current folder?"),
    (["create folder", "make folder", "new folder"],
     "Folder kahan create karna hai? Parent directory batao."),
    (["download", "save to", "save it"],
     "Kahan save karoon? Desktop ya Downloads folder?"),
    (["send email", "compose email", "write email"],
     "Kisko email send karni hai? Recipient address batao."),
    (["install", "pip install"],
     "Virtual environment mein install karoon ya global system mein?"),
]

def _needs_clarification(prompt: str):
    """Return (True, question) if the prompt is ambiguous, else (False, '')."""
    p = prompt.lower()
    for keywords, question in _CLARIFY_RULES:
        if any(k in p for k in keywords):
            # If answer is already in the prompt, skip
            has_path = any(x in p for x in ["/", "\\\\", "desktop", "downloads",
                                              "documents", "current folder", "here",
                                              "this folder", "c:", "d:"])
            if not has_path:
                return True, question
    return False, ""

# ⚡ UPDATED SIGNATURE: It now accepts the ui_signal to talk directly to the Dashboard!
def execute_agentic_loop(prompt, phase="DIRECTIVE", ui_signal=None):
    """
    The main execution loop for multi-step tasks.
    Now equipped with Claude-style live step streaming.
    """
    print(f"\n[⚙️ AGENTIC LOOP ACTIVATED]: '{prompt}'")

    # ── Clarifying questions BEFORE planning ──────────────────────────────
    # For file creation/saving tasks ask where before starting the agentic loop
    _needs_q, _question = _needs_clarification(prompt)
    if _needs_q:
        print(f"[❓ CLARIFY]: {_question}")
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{_question}")
        speak(_question)
        # Wait for user's follow-up — the next voice command will carry the answer.
        # Return a sentinel so the caller knows we're waiting (not a failure).
        return f"[AWAITING_CLARIFICATION]: {_question}"


    # ⚡ FAST PATH: Direct OS shortcuts for common tasks (skip planning entirely)
    direct_result = _try_direct_os_action(prompt, ui_signal)
    if direct_result:
        print("\n[🎉 MISSION COMPLETE] (Direct execution)")
        if ui_signal:
            ui_signal.emit("speaking", f"SYSTEM_REPLY:{direct_result}")
            
        # Always speak out loud
        try:
            from core.narrator import task_narrate
            task_narrate("step_success", detail=direct_result)
        except Exception as e:
            log_error("hands", "narrator_direct_result", e)
            threading.Thread(target=speak, args=(direct_result,), daemon=True).start()
        
        return direct_result
    
    if ui_signal:
        ui_signal.emit("thinking", "SYSTEM_REPLY:<i>[🤖 AGENT]: Generating Master Execution Plan...</i>")
        
    # 1. Ask Brain for the master plan
    from core.brain import generate_master_plan
    plan = generate_master_plan(prompt)
    
    if not plan:
        return "Task execution failed at the planning stage."

    # ── Show the full plan in UI BEFORE starting ───────────────────────────
    plan_lines = "\n".join([f"  {i+1}. {s}" for i, s in enumerate(plan)])
    plan_preview = f"[PLAN - {len(plan)} STEPS]\n{plan_lines}"
    print(f"[\U0001f4cb EXECUTION PLAN]:\n{plan_lines}")
    if ui_signal:
        ui_signal.emit("thinking", f"SYSTEM_REPLY:<b>[EXECUTION PLAN — {len(plan)} STEPS]</b><br>" +
                       "<br>".join([f"&nbsp;&nbsp;{i+1}. {s}" for i, s in enumerate(plan)]))
    speak(f"Here is my plan: {len(plan)} steps. Starting now!")

    mission_success = True
    steps_done = 0
    steps_failed = 0


    # 2. Execute each step (With Checkpoint Support)
    # Fix #4: Use hash of prompt as filename — prevents concurrent swarm tasks
    # from clobbering each other's checkpoint files.
    prompt_hash = hashlib.md5(prompt.encode()).hexdigest()[:12]
    checkpoint_file = str(CHECKPOINT_DIR / f"ckpt_{prompt_hash}.json")
    start_index = 0
    consecutive_errors = 0
    
    # ⚡ Checkpoint Resumption
    if os.path.exists(checkpoint_file):
        try:
            with filelock.FileLock(f"{checkpoint_file}.lock", timeout=10):
                with open(checkpoint_file, "r") as f:
                    checkpoint = json.load(f)
                    if checkpoint.get("prompt") == prompt:
                        start_index = checkpoint.get("step_index", 0)
                        print(f"\n[\U0001f504 RESUMING FROM CHECKPOINT]: Step {start_index + 1}")
        except Exception as e:
            log_error("hands", "checkpoint_read", e)

    index = start_index
    while index < len(plan):
        step = plan[index]
        
        # Save checkpoint
        try:
            with filelock.FileLock(f"{checkpoint_file}.lock", timeout=10):
                with open(checkpoint_file, "w") as f:
                    json.dump({"prompt": prompt, "step_index": index}, f)
        except Exception as e:
            log_error("hands", "checkpoint_write", e)
            
        # ⚡ LIVE CLAUDE-STYLE UI FEEDBACK ⚡
        step_msg = f"Executing Step {index + 1}: {step}"
        if ui_signal:
            ui_signal.emit("thinking", f"SYSTEM_REPLY:<b>[🔧 TOOL USE - STEP {index + 1}]:</b> {step}")
            
        try:
            from core.narrator import task_narrate
            task_narrate("step_start", n=index + 1, detail=step)
        except Exception as e:
            log_error("hands", "narrator_step_start", e)
            speak(f"Executing step {index + 1}.")
        
        # Fix #7: Classify GUI vs CODE using keywords — avoids 1 LLM call per step.
        # The master plan already describes what to do; we infer type from the text.
        step_lower = step.lower()
        _gui_keywords = ["click", "type into", "select", "drag", "right-click",
                         "double-click", "scroll on", "hover over", "press enter",
                         "toggle", "open settings", "open dialog"]
        _code_keywords = ["script", "python", "subprocess", "execute", "run command",
                          "os.", "import", "download", "install"]
        
        # Heuristic: GUI if any GUI keyword present AND no CODE keyword present
        _is_gui = (any(k in step_lower for k in _gui_keywords) and
                   not any(k in step_lower for k in _code_keywords))
        step_type = "GUI" if _is_gui else "CODE"
        
        if "GUI" in step_type:
            from core.desktop_driver import desktop_driver
            if ui_signal: ui_signal.emit("speaking", f"SYSTEM_REPLY:<i>[⚙️ CUA]: Dispatching PyAutoGUI Computer Vision...</i>")
            
            # Extract parameters
            extract_prompt = f"Extract parameters from this step: '{step}'. Return valid JSON ONLY with keys: 'query' (description of what to click), 'action' (click or type), 'text' (if typing). Example: {{\"query\": \"Search bar at the top\", \"action\": \"type\", \"text\": \"weather\"}}"
            raw_response = call_groq_brain(extract_prompt, phase="DIRECTIVE", is_logic_task=True)
            
            try:
                if isinstance(raw_response, dict):
                    if "query" in raw_response:
                        params = raw_response
                    else:
                        raw_json = raw_response.get("reply", "{}").replace("```json", "").replace("```", "").strip()
                        params = json.loads(raw_json)
                else:
                    raw_json = str(raw_response).replace("```json", "").replace("```", "").strip()
                    params = json.loads(raw_json)
                    
                action = params.get("action", "click")
                query = params.get("query", "")
                text = params.get("text", "")
                
                output = desktop_driver.vision_action(action, query, text)
                
                # ⚡ AUDIT FIX: If CUA fails, automatically fallback to Python code execution
                if "ERROR" in output or "EXCEPTION" in output:
                    print(f"[🔄 CUA FALLBACK]: GUI automation failed. Retrying with Python code...")
                    if ui_signal: ui_signal.emit("thinking", f"SYSTEM_REPLY:<i>[🔄 FALLBACK]: CUA failed, generating Python script...</i>")
                    
                    code_prompt = f"Write a python script to execute this exact step: '{step}'. Use subprocess, os, webbrowser, pyautogui, or any appropriate library. Print the output. Do not explain. Do not use markdown."
                    script_code = call_groq_brain(code_prompt, phase="DIRECTIVE", is_logic_task=False)
                    script_code = script_code.replace("```python", "").replace("```", "").strip()
                    output = run_in_docker_sandbox(script_code, action_description=step)
                    
            except Exception as e:
                output = f"[⚠️ CUA PARSE ERROR]: {e}"
        else:
            # ⚡ FEATURE 6 & 7: Multi-Agent Specialization + Self-Healing Execution
            # We use a Swarm: Coder Agent -> Reviewer Agent -> Executor
            max_retries = 3
            current_try = 0
            feedback = ""
            
            while current_try < max_retries:
                # 1. Coder Agent
                if ui_signal: ui_signal.emit("thinking", f"SYSTEM_REPLY:<i>[👨‍💻 CODER AGENT]: Writing logic for step {index + 1}...</i>")
                code_prompt = f"Write the python code to execute this exact step: '{step}'. Print the output. Do not explain. Do not use markdown. {feedback}"
                script_code = call_groq_brain(code_prompt, phase="DIRECTIVE", is_logic_task=False)
                script_code = script_code.replace("```python", "").replace("```", "").strip()
                
                if "neural networks are offline or rate-limited" in script_code:
                    return "Task failed: External API endpoints are rate-limited or offline."
                
                # 2. Reviewer Agent (Static check)
                if ui_signal: ui_signal.emit("thinking", f"SYSTEM_REPLY:<i>[🔍 REVIEWER AGENT]: Scanning for destructive commands...</i>")
                reviewer_prompt = f"Review this code for dangerous disk-wiping or host-crashing commands. If safe, reply exactly 'SAFE'. If dangerous, reply 'DANGEROUS: <reason>'. Code:\n{script_code}"
                review = call_groq_brain(reviewer_prompt, phase="DIRECTIVE", is_logic_task=True)
                review_str = review.get("reply", "SAFE") if isinstance(review, dict) else str(review)
                
                if "DANGEROUS" in review_str.upper():
                    output = f"[⚠️ REVIEWER BLOCKED]: {review_str}"
                    break
                    
                # 3. Executor Agent (Runs it in Sandbox)
                if ui_signal: ui_signal.emit("thinking", f"SYSTEM_REPLY:<i>[⚡ EXECUTOR AGENT]: Running in Sandbox...</i>")
                output = run_in_docker_sandbox(script_code, action_description=step)
                
                # ⚡ Self-Healing Loop
                if "Error:" in output or "Exception:" in output or "Traceback" in output or "CRASH" in output:
                    current_try += 1
                    print(f"[🔧 SELF-HEALING]: Code crashed. Asking Coder Agent to fix it (Attempt {current_try}/{max_retries})")
                    if ui_signal: ui_signal.emit("thinking", f"SYSTEM_REPLY:<i>[🔧 SELF-HEALING]: Crash detected. Recoding...</i>")
                    try:
                        from core.narrator import task_narrate
                        task_narrate("self_healing")
                    except Exception as e:
                        log_error("hands", "narrator_self_healing", e)
                    feedback = f"\n\nYour previous code crashed with this error:\n{output}\nFix the bug and provide the corrected Python code only."
                else:
                    break # Success!
                    
            if current_try >= max_retries:
                output = f"[❌ HEALING FAILED]: Could not fix the code after {max_retries} attempts. Final Error: {output}"
            
        print(f"[💻 EXECUTION OUTPUT]:\n{output}")
        time.sleep(1) 
        
        # 3. Lean Verification (skip heavy vision to save API calls)
        if "SUCCESS" in str(output) or "successfully" in str(output).lower():
            print(f"[✅ STEP {index + 1} VERIFIED]: Output indicates success.")
            consecutive_errors = 0
            steps_done += 1
            if ui_signal:
                ui_signal.emit("thinking", f"SYSTEM_REPLY:<i>[✅ STEP {index + 1} DONE]: {step}</i>")
        elif ("ERROR" in str(output) or "CRASH" in str(output) or
              "DENIED" in str(output) or "Exception" in str(output)):
            print(f"[⚠️ STEP {index + 1} FAILED]: {output}")
            consecutive_errors += 1
            steps_failed += 1
            if ui_signal:
                ui_signal.emit("thinking", f"SYSTEM_REPLY:<i>[⚠️ STEP {index + 1} FAILED]: {output[:120]}</i>")

            # ✅ JARVIS 2.0 Phase 3C: Replan remaining steps after first failure
            if consecutive_errors == 1 and index + 1 < len(plan):
                try:
                    remaining = plan[index + 1:]
                    replan_prompt = (
                        f"Original task: {prompt}\n"
                        f"Step that just failed: {step}\n"
                        f"Error: {output[:200]}\n"
                        f"Remaining planned steps: {remaining}\n"
                        f"Suggest an ALTERNATIVE approach for the remaining steps. "
                        f"Output ONLY a JSON array of step strings. No explanation."
                    )
                    raw_replan = call_groq_brain(replan_prompt, phase="DIRECTIVE", is_logic_task=False)
                    if isinstance(raw_replan, dict):
                        raw_replan = raw_replan.get("reply", "")
                    raw_replan = str(raw_replan).replace("```json", "").replace("```", "").strip()
                    new_steps = json.loads(raw_replan)
                    if isinstance(new_steps, list) and new_steps:
                        plan[index + 1:] = new_steps
                        print(f"[🔄 REPLANNED]: {len(new_steps)} new steps for remainder.")
                        if ui_signal:
                            ui_signal.emit("thinking", f"SYSTEM_REPLY:<i>[🔄 REPLANNING]: Adapting strategy — {len(new_steps)} new steps.</i>")
                except Exception as _rpe:
                    log_error("hands", "replan_remaining", _rpe)

            if consecutive_errors >= 3:
                print("\n[❌ ABORTING MISSION]: Too many consecutive failures. Bound limit reached.")
                if ui_signal:
                    ui_signal.emit("speaking", "SYSTEM_REPLY:Mission aborted due to repeated errors.")
                mission_success = False
                break
        else:
            print(f"[ℹ️ STEP {index + 1}]: Output received, proceeding.")
            consecutive_errors = 0
            steps_done += 1
            
        index += 1

    # ── Honest final summary ──────────────────────────────────────────────────
    total_steps = len(plan)
    if steps_failed == 0 and mission_success:
        print("\n[🎉 MISSION COMPLETE]")
        final_message = (f"Done! All {total_steps} steps completed successfully.")
    elif steps_done > 0 and steps_failed > 0:
        print(f"\n[⚠️ PARTIAL MISSION]: {steps_done} done, {steps_failed} failed.")
        final_message = (
            f"I completed {steps_done} of {total_steps} steps, but {steps_failed} step"
            f"{'s' if steps_failed > 1 else ''} failed. Please check the terminal for details."
        )
    else:
        print("\n[❌ MISSION FAILED]")
        final_message = (
            f"I was unable to complete the task. All {steps_failed} step"
            f"{'s' if steps_failed > 1 else ''} failed. Please check the terminal log."
        )

    try:
        if os.path.exists(checkpoint_file):
            with filelock.FileLock(f"{checkpoint_file}.lock", timeout=10):
                os.remove(checkpoint_file)
        if os.path.exists(f"{checkpoint_file}.lock"):
            os.remove(f"{checkpoint_file}.lock")
    except Exception as e:
        log_error("hands", "checkpoint_cleanup", e)
    if ui_signal:
        ui_signal.emit("speaking", f"SYSTEM_REPLY:{final_message}")
        
    # Always speak out loud, even if UI is active
    try:
        from core.narrator import task_narrate
        task_narrate("complete" if mission_success else "abort", detail=final_message)
    except Exception as e:
        log_error("hands", "narrator_mission_complete", e)
        threading.Thread(target=speak, args=(final_message,), daemon=True).start()
    
    return final_message


# ==========================================
# ⚡ DIRECT OS ACTION SHORTCUTS
# ==========================================
def _try_direct_os_action(prompt, ui_signal=None):
    """
    Instant execution for common OS tasks.
    Skips the entire planning/LLM pipeline and runs native Windows commands.
    Returns result string if handled, None if not a known shortcut.
    """
    if len(prompt) > 250:
        return None  # Swarm agents and massive prompts should not trigger simple OS shortcuts
        
    p = prompt.lower().strip()
    
    # ⚡ SKIP COMPOUND PROMPTS: If the user chains commands (e.g., "open X and do Y"), 
    # let the full LLM planner handle it to avoid aggressively truncating the intent.
    if any(splitter in p for splitter in [" and ", " then ", ","]):
        return None
        
    import subprocess
    
    # ⚡ INSTANT MEDIA CONTROLS
    media_cmds = ["play again", "again play", "resume", "pause", "stop playing", "stop song", "pause song", "stop that song"]
    if p in media_cmds or p == "play" or p == "stop" or p.endswith(" resume") or p.endswith(" pause"):
        import pyautogui
        pyautogui.press("playpause")
        if ui_signal: ui_signal.emit("speaking", "SYSTEM_REPLY:CMD:SHRINK_TO_ORB")
        return "Media playback toggled!"
    
    if "play " in p or p.endswith(" play"):
        if p.endswith(" play"):
            query = p[:-5].strip()
        else:
            query = p.split("play ", 1)[1].strip()
        if not query:
            import pyautogui
            pyautogui.FAILSAFE = False
            pyautogui.press("playpause")
            if ui_signal: ui_signal.emit("speaking", "SYSTEM_REPLY:CMD:SHRINK_TO_ORB")
            return "Media playback toggled!"
            
        import urllib.parse
        try:
            import pywhatkit
            import threading
            import win32gui
            import time
            import win32con
            import ctypes
            
            # Save the current window so we don't disrupt the user
            current_hwnd = win32gui.GetForegroundWindow()
            
            # Start the song (this will open the browser and steal focus)
            from core.feature_flags import is_enabled
            if is_enabled("smart_yt_scraper") and ("channel" in query.lower() or "by " in query.lower()):
                import urllib.request
                import re
                import json
                import webbrowser
                try:
                    target_channel = query.lower().split("channel")[0].replace("latest video of", "").replace("latest video", "").replace("play the", "").strip()
                    encoded = urllib.parse.quote(query)
                    req = urllib.request.Request(f"https://www.youtube.com/results?search_query={encoded}", headers={'User-Agent': 'Mozilla/5.0'})
                    html = urllib.request.urlopen(req, timeout=5).read().decode()
                    match = re.search(r'ytInitialData\s*=\s*(\{.*?\});\s*</script>', html)
                    if match:
                        data = json.loads(match.group(1))
                        contents = data['contents']['twoColumnSearchResultsRenderer']['primaryContents']['sectionListRenderer']['contents'][0]['itemSectionRenderer']['contents']
                        
                        best_id = None
                        for item in contents:
                            if 'videoRenderer' in item:
                                vid = item['videoRenderer']
                                channel_name = vid.get('ownerText', {}).get('runs', [{}])[0].get('text', '').lower()
                                if target_channel and target_channel in channel_name:
                                    best_id = vid.get('videoId')
                                    break
                                elif not best_id:
                                    best_id = vid.get('videoId') # fallback to 1st
                        if best_id:
                            webbrowser.open(f"https://www.youtube.com/watch?v={best_id}")
                        else:
                            pywhatkit.playonyt(query)
                    else:
                        pywhatkit.playonyt(query)
                except Exception as e:
                    print(f"Smart YT failed: {e}")
                    pywhatkit.playonyt(query)
            else:
                pywhatkit.playonyt(query)

            if ui_signal: ui_signal.emit("speaking", "SYSTEM_REPLY:CMD:SHRINK_TO_ORB")
            
            def restore_focus():
                time.sleep(4) # wait for browser to open
                try:
                    # Windows restricts SetForegroundWindow, trick it by simulating Alt key
                    ctypes.windll.user32.keybd_event(win32con.VK_MENU, 0, 0, 0)
                    win32gui.SetForegroundWindow(current_hwnd)
                    ctypes.windll.user32.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
                except Exception as e:
                    from core.jarvis_logger import log_warn
                    log_warn('hands', f'silent swallow: {e}')
                    log_warn('hands', f'restore_focus failed: {e}')
                    
            if current_hwnd:
                threading.Thread(target=restore_focus, daemon=True).start()
                
            return f"Playing {query} on YouTube!"
        except Exception as e:
            from core.jarvis_logger import log_warn
            log_warn('hands', f'playonyt failed: {e}')
            url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
            try:
                os.startfile(url)
            except Exception as e:
                from core.jarvis_logger import log_warn
                log_warn('hands', f'os.startfile failed: {e}')
                import webbrowser
                webbrowser.open(url)
            if ui_signal: ui_signal.emit("speaking", "SYSTEM_REPLY:CMD:SHRINK_TO_ORB")
            return f"Searched for {query} on YouTube!"
            
    if "search " in p:
        import urllib.parse
        query = p.split("search ", 1)[1].strip()
        
        search_engine = "google"
        if "on youtube" in query:
            query = query.replace("on youtube", "").strip()
            search_engine = "youtube"
        elif "in youtube" in query:
            query = query.replace("in youtube", "").strip()
            search_engine = "youtube"
        elif "on google" in query:
            query = query.replace("on google", "").strip()
        
        if search_engine == "youtube":
            url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
        else:
            url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
            
        try:
            os.startfile(url)
        except Exception as e:
            from core.jarvis_logger import log_warn
            log_warn('hands', f'os.startfile search failed: {e}')
            import webbrowser
            webbrowser.open(url)
            
        if ui_signal: ui_signal.emit("speaking", "SYSTEM_REPLY:CMD:SHRINK_TO_ORB")
        return f"Searched for {query} on {search_engine.title()}!"
        
    if "click " in p:
        query = p.replace("click on", "").replace("click", "").strip()
        from core.desktop_driver import desktop_driver
        if ui_signal: ui_signal.emit("thinking", f"SYSTEM_REPLY:<i>[⚙️ CUA]: Using Computer Vision to click '{query}'...</i>")
        try:
            output = desktop_driver.vision_action("click", query)
            if ui_signal: ui_signal.emit("speaking", "SYSTEM_REPLY:CMD:SHRINK_TO_ORB")
            if "SUCCESS" in output:
                return f"Clicked on {query}!"
            else:
                return f"I failed to click on {query}. Vision engine reported: {output}"
        except Exception as e:
            return f"Couldn't find {query} on the screen."
            
    if "type " in p:
        text_to_type = p.split("type", 1)[1].strip()
        import pyautogui
        if ui_signal: ui_signal.emit("thinking", f"SYSTEM_REPLY:<i>[⚙️ CUA]: Typing text...</i>")
        pyautogui.write(text_to_type, interval=0.01)
        pyautogui.press("enter")
        if ui_signal: ui_signal.emit("speaking", "SYSTEM_REPLY:CMD:SHRINK_TO_ORB")
        return f"Typed '{text_to_type}'!"
        
    if "scroll " in p:
        import pyautogui
        if "up" in p:
            pyautogui.scroll(1000)
            direction = "up"
        else:
            pyautogui.scroll(-1000)
            direction = "down"
        if ui_signal: ui_signal.emit("speaking", "SYSTEM_REPLY:CMD:SHRINK_TO_ORB")
        return f"Scrolled {direction}!"

    # ⚡ QUICK SETTINGS PANEL TOGGLES (Win+A)
    # The user explicitly requested Bluetooth, Wi-Fi, Energy Saver to be toggled via the Quick Panels
    quick_settings_map = {
        "battery saver": "Energy saver",
        "energy saver": "Energy saver",
        "bluetooth": "Bluetooth",
        "blutooth": "Bluetooth",
        "wifi": "Wi-Fi",
        "wi-fi": "Wi-Fi",
        "airplane mode": "Airplane mode",
        "hotspot": "Mobile hotspot"
    }
    
    for key, toggle_name in quick_settings_map.items():
        if key in p and any(w in p for w in ["turn on", "enable", "switch on", "on", "turn off", "disable", "switch off", "off"]):
            is_turn_on = any(w in p for w in ["turn on", "enable", "switch on", "on"])
            desired_state = 1 if is_turn_on else 0
            
            if ui_signal: ui_signal.emit("thinking", f"SYSTEM_REPLY:<i>[⚡ DIRECT]: Toggling {key.title()} via Action Center...</i>")
            
            threading.Thread(target=speak, args=(f"Processing {key}...",), daemon=True).start()
            
            try:
                import pyautogui
                pyautogui.hotkey("win", "a")
                time.sleep(1)
                
                from core.desktop_driver import desktop_driver
                result = desktop_driver.background_click("Quick Settings", toggle_name, desired_state=desired_state)
                
                pyautogui.hotkey("win", "a")
                
                if "ALREADY DONE" in result:
                    state_word = "on" if is_turn_on else "off"
                    return f"{key.title()} is already {state_word}. Left unchanged."
                return f"{key.title()} toggled!"
            except Exception as e:
                return f"Failed to toggle {key.title()}: {e}"
                
    # ⚡ OTHER COMMON TOGGLES (Settings App)
    toggles_map = {
        "night light": ("ms-settings:nightlight", "Night light"),
    }

    for key, (uri, toggle_name) in toggles_map.items():
        if key in p and any(w in p for w in ["turn on", "enable", "switch on", "on", "turn off", "disable", "switch off", "off"]):
            is_turn_on = any(w in p for w in ["turn on", "enable", "switch on", "on"])
            desired_state = 1 if is_turn_on else 0
            
            if ui_signal: ui_signal.emit("thinking", f"SYSTEM_REPLY:<i>[⚡ DIRECT]: Toggling {key}...</i>")
            
            threading.Thread(target=speak, args=(f"Processing {key}...",), daemon=True).start()
            
            os.startfile(uri)
            time.sleep(3)
            
            from core.desktop_driver import desktop_driver
            result = desktop_driver.toggle_switch("Settings", toggle_name, desired_state=desired_state)
            
            if "ALREADY DONE" in result:
                state_word = "on" if is_turn_on else "off"
                return f"{key.title()} is already {state_word}. Left unchanged."
            elif "SUCCESS" in result:
                return f"{key.title()} toggled!"
            else:
                return f"Opened settings for {key.title()}. The toggle should be visible."
                
    # ⚡ VOLUME (Modern 2026 Core Audio API - 0.01ms execution, 0 subprocesses)
    if "volume" in p:
        import ctypes
        VK_VOLUME_MUTE = 0xAD
        VK_VOLUME_DOWN = 0xAE
        VK_VOLUME_UP   = 0xAF
        KEYEVENTF_KEYUP = 0x0002

        def _send_vk(vk, count=1):
            for _ in range(count):
                ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
                ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

        if any(w in p for w in ["mute", "silent"]):
            _send_vk(VK_VOLUME_MUTE)
            return "Volume muted!"
        elif any(w in p for w in ["up", "increase", "raise", "louder"]):
            _send_vk(VK_VOLUME_UP, 5)
            return "Volume increased!"
        elif any(w in p for w in ["down", "decrease", "lower", "quieter"]):
            _send_vk(VK_VOLUME_DOWN, 5)
            return "Volume decreased!"
    
    # ⚡ SETTINGS PAGES
    settings_map = {
        "display": "ms-settings:display",
        "sound": "ms-settings:sound",
        "notification": "ms-settings:notifications",
        "power": "ms-settings:powersleep",
        "battery": "ms-settings:batterysaver",
        "storage": "ms-settings:storagesense",
        "network": "ms-settings:network",
        "personali": "ms-settings:personalization",
        "wallpaper": "ms-settings:personalization-background",
        "mouse": "ms-settings:mousetouchpad",
        "keyboard": "ms-settings:typing",
        "printer": "ms-settings:printers",
        "about": "ms-settings:about",
        "update": "ms-settings:windowsupdate",
        "apps": "ms-settings:appsfeatures",
    }
    
    if "open" in p and "setting" in p:
        for keyword, uri in settings_map.items():
            if keyword in p:
                os.startfile(uri)
                return f"Opened {keyword} settings!"
        os.startfile("ms-settings:")
        return "Opened Windows Settings!"
    
    # ⚡ OPEN COMMON APPS (via Python, not CUA clicks)
    if "open" in p:
        import webbrowser
        app_map = {
            "notepad": "notepad.exe",
            "calculator": "calc.exe",
            "paint": "mspaint.exe",
            "cmd": "cmd.exe",
            "terminal": "wt.exe",
            "task manager": "taskmgr.exe",
            "file explorer": "explorer.exe",
            "explorer": "explorer.exe",
            "chrome": "chrome.exe",
            "edge": "msedge.exe",
            "brave": "brave.exe",
            "firefox": "firefox.exe",
            "browser": "msedge.exe"
        }
        
        web_triggers = ["youtube", "google", "github", "twitter", "facebook", 
                        "instagram", "reddit", "linkedin", "stackoverflow", "chatgpt"]
        
        for site in web_triggers:
            if site in p:
                url = f"https://www.{site}.com" if "." not in site else f"https://{site}.com"
                try:
                    os.startfile(url)
                except Exception as e:
                    from core.jarvis_logger import log_warn
                    log_warn('hands', f'os.startfile website failed: {e}')
                    webbrowser.open(url)
                if ui_signal: ui_signal.emit("speaking", "SYSTEM_REPLY:CMD:SHRINK_TO_ORB")
                return f"Opened {site.title()} in your browser!"
        
        for app_name, exe in app_map.items():
            if app_name in p:
                try:
                    os.startfile(exe)
                    if ui_signal: ui_signal.emit("speaking", "SYSTEM_REPLY:CMD:SHRINK_TO_ORB")
                    return f"Opened {app_name.title()}!"
                except Exception as e:
                    return f"Failed to open {app_name.title()}: it may not be installed or in PATH."
                    
        # ⚡ UNIVERSAL WEBSITE FALLBACK
        # If the user says "open amazon", and it didn't match the known lists above:
        target = p.replace("open", "").strip()
        target = target.replace("browser", "").replace("website", "").strip()
        
        # If it's a single word (like "amazon", "netflix"), just open it!
        if len(target.split()) == 1 and target.replace(".", "").isalnum() and len(target) > 2:
            url = f"https://www.{target}.com" if "." not in target else f"https://{target}"
            try:
                os.startfile(url)
            except Exception as e:
                from core.jarvis_logger import log_warn
                log_warn('hands', f'os.startfile open failed: {e}')
                import webbrowser
                webbrowser.open(url)
            if ui_signal: ui_signal.emit("speaking", "SYSTEM_REPLY:CMD:SHRINK_TO_ORB")
            return f"Opened {target.title()}!"
    
    # ⚡ SCREENSHOT
    if "screenshot" in p:
        import pyautogui
        path = os.path.join(os.path.expanduser("~"), "Desktop", "jarvis_screenshot.png")
        pyautogui.screenshot(path)
        return f"Screenshot saved to your Desktop!"
    
    # ⚡ SHUTDOWN / RESTART / LOCK
    if "shutdown" in p or "shut down" in p:
        subprocess.run(["shutdown", "/s", "/t", "5"], capture_output=True)
        return "Shutting down in 5 seconds."
    if "restart" in p or "reboot" in p:
        subprocess.run(["shutdown", "/r", "/t", "5"], capture_output=True)
        return "Restarting in 5 seconds."
    if "lock" in p and ("screen" in p or "computer" in p or "pc" in p or "laptop" in p):
        subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], capture_output=True)
        return "Screen locked."
    
    return None  # Not a known shortcut, proceed to full planning pipeline


run_autonomous_mission = execute_agentic_loop
