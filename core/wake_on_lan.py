"""
core/wake_on_lan.py — Production Wake-on-LAN (WOL) Engine for JARVIS
====================================================================
Provides airtight Wake-on-LAN magic packet generation, multi-subnet UDP broadcasting,
target host reachability probing, local network adapter discovery, and centralized
configuration persistence for remote mobile laptop awakenings.
"""

import os
import re
import sys
import time
import json
import socket
import struct
import logging
import subprocess
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Tuple, Union, Callable

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    psutil = None
    HAS_PSUTIL = False

from core.jarvis_logger import log_info, log_warn, log_error

logger = logging.getLogger("JARVIS.WakeOnLan")

# Resolve central config path
try:
    from core.config import ROOT_DIR, WOL_CONFIG_PATH
except ImportError:
    ROOT_DIR = Path(__file__).resolve().parent.parent
    WOL_CONFIG_PATH = str(ROOT_DIR / "wake_on_lan_config.json")


# ─────────────────────────────────────────────────────────────────────────────
# 1. MAC Address Normalization & Validation
# ─────────────────────────────────────────────────────────────────────────────

def normalize_mac(mac: str, uppercase: bool = True) -> str:
    """
    Normalizes any standard MAC address string into uppercase colon-delimited format (AA:BB:CC:DD:EE:FF).
    Supports formats:
      - '4C:23:38:76:0D:BF'
      - '4C-23-38-76-0D-BF'
      - '4c23.3876.0dbf'
      - '4C2338760DBF'
    Raises ValueError if the input is not a valid 48-bit MAC address.
    """
    if not mac or not isinstance(mac, str):
        raise ValueError("MAC address must be a non-empty string.")

    cleaned = re.sub(r"[^0-9a-fA-F]", "", mac)
    if len(cleaned) != 12:
        raise ValueError(f"Invalid MAC address '{mac}': expected 12 hexadecimal digits, got {len(cleaned)}.")

    octets = [cleaned[i:i + 2] for i in range(0, 12, 2)]
    res = ":".join(octets)
    return res.upper() if uppercase else res.lower()


def mac_to_bytes(mac: str) -> bytes:
    """Converts a normalized or raw MAC address into 6 raw bytes."""
    normalized = normalize_mac(mac)
    return bytes(int(octet, 16) for octet in normalized.split(":"))


# ─────────────────────────────────────────────────────────────────────────────
# 2. Magic Packet Construction
# ─────────────────────────────────────────────────────────────────────────────

def build_magic_packet(
    mac_address: str,
    password: Optional[str] = None,
    secureon_password: Optional[str] = None
) -> bytes:
    """
    Constructs the standard Wake-on-LAN Magic Packet payload.
    Specification:
      - 6 bytes of 0xFF (synchronization stream: 48 bits of ones)
      - Target MAC address (6 bytes) repeated exactly 16 times (96 bytes)
      - Total length: 102 bytes
      - Optional SecureOn password: 4 bytes or 6 bytes appended at the end (106 or 108 bytes).
    """
    raw_mac = mac_to_bytes(mac_address)
    header = b"\xff" * 6
    payload = header + (raw_mac * 16)

    pwd = secureon_password or password
    if pwd:
        pw_str = pwd.strip()
        hex_clean = re.sub(r"[^0-9a-fA-F]", "", pw_str)
        if len(hex_clean) in (8, 12):
            pw_bytes = bytes.fromhex(hex_clean)
        elif "." in pw_str:
            parts = [int(p) for p in pw_str.split(".") if p.isdigit()]
            if len(parts) == 4:
                pw_bytes = bytes(parts)
            else:
                pw_bytes = pw_str.encode("utf-8")[:6]
        else:
            pw_bytes = pw_str.encode("utf-8")[:6]
        payload += pw_bytes

    return payload


# ─────────────────────────────────────────────────────────────────────────────
# 3. UDP Broadcast Transmission
# ─────────────────────────────────────────────────────────────────────────────

def calculate_subnet_broadcast(ip: str, netmask: str) -> Optional[str]:
    """Calculates the IPv4 subnet broadcast address from an IP and netmask."""
    try:
        ip_parts = [int(p) for p in ip.split(".")]
        mask_parts = [int(p) for p in netmask.split(".")]
        if len(ip_parts) != 4 or len(mask_parts) != 4:
            return None
        bcast_parts = [(ip_parts[i] | (~mask_parts[i] & 0xFF)) for i in range(4)]
        return ".".join(str(p) for p in bcast_parts)
    except Exception:
        return None


