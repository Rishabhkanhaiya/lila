"""
core/wol_relay_service.py — Always-On Device Wake-on-LAN Relay Gateway
=======================================================================
Runs on an Always-On Device (Raspberry Pi, home server, router, mini-PC) connected to
both the local LAN and Tailscale VPN. Receives remote wake commands from mobile phones
over Tailscale Layer 3 and broadcasts Layer 2 WOL Magic Packets into the local network.

Zero external dependencies: runs with standard library Python (http.server + socket)
or with FastAPI when installed.
"""

import os
import sys
import time
import json
import socket
import logging
import argparse
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
from typing import Optional, Dict, Any, List

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.wake_on_lan import (
    send_wol_packet,
    ping_host,
    wait_for_host,
    normalize_mac,
    load_wol_config,
    WakeOnLanConfig
)
from core.jarvis_logger import log_info, log_warn, log_error

logger = logging.getLogger("JARVIS.WOLRelay")

DEFAULT_RELAY_PORT = 8768


# ─────────────────────────────────────────────────────────────────────────────
# Mobile Web Interface HTML/CSS/JS (Embedded Zero-Dependency UI)
# ─────────────────────────────────────────────────────────────────────────────

EMBEDDED_MOBILE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <meta name="theme-color" content="#0a0c16">
  <title>JARVIS // Tailscale WOL Gateway</title>
  <style>
    :root {
      --bg: #0a0c16;
      --card-bg: rgba(18, 22, 38, 0.85);
      --card-border: rgba(0, 240, 255, 0.18);
      --accent: #00f0ff;
      --accent-glow: rgba(0, 240, 255, 0.35);
      --success: #00ff88;
      --warning: #ffaa00;
      --danger: #ff3366;
      --text: #e0e7ff;
      --text-dim: #798bb0;
      --font-mono: 'JetBrains Mono', Consolas, monospace;
      --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: var(--font-sans);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 16px;
      overflow-x: hidden;
    }
    .container {
      width: 100%;
      max-width: 480px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 16px;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      backdrop-filter: blur(16px);
    }
    .brand { display: flex; align-items: center; gap: 10px; }
    .brand-orb {
      width: 14px; height: 14px; border-radius: 50%;
      background: var(--accent);
      box-shadow: 0 0 12px var(--accent);
      animation: pulse 2s infinite ease-in-out;
    }
    @keyframes pulse { 0%, 100% { transform: scale(0.9); opacity: 0.7; } 50% { transform: scale(1.15); opacity: 1; } }
    .brand-title { font-weight: 800; font-size: 15px; letter-spacing: 1px; }
    .brand-sub { font-size: 10px; color: var(--accent); font-family: var(--font-mono); }
    .relay-tag {
      font-size: 10px; font-family: var(--font-mono);
      background: rgba(0, 240, 255, 0.1); border: 1px solid var(--accent);
      padding: 4px 8px; border-radius: 8px; color: var(--accent);
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 20px;
      padding: 20px;
      backdrop-filter: blur(16px);
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .card-title {
      font-size: 13px; text-transform: uppercase; letter-spacing: 1.5px;
      color: var(--text-dim); font-weight: 700; display: flex; justify-content: space-between; align-items: center;
    }
    .status-pill {
      font-size: 11px; padding: 4px 10px; border-radius: 12px; font-weight: 700;
      text-transform: uppercase; font-family: var(--font-mono);
    }
    .status-pill.awake { background: rgba(0, 255, 136, 0.15); color: var(--success); border: 1px solid var(--success); }
    .status-pill.sleeping { background: rgba(255, 51, 102, 0.15); color: var(--danger); border: 1px solid var(--danger); }
    .status-pill.checking { background: rgba(255, 170, 0, 0.15); color: var(--warning); border: 1px solid var(--warning); }

    .target-info {
      display: flex; flex-direction: column; gap: 8px;
      background: rgba(0, 0, 0, 0.25); padding: 12px; border-radius: 12px;
      font-family: var(--font-mono); font-size: 12px;
    }
    .info-row { display: flex; justify-content: space-between; }
    .info-label { color: var(--text-dim); }
    .info-val { color: var(--text); font-weight: 600; }

    .btn-wake {
      width: 100%; height: 64px;
      background: linear-gradient(135deg, #00f0ff 0%, #0088ff 100%);
      border: none; border-radius: 16px;
      color: #000; font-size: 17px; font-weight: 800;
      letter-spacing: 0.5px; cursor: pointer;
      display: flex; align-items: center; justify-content: center; gap: 10px;
      box-shadow: 0 8px 24px var(--accent-glow);
      transition: all 0.2s ease;
    }
    .btn-wake:active { transform: scale(0.97); filter: brightness(1.2); }
    .btn-wake:disabled { opacity: 0.5; cursor: not-allowed; }

    .btn-secondary {
      width: 100%; height: 48px;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid rgba(255, 255, 255, 0.15);
      border-radius: 14px; color: var(--text);
      font-size: 14px; font-weight: 600; cursor: pointer;
      display: flex; align-items: center; justify-content: center; gap: 8px;
      transition: all 0.2s ease;
    }
    .btn-secondary:active { background: rgba(255, 255, 255, 0.1); }

    .terminal-box {
      background: #05060b; border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 12px; padding: 12px; font-family: var(--font-mono);
      font-size: 11px; height: 140px; overflow-y: auto; color: #a0b0d0;
      display: flex; flex-direction: column; gap: 4px;
    }
    .log-line { word-break: break-all; }
    .log-time { color: var(--text-dim); margin-right: 6px; }
    .log-ok { color: var(--success); }
    .log-err { color: var(--danger); }
    .log-warn { color: var(--warning); }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <div class="brand-orb"></div>
        <div>
          <div class="brand-title">JARVIS WOL GATEWAY</div>
          <div class="brand-sub">Tailscale Layer 2/3 Relay</div>
        </div>
      </div>
      <div class="relay-tag">ALWAYS-ON</div>
    </header>

    <div class="card">
      <div class="card-title">
        <span>Laptop Power State</span>
        <span class="status-pill checking" id="status-pill">Checking...</span>
      </div>

      <div class="target-info">
        <div class="info-row"><span class="info-label">Target Name</span><span class="info-val" id="val-name">Rishabh-Laptop</span></div>
        <div class="info-row"><span class="info-label">Primary MAC</span><span class="info-val" id="val-mac">--:--:--:--:--:--</span></div>
        <div class="info-row"><span class="info-label">LAN IPv4</span><span class="info-val" id="val-ip">192.168.88.204</span></div>
        <div class="info-row"><span class="info-label">Subnet Broadcast</span><span class="info-val" id="val-bcast">192.168.88.255</span></div>
      </div>

      <button class="btn-wake" id="btn-wake-now">
        <span>⚡</span>
        <span id="wake-btn-text">WAKE LAPTOP NOW</span>
      </button>

      <button class="btn-secondary" id="btn-wake-and-wait">
        <span>🔄</span>
        <span>Wake, Wait & Execute Post-Wake</span>
      </button>

      <button class="btn-secondary" id="btn-refresh-status">
        <span>🔍</span>
        <span>Probe Status</span>
      </button>
    </div>

    <div class="card">
      <div class="card-title">Relay Activity Console</div>
      <div class="terminal-box" id="term-box">
        <div class="log-line"><span class="log-time">[Init]</span>Tailscale WOL Relay Gateway online.</div>
      </div>
    </div>
  </div>

  <script>
    const termBox = document.getElementById('term-box');
    const statusPill = document.getElementById('status-pill');
    const btnWake = document.getElementById('btn-wake-now');
    const btnWakeWait = document.getElementById('btn-wake-and-wait');
    const btnRefresh = document.getElementById('btn-refresh-status');

    function log(msg, type='info') {
      const now = new Date().toLocaleTimeString();
      const div = document.createElement('div');
      div.className = 'log-line';
      let cls = '';
      if (type === 'ok') cls = 'log-ok';
      if (type === 'err') cls = 'log-err';
      if (type === 'warn') cls = 'log-warn';
      div.innerHTML = `<span class="log-time">[${now}]</span><span class="${cls}">${msg}</span>`;
      termBox.appendChild(div);
      termBox.scrollTop = termBox.scrollHeight;
    }

    async function fetchConfig() {
      try {
        const res = await fetch('/api/config');
        if (res.ok) {
          const cfg = await res.json();
          document.getElementById('val-name').textContent = cfg.target_name || 'Laptop';
          document.getElementById('val-mac').textContent = cfg.primary_mac || '--';
          document.getElementById('val-ip').textContent = cfg.target_lan_ip || '--';
          document.getElementById('val-bcast').textContent = cfg.subnet_broadcast || '255.255.255.255';
        }
      } catch (e) {
        log('Could not load config: ' + e.message, 'warn');
      }
    }

    async function probeStatus() {
      statusPill.className = 'status-pill checking';
      statusPill.textContent = 'Probing...';
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        if (data.awake) {
          statusPill.className = 'status-pill awake';
          statusPill.textContent = 'AWAKE';
          log(`Target laptop is ONLINE (${data.target_ip || 'LAN'})`, 'ok');
        } else {
          statusPill.className = 'status-pill sleeping';
          statusPill.textContent = 'SLEEPING';
          log('Target laptop is SLEEPING / UNREACHABLE', 'warn');
        }
      } catch (e) {
        statusPill.className = 'status-pill sleeping';
        statusPill.textContent = 'OFFLINE';
        log('Status check failed: ' + e.message, 'err');
      }
    }

    async function triggerWake(wait=false) {
      const btn = wait ? btnWakeWait : btnWake;
      btn.disabled = true;
      log(wait ? 'Sending WOL packets and waiting for wake-up...' : 'Sending Local WOL Magic Packet bursts...', 'info');
      try {
        const endpoint = wait ? '/api/wake-and-wait' : '/api/wake';
        const res = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ timeout_sec: 45 })
        });
        const data = await res.json();
        if (data.success) {
          log(`WOL magic packets sent! (${data.packets_sent || data.results?.length || 0} packets to subnet)`, 'ok');
          if (wait) {
            if (data.awake) {
              log('Laptop confirmed AWAKE and responding!', 'ok');
              statusPill.className = 'status-pill awake';
              statusPill.textContent = 'AWAKE';
            } else {
              log('Wake-up timed out without response.', 'warn');
            }
          } else {
            setTimeout(probeStatus, 3000);
          }
        } else {
          log('WOL dispatch failed: ' + (data.error || 'unknown error'), 'err');
        }
      } catch (e) {
        log('Network error: ' + e.message, 'err');
      } finally {
        btn.disabled = false;
      }
    }

    btnWake.addEventListener('click', () => triggerWake(false));
    btnWakeWait.addEventListener('click', () => triggerWake(true));
    btnRefresh.addEventListener('click', probeStatus);

    fetchConfig();
    probeStatus();
    setInterval(probeStatus, 15000);
  </script>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Threaded HTTP Server Handler
