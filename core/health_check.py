"""
health_check.py — JARVIS Startup Health Check (POLISH P1)
=========================================================
Runs on boot. Verifies every subsystem loaded correctly.
Reports clear status to console + returns a summary dict.
If any critical module is missing, JARVIS still boots — just warns.

Usage:
    from core.health_check import run_health_check
    report = run_health_check()  # prints + returns dict
"""

import sys
import time
import importlib
from core.jarvis_logger import log_info, log_warn, log_error


# ── Module definitions ─────────────────────────────────────────────────────────
# (module_path, display_name, critical?)
# Critical = JARVIS can't function at all without it
# Non-critical = JARVIS works but with degraded capability

MODULES = [
    # ── CRITICAL (JARVIS won't work without these) ──
    ("core.brain",               "Brain (LLM Engine)",        True),
    ("core.master_router",       "Master Router",             True),
    ("core.voice",               "Voice (TTS)",               True),
    ("core.ears",                "Ears (STT)",                True),
    ("core.config",              "Config",                    True),
    ("core.database",            "Database",                  True),
    ("core.memory",              "Memory",                    True),

    # ── IMPORTANT (core features degraded without these) ──
    ("core.hands",               "Hands (Agentic Executor)",  False),
    ("core.web_agent",           "Web Agent",                 False),
    ("core.win_fast_voice",      "Windows Fast Voice",        False),
    ("core.narrator",            "Narrator",                  False),
    ("core.command_decomposer",  "Command Decomposer",        False),
    ("core.smart_interrupt",     "Smart Interrupt",           False),

    # ── ENHANCEMENT (JARVIS is fine without these) ──
    ("core.persistent_vision",   "Persistent Vision",         False),
    ("core.self_evolution",      "Self-Evolution Engine",      False),
    ("core.duplex_audio",        "Duplex Audio",              False),
    ("core.graph_memory",        "Graph Memory",              False),
    ("core.memory_engine",       "Memory Engine",             False),
    ("core.vector_vault",        "Vector Vault",              False),
    ("core.emotional_intelligence", "Emotional Intelligence", False),
    ("core.omni_synthesis",      "Omni Synthesis",            False),
    ("core.biometrics",          "Biometrics",                False),
    ("core.trajectory_memory",   "Trajectory Memory",         False),
    ("core.finance_engine",      "Finance Engine",            False),
    ("core.mesh_network",        "Mesh Network",              False),
    ("core.mobile_agent",        "Mobile Agent (ADB)",        False),
    ("core.mqtt_client",         "IoT / MQTT Client",         False),
    ("core.analytics_engine",    "Analytics Engine",          False),
    ("core.computer_use_agent",  "Computer Use Agent",        False),
    ("core.api_quota_tracker",   "API Quota Tracker",         False),
    ("core.dream_mode" if False else "core.feature_flags", "Feature Flags", False),
    ("core.ws_server",           "WebSocket Server",          False),
    ("core.eyes",                "Eyes (Vision)",             False),
    ("core.web_reader",          "Web Reader",                False),
    ("core.swarm",               "Swarm Agent",               False),
]


def run_health_check(verbose: bool = True) -> dict:
    """
    Run startup health check on all JARVIS modules.

    Returns:
        {
            "ok": int,           # modules loaded
            "failed": int,       # modules that failed
            "critical_fail": int, # critical modules that failed
            "modules": { "name": {"status": "OK"|"FAIL", "error": str|None} }
            "boot_time_ms": int,
            "healthy": bool      # True if no critical failures
        }
    """
    _t0 = time.time()
    results = {}
    ok = 0
    failed = 0
    critical_fail = 0

    if verbose:
        print("\n" + "=" * 60)
        print("  🔍 JARVIS STARTUP HEALTH CHECK")
        print("=" * 60)

    for module_path, display_name, is_critical in MODULES:
        try:
            importlib.import_module(module_path)
            results[display_name] = {"status": "OK", "error": None, "critical": is_critical}
            ok += 1
            if verbose:
                tag = "CORE" if is_critical else " OK "
                print(f"  ✅ [{tag}] {display_name}")
        except Exception as e:
            err_msg = str(e)[:80]
            results[display_name] = {"status": "FAIL", "error": err_msg, "critical": is_critical}
            failed += 1
            if is_critical:
                critical_fail += 1
            if verbose:
                tag = "CRIT" if is_critical else "WARN"
                print(f"  ❌ [{tag}] {display_name} — {err_msg}")
            log_warn("health_check", f"{display_name} failed: {err_msg}")

    boot_ms = int((time.time() - _t0) * 1000)
    healthy = critical_fail == 0

    if verbose:
        print("-" * 60)
        print(f"  📊 Result: {ok}/{ok + failed} modules loaded ({boot_ms}ms)")
        if failed > 0:
            print(f"  ⚠️  {failed} modules unavailable (degraded features)")
        if critical_fail > 0:
            print(f"  🔴 {critical_fail} CRITICAL modules failed! JARVIS may not function.")
        else:
            print(f"  🟢 All critical systems operational.")
        print("=" * 60 + "\n")

    # Check LLM providers
    try:
        from core.brain import PROVIDERS
        import os
        active_providers = 0
        for p in PROVIDERS:
            k = p.get("api_key", "")
            if k and str(k).strip() not in ["", "None", "null"] and not str(k).startswith("your_"):
                active_providers += 1
        if verbose:
            print(f"  🧠 LLM Providers: {active_providers}/{len(PROVIDERS)} configured with valid API keys")
    except Exception:
        pass

    log_info("health_check", f"Boot check: {ok}/{ok + failed} OK, {failed} failed, {boot_ms}ms")

    return {
        "ok": ok,
        "failed": failed,
        "critical_fail": critical_fail,
        "modules": results,
        "boot_time_ms": boot_ms,
        "healthy": healthy,
    }


def get_status_summary() -> str:
    """One-line summary for voice/UI display."""
    report = run_health_check(verbose=False)
    total = report["ok"] + report["failed"]
    if report["healthy"]:
        return f"All systems go. {report['ok']}/{total} modules active."
    else:
        return f"Warning: {report['critical_fail']} critical systems offline. {report['ok']}/{total} modules loaded."