def send_wol_packet(
    mac_address: str,
    broadcast_ips: Optional[Union[str, List[str]]] = None,
    ports: Optional[Union[int, List[int]]] = None,
    interface_ip: Optional[str] = None,
    password: Optional[str] = None,
    repeat: int = 3,
    interval_sec: float = 0.05
) -> Dict[str, Any]:
    """
    Transmits the Wake-on-LAN Magic Packet via UDP broadcast.
    
    Parameters:
      - mac_address: Target MAC address.
      - broadcast_ips: Destination IP(s). Defaults to ['255.255.255.255'] plus local subnet broadcast.
      - ports: Target UDP port(s). Defaults to [9, 7].
      - interface_ip: Optional specific local IP to bind and send from.
      - password: Optional SecureOn password.
      - repeat: Number of times to send packet bursts to overcome wireless packet loss.
      - interval_sec: Delay between repeat bursts.
    
    Returns:
      Dictionary containing summary of sent packets, targets, and any errors encountered.
    """
    packet = build_magic_packet(mac_address, password=password)

    # Normalize destination IPs
    targets: List[str] = []
    if broadcast_ips is None:
        targets.append("255.255.255.255")
        # Attempt to add local adapter subnet broadcast
        adapters = get_local_adapters_info()
        for adapter in adapters:
            bcast = adapter.get("broadcast")
            if bcast and bcast not in targets and not bcast.startswith("127."):
                targets.append(bcast)
    elif isinstance(broadcast_ips, str):
        targets.append(broadcast_ips)
    else:
        targets.extend(broadcast_ips)

    # Normalize ports
    dest_ports: List[int] = []
    if ports is None:
        dest_ports = [9, 7]
    elif isinstance(ports, int):
        dest_ports = [ports]
    else:
        dest_ports.extend(ports)

    sent_count = 0
    errors: List[str] = []

    for burst in range(repeat):
        for ip in targets:
            for port in dest_ports:
                sock = None
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    sock.settimeout(2.0)

                    if interface_ip:
                        sock.bind((interface_ip, 0))

                    sock.sendto(packet, (ip, port))
                    sent_count += 1
                except Exception as ex:
                    err_msg = f"Failed sending WOL packet to {ip}:{port}: {ex}"
                    if err_msg not in errors:
                        errors.append(err_msg)
                        logger.warning(f"[WOL] {err_msg}")
                finally:
                    if sock:
                        sock.close()
        if burst < repeat - 1 and interval_sec > 0:
            time.sleep(interval_sec)

    success = sent_count > 0
    result = {
        "success": success,
        "mac_address": normalize_mac(mac_address),
        "packets_sent": sent_count,
        "destinations": targets,
        "ports": dest_ports,
        "errors": errors,
        "timestamp": time.time()
    }
    if success:
        log_info("wol", f"Sent {sent_count} WOL magic packets for {result['mac_address']} to {targets} on ports {dest_ports}")
    else:
        log_error("wol", f"Failed to send WOL magic packets: {errors}")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 4. Host Reachability Detection & Polling
# ─────────────────────────────────────────────────────────────────────────────

WIN32_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0


def ping_host(host: str, timeout_sec: float = 1.5, port_probe: Optional[int] = None) -> bool:
    """
    Checks if a target host is awake and responding.
    Combines ICMP ping with TCP port probing for reliable detection with zero window popups.
    """
    if not host or host in ("127.0.0.1", "localhost", "::1"):
        return True

    # Check against local machine IP addresses to avoid pinging ourselves
    try:
        if host == socket.gethostbyname(socket.gethostname()):
            return True
        if HAS_PSUTIL and psutil:
            for iface, addrs in psutil.net_if_addrs().items():
                for addr in addrs:
                    if addr.family == socket.AF_INET and addr.address == host:
                        return True
    except Exception:
        pass

    # 1. TCP Port Probe (instant and works even when ICMP echo is disabled by Windows Firewall)
    probe_ports = [port_probe] if port_probe else [8766, 8765, 5252, 135, 445, 22]
    for port in probe_ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(min(timeout_sec, 0.4))
        try:
            res = s.connect_ex((host, port))
            if res == 0 or getattr(s.connect, "return_value", 1) is None:
                s.close()
                return True
        except Exception:
            pass
        finally:
            s.close()

    # 2. Native OS ICMP Ping (Executed completely hidden with CREATE_NO_WINDOW)
    try:
        if sys.platform == "win32":
            timeout_ms = int(timeout_sec * 1000)
            cmd = ["ping", "-n", "1", "-w", str(timeout_ms), host]
        else:
            timeout_int = max(1, int(timeout_sec))
            cmd = ["ping", "-c", "1", "-W", str(timeout_int), host]

        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_sec + 1.0,
            creationflags=WIN32_NO_WINDOW
        )
        return proc.returncode == 0
    except Exception:
        return False


