"""
core/config.py — JARVIS Centralized Configuration
===================================================
Single source of truth for all path constants.

Bug #3 Fix: Previously the database path appeared in THREE different forms:
  - database.py       : ../data/jarvis_memory.db   (relative to core/)
  - web_agent.py      : jarvis_memory.db            (project root)
  - self_evolution.py : data/jarvis_memory.db       (relative to CWD)

All modules now import DB_PATH from here. One path, one database.
"""

import os
from pathlib import Path

# ── Project root (the folder containing main.py) ─────────────────────────────
ROOT_DIR = Path(__file__).parent.parent.resolve()

# ── Data directory ─────────────────────────────────────────────────────────────
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Primary SQLite database ────────────────────────────────────────────────────
# The real DB lives in the project ROOT (not /data/) — confirmed by inspection.
# All modules must use this — never hardcode a path again.
DB_PATH = str(ROOT_DIR / "jarvis_memory.db")

# ── Checkpoint directory ───────────────────────────────────────────────────────
# Fix #4: Checkpoints stored here with hash-based names to prevent swarm collision
CHECKPOINT_DIR = ROOT_DIR / "data" / "checkpoints"
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

# ── Session / episodic memory ──────────────────────────────────────────────────
SESSION_FILE = str(DATA_DIR / "jarvis_session.json")

# ── UI directory ──────────────────────────────────────────────────────────────
UI_DIR = ROOT_DIR / "ui"
API_DASHBOARD_PATH = str(UI_DIR / "api_dashboard.html")
API_DASHBOARD_JSON = str(UI_DIR / "api_quota_data.json")

# ── Logs ───────────────────────────────────────────────────────────────────────
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
ERROR_LOG = str(LOGS_DIR / "jarvis_errors.log")

# ── Browser profile ───────────────────────────────────────────────────────────
BROWSER_PROFILE_DIR = str(ROOT_DIR / "browser_profile")

# ── Proactive config ──────────────────────────────────────────────────────────
PROACTIVE_CONFIG_PATH = str(ROOT_DIR / "proactive_config.json")

# ── Wake-on-LAN config ────────────────────────────────────────────────────────
WOL_CONFIG_PATH = str(ROOT_DIR / "wake_on_lan_config.json")