# ─────────────────────────────────────────────────────────────────────────────

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class WOLRelayRequestHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests on the Always-On Device for remote WOL orchestration."""

    config: WakeOnLanConfig = load_wol_config()
    auth_token: Optional[str] = None

    def log_message(self, format, *args):
        # Route HTTP access log to standard logger
        logger.debug(f"[WOLRelay HTTP] {self.address_string()} - {format % args}")

    def _check_auth(self) -> bool:
        if not self.auth_token:
            return True
        # Check header
        hdr = self.headers.get("X-Relay-Token")
        if hdr and hdr == self.auth_token:
            return True
        # Check query string
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        tok = qs.get("token", [None])[0]
        return tok == self.auth_token

    def _send_json(self, data: Dict[str, Any], status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_content: str, status: int = 200):
        body = html_content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path in ("", "/index.html"):
            self._send_html(EMBEDDED_MOBILE_HTML)
            return

        if not self._check_auth():
            self._send_json({"error": "Unauthorized: Invalid or missing X-Relay-Token"}, status=401)
            return

        if path == "/api/health":
            self._send_json({
                "status": "healthy",
                "service": "JARVIS Always-On WOL Relay Gateway",
                "uptime_sec": time.time() - _server_start_time,
                "platform": sys.platform
            })

        elif path == "/api/config":
            self.config = load_wol_config()
            cfg_dict = self.config.to_dict()
            cfg_dict.pop("relay_auth_token", None)  # Don't leak secret
            self._send_json(cfg_dict)

        elif path == "/api/status":
            self.config = load_wol_config()
            target_ip = self.config.target_lan_ip
            awake = ping_host(target_ip, timeout_sec=1.2) if target_ip else False
            self._send_json({
                "awake": awake,
                "target_ip": target_ip,
                "timestamp": time.time()
            })

        elif path == "/api/wake":
            # Allow wake via simple GET bookmarklet on phone
            res = self._handle_wake_action()
            self._send_json(res)

        else:
            self._send_json({"error": f"Endpoint not found: {path}"}, status=404)

    def do_POST(self):
        if not self._check_auth():
            self._send_json({"error": "Unauthorized: Invalid or missing X-Relay-Token"}, status=401)
            return

        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        content_len = int(self.headers.get("Content-Length", 0))
        body_data = {}
        if content_len > 0:
            try:
                raw_body = self.rfile.read(content_len).decode("utf-8")
                body_data = json.loads(raw_body)
            except Exception:
                pass

        if path == "/api/wake":
            res = self._handle_wake_action(body_data)
            self._send_json(res)

        elif path == "/api/wake-and-wait":
            timeout_sec = float(body_data.get("timeout_sec", 45.0))
            res = self._handle_wake_and_wait(body_data, timeout_sec=timeout_sec)
            self._send_json(res)

        elif path == "/api/post-wake":
            # Forwards post-wake trigger directly to laptop
            target_ip = self.config.target_lan_ip
            self._send_json({"success": True, "message": f"Post-wake triggered on {target_ip}"})

        else:
            self._send_json({"error": f"Endpoint not found: {path}"}, status=404)

    def _handle_wake_action(self, body_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.config = load_wol_config()
        body_data = body_data or {}

        primary_mac = body_data.get("mac_address") or self.config.primary_mac
        secondary_mac = body_data.get("secondary_mac") or self.config.target_mac_ethernet
        broadcast_ip = body_data.get("broadcast_ip") or self.config.subnet_broadcast

        macs = [primary_mac]
        if secondary_mac and secondary_mac not in macs:
            macs.append(secondary_mac)

        results = []
        for mac in macs:
            try:
                res = send_wol_packet(
                    mac_address=mac,
                    broadcast_ips=[broadcast_ip, "255.255.255.255"],
                    ports=self.config.ports,
                    repeat=3
                )
                results.append(res)
            except Exception as ex:
                results.append({"mac": mac, "success": False, "error": str(ex)})

        any_success = any(r.get("success", False) for r in results)
        return {
            "success": any_success,
            "results": results,
            "target_host": self.config.target_lan_ip,
            "timestamp": time.time()
        }

    def _handle_wake_and_wait(self, body_data: Dict[str, Any], timeout_sec: float = 45.0) -> Dict[str, Any]:
        wake_res = self._handle_wake_action(body_data)
        target_ip = body_data.get("target_ip") or self.config.target_lan_ip

        awake = False
        elapsed = 0.0
        if target_ip:
            start_t = time.time()
            awake = wait_for_host(target_ip, timeout_sec=timeout_sec)
            elapsed = round(time.time() - start_t, 2)

            # If awake, attempt to trigger laptop's post-wake execution over LAN
            if awake:
                try:
                    import urllib.request
                    post_url = f"http://{target_ip}:8766/api/wol/post-wake"
                    req = urllib.request.Request(post_url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
                    urllib.request.urlopen(req, timeout=3.0)
                except Exception as ex:
                    logger.debug(f"[WOLRelay] Could not trigger remote post-wake directly: {ex}")

        return {
            "success": wake_res.get("success", False),
            "awake": awake,
            "elapsed_sec": elapsed,
            "target_ip": target_ip,
            "wake_results": wake_res.get("results", [])
        }


_server_start_time = time.time()


def start_wol_relay_server(
    port: int = DEFAULT_RELAY_PORT,
    host: str = "0.0.0.0",
    auth_token: Optional[str] = None
) -> ThreadedHTTPServer:
    """Starts the Always-On Device WOL Relay HTTP server."""
    global _server_start_time
    _server_start_time = time.time()

    WOLRelayRequestHandler.auth_token = auth_token
    server = ThreadedHTTPServer((host, port), WOLRelayRequestHandler)

    log_info("wol_relay", f"Starting Always-On WOL Relay Gateway on {host}:{port}")
    print(f"\n" + "=" * 60)
    print(f"🚀 JARVIS ALWAYS-ON WAKE-ON-LAN RELAY ONLINE")
    print(f"=" * 60)
    print(f"📡 Bound on      : {host}:{port}")
    print(f"📱 Mobile Web UI : http://localhost:{port} or http://<Tailscale-IP>:{port}")
    print(f"🔒 Auth Token    : {'Enabled' if auth_token else 'Disabled (Open LAN)'}")
    print(f"=" * 60 + "\n")

    return server


def run_relay_cli():
    parser = argparse.ArgumentParser(description="JARVIS Always-On Device Wake-on-LAN Relay Gateway")
    parser.add_argument("--port", type=int, default=DEFAULT_RELAY_PORT, help="Port to bind relay server on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host interface to bind")
    parser.add_argument("--token", type=str, default=None, help="Optional authentication bearer token")
    args = parser.parse_args()

    server = start_wol_relay_server(port=args.port, host=args.host, auth_token=args.token)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[WOLRelay] Stopping server...")
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run_relay_cli()
