"""
self_evolution.py  —  JARVIS Self-Evolving Codebase Engine v1.0

JARVIS monitors his own error log, detects recurring crash patterns, uses the
LLM to generate targeted code patches, validates them in a subprocess sandbox,
backs up the original file, and applies the fix — then reports what he changed.

SAFETY RULES:
  1. Original file always backed up as filename.py.bak before any patch
  2. Patch goes through syntax check (ast.parse) before application
  3. Patch goes through subprocess sandbox test before application
  4. Maximum 1 patch per file per 30 minutes (cooldown)
  5. self_evolution.py itself is permanently excluded from patching
  6. Dry-run mode by default (proactive_config.json: "auto_apply": false)
  7. User is always notified of any patch via ProactiveEvent

FLOW:
  ErrorLogBuffer → ErrorPatternAnalyzer → PatchGenerator →
  PatchSandbox → PatchApplicator → ProactiveEvent
"""

import os
import sys
import ast
import re
import json
import time
import queue
import shutil
import sqlite3
import threading
_patch_lock = threading.Lock()
import hashlib
import subprocess
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from io import StringIO

from core.monitors.base_monitor import BaseMonitor
from core.jarvis_logger import log_error, log_warn

# ── Paths ───────────────────────────────────────────────────────────────────────
JARVIS_ROOT    = str(Path(__file__).parent.parent)
from core.config import DB_PATH as EVOLUTION_DB   # Fix: use centralized DB path
ERROR_LOG_PATH = str(Path(__file__).parent.parent / "logs" / "jarvis_errors.log")
PATCH_LOG_DIR  = str(Path(__file__).parent.parent / "logs" / "patch_logs")
EXCLUDED_FILES = {
    "self_evolution.py",
}


