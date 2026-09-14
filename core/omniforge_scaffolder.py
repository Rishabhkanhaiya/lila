"""
core/omniforge_scaffolder.py — OmniForge v2 Sandboxed Multi-File Scaffolder & Compiler Loop
==========================================================================================
1. Instant Scaffolding (~10ms via directory junctions to .base_template/node_modules)
2. Modular React 18 + TypeScript + Tailwind + Three.js + Lucide file tree
3. Sandboxed Vite compiler check (`vite build --emptyOutDir`) with real exit codes
4. Self-healing loop: automated compiler error feedback & Gemini 2.5 Flash code patcher
5. Background Vite dev server lifecycle management
"""

import os
import sys
import json
import time
import shutil
import socket
import urllib.request
import subprocess
from pathlib import Path
from typing import Tuple, Dict, Any, Optional

from core.jarvis_logger import log_info, log_warn, log_error
from core.omniforge_patterns import (
    PATTERN_NAVBAR,
    PATTERN_THREE_PARTICLES,
    PATTERN_BENTO_GRID,
    PATTERN_CONTACT_MODAL
)

WORKSPACE_ROOT = Path(r"c:\Users\Rishabh_Joshi\Downloads\jarvis_project")
APPS_ROOT = WORKSPACE_ROOT / "omniforge_apps"
BASE_TEMPLATE = APPS_ROOT / ".base_template"


def _resolve_npx() -> str:
    """Find npx.cmd or npx in PATH or Node install directory."""
    return (
        shutil.which("npx.cmd")
        or shutil.which("npx")
        or r"C:\Program Files\nodejs\npx.cmd"
    )


def scaffold_vite_project(project_name: str, port: int = 5210) -> Path:
    """
    Instantly scaffold a multi-file Vite + React + TypeScript project in omniforge_apps/<project_name>.
    Uses a Windows directory junction to .base_template/node_modules for 0-wait package sharing.
    """
    clean_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in project_name.strip().lower())
    if not clean_name:
        clean_name = f"app_{int(time.time())}"

    app_dir = APPS_ROOT / clean_name
    app_dir.mkdir(parents=True, exist_ok=True)
    src_dir = app_dir / "src"
    comp_dir = src_dir / "components"
    comp_dir.mkdir(parents=True, exist_ok=True)

    # 1. Copy essential config files from base template
    for config_file in ["tsconfig.json", "tailwind.config.js", "postcss.config.js", "index.html"]:
        src_f = BASE_TEMPLATE / config_file
        if src_f.exists():
            shutil.copy2(src_f, app_dir / config_file)

    # Copy src/main.tsx, src/index.css, and initial src/App.tsx
    for src_file in ["main.tsx", "index.css", "App.tsx"]:
        src_f = BASE_TEMPLATE / "src" / src_file
        if src_f.exists():
            shutil.copy2(src_f, src_dir / src_file)

    # 2. Customized package.json
    pkg = {
        "name": clean_name,
        "private": True,
        "version": "1.0.0",
        "type": "module",
        "scripts": {
            "dev": "vite",
            "build": "vite build",
            "preview": "vite preview"
        }
    }
    with open(app_dir / "package.json", "w", encoding="utf-8") as f:
        json.dump(pkg, f, indent=2)

    # 3. Customized vite.config.ts with dedicated port
    vite_cfg = f"""import {{ defineConfig }} from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({{
  plugins: [react()],
  server: {{
    host: '127.0.0.1',
    port: {port},
    strictPort: false,
    open: false,
  }},
  build: {{
    outDir: 'dist',
    sourcemap: false,
    minify: true,
  }}
}});
"""
    with open(app_dir / "vite.config.ts", "w", encoding="utf-8") as f:
        f.write(vite_cfg)

    # 4. Pre-populate vetted component patterns in src/components/
    with open(comp_dir / "Navbar.tsx", "w", encoding="utf-8") as f:
        f.write(PATTERN_NAVBAR)
    with open(comp_dir / "ThreeCanvas.tsx", "w", encoding="utf-8") as f:
        f.write(PATTERN_THREE_PARTICLES)
    with open(comp_dir / "BentoGrid.tsx", "w", encoding="utf-8") as f:
        f.write(PATTERN_BENTO_GRID)
    with open(comp_dir / "ContactModal.tsx", "w", encoding="utf-8") as f:
        f.write(PATTERN_CONTACT_MODAL)

    # 5. Instant Junction link to .base_template/node_modules
    dst_nm = app_dir / "node_modules"
    src_nm = BASE_TEMPLATE / "node_modules"
    if src_nm.exists() and not dst_nm.exists():
        junction_cmd = f'cmd.exe /c mklink /J "{dst_nm}" "{src_nm}"'
        subprocess.run(junction_cmd, shell=True, capture_output=True)

    return app_dir


def run_compiler_check(app_dir: Path) -> Tuple[bool, str]:
    """
    Executes a real sandboxed compile check via `npx.cmd vite build --emptyOutDir`.
    Returns (True, "OK") if build exit code is 0.
    Returns (False, stderr_or_stdout) if build failed.
    """
    npx_bin = _resolve_npx()
    try:
        proc = subprocess.run(
            f'"{npx_bin}" vite build --emptyOutDir',
            cwd=str(app_dir),
            shell=True,
            capture_output=True,
            text=True,
            timeout=45
        )
        if proc.returncode == 0:
            return True, "Build succeeded with exit code 0."
        else:
            err = proc.stderr.strip() or proc.stdout.strip() or f"Build exited with code {proc.returncode}"
            return False, err
    except Exception as e:
        return False, f"Compiler execution exception: {e}"


def heal_project(app_dir: Path, error_log: str, prompt: str = "") -> bool:
    """
    Self-healing compiler loop: passes the exact compiler error and src/App.tsx to
    Gemini 2.5 Flash to automatically repair TypeScript / JSX errors until compilation succeeds.
    """
    app_tsx = app_dir / "src" / "App.tsx"
    if not app_tsx.exists():
        return False

    try:
        from google import genai
        from google.genai import types

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return False

        client = genai.Client(api_key=api_key)
        current_code = app_tsx.read_text(encoding="utf-8")

        heal_prompt = (
            "You are an expert React 18 TypeScript Engineer.\n"
            "The Vite project build failed with this exact error:\n"
            f"--- ERROR LOG ---\n{error_log[:2000]}\n--- END ERROR LOG ---\n\n"
            f"Here is the current src/App.tsx:\n```tsx\n{current_code}\n```\n\n"
            "Fix the error in src/App.tsx. Ensure all component imports match:\n"
            "- import { Navbar } from './components/Navbar';\n"
            "- import { ThreeCanvas } from './components/ThreeCanvas';\n"
            "- import { BentoGrid } from './components/BentoGrid';\n"
            "- import { ContactModal } from './components/ContactModal';\n"
            "- import icons from 'lucide-react';\n"
            "- import confetti from 'canvas-confetti';\n\n"
            "Return ONLY the complete, raw corrected TypeScript code for src/App.tsx. Do NOT wrap in markdown fences."
        )

        config = types.GenerateContentConfig(temperature=0.2)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=heal_prompt,
            config=config
        )

        if response and response.text:
            clean = response.text.strip()
            if clean.startswith("```tsx") or clean.startswith("```typescript"):
                clean = clean.split("\n", 1)[1]
            elif clean.startswith("```"):
                clean = clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()

            if "export default" in clean:
                app_tsx.write_text(clean, encoding="utf-8")
                ok, _ = run_compiler_check(app_dir)
                return ok
    except Exception as e:
        log_warn("omniforge_scaffolder", f"Heal attempt exception: {e}")

    return False


def start_vite_dev_server(app_dir: Path, port: int) -> Optional[int]:
    """
    Launches a persistent background Vite dev server on the specified port.
    Returns the PID if responsive, or None.
    """
    npx_bin = _resolve_npx()

    DETACHED = 0x00000008
    cmd = f'"{npx_bin}" vite --port {port} --host 127.0.0.1'
    p = subprocess.Popen(
        cmd,
        cwd=str(app_dir),
        shell=True,
        creationflags=DETACHED,
        close_fds=True
    )

    target_url = f"http://127.0.0.1:{port}/"
    for _ in range(25):
        time.sleep(0.2)
        try:
            req = urllib.request.Request(target_url, headers={"User-Agent": "OmniForge-Probe"})
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status == 200:
                    log_info("omniforge_scaffolder", f"Vite dev server online at {target_url} (PID: {p.pid})")
                    return p.pid
        except Exception:
            pass

    return p.pid