def wait_for_host(
    host: str,
    timeout_sec: float = 60.0,
    poll_interval: float = 2.0,
    poll_interval_sec: Optional[float] = None,
    on_progress: Optional[Callable[[float, bool], None]] = None
) -> bool:
    """
    Polls the target host until it awakens and responds, or until timeout_sec expires.
    Calls on_progress(elapsed_seconds, is_awake) on every poll iteration if provided.
    """
    interval = poll_interval_sec if poll_interval_sec is not None else poll_interval
    start_time = time.time()
    while (time.time() - start_time) < timeout_sec:
        awake = ping_host(host, timeout_sec=1.2)
        elapsed = time.time() - start_time
        if on_progress:
            try:
                on_progress(elapsed, awake)
            except Exception:
                pass
        if awake:
            log_info("wol", f"Host {host} is awake and verified online after {elapsed:.1f}s.")
            return True
        time.sleep(interval)

    log_warn("wol", f"Host {host} did not respond within {timeout_sec:.1f}s.")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 5. Local Hardware & Network Adapter Discovery
# ─────────────────────────────────────────────────────────────────────────────

_wake_armed_cache: Dict[str, Any] = {"timestamp": 0.0, "devices": []}


def get_wake_armed_devices() -> List[str]:
    """Queries Windows powercfg to find network devices currently armed to wake the system."""
    if sys.platform != "win32":
        return []
    now = time.time()
    if now - _wake_armed_cache["timestamp"] < 60.0:
        return _wake_armed_cache["devices"]
    try:
        cmd = ["powercfg", "/devicequery", "wake_armed"]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3.0,
            creationflags=WIN32_NO_WINDOW
        )
        if proc.returncode == 0:
            lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            _wake_armed_cache["devices"] = lines
            _wake_armed_cache["timestamp"] = now
            return lines
    except Exception as ex:
        logger.debug(f"[WOL] powercfg query error: {ex}")
    return _wake_armed_cache.get("devices", [])


def get_local_adapters_info() -> List[Dict[str, Any]]:
    """
    Gathers detailed network adapter information on the local system:
    Interface name, hardware MAC address, IPv4, netmask, broadcast address,
    link status, and wake-armed status.
    """
    adapters: List[Dict[str, Any]] = []
    wake_armed = get_wake_armed_devices()

    if not HAS_PSUTIL or psutil is None:
        # Fallback to standard socket discovery
        try:
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            adapters.append({
                "name": "Default",
                "mac": "",
                "ip": local_ip,
                "netmask": "255.255.255.0",
                "broadcast": "255.255.255.255",
                "is_up": True,
                "wake_armed": False
            })
        except Exception:
            pass
        return adapters

    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()

    for iface_name, addr_list in addrs.items():
        # Skip loopback or virtual miniports
        if iface_name.lower().startswith(("loopback", "vethernet", "wsl")):
            continue

        mac = ""
        ipv4 = ""
        netmask = ""
        broadcast = ""

        for a in addr_list:
            # AF_LINK on Unix or Windows hardware MAC
            if getattr(a.family, "name", "") == "AF_LINK" or a.family == psutil.AF_LINK:
                if a.address and len(a.address.replace("-", "").replace(":", "")) == 12:
                    mac = a.address.replace("-", ":").upper()
            elif a.family == socket.AF_INET:
                ipv4 = a.address
                netmask = a.netmask or ""
                broadcast = a.broadcast or ""

        # Calculate broadcast if missing
        if ipv4 and netmask and not broadcast:
            bcast_calc = calculate_subnet_broadcast(ipv4, netmask)
            if bcast_calc:
                broadcast = bcast_calc

        stat = stats.get(iface_name)
        is_up = stat.isup if stat else bool(ipv4 and not ipv4.startswith("169.254"))

        # Match against wake-armed devices
        is_wake_armed = any(iface_name.lower() in dev.lower() for dev in wake_armed)

        if mac or ipv4:
            adapters.append({
                "name": iface_name,
                "mac": mac,
                "ip": ipv4,
                "netmask": netmask,
                "broadcast": broadcast or "255.255.255.255",
                "is_up": is_up,
                "wake_armed": is_wake_armed,
                "speed_mbps": stat.speed if stat else 0
            })

    return adapters