# ── DB Setup ──────────────────────────────────────────────────────────────────
def _init_evolution_tables():
    conn = sqlite3.connect(EVOLUTION_DB, timeout=10.0)
    cur  = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS error_patterns (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            error_hash      TEXT UNIQUE NOT NULL,
            error_type      TEXT,
            error_message   TEXT,
            file_path       TEXT,
            line_number     INTEGER,
            occurrence_count INTEGER DEFAULT 1,
            first_seen      TEXT DEFAULT (datetime('now','localtime')),
            last_seen       TEXT DEFAULT (datetime('now','localtime')),
            patched         INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS evolution_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT DEFAULT (datetime('now','localtime')),
            error_hash      TEXT,
            target_file     TEXT NOT NULL,
            patch_diff      TEXT,
            sandbox_result  TEXT,
            applied         INTEGER DEFAULT 0,
            auto_applied    INTEGER DEFAULT 0,
            reverted        INTEGER DEFAULT 0,
            patch_log_path  TEXT
        );
    """)
    conn.commit()
    conn.close()
    os.makedirs(PATCH_LOG_DIR, exist_ok=True)



# ── SE-3: AST Function Extractor ──────────────────────────────────────────────
class FunctionExtractor(ast.NodeVisitor):
    def __init__(self, target_line):
        self.target_line = target_line
        self.extracted_code = None
        self.node_type = None
        self.node_name = None

    def visit_FunctionDef(self, node):
        if hasattr(node, 'lineno') and hasattr(node, 'end_lineno'):
            if node.lineno <= self.target_line <= node.end_lineno:
                self.extracted_code = (node.lineno, node.end_lineno)
                self.node_type = "FunctionDef"
                self.node_name = node.name
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        if hasattr(node, 'lineno') and hasattr(node, 'end_lineno'):
            if node.lineno <= self.target_line <= node.end_lineno:
                self.extracted_code = (node.lineno, node.end_lineno)
                self.node_type = "AsyncFunctionDef"
                self.node_name = node.name
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        if hasattr(node, 'lineno') and hasattr(node, 'end_lineno'):
            if node.lineno <= self.target_line <= node.end_lineno:
                if not self.extracted_code:
                    self.extracted_code = (node.lineno, node.end_lineno)
                    self.node_type = "ClassDef"
                    self.node_name = node.name
        self.generic_visit(node)

# ── Error Log Buffer ──────────────────────────────────────────────────────────
class ErrorLogBuffer:
    """
    Captures stderr from the JARVIS process into a rolling in-memory buffer
    AND writes to a persistent log file. Installed by calling install().
    """

    MAX_LINES = 500

    def __init__(self):
        self._buffer = []
        self._lock   = threading.Lock()
        self._original_stderr = sys.stderr

    def install(self):
        """Redirect sys.stderr through this buffer and start log file watcher."""
        sys.stderr = self
        print("[EVOLUTION]: Error buffer installed on stderr.")
        # Also tail the jarvis_errors.log file for errors from jarvis_logger.py
        self._tail_watcher = _LogFileTailWatcher(ERROR_LOG_PATH, self)
        self._tail_watcher.start()
        print("[EVOLUTION]: Log file tail watcher started.")

    def write(self, text: str):
        self._original_stderr.write(text)
        if text.strip():
            with self._lock:
                self._buffer.append((time.time(), text.rstrip()))
                if len(self._buffer) > self.MAX_LINES:
                    self._buffer.pop(0)
            # Persist to log file
            try:
                with open(ERROR_LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
                    f.write(f"[{datetime.now().isoformat()}] {text.rstrip()}\n")
            except Exception as e:
                from core.jarvis_logger import log_warn
                log_warn('self_evolution', f'silent swallow: {e}')

    def flush(self):
        self._original_stderr.flush()

    def get_recent_errors(self, n: int = 50) -> list:
        with self._lock:
            return list(self._buffer[-n:])


# ── Log File Tail Watcher ────────────────────────────────────────────────────
class _LogFileTailWatcher:
    """
    Polls jarvis_errors.log every 5 seconds and feeds new lines into
    the ErrorLogBuffer. This catches errors from jarvis_logger.py that
    write directly to the file rather than going through sys.stderr.
    """
    def __init__(self, log_path: str, buffer: "ErrorLogBuffer"):
        self._log_path = log_path
        self._buffer   = buffer
        self._offset   = 0
        self._stop_event = threading.Event()
        self._thread   = threading.Thread(
            target=self._run, daemon=True, name="JARVIS-LogTail"
        )

    def start(self):
        # Start reading from END of existing file (don't replay old errors)
        try:
            if os.path.exists(self._log_path):
                self._offset = os.path.getsize(self._log_path)
        except Exception as e:
            from core.jarvis_logger import log_warn
            log_warn('self_evolution', f'silent swallow: {e}')
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _run(self):
        while not self._stop_event.is_set():
            if self._stop_event.wait(5.0):
                break
            try:
                if not os.path.exists(self._log_path):
                    continue
                with open(self._log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(self._offset)
                    new_text = f.read()
                    self._offset = f.tell()
                if new_text.strip():
                    for line in new_text.splitlines():
                        if line.strip():
                            with self._buffer._lock:
                                self._buffer._buffer.append((time.time(), line))
                                if len(self._buffer._buffer) > self._buffer.MAX_LINES:
                                    self._buffer._buffer.pop(0)
            except Exception as e:
                from core.jarvis_logger import log_warn
                log_warn('self_evolution', f'silent swallow: {e}')


# ── Error Pattern Analyzer ────────────────────────────────────────────────────
class ErrorPatternAnalyzer:
    """
    Parses Python tracebacks from the error buffer and groups them by
    (error_type, file, line). Alerts when the same pattern occurs >= 3 times.
    """

    # Fix #3: Match BOTH standard Python tracebacks AND jarvis_logger format:
    #   Python:      File "path", line N\n  ExcType: message
    #   jarvis_logger: [ERROR | module]: action → ExcType: message
    TRACEBACK_PATTERN = re.compile(
        r'(?:'
        r'File "(.+?)", line (\d+).*?\n.*?(\w+Error|\w+Exception|SyntaxError|'
        r'AttributeError|ImportError|TypeError|ValueError|RuntimeError|ZeroDivisionError): (.+)'
        r'|'
        r'\[ERROR \| ([\w./]+)\]: ([\w_]+) → '
        r'(\w+Error|\w+Exception|TypeError|ValueError|RuntimeError|AttributeError|ZeroDivisionError): (.+)'
        r'|'
        r'\[ERROR\] \[([\w./]+)\] \[ACTION: ([\w_]+)\] '
        r'(\w+Error|\w+Exception|TypeError|ValueError|RuntimeError|AttributeError|ZeroDivisionError): (.+)'
        r')',
        re.DOTALL
    )

    def __init__(self, trigger_count: int = 3):
        self.trigger_count = trigger_count
        self._cooldowns: dict = {}  # file_path -> last_patch_time

    def analyze(self, error_lines: list) -> list:
        """
        Parse error lines for traceback patterns.
        Returns list of error_info dicts that have hit the trigger threshold.
        """
        text = "\n".join(line for _, line in error_lines)
        matches = self.TRACEBACK_PATTERN.findall(text)

        new_alerts = []
        for m in matches:
            if m[0]:
                file_path, line_no, err_type, err_msg = m[0], m[1], m[2], m[3]
            elif m[4]:
                file_path, line_no, err_type, err_msg = m[4], "1", m[6], m[7]
            else:
                file_path, line_no, err_type, err_msg = m[8], "1", m[10], m[11]

            # Resolve module name to full path if not absolute
            if not os.path.isabs(file_path):
                candidates = [
                    os.path.join(JARVIS_ROOT, "core", f"{file_path}.py"),
                    os.path.join(JARVIS_ROOT, f"{file_path}.py"),
                    os.path.join(JARVIS_ROOT, file_path),
                ]
                matched = False
                for c in candidates:
                    if os.path.exists(c):
                        file_path = c
                        matched = True
                        break
                if not matched:
                    file_path = candidates[0]

            # Skip files outside JARVIS root
            if JARVIS_ROOT.replace("\\", "/") not in file_path.replace("\\", "/"):
                continue
            # Skip excluded files
            if any(excl in file_path for excl in EXCLUDED_FILES):
                continue

            error_hash = hashlib.md5(
                f"{err_type}:{file_path}:{line_no}".encode()
            ).hexdigest()[:12]

            try:
                conn = sqlite3.connect(EVOLUTION_DB, timeout=10.0)
                cur  = conn.cursor()
                cur.execute("""
                    INSERT INTO error_patterns
                        (error_hash, error_type, error_message, file_path, line_number)
                    VALUES (?,?,?,?,?)
                    ON CONFLICT(error_hash) DO UPDATE SET
                        occurrence_count = occurrence_count + 1,
                        last_seen = datetime('now','localtime')
                """, (error_hash, err_type, err_msg[:500], file_path, int(line_no)))
                cur.execute(
                    "SELECT occurrence_count, patched FROM error_patterns WHERE error_hash=?",
                    (error_hash,)
                )
                row = cur.fetchone()
                conn.commit()
                conn.close()

                if row:
                    count, patched = row
                    in_cooldown = (
                        file_path in self._cooldowns and
                        time.time() - self._cooldowns[file_path] < 1800
                    )
                    if count >= self.trigger_count and not patched and not in_cooldown:
                        new_alerts.append({
                            "error_hash": error_hash,
                            "file_path":  file_path,
                            "line_no":    int(line_no),
                            "err_type":   err_type,
                            "err_msg":    err_msg,
                            "count":      count,
                        })
            except Exception as e:
                log_error("self_evolution", "pattern_db_upsert", e)

        # Fix #3: Also extract jarvis_logger-format errors from the text
        # Format: [ERROR | module/path]: action → ExcType: message
        _logger_pattern = re.compile(
            r'\[ERROR \| ([\w./\\:]+)\]: ([\w_. ]+) → (\w+(?:Error|Exception)): (.+)'
        )
        for m in _logger_pattern.finditer(text):
            module_path, action, err_type, err_msg = m.group(1), m.group(2), m.group(3), m.group(4)
            # Map module name to a best-guess file path
            file_path = os.path.join(JARVIS_ROOT, "core", f"{module_path.split('/')[-1]}.py")
            if not os.path.exists(file_path):
                continue
            line_no = 0
            error_hash = hashlib.md5(
                f"{err_type}:{file_path}:{action}".encode()
            ).hexdigest()[:12]
            try:
                conn = sqlite3.connect(EVOLUTION_DB, timeout=10.0)
                cur  = conn.cursor()
                cur.execute("""
                    INSERT INTO error_patterns
                        (error_hash, error_type, error_message, file_path, line_number)
                    VALUES (?,?,?,?,?)
                    ON CONFLICT(error_hash) DO UPDATE SET
                        occurrence_count = occurrence_count + 1,
                        last_seen = datetime('now','localtime')
                """, (error_hash, err_type, err_msg[:500], file_path, line_no))
                cur.execute(
                    "SELECT occurrence_count, patched FROM error_patterns WHERE error_hash=?",
                    (error_hash,)
                )
                row = cur.fetchone()
                conn.commit()
                conn.close()
                if row:
                    count, patched = row
                    in_cooldown = (
                        file_path in self._cooldowns and
                        time.time() - self._cooldowns[file_path] < 1800
                    )
                    if count >= self.trigger_count and not patched and not in_cooldown:
                        new_alerts.append({
                            "error_hash": error_hash,
                            "file_path":  file_path,
                            "line_no":    line_no,
                            "err_type":   err_type,
                            "err_msg":    err_msg,
                            "count":      count,
                        })
            except Exception as e:
                log_error("self_evolution", "logger_pattern_db", e)

        return new_alerts


# ── Patch Generator ───────────────────────────────────────────────────────────
class PatchGenerator:
    """
    Uses the LLM to generate a targeted code patch given an error context.
    Returns the patched file content as a string.
    """

    CONTEXT_LINES = 30  # Lines around error to include in prompt

    def _get_code_context(self, file_path: str, line_no: int) -> tuple:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                code = f.read()
            tree = ast.parse(code)
            extractor = FunctionExtractor(line_no)
            extractor.visit(tree)
            
            lines = code.splitlines()
            if extractor.extracted_code:
                start_line, end_line = extractor.extracted_code
                start = max(0, start_line - 2)
                end = min(len(lines), end_line + 2)
            else:
                start = max(0, line_no - self.CONTEXT_LINES)
                end = min(len(lines), line_no + self.CONTEXT_LINES)
                
            numbered = [f"{i+1}: {l}" for i, l in enumerate(lines[start:end], start)]
            return "\n".join(numbered), start, end
        except Exception:
            return "", 0, 0

    def generate(self, error_info: dict) -> tuple:
        """
        Generate a patch for the given error using surgical Git-Diff replacement.
        Returns: (patched_full_code: str | None, patch_diff: str)
        """
        file_path = error_info["file_path"]
        line_no   = error_info["line_no"]
        err_type  = error_info["err_type"]
        err_msg   = error_info["err_msg"]

        context, start_idx, end_idx = self._get_code_context(file_path, line_no)
        if not context:
            return None, "Could not read source file"

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                full_code = f.read()
        except Exception:
            return None, "Could not read full source file"

        prompt = f"""
