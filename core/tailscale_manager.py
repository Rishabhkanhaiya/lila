"""
core/tailscale_manager.py — Tailscale VPN Subnet & Peer Topology Manager
=========================================================================
Manages Tailscale VPN integration for JARVIS and Wake-on-LAN:
- Discovers Tailscale installation, daemon status, and local tailnet IPv4 (100.x.y.z).
- Enumerates peer devices (identifying mobile phones and Always-On Relay nodes).
- Verifies tailnet peer-to-peer connectivity via ICMP and port probing.
- Provides Subnet Router configuration recipes for Layer 2 / Layer 3 bridging.
"""

import os
import sys
import json
import socket
import logging
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any

from core.jarvis_logger import log_info, log_warn, log_error

logger = logging.getLogger("JARVIS.TailscaleManager")


WIN32_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0

# Common Tailscale executable locations
TAILSCALE_EXECUTABLES = [
    "tailscale",
    "tailscale.exe",
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
    r"C:\Users\Rishabh_Joshi\AppData\Local\Tailscale\tailscale.exe",
]

_cached_tailscale_binary: Optional[str] = None
_binary_checked: bool = False

_cached_status: Dict[str, Any] = {}
_last_status_time: float = 0.0


def find_tailscale_binary() -> Optional[str]:
    """Locates the Tailscale CLI binary silently on the host system."""
    global _cached_tailscale_binary, _binary_checked
    if _binary_checked:
        return _cached_tailscale_binary

    import shutil
    for exe in TAILSCALE_EXECUTABLES:
        resolved = shutil.which(exe) if not os.path.isabs(exe) else (exe if os.path.exists(exe) else None)
        if resolved:
            try:
                cmd = [resolved, "version"]
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                    creationflags=WIN32_NO_WINDOW
                )
                if proc.returncode == 0:
                    _cached_tailscale_binary = resolved
                    _binary_checked = True
                    return resolved
            except Exception:
                continue

    _binary_checked = True
    return None


def get_tailscale_ip() -> Optional[str]:
    """
    Returns the local machine's Tailscale IPv4 address (100.64.0.0/10 range).
    Inspects network adapters first (0 processes, <0.1ms), then CLI as fallback.
    """
    # 1. Fast in-memory inspection of system network adapters (Zero Subprocess / Zero Window)
    try:
        import psutil
        for iface_name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET and a.address.startswith("100."):
                    parts = [int(p) for p in a.address.split(".")]
                    if len(parts) == 4 and 64 <= parts[1] <= 127:
                        return a.address
    except Exception:
        pass

    # 2. CLI fallback with CREATE_NO_WINDOW
    binary = find_tailscale_binary()
    if binary:
        try:
            proc = subprocess.run(
                [binary, "ip", "-4"],
                capture_output=True,
                text=True,
                timeout=3.0,
                creationflags=WIN32_NO_WINDOW
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip().splitlines()[0].strip()
        except Exception as ex:
            logger.debug(f"[Tailscale] CLI ip lookup failed: {ex}")

    return None


def get_tailscale_status() -> Dict[str, Any]:
    """
    Queries Tailscale daemon status and topology.
    Cached for 10 seconds to prevent rapid subprocess spamming.
    """
    global _cached_status, _last_status_time
    import time
    now = time.time()
    if _cached_status and (now - _last_status_time < 10.0):
        return dict(_cached_status)

    binary = find_tailscale_binary()
    local_ip = get_tailscale_ip()

    status_data: Dict[str, Any] = {
        "installed": bool(binary or local_ip),
        "running": bool(local_ip),
        "cli_path": binary or "Not in PATH",
        "tailscale_ip": local_ip or "Offline/Unassigned",
        "is_online": bool(local_ip),
        "machine_name": socket.gethostname(),
        "peers": [],
        "version": "Unknown"
    }

    if not binary:
        _cached_status = dict(status_data)
        _last_status_time = now
        return status_data

    try:
        proc_ver = subprocess.run(
            [binary, "version"],
            capture_output=True,
            text=True,
            timeout=2.0,
            creationflags=WIN32_NO_WINDOW
        )
        if proc_ver.returncode == 0:
            status_data["version"] = proc_ver.stdout.splitlines()[0].strip()
    except Exception:
        pass

    try:
        proc_status = subprocess.run(
            [binary, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=4.0,
            creationflags=WIN32_NO_WINDOW
        )
        if proc_status.returncode == 0 and proc_status.stdout.strip():
            raw_json = json.loads(proc_status.stdout)
            self_node = raw_json.get("Self", {})
            status_data["machine_name"] = self_node.get("HostName", status_data["machine_name"])
            status_data["tailscale_ip"] = (self_node.get("TailscaleIPs") or [local_ip])[0]
            status_data["is_online"] = self_node.get("Online", True)
            status_data["running"] = status_data["is_online"]

            peers_dict = raw_json.get("Peer", {})
            for peer_key, peer_val in peers_dict.items():
                peer_ips = peer_val.get("TailscaleIPs", [])
                status_data["peers"].append({
                    "id": peer_key,
                    "host_name": peer_val.get("HostName", "unknown"),
                    "ip": peer_ips[0] if peer_ips else "none",
                    "os": peer_val.get("OS", "unknown"),
                    "online": peer_val.get("Online", False),
                    "relay": peer_val.get("Relay", "")
                })
    except Exception as ex:
        logger.debug(f"[Tailscale] Status parse exception: {ex}")

    _cached_status = dict(status_data)
    _last_status_time = now
    return status_data


def check_peer_connectivity(target_tailscale_ip: str, timeout_sec: float = 2.0) -> Dict[str, Any]:
    """
    Tests connectivity to a Tailscale peer node via `tailscale ping` and TCP probing.
    Zero terminal windows created.
    """
    result: Dict[str, Any] = {
        "target_ip": target_tailscale_ip,
        "reachable": False,
        "latency_ms": None,
        "method": "none"
    }

    binary = find_tailscale_binary()
    if binary:
        try:
            proc = subprocess.run(
                [binary, "ping", "--until-direct=false", "--timeout=2s", "--c=1", target_tailscale_ip],
                capture_output=True,
                text=True,
                timeout=timeout_sec + 1.0,
                creationflags=WIN32_NO_WINDOW
            )
            if proc.returncode == 0 and "pong" in proc.stdout.lower():
                result["reachable"] = True
                result["method"] = "tailscale_ping"
                import re
                m = re.search(r"(\d+(?:\.\d+)?)\s*ms", proc.stdout)
                if m:
                    result["latency_ms"] = float(m.group(1))
        except Exception:
            pass

    # Fallback to standard socket probe on relay port 8768 or standard ports
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout_sec)
    import time
    start_t = time.time()
    for port in (8768, 8766, 8765, 22):
        try:
            res = s.connect_ex((target_tailscale_ip, port))
            if res == 0:
                result["reachable"] = True
                result["latency_ms"] = round((time.time() - start_t) * 1000, 2)
                result["method"] = f"tcp_probe_{port}"
                break
        except Exception:
            pass
    s.close()
    return result


def generate_subnet_router_instructions(local_subnet: str = "192.168.88.0/24") -> Dict[str, str]:
    """
    Generates exact setup instructions and commands for turning an Always-On Device
    (e.g., Raspberry Pi, Linux PC) into a Tailscale Subnet Router.
    """
    linux_cmd = (
        f"# 1. Enable IP forwarding on Always-On Device:\n"
        f"echo 'net.ipv4.ip_forward = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf\n"
        f"echo 'net.ipv6.conf.all.forwarding = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf\n"
        f"sudo sysctl -p /etc/sysctl.d/99-tailscale.conf\n\n"
        f"# 2. Advertise your home LAN subnet via Tailscale:\n"
        f"sudo tailscale up --advertise-routes={local_subnet} --accept-dns=false\n\n"
        f"# 3. In the Tailscale Admin Console (https://login.tailscale.com/admin/machines):\n"
        f"#    Locate the device -> Edit route settings -> Enable subnet route '{local_subnet}'."
    )

    windows_cmd = (
        f":: On Windows Always-On Device (in Administrator PowerShell):\n"
        f"tailscale up --advertise-routes={local_subnet}\n"
    )

    return {
        "local_subnet": local_subnet,
        "linux_instructions": linux_cmd,
        "windows_instructions": windows_cmd
    }