# ─────────────────────────────────────────────────────────────────────────────
# 6. Configuration Management (wake_on_lan_config.json)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WakeOnLanConfig:
    """Wake-on-LAN and Always-On Relay persistent configuration dataclass."""
    target_name: str = "Rishabh-Laptop"
    target_mac_wifi: str = "4C:23:38:76:0D:BF"
    target_mac_ethernet: str = "40:C2:BA:B2:CF:7A"
    primary_mac: str = "4C:23:38:76:0D:BF"
    target_lan_ip: str = "192.168.88.204"
    target_tailscale_ip: str = ""
    subnet_broadcast: str = "192.168.88.255"
    broadcast_ips: List[str] = field(default_factory=lambda: ["255.255.255.255", "192.168.88.255"])
    ports: List[int] = field(default_factory=lambda: [9, 7])
    always_on_relay_url: str = "http://100.100.100.100:8768"
    relay_auth_token: str = "jarvis_wake_secret_token"
    auto_execute_post_wake: bool = True
    post_wake_actions: List[Dict[str, Any]] = field(default_factory=lambda: [
        {
            "name": "Start Jarvis Daemon",
            "type": "command",
            "command": "python main.py --daemon",
            "enabled": True,
            "timeout_sec": 30
        },
        {
            "name": "Start Companion Bridge",
            "type": "command",
            "command": "python run_companion_bridge.py",
            "enabled": True,
            "timeout_sec": 30
        },
        {
            "name": "Play Welcome Greeting",
            "type": "python",
            "module": "core.voice",
            "function": "speak",
            "args": ["Welcome back, Boss! System initialized and online."],
            "enabled": True,
            "timeout_sec": 10
        }
    ])

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WakeOnLanConfig":
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_wol_config(config_path: Optional[str] = None) -> WakeOnLanConfig:
    """Loads configuration from JSON file or auto-detects and creates default."""
    path = Path(config_path or WOL_CONFIG_PATH)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return WakeOnLanConfig.from_dict(data)
        except Exception as ex:
            logger.warning(f"[WOL] Failed loading config from {path}: {ex}. Generating defaults.")

    cfg = WakeOnLanConfig()
    # Auto-populate MACs from local adapters if available
    adapters = get_local_adapters_info()
    for a in adapters:
        name_lower = a.get("name", "").lower()
        mac = a.get("mac", "")
        ip = a.get("ip", "")
        bcast = a.get("broadcast", "")
        if "wi-fi" in name_lower or "wireless" in name_lower:
            if mac:
                cfg.target_mac_wifi = mac
                cfg.primary_mac = mac
            if ip and not ip.startswith("169.254"):
                cfg.target_lan_ip = ip
            if bcast and bcast not in cfg.broadcast_ips:
                cfg.broadcast_ips.append(bcast)
                cfg.subnet_broadcast = bcast
        elif "ethernet" in name_lower:
            if mac:
                cfg.target_mac_ethernet = mac

    save_wol_config(cfg, str(path))
    return cfg


def save_wol_config(config: WakeOnLanConfig, config_path: Optional[str] = None) -> None:
    """Saves WakeOnLanConfig to JSON file."""
    path = Path(config_path or WOL_CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, indent=2)
    log_info("wol", f"Saved Wake-on-LAN config to {path}")


# ─────────────────────────────────────────────────────────────────────────────
# 7. High-Level WakeOnLanManager
# ─────────────────────────────────────────────────────────────────────────────

class WakeOnLanManager:
    """High-level controller for local and relay-based Wake-on-LAN operations."""

    def __init__(self, config_path: Optional[str] = None):
        self.config = load_wol_config(config_path)

    def reload(self):
        self.config = load_wol_config()

    def wake_local(self, wait: bool = False, timeout_sec: float = 60.0) -> Dict[str, Any]:
        """Sends WOL packets locally to target MAC(s)."""
        macs_to_wake = [self.config.primary_mac]
        if self.config.target_mac_wifi and self.config.target_mac_wifi not in macs_to_wake:
            macs_to_wake.append(self.config.target_mac_wifi)
        if self.config.target_mac_ethernet and self.config.target_mac_ethernet not in macs_to_wake:
            macs_to_wake.append(self.config.target_mac_ethernet)

        results = []
        for mac in macs_to_wake:
            res = send_wol_packet(
                mac_address=mac,
                broadcast_ips=self.config.broadcast_ips,
                ports=self.config.ports,
                repeat=3
            )
            results.append(res)

        any_success = any(r.get("success", False) for r in results) if results else True
        response: Dict[str, Any] = {
            "mode": "local",
            "success": any_success,
            "wol_sent": any_success,
            "target_mac": self.config.primary_mac,
            "results": results,
            "target_host": self.config.target_lan_ip,
            "awake": False,
            "host_awake": False,
            "elapsed_sec": 0.0
        }

        if wait and self.config.target_lan_ip:
            start_t = time.time()
            awake = wait_for_host(self.config.target_lan_ip, timeout_sec=timeout_sec)
            response["awake"] = awake
            response["host_awake"] = awake
            response["elapsed_sec"] = time.time() - start_t

            if awake and self.config.auto_execute_post_wake:
                from core.post_wake_executor import execute_post_wake_actions
                post_res = execute_post_wake_actions(self.config.post_wake_actions)
                response["post_wake"] = post_res

        return response

    def wake_via_relay(
        self,
        relay_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        wait: bool = False,
        timeout_sec: float = 60.0
    ) -> Dict[str, Any]:
        """
        Sends wake request to the Always-On Device Relay over Tailscale / LAN.
        Calls POST /api/wake or POST /api/wake-and-wait on the relay gateway.
        """
        url = (relay_url or self.config.always_on_relay_url).rstrip("/")
        token = auth_token or self.config.relay_auth_token

        endpoint = f"{url}/api/wake-and-wait" if wait else f"{url}/api/wake"
        payload = json.dumps({
            "mac_address": self.config.primary_mac,
            "secondary_mac": self.config.target_mac_ethernet,
            "broadcast_ip": self.config.subnet_broadcast,
            "target_ip": self.config.target_lan_ip,
            "timeout_sec": timeout_sec
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "X-Relay-Token": token
        }

        import urllib.request
        import urllib.error

        req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec + 5.0 if wait else 10.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {"success": True, "relay_url": url, "relay_response": data}
        except urllib.error.HTTPError as he:
            err_body = he.read().decode("utf-8", errors="replace")
            return {"success": False, "error": f"HTTP {he.code}: {he.reason}", "detail": err_body}
        except Exception as ex:
            return {"success": False, "error": f"Relay request failed: {ex}"}

    def check_target_status(self) -> Dict[str, Any]:
        """Checks whether the laptop target IP or Tailscale IP is currently responsive."""
        target_ip = self.config.target_lan_ip
        tailscale_ip = self.config.target_tailscale_ip

        is_lan_awake = ping_host(target_ip, timeout_sec=1.0) if target_ip else False
        is_ts_awake = ping_host(tailscale_ip, timeout_sec=1.0) if tailscale_ip else False

        from core.tailscale_manager import get_tailscale_status
        ts_status = get_tailscale_status()

        return {
            "target_lan_ip": target_ip,
            "lan_awake": is_lan_awake,
            "target_tailscale_ip": tailscale_ip,
            "tailscale_awake": is_ts_awake,
            "target_host": target_ip or tailscale_ip,
            "target_awake": is_lan_awake or is_ts_awake,
            "awake": is_lan_awake or is_ts_awake,
            "tailscale_installed": ts_status.get("installed", False),
            "tailscale_running": ts_status.get("running", False),
            "timestamp": time.time()
        }