You are JARVIS's self-repair module. Analyze this Python error and produce a minimal fix.

ERROR TYPE: {err_type}
ERROR MESSAGE: {err_msg}
FILE: {os.path.basename(file_path)}
LINE: {line_no}

CODE CONTEXT (lines around error):
{context}

TASK:
1. Identify the exact cause of the {err_type} on line {line_no}
2. Generate the MINIMAL code change to fix it.
3. Return ONLY the exact, rewritten code block to replace the CONTEXT provided above (from line {start_idx+1} to {end_idx}). DO NOT output the entire file. Just the exact replacement block.
4. The fix must be safe: no logic changes, no feature removal, only bug fixes.
5. Do NOT include line numbers in your output. Ensure indentation perfectly matches the original context.

Return ONLY the fixed Python code block. No explanations, no markdown fences.
"""

        try:
            from core.brain import call_groq_brain
            result = call_groq_brain(prompt, phase="LOGIC", is_logic_task=False)
            if isinstance(result, dict):
                result = result.get("reply", "")

            # Strip any accidental markdown fences
            result = re.sub(r'^```python\s*', '', result, flags=re.IGNORECASE)
            result = re.sub(r'^```\s*',       '', result)
            result = re.sub(r'\s*```$',       '', result).strip()
            
            # Remove any line numbers the LLM might have outputted
            cleaned_lines = []
            for line in result.splitlines():
                line = re.sub(r'^\d+:\s', '', line)
                cleaned_lines.append(line)
            new_block = "\n".join(cleaned_lines)

            if len(new_block) < 10:
                return None, "LLM returned insufficient patch content"

            # SE-2: Apply surgical patch
            old_lines = full_code.splitlines()
            patched_full_code = "\n".join(old_lines[:start_idx] + cleaned_lines + old_lines[end_idx:])

            # Generate a simple diff summary
            changed = len(cleaned_lines)
            patch_diff = (
                f"Surgically patched lines {start_idx+1}-{end_idx} in {os.path.basename(file_path)} "
                f"to fix {err_type}: {err_msg[:60]}"
            )
            return patched_full_code, patch_diff

        except Exception as e:
            return None, f"LLM patch generation failed: {e}"


# ── Patch Sandbox ─────────────────────────────────────────────────────────────
class PatchSandbox:
    """
    Validates a patched file by:
    1. ast.parse() syntax check
    2. subprocess import test (import the module in isolation)
    Returns: (passed: bool, reason: str)
    """

    def validate(self, patched_code: str, file_path: str) -> tuple:
        # Step 1: In-process syntax check (fast, first gate)
        try:
            tree = ast.parse(patched_code)
        except SyntaxError as e:
            return False, f"Syntax error in patch: {e}"

        # ✅ TIER-1 GAP 5: AST Security Audit (Harden Sandbox)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # Block builtins like eval(), exec(), open()
                if isinstance(node.func, ast.Name):
                    if node.func.id in {"eval", "exec", "globals", "locals", "open"}:
                        return False, f"Security violation: unsafe built-in call '{node.func.id}()'"
                # Block module calls like subprocess.run(), os.remove()
                elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                    module_func = f"{node.func.value.id}.{node.func.attr}"
                    dangerous = {
                        "subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_output",
                        "os.system", "os.remove", "os.unlink", "os.rmdir", "os.removedirs", "os.rename",
                        "shutil.rmtree", "sys.exit"
                    }
                    if module_func in dangerous:
                        return False, f"Security violation: dangerous call '{module_func}'"

        # Step 2: Write to temp file for out-of-process validation
        temp_path = file_path + ".patch_test.py"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(patched_code)

            # ✅ SE-1: True bytecode compilation test (py_compile catches more than ast.parse)
            # py_compile.compile() builds actual .pyc bytecode — catches IndentationError,
            # encoding issues, and constant-folding errors that ast.parse silently misses.
            result = subprocess.run(
                [sys.executable, "-c",
                 f"import py_compile, sys; py_compile.compile(r'{temp_path}', doraise=True); print('COMPILE_OK')"],
                capture_output=True, text=True, timeout=15
            )

            if result.returncode != 0 or "COMPILE_OK" not in result.stdout:
                err = result.stderr[:300] if result.stderr else "Unknown compilation error"
                try: os.remove(temp_path)
                except Exception as e:
                    from core.jarvis_logger import log_warn
                    log_warn('self_evolution', f'silent swallow: {e}')
                return False, f"Bytecode compilation failed: {err}"

            # Step 3: Optional pyflakes static analysis (catches NameError, unused imports)
            # Only runs if pyflakes is installed — non-blocking if not available
            try:
                flakes_result = subprocess.run(
                    [sys.executable, "-m", "pyflakes", temp_path],
                    capture_output=True, text=True, timeout=10
                )
                if flakes_result.returncode != 0 and flakes_result.stdout:
                    # Pyflakes found issues — log as warning but don't block (some false positives)
                    log_warn("self_evolution", f"Pyflakes warnings in patch: {flakes_result.stdout[:200]}")
            except Exception as e:
                from core.jarvis_logger import log_warn
                log_warn('self_evolution', f'silent swallow: {e}')

            try: os.remove(temp_path)
            except Exception as e:
                from core.jarvis_logger import log_warn
                log_warn('self_evolution', f'silent swallow: {e}')
            return True, "Sandbox validation passed (ast + py_compile bytecode)"

        except subprocess.TimeoutExpired:
            try: os.remove(temp_path)
            except Exception as e:
                from core.jarvis_logger import log_warn
                log_warn('self_evolution', f'silent swallow: {e}')
            return False, "Sandbox timed out (>15s)"
        except Exception as e:
            try: os.remove(temp_path)
            except Exception as e:
                from core.jarvis_logger import log_warn
                log_warn('self_evolution', f'silent swallow: {e}')
            return False, f"Sandbox error: {e}"


# ── Patch Applicator ──────────────────────────────────────────────────────────
class PatchApplicator:
    """
    Safely applies a validated patch:
    1. Backs up original as filename.py.bak
    2. Atomically writes new content
    3. Logs to evolution_log table
    """

    def apply(self, file_path: str, patched_code: str,
               error_info: dict, patch_diff: str, auto: bool = False) -> tuple:
        """Returns (success: bool, message: str)"""

        # Backup original
        bak_path = file_path + ".bak"
        try:
            shutil.copy2(file_path, bak_path)
        except Exception as e:
            return False, f"Backup failed: {e}"

        # Atomic write
        try:
            tmp_path = file_path + ".patch_tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(patched_code)
            os.replace(tmp_path, file_path)
        except Exception as e:
            # Restore backup
            try:
                shutil.copy2(bak_path, file_path)
            except Exception as e:
                from core.jarvis_logger import log_warn
                log_warn('self_evolution', f'silent swallow: {e}')
            return False, f"Write failed: {e}"

        # Save patch log
        log_path = ""
        try:
            os.makedirs(PATCH_LOG_DIR, exist_ok=True)
            ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_name = f"patch_{ts}_{os.path.basename(file_path)}.json"
            log_path = os.path.join(PATCH_LOG_DIR, log_name)
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump({
                    "timestamp":  datetime.now().isoformat(),
                    "file":       file_path,
                    "error":      error_info,
                    "diff":       patch_diff,
                    "auto":       auto,
                    "backup":     bak_path,
                }, f, indent=2)
        except Exception as e:
            log_error("self_evolution", "patch_log_write", e)

        # Mark error as patched in DB
        try:
            conn = sqlite3.connect(EVOLUTION_DB, timeout=10.0)
            cur  = conn.cursor()
            cur.execute(
                "UPDATE error_patterns SET patched=1 WHERE error_hash=?",
                (error_info["error_hash"],)
            )
            cur.execute("""
                INSERT INTO evolution_log
                    (error_hash, target_file, patch_diff, sandbox_result,
                     applied, auto_applied, patch_log_path)
                VALUES (?,?,?,?,1,?,?)
            """, (
                error_info["error_hash"], file_path, patch_diff,
                "passed", int(auto), log_path
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            log_error("self_evolution", "mark_patched_db", e)

        fname = os.path.basename(file_path)
        mode  = "automatically" if auto else "pending approval"
        return True, (
            f"Rishabh, I detected a recurring {error_info['err_type']} in {fname} "
            f"(occurred {error_info['count']} times). I generated and validated a patch. "
            f"The fix has been applied {mode}. "
            f"Original backed up to {os.path.basename(bak_path)}. "
            f"Patch summary: {patch_diff}"
        )

    def revert(self, file_path: str) -> tuple:
        """Revert to the .bak backup if the patch caused issues."""
        bak_path = file_path + ".bak"
        if not os.path.exists(bak_path):
            return False, "No backup file found"
        try:
            shutil.copy2(bak_path, file_path)
            return True, f"Reverted {os.path.basename(file_path)} to backup."
        except Exception as e:
            return False, f"Revert failed: {e}"


# ── Self-Evolution Monitor (BaseMonitor) ─────────────────────────────────────
class SelfEvolutionMonitor(BaseMonitor):
    """
    Orchestrator-compatible monitor that:
    1. Reads the error buffer every 120 seconds
    2. Passes errors to ErrorPatternAnalyzer
    3. If threshold hit → generates + validates patch
    4. If auto_apply=True → applies it; else queues ProactiveEvent for user
    """
    name = "SelfEvolution"
    interval_seconds = 120

    def __init__(self, event_queue=None, config=None):
        super().__init__(event_queue, config)
        _init_evolution_tables()

        self.auto_apply    = self.config.get("auto_apply", False)
        self.trigger_count = self.config.get("trigger_count", 3)

        self.analyzer  = ErrorPatternAnalyzer(trigger_count=self.trigger_count)
        self.generator = PatchGenerator()
        self.sandbox   = PatchSandbox()
        self.applicator = PatchApplicator()

        # Install error buffer (only once globally)
        if not hasattr(SelfEvolutionMonitor, "_buffer_installed"):
            self._error_buffer = ErrorLogBuffer()
            self._error_buffer.install()
            SelfEvolutionMonitor._error_buffer = self._error_buffer
            SelfEvolutionMonitor._buffer_installed = True
        else:
            self._error_buffer = SelfEvolutionMonitor._error_buffer

        print(
            f"[SELF-EVOLUTION]: Online. auto_apply={self.auto_apply}, "
            f"trigger={self.trigger_count} occurrences."
        )

    def check(self):
        events = []
        try:
            # Get recent errors from buffer
            recent = self._error_buffer.get_recent_errors(n=100)
            if not recent:
                return events

            # Analyze for patterns
            alerts = self.analyzer.analyze(recent)
            for alert in alerts:
                print(
                    f"[SELF-EVOLUTION]: Pattern threshold hit for "
                    f"{alert['err_type']} in {os.path.basename(alert['file_path'])} "
                    f"({alert['count']}x). Generating patch..."
                )
                # Set cooldown immediately to prevent duplicate work
                self.analyzer._cooldowns[alert["file_path"]] = time.time()

                # Generate in background thread so monitor doesn't block
                t = threading.Thread(
                    target=self._handle_alert,
                    args=(alert, events),
                    daemon=True,
                    name="JARVIS-SelfPatch",
                )
                t.start()

        except Exception as e:
            print(f"[SELF-EVOLUTION]: Check error: {e}")

        return events

    def _handle_alert(self, alert: dict, event_list: list):
        """Run patch generation + validation in a background thread."""
        try:
            from core.proactive_orchestrator import ProactiveEvent
        except ImportError:
            from dataclasses import dataclass, field as dfield
            @dataclass
            class ProactiveEvent:
                priority: int = 5; category: str = ""; title: str = ""
                message: str = ""; data: dict = dfield(default_factory=dict)
                timestamp: float = 0.0

        # Generate patch
        patched_code, patch_diff = self.generator.generate(alert)
        if not patched_code:
            print(f"[SELF-EVOLUTION]: Patch generation failed: {patch_diff}")
            return

        # Validate
        passed, reason = self.sandbox.validate(patched_code, alert["file_path"])
        if not passed:
            print(f"[SELF-EVOLUTION]: Sandbox rejected patch: {reason}")
            return

        print(f"[SELF-EVOLUTION]: Patch validated. auto_apply={self.auto_apply}")

        if self.auto_apply:
            ok, message = self.applicator.apply(
                alert["file_path"], patched_code, alert, patch_diff, auto=True
            )
        else:
            # Dry-run: save patch but tell user
            message = (
                f"Rishabh, I detected a recurring {alert['err_type']} in "
                f"{os.path.basename(alert['file_path'])} ({alert['count']}x). "
                f"I have a validated patch ready. "
                f"Diff: {patch_diff}. "
                f"Say 'JARVIS apply the patch' to approve, or I'll hold it for review."
            )
            # Store patch for later application
            with _patch_lock:
                SelfEvolutionMonitor._pending_patch = {
                    "file_path":    alert["file_path"],
                    "patched_code": patched_code,
                    "error_info":   alert,
                    "patch_diff":   patch_diff,
                }

        evt = ProactiveEvent(
            priority=2 if self.auto_apply else 3,
            category="evolution",
            title="Self-Patch " + ("Applied" if self.auto_apply else "Ready"),
            message=message,
            data={
                "file":      alert["file_path"],
                "error":     alert["err_type"],
                "count":     alert["count"],
                "auto":      self.auto_apply,
            },
            timestamp=time.time(),
        )
        if self.event_queue:
            self.event_queue.put((evt.priority, evt.timestamp, evt))

    def apply_pending_patch(self) -> str:
        """Called when user says 'apply the patch'."""
        with _patch_lock:
            pending = getattr(SelfEvolutionMonitor, "_pending_patch", None)
            if not pending:
                return "No pending patch to apply, Rishabh."
            SelfEvolutionMonitor._pending_patch = None
        ok, msg = self.applicator.apply(
            pending["file_path"], pending["patched_code"],
            pending["error_info"], pending["patch_diff"], auto=False
        )
        return msg

    def revert_last_patch(self, file_path: str = None) -> str:
        """Called when user says 'revert the patch'."""
        if not file_path:
            # Find most recent patched file from DB
            try:
                conn = sqlite3.connect(EVOLUTION_DB, timeout=10.0)
                cur  = conn.cursor()
                cur.execute(
                    "SELECT target_file FROM evolution_log WHERE applied=1 ORDER BY id DESC LIMIT 1"
                )
                row = cur.fetchone()
                conn.close()
                file_path = row[0] if row else None
            except Exception as e:
                from core.jarvis_logger import log_warn
                log_warn('self_evolution', f'silent swallow: {e}')
        if not file_path:
            return "No patch to revert, Rishabh."
        ok, msg = self.applicator.revert(file_path)
        return msg

    @staticmethod
    def get_evolution_log(limit: int = 10) -> list:
        try:
            conn = sqlite3.connect(EVOLUTION_DB, timeout=10.0)
            conn.row_factory = sqlite3.Row
            cur  = conn.cursor()
            cur.execute("""
                SELECT id, timestamp, target_file, patch_diff,
                       applied, auto_applied, reverted
                FROM evolution_log ORDER BY id DESC LIMIT ?
            """, (limit,))
            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []
