"""
core/omniforge_server.py — Dedicated Background Server Daemon for OmniForge Apps
================================================================================
Runs as a lightweight, persistent background daemon serving all apps under
`omniforge_apps/` on port 5252.

Endpoints:
  • http://localhost:5252/              -> OmniForge Studio Hub & Project Browser
  • http://localhost:5252/<app_name>/   -> Specific interactive web application
"""

import os
import sys
import json
import time
import socket
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 5252
WORKSPACE_ROOT = Path(r"c:\Users\Rishabh_Joshi\Downloads\jarvis_project")
APPS_ROOT = WORKSPACE_ROOT / "omniforge_apps"
APPS_ROOT.mkdir(parents=True, exist_ok=True)
REGISTRY_FILE = APPS_ROOT / "apps_registry.json"


def generate_studio_hub_html() -> str:
    """Generate a sleek, modern dashboard listing all created OmniForge applications."""
    projects = []
    if REGISTRY_FILE.exists():
        try:
            with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                reg = json.load(f)
                for slug, meta in reg.items():
                    raw_url = meta.get("url", f"/{slug}/")
                    full_url = raw_url if raw_url.startswith("http") else f"http://localhost:5252{raw_url}"
                    projects.append({
                        "slug": slug,
                        "title": meta.get("title", slug.replace("_", " ").title()),
                        "blueprint": meta.get("blueprint", "Web App"),
                        "created_at": meta.get("created_at", "Recently"),
                        "url": full_url
                    })
        except Exception:
            pass

    # Also check directory folders if not in registry
    existing_slugs = {p["slug"] for p in projects}
    for item in APPS_ROOT.iterdir():
        if item.is_dir() and item.name not in existing_slugs and not item.name.startswith("."):
            projects.append({
                "slug": item.name,
                "title": item.name.replace("_", " ").title(),
                "blueprint": "Custom App",
                "created_at": "Active",
                "url": f"http://localhost:5252/{item.name}/"
            })

    cards_html = ""
    for p in projects:
        cards_html += f"""
        <div class="glass-card rounded-2xl p-6 border border-white/10 hover:border-indigo-500/50 transition-all group flex flex-col justify-between">
          <div>
            <div class="flex items-center justify-between mb-3">
              <span class="px-2.5 py-1 rounded-full text-[11px] font-semibold bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 uppercase tracking-wider">{p['blueprint']}</span>
              <span class="text-xs text-slate-500">{p['created_at']}</span>
            </div>
            <h3 class="text-xl font-bold text-white group-hover:text-indigo-400 transition-colors mb-2">{p['title']}</h3>
            <p class="text-xs text-slate-400 font-mono">{p['url']}</p>
          </div>
          <div class="mt-6 pt-4 border-t border-white/5 flex items-center justify-between">
            <span class="inline-flex items-center gap-1.5 text-xs text-emerald-400 font-medium">
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> Online
            </span>
            <a href="{p['url']}" target="_blank" class="px-4 py-2 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-400 hover:to-purple-500 text-white font-semibold text-xs shadow-lg shadow-indigo-500/25 transition-all flex items-center gap-1.5">
              <span>Launch App</span>
              <i data-lucide="external-link" class="w-3.5 h-3.5"></i>
            </a>
          </div>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>JARVIS OmniForge Studio Hub</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/lucide@latest"></script>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel="stylesheet">
  <style>
    body {{ background: #06080d; color: #f8fafc; font-family: 'Plus Jakarta Sans', sans-serif; }}
    .glass-card {{ background: rgba(15, 23, 42, 0.7); backdrop-filter: blur(16px); border: 1px solid rgba(255, 255, 255, 0.08); }}
  </style>
</head>
<body class="p-6 sm:p-10 min-h-screen">
  <div class="max-w-6xl mx-auto">
    <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center pb-8 border-b border-white/10 mb-8 gap-4">
      <div>
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-2xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white font-extrabold text-xl shadow-lg shadow-indigo-500/30">
            ⚡
          </div>
          <div>
            <h1 class="text-2xl font-extrabold text-white">JARVIS OmniForge Studio</h1>
            <p class="text-xs text-slate-400 mt-0.5">Autonomous Web App & Prototype Hub • Port 5252</p>
          </div>
        </div>
      </div>
      <div class="flex items-center gap-3">
        <span class="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-semibold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
          <span class="w-2 h-2 rounded-full bg-emerald-400 animate-ping"></span> Daemon Active & Persistent
        </span>
      </div>
    </div>

    <div class="mb-6 flex justify-between items-center">
      <h2 class="text-lg font-bold text-white flex items-center gap-2">
        <i data-lucide="layers" class="w-5 h-5 text-indigo-400"></i>
        <span>Forged Applications ({len(projects)})</span>
      </h2>
      <button onclick="location.reload()" class="text-xs text-slate-400 hover:text-white flex items-center gap-1">
        <i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i> Refresh List
      </button>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
      {cards_html}
    </div>
  </div>

  <script>
    document.addEventListener('DOMContentLoaded', () => {{
      lucide.createIcons();
    }});
  </script>
</body>
</html>
"""


class OmniForgeRequestHandler(SimpleHTTPRequestHandler):
    """Custom request handler that serves omniforge_apps and generates dynamic index."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APPS_ROOT), **kwargs)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            # Dynamically update the studio hub index
            hub_html = generate_studio_hub_html()
            hub_path = APPS_ROOT / "index.html"
            with open(hub_path, "w", encoding="utf-8") as f:
                f.write(hub_html)
            return super().do_GET()

        # Intercept app directories to redirect to dedicated Vite dev servers if applicable
        raw_slug = self.path.strip("/").split("/")[0] if self.path.strip("/") else ""
        if raw_slug and REGISTRY_FILE.exists():
            try:
                with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                    reg = json.load(f)
                    if raw_slug in reg:
                        info = reg[raw_slug]
                        target_port = info.get("port", PORT)
                        if target_port != PORT:
                            # If Vite dev server is offline, restart it
                            if not is_port_in_use(target_port):
                                from core.omniforge_scaffolder import start_vite_dev_server
                                start_vite_dev_server(APPS_ROOT / raw_slug, target_port)
                                time.sleep(0.4)
                            target_url = info.get("url", f"http://localhost:{target_port}/")
                            self.send_response(302)
                            self.send_header("Location", target_url)
                            self.end_headers()
                            return
            except Exception:
                pass

        # Standard file serving
        return super().do_GET()

    def log_message(self, format, *args):
        # Quiet logger
        pass


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def run_server():
    # Update hub HTML
    hub_html = generate_studio_hub_html()
    with open(APPS_ROOT / "index.html", "w", encoding="utf-8") as f:
        f.write(hub_html)

    if is_port_in_use(PORT):
        print(f"OmniForge server is already listening on port {PORT}.")
        return

    server = HTTPServer(("127.0.0.1", PORT), OmniForgeRequestHandler)
    print(f"OmniForge Master Daemon running on http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    run_server()
