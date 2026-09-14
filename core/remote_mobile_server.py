"""
core/remote_mobile_server.py — Dedicated Zero-ADB Remote Mobile Companion & Control Hub
========================================================================================
Empowers Rishabh to control his laptop, Jarvis, and Google Antigravity entirely from his
mobile phone over local Wi-Fi or secure internet tunnels without USB cables or ADB.

Key Capabilities:
  1. Bidirectional Real-time Voice Link:
     - Uplink: Mobile phone microphone (16kHz PCM) -> Gemini Live / Jarvis VAD.
     - Downlink: Gemini Live / Lila voice (24kHz PCM) -> Mobile phone speaker in real-time.
  2. Live Desktop Screen Mirror & On-Demand Snapshots:
     - Instant in-memory screen capture via MSS / PIL (JPEG/WebP).
  3. Real-Time Computer Telemetry:
     - CPU %, RAM %, Battery %, and Active Window title streamed over WebSocket.
  4. Far-Away Google Antigravity Remote Dispatch:
     - Dispatches tasks directly to core/antigravity_agent.py, focuses Antigravity window,
       injects engineered prompts, and returns screenshots of execution.
  5. Zero-Config Discovery:
     - Auto-detects LAN Wi-Fi IP, prints scannable ASCII QR code in terminal, and generates QR PNG.
"""

import os
import sys
import io
import time
import json
import base64
import socket
import asyncio
import logging
import threading
from pathlib import Path
from typing import Optional, Set, Dict, Any, List

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import psutil
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import Response, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

try:
    import qrcode
    HAS_QRCODE = True
except ImportError:
    qrcode = None
    HAS_QRCODE = False

try:
    import pygetwindow as gw
    HAS_GW = True
except ImportError:
    gw = None
    HAS_GW = False

from core.jarvis_logger import log_info, log_warn, log_error

logger = logging.getLogger("JARVIS.RemoteMobile")

# Directory resolution
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_REMOTE_DIR = PROJECT_ROOT / "web_remote"
WEB_REMOTE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_PORT = 8766

app = FastAPI(title="Lila Remote Companion & Control Studio", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ─────────────────────────────────────────────────────────────────────────────
# Network & IP Utilities
# ─────────────────────────────────────────────────────────────────────────────

def get_lan_ip() -> str:
    """Detects local LAN IPv4 address (e.g. 192.168.x.x)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def ensure_ssl_certificates(lan_ip: str) -> tuple[str, str]:
    """Generates self-signed SSL certificates for HTTPS so mobile browsers grant microphone permissions."""
    cert_dir = PROJECT_ROOT / "certs"
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / "mobile_cert.pem"
    key_path = cert_dir / "mobile_key.pem"

    if cert_path.exists() and key_path.exists():
        return str(cert_path), str(key_path)

    import datetime, ipaddress
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(65537, 2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Lila Remote Studio')])
    san_list = [
        x509.IPAddress(ipaddress.IPv4Address('127.0.0.1')),
        x509.DNSName('localhost')
    ]
    try:
        if lan_ip and lan_ip != "127.0.0.1":
            san_list.append(x509.IPAddress(ipaddress.IPv4Address(lan_ip)))
    except Exception:
        pass

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
        .sign(key, hashes.SHA256())
    )

    with open(key_path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    return str(cert_path), str(key_path)


def get_remote_urls(port: int = DEFAULT_PORT) -> Dict[str, str]:
    """Returns local LAN, Tailscale VPN, and loopback URLs for both HTTP and HTTPS."""
    lan_ip = get_lan_ip()
    ssl_port = port + 1
    urls = {
        "lan": f"http://{lan_ip}:{port}",
        "local": f"http://127.0.0.1:{port}",
        "https_lan": f"https://{lan_ip}:{ssl_port}",
        "https_local": f"https://127.0.0.1:{ssl_port}",
        "lan_ip": lan_ip,
        "port": str(port),
        "ssl_port": str(ssl_port),
        "tailscale": None,
        "tailscale_ip": None,
        "https_tailscale": None
    }

    try:
        from core.tailscale_manager import get_tailscale_ip
        ts_ip = get_tailscale_ip()
        if ts_ip:
            urls["tailscale"] = f"http://{ts_ip}:{port}"
            urls["tailscale_ip"] = ts_ip
            urls["https_tailscale"] = f"https://{ts_ip}:{ssl_port}"
    except Exception:
        pass

    return urls


def generate_qr_png_bytes(url: str) -> bytes:
    """Generates PNG image bytes of QR code pointing to specified URL."""
    if not HAS_QRCODE or qrcode is None:
        return b""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#00ffcc", back_color="#0d0d15")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def print_ascii_qr(https_url: str, http_url: str = ""):
    """Prints a clean ASCII QR code directly to the terminal pointing to the HTTPS URL for mic support."""
    if not HAS_QRCODE or qrcode is None:
        print(f"\n[RemoteMobile] Open on mobile (Microphone Enabled): {https_url}\n")
        return
    try:
        qr = qrcode.QRCode(border=1)
        qr.add_data(https_url)
        qr.make(fit=True)
        print("\n" + "="*56)
        print("📱 SCAN WITH MOBILE CAMERA (HTTPS - MICROPHONE READY):")
        print(f"   🔒 HTTPS: {https_url}")
        if http_url:
            print(f"   🌐 HTTP:  {http_url}")
        print("="*56)
        qr.print_ascii(invert=True)
        print("="*56)
        print("💡 NOTE FOR MOBILE: Tap 'Advanced' -> 'Proceed' to allow mic.")
        print("="*56 + "\n")
    except Exception as e:
        print(f"\n[RemoteMobile] Open on mobile: {https_url} (QR print error: {e})\n")


# ─────────────────────────────────────────────────────────────────────────────
# Active Connections & Telemetry State
# ─────────────────────────────────────────────────────────────────────────────

class RemoteAudioClient:
    def __init__(self, ws: WebSocket, loop: asyncio.AbstractEventLoop, client_ip: str):
        self.ws = ws
        self.loop = loop
        self.client_ip = client_ip
        # Generous queue size (1200 chunks ~ 12-24 seconds of audio) prevents any WiFi buffer overflow drops
        self.queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=1200)
        self.active = True

    def _enqueue_chunk(self, chunk: Optional[bytes]):
        if not self.active:
            return
        if chunk == b"__INTERRUPT__":
            # Purge any old audio chunks currently waiting in queue
            while not self.queue.empty():
                try:
                    self.queue.get_nowait()
                except Exception:
                    break
        try:
            self.queue.put_nowait(chunk)
        except asyncio.QueueFull:
            # Drop oldest only if severely backlogged
            try:
                self.queue.get_nowait()
            except Exception:
                pass
            try:
                self.queue.put_nowait(chunk)
            except Exception:
                pass

    def push_chunk(self, chunk: Optional[bytes]):
        if not self.active or self.loop.is_closed():
            return
        try:
            self.loop.call_soon_threadsafe(self._enqueue_chunk, chunk)
        except Exception:
            pass

    async def run_sender(self):
        try:
            while self.active:
                chunk = await self.queue.get()
                if chunk is None or not self.active:
                    break

                if chunk == b"__INTERRUPT__":
                    # Send interruption signal immediately to mobile
                    await self.ws.send_bytes(b"__INTERRUPT__")
                    continue

                # Coalesce small adjacent chunks up to 3840 bytes (~80ms of 24kHz audio)
                # This eliminates packet overhead and prevents network-level stuttering over WiFi
                buf = bytearray(chunk)
                while not self.queue.empty() and len(buf) < 3840:
                    try:
                        next_c = self.queue.get_nowait()
                        if next_c is None:
                            self.active = False
                            break
                        if next_c == b"__INTERRUPT__":
                            # Send current buffer first, then send interrupt next
                            await self.ws.send_bytes(bytes(buf))
                            buf = bytearray()
                            await self.ws.send_bytes(b"__INTERRUPT__")
                            break
                        buf.extend(next_c)
                    except asyncio.QueueEmpty:
                        break

                if buf:
                    await self.ws.send_bytes(bytes(buf))
        except Exception:
            pass
        finally:
            self.active = False


class RemoteControlClient:
    def __init__(self, ws: WebSocket, loop: asyncio.AbstractEventLoop, client_ip: str):
        self.ws = ws
        self.loop = loop
        self.client_ip = client_ip
        self.queue: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=40)
        self.active = True

    def _enqueue_text(self, text: Optional[str]):
        if not self.active:
            return
        try:
            self.queue.put_nowait(text)
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()
            except Exception:
                pass
            try:
                self.queue.put_nowait(text)
            except Exception:
                pass

    def push_text(self, text: Optional[str]):
        if not self.active or self.loop.is_closed():
            return
        try:
            self.loop.call_soon_threadsafe(self._enqueue_text, text)
        except Exception:
            pass

    async def run_sender(self):
        try:
            while self.active:
                text = await self.queue.get()
                if text is None or not self.active:
                    break
                await self.ws.send_text(text)
        except Exception:
            pass
        finally:
            self.active = False


_audio_clients: List[RemoteAudioClient] = []
_audio_clients_lock = threading.Lock()

_control_clients: List[RemoteControlClient] = []
_control_clients_lock = threading.Lock()


def get_system_telemetry() -> Dict[str, Any]:
    """Gathers live hardware, active window, and system metrics."""
    try:
        cpu = psutil.cpu_percent(interval=None)
    except Exception:
        cpu = 0.0

    try:
        ram = psutil.virtual_memory().percent
    except Exception:
        ram = 0.0

    battery_pct = 100
    battery_plugged = True
    try:
        b = psutil.sensors_battery()
        if b:
            battery_pct = int(b.percent)
            battery_plugged = bool(b.power_plugged)
    except Exception:
        pass

    active_win_title = "Desktop"
    if HAS_GW and gw is not None:
        try:
            w = gw.getActiveWindow()
            if w and w.title:
                active_win_title = w.title.strip()
        except Exception:
            pass

    return {
        "cpu": cpu,
        "ram": ram,
        "battery": battery_pct,
        "plugged": battery_plugged,
        "active_window": active_win_title,
        "timestamp": time.time()
    }


# ─────────────────────────────────────────────────────────────────────────────
# Audio Downlink Hook (PC Speaker/Gemini Live Voice -> Mobile Phones)
# ─────────────────────────────────────────────────────────────────────────────

def _on_modern_audio_chunk(chunk: bytes):
    """Callback invoked when Gemini Live or Persistent Voice outputs a 24kHz PCM chunk."""
    if not chunk or not _audio_clients:
        return

    with _audio_clients_lock:
        clients = list(_audio_clients)

    for client in clients:
        client.push_chunk(chunk)


# ─────────────────────────────────────────────────────────────────────────────
# Companion State Listener (state_bridge -> Mobile Control Clients)
# ─────────────────────────────────────────────────────────────────────────────

def _on_companion_state_update(state: dict):
    """Callback invoked when core/state_bridge broadcasts an update."""
    if not _control_clients:
        return

    payload = {
        "type": "state_update",
        **state
    }
    msg_str = json.dumps(payload)

    with _control_clients_lock:
        clients = list(_control_clients)

    for client in clients:
        client.push_text(msg_str)


def broadcast_to_mobile(payload: dict):
    """Broadcasts arbitrary JSON payload to all connected mobile control clients."""
    try:
        msg_str = json.dumps(payload)
        with _control_clients_lock:
            clients = list(_control_clients)
        for client in clients:
            client.push_text(msg_str)
    except Exception as e:
        logger.debug(f"[RemoteMobile] broadcast_to_mobile error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Periodic Telemetry Broadcaster
# ─────────────────────────────────────────────────────────────────────────────

async def _telemetry_broadcast_loop():
    """Broadcasts system telemetry every 1.5 seconds to connected mobile devices."""
    while True:
        try:
            await asyncio.sleep(1.5)
            if _control_clients:
                telemetry = get_system_telemetry()
                payload = json.dumps({"type": "telemetry", "data": telemetry})
                with _control_clients_lock:
                    clients = list(_control_clients)
                for client in clients:
                    client.push_text(payload)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"[RemoteMobile] Telemetry loop error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI WebSocket Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/audio")
async def websocket_audio_endpoint(websocket: WebSocket):
    """
    Bidirectional streaming audio socket for mobile:
    - Inbound (Binary): 16kHz 16-bit mono PCM from mobile microphone -> feeds into Gemini Live.
    - Outbound (Binary): 24kHz 16-bit mono PCM from Lila voice -> plays through mobile phone speaker.
    """
    await websocket.accept()
    loop = asyncio.get_running_loop()
    client_ip = websocket.client.host if websocket.client else "unknown"
    client = RemoteAudioClient(websocket, loop, client_ip)

    # Automatically mute laptop audio when a mobile/remote device connects
    is_remote = client_ip not in ("127.0.0.1", "::1", "localhost")
    if is_remote:
        try:
            from core.modern_audio_worker import register_mobile_client
            register_mobile_client()
        except Exception:
            pass

    with _audio_clients_lock:
        _audio_clients.append(client)
    log_info("remote_mobile", f"Mobile audio link connected from {client_ip} (is_remote={is_remote})")

    sender_task = asyncio.create_task(client.run_sender())

    try:
        from live_voice import enqueue_remote_mic
    except Exception:
        enqueue_remote_mic = None

    try:
        while True:
            chunk = await websocket.receive_bytes()
            if chunk and enqueue_remote_mic:
                enqueue_remote_mic(chunk)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"[RemoteMobile] Audio websocket error from {client_ip}: {e}")
    finally:
        client.active = False
        client.push_chunk(None)
        sender_task.cancel()
        with _audio_clients_lock:
            if client in _audio_clients:
                _audio_clients.remove(client)

        if is_remote:
            try:
                from core.modern_audio_worker import unregister_mobile_client
                unregister_mobile_client()
            except Exception:
                pass
        log_info("remote_mobile", f"Mobile audio link disconnected from {client_ip}")


@app.websocket("/ws/control")
async def websocket_control_endpoint(websocket: WebSocket):
    """
    JSON state and remote action control socket for mobile:
    - Streams live telemetry, captions, speaking state, and mood.
    - Receives mobile actions: Antigravity execution, screenshot capture, chat commands.
    """
    await websocket.accept()
    loop = asyncio.get_running_loop()
    client_ip = websocket.client.host if websocket.client else "unknown"
    client = RemoteControlClient(websocket, loop, client_ip)

    with _control_clients_lock:
        _control_clients.append(client)
    log_info("remote_mobile", f"Mobile control client connected from {client_ip}")

    # Immediately send initial state snapshot & telemetry
    try:
        from core.state_bridge import get_current_state
        init_state = get_current_state()
    except Exception:
        init_state = {"mood": "idle", "speaking": False}

    try:
        from core.modern_audio_worker import is_laptop_audio_muted
        laptop_muted = is_laptop_audio_muted()
    except Exception:
        laptop_muted = False

    telemetry = get_system_telemetry()
    client.push_text(json.dumps({
        "type": "init",
        "state": init_state,
        "telemetry": telemetry,
        "laptop_muted": laptop_muted
    }))

    sender_task = asyncio.create_task(client.run_sender())

    try:
        while True:
            msg_text = await websocket.receive_text()
            try:
                data = json.loads(msg_text)
            except Exception:
                continue

            action = data.get("action", "")

            # 1. Instant Desktop Screenshot
            if action == "screenshot":
                try:
                    from core.eyes import capture_screen_jpeg_bytes
                    img_bytes = capture_screen_jpeg_bytes(quality=80, max_dim=1920)
                    if img_bytes:
                        b64 = base64.b64encode(img_bytes).decode("ascii")
                        client.push_text(json.dumps({
                            "type": "screenshot",
                            "image": f"data:image/jpeg;base64,{b64}",
                            "timestamp": time.time()
                        }))
                except Exception as ex:
                    client.push_text(json.dumps({
                        "type": "error",
                        "message": f"Screenshot failed: {ex}"
                    }))

            # 2. Remote Antigravity Task Execution
            elif action == "antigravity_execute":
                task_desc = data.get("task", "").strip()
                proj_name = data.get("project", None)
                if task_desc:
                    client.push_text(json.dumps({
                        "type": "notification",
                        "level": "info",
                        "message": f"Dispatching to Google Antigravity: {task_desc}..."
                    }))

                    def _run_agy():
                        try:
                            from core.antigravity_agent import execute_antigravity_task
                            res = execute_antigravity_task(task_description=task_desc, project_name=proj_name)
                            time.sleep(1.0)
                            from core.eyes import capture_screen_jpeg_bytes
                            snap_bytes = capture_screen_jpeg_bytes(quality=80, max_dim=1920)
                            snap_b64 = base64.b64encode(snap_bytes).decode("ascii") if snap_bytes else None

                            client.push_text(json.dumps({
                                "type": "antigravity_result",
                                "task": task_desc,
                                "result": res,
                                "screenshot": f"data:image/jpeg;base64,{snap_b64}" if snap_b64 else None
                            }))
                        except Exception as aerr:
                            client.push_text(json.dumps({
                                "type": "error",
                                "message": f"Antigravity error: {aerr}"
                            }))
                    threading.Thread(target=_run_agy, daemon=True, name="RemoteAGYTask").start()

            # 3. Chat Command to Jarvis/Lila
            elif action == "chat_command":
                text = data.get("text", "").strip()
                if text:
                    try:
                        from core.state_bridge import _handle_chat_command
                        _handle_chat_command(text)
                        client.push_text(json.dumps({
                            "type": "chat_ack",
                            "text": text
                        }))
                    except Exception as ex:
                        client.push_text(json.dumps({
                            "type": "error",
                            "message": f"Chat command failed: {ex}"
                        }))

            # 4. Microphone Mute Toggle
            elif action == "toggle_mic":
                try:
                    from core.state_bridge import _handle_action
                    res = _handle_action("toggle_mic")
                    client.push_text(json.dumps({
                        "type": "action_result",
                        "action": "toggle_mic",
                        **res
                    }))
                except Exception as ex:
                    client.push_text(json.dumps({"type": "error", "message": str(ex)}))

            # 5. Audio Profile Switch (PC, Rockerz, Earbuds)
            elif action == "set_audio_profile":
                prof = data.get("profile", "rockerz")
                try:
                    from core.state_bridge import _handle_action
                    res = _handle_action(f"set_audio_profile:{prof}")
                    client.push_text(json.dumps({
                        "type": "action_result",
                        "action": "set_audio_profile",
                        **res
                    }))
                except Exception as ex:
                    client.push_text(json.dumps({"type": "error", "message": str(ex)}))

            # 6. Toggle Laptop Sound Output (control whether laptop soundcard speaks)
            elif action == "toggle_laptop_sound":
                muted = data.get("muted", None)
                try:
                    from core.modern_audio_worker import set_laptop_audio_muted, is_laptop_audio_muted
                    if muted is None:
                        new_state = not is_laptop_audio_muted()
                    else:
                        new_state = bool(muted)
                    set_laptop_audio_muted(new_state, user_explicit=True)
                    # Broadcast to all connected mobile clients
                    broadcast_to_mobile({
                        "type": "laptop_sound_state",
                        "action": "toggle_laptop_sound",
                        "laptop_muted": new_state
                    })
                except Exception as ex:
                    client.push_text(json.dumps({"type": "error", "message": str(ex)}))

            # 7. Launch Lila 3D UI Desktop Shortcut & Self-Kill Non-UI Process
            elif action in ("launch_ui_shortcut", "open_ui", "open_lila_ui"):
                try:
                    from core.lila_companion_launcher import launch_ui_shortcut_and_self_kill
                    res = launch_ui_shortcut_and_self_kill()
                    client.push_text(json.dumps({
                        "type": "action_result",
                        "action": action,
                        **res
                    }))
                except Exception as ex:
                    client.push_text(json.dumps({"type": "error", "message": str(ex)}))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"[RemoteMobile] Control websocket error: {e}")
    finally:
        client.active = False
        client.push_text(None)
        sender_task.cancel()
        with _control_clients_lock:
            if client in _control_clients:
                _control_clients.remove(client)
        log_info("remote_mobile", "Mobile control client disconnected")


# ─────────────────────────────────────────────────────────────────────────────
# REST API Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/info")
async def api_info():
    """Returns server and network status."""
    urls = get_remote_urls(DEFAULT_PORT)
    try:
        from core.state_bridge import get_current_state
        state = get_current_state()
    except Exception:
        state = {}
    return {
        "status": "online",
        "service": "Lila Zero-ADB Remote Mobile Studio",
        "version": "2.0.0",
        "lan_ip": urls["lan_ip"],
        "lan_url": urls["lan"],
        "audio_profile": state.get("audio_profile", "rockerz"),
        "mic_muted": state.get("mic_muted", False),
        "speaking": state.get("speaking", False),
        "connected_audio_clients": len(_audio_clients),
        "connected_control_clients": len(_control_clients)
    }


@app.get("/api/telemetry")
async def api_telemetry():
    """Returns real-time laptop hardware and active window status."""
    return get_system_telemetry()


@app.get("/api/screenshot")
async def api_screenshot(quality: int = 80, max_dim: int = 1920):
    """Returns instant desktop screenshot image directly as JPEG, supporting optional zoom resolution and quality."""
    try:
        from core.eyes import capture_screen_jpeg_bytes
        q = max(30, min(100, int(quality)))
        d = max(640, min(3840, int(max_dim)))
        img_bytes = capture_screen_jpeg_bytes(quality=q, max_dim=d)
        if not img_bytes:
            raise HTTPException(status_code=500, detail="Failed to capture screen")
        return Response(content=img_bytes, media_type="image/jpeg")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/qr")
async def api_qr():
    """Returns PNG QR code image pointing to the mobile web app."""
    lan_url = get_remote_urls(DEFAULT_PORT)["lan"]
    png_bytes = generate_qr_png_bytes(lan_url)
    if not png_bytes:
        raise HTTPException(status_code=500, detail="QR code generator unavailable")
    return Response(content=png_bytes, media_type="image/png")


@app.get("/api/antigravity/projects")
async def api_antigravity_projects():
    """Returns indexed projects available for Antigravity pair-programming."""
    try:
        from core.antigravity_agent import list_antigravity_projects
        projects = list_antigravity_projects()
        return {"projects": projects}
    except Exception as e:
        return {"projects": [], "error": str(e)}


@app.post("/api/antigravity/execute")
async def api_antigravity_execute(request: Request):
    """Executes a prompt directly in Google Antigravity."""
    try:
        body = await request.json()
        task = body.get("task", "").strip()
        project = body.get("project", None)
        if not task:
            raise HTTPException(status_code=400, detail="Task description required")

        from core.antigravity_agent import execute_antigravity_task
        res = execute_antigravity_task(task_description=task, project_name=project)
        return {"status": "ok", "result": res, "task": task, "project": project}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/antigravity/status")
async def api_antigravity_status(project: Optional[str] = None):
    """Returns Antigravity runtime metrics, active task, and lifecycle status."""
    try:
        from core.antigravity_agent import get_antigravity_status
        status = get_antigravity_status(project or "jarvis_project")
        return {"status": "ok", "data": status}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/antigravity/actions")
async def api_antigravity_actions(project: Optional[str] = None, max_steps: int = 50):
    """Returns completed actions, tool calls, and files modified by Antigravity."""
    try:
        from core.antigravity_agent import analyze_antigravity_actions
        analysis = analyze_antigravity_actions(project_name=project or "jarvis_project", max_steps=max_steps)
        return {"status": "ok", "data": analysis}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/antigravity/artifact")
async def api_antigravity_artifact(artifact: str = "walkthrough", project: Optional[str] = None):
    """Retrieves walkthrough.md or implementation_plan.md artifact content."""
    try:
        from core.antigravity_agent import read_antigravity_artifact
        content = read_antigravity_artifact(artifact_name=artifact, project_name=project or "jarvis_project")
        return {"status": "ok", "artifact": artifact, "content": content}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Wake-on-LAN (WOL) & Power Management REST Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/wol/info")
async def api_wol_info():
    """Returns network adapters, MAC addresses, Tailscale status, and WOL config."""
    try:
        from core.wake_on_lan import get_local_adapters_info, load_wol_config
        from core.tailscale_manager import get_tailscale_status, get_tailscale_ip

        cfg = load_wol_config()
        adapters = get_local_adapters_info()
        ts_status = get_tailscale_status()

        return {
            "config": cfg.to_dict(),
            "adapters": adapters,
            "tailscale": ts_status
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/wol/save-config")
async def api_wol_save_config(request: Request):
    """Saves updated Wake-on-LAN configuration."""
    try:
        data = await request.json()
        from core.wake_on_lan import load_wol_config, save_wol_config, WakeOnLanConfig
        current_cfg = load_wol_config()
        cfg_dict = current_cfg.to_dict()
        cfg_dict.update(data)
        updated_cfg = WakeOnLanConfig.from_dict(cfg_dict)
        save_wol_config(updated_cfg)
        return {"status": "ok", "config": updated_cfg.to_dict()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/wol/wake")
async def api_wol_wake(request: Request):
    """Triggers Wake-on-LAN packets locally or relays them."""
    try:
        body = await request.json() if request.headers.get("content-length") else {}
        mac = body.get("mac")
        wait = body.get("wait", False)
        timeout = float(body.get("timeout_sec", 45.0))

        from core.wake_on_lan import WakeOnLanManager, send_wol_packet, load_wol_config
        manager = WakeOnLanManager()

        if mac:
            cfg = load_wol_config()
            res = send_wol_packet(mac_address=mac, broadcast_ips=cfg.broadcast_ips, ports=cfg.ports)
            return {"status": "ok", "mode": "direct_mac", "result": res}
        else:
            # Since request is received on this machine, host is already awake!
            # Send packet non-blocking without waiting 45s ping
            try:
                res = manager.wake_local(wait=False, timeout_sec=1.0)
            except Exception:
                res = {"status": "sent", "online": True}

            # Trigger Lila to immediately greet Rishabh aloud and open her 3D desktop companion UI
            def _wake_pipeline():
                try:
                    time.sleep(0.2)
                    from core.lila_companion_launcher import open_lila_companion_ui
                    target_model = os.environ.get("LILA_MODEL", "ana")
                    open_lila_companion_ui(model=target_model)

                    # Greet Rishabh aloud
                    from core.state_bridge import broadcast_state
                    msg = "Haan Rishabh! Main online hoon aur bilkul ready hoon! Batao kya kaam hai? ✨"
                    broadcast_state(mood="excited", caption=msg, speaking=True)
                    from core.voice import speak
                    speak("Haan Rishabh! Main online hoon aur bilkul ready hoon! Batao kya kaam hai?")
                except Exception as ex:
                    logger.debug(f"[RemoteMobile] Wake pipeline error: {ex}")
            threading.Thread(target=_wake_pipeline, daemon=True, name="WakePipelineThread").start()

            return {"status": "ok", "mode": "manager", "result": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/wol/status")
async def api_wol_status():
    """Probes status of target laptop and Tailscale reachability."""
    try:
        from core.wake_on_lan import WakeOnLanManager
        manager = WakeOnLanManager()
        return manager.check_target_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/wol/post-wake")
async def api_wol_post_wake():
    """Executes configured post-wake actions on this machine."""
    try:
        from core.post_wake_executor import execute_post_wake_actions
        res = execute_post_wake_actions(force=True)
        return {"status": "ok", "execution": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _put_laptop_to_sleep():
    """Puts the host machine immediately into sleep/standby mode."""
    import ctypes
    if sys.platform == "win32":
        try:
            ctypes.windll.PowrProf.SetSuspendState(0, 1, 0)
        except Exception:
            no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=no_win)
    else:
        subprocess.run(["systemctl", "suspend"])


@app.post("/api/power/sleep")
async def api_power_sleep():
    """Puts the laptop into low-power sleep mode from mobile."""
    try:
        def _deferred_sleep():
            time.sleep(1.0)
            _put_laptop_to_sleep()
        threading.Thread(target=_deferred_sleep, daemon=True, name="DeferredSleepThread").start()
        return {"status": "ok", "message": "Laptop is entering sleep mode..."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/lila/launch-ui")
async def api_lila_launch_ui():
    """Launches Lila Desktop Companion 3D UI shortcut and cleanly transitions non-UI instance."""
    try:
        from core.lila_companion_launcher import launch_ui_shortcut_and_self_kill
        res = launch_ui_shortcut_and_self_kill()
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# ─────────────────────────────────────────────────────────────────────────────
# Web UI Static & Index Route

# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
@app.get("/app")
@app.get("/chat")
@app.get("/ui")
@app.get("/m")
async def get_index():
    """Serves the mobile web companion app."""
    index_file = WEB_REMOTE_DIR / "index.html"
    if index_file.exists():
        return FileResponse(
            str(index_file),
            media_type="text/html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
    return HTMLResponse("<h2>Lila Remote Mobile Companion starting...</h2>")

# Mount static files for assets, CSS, JS
if WEB_REMOTE_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_REMOTE_DIR)), name="static")


# ─────────────────────────────────────────────────────────────────────────────
# Server Lifecycle & Background Runner
# ─────────────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup():
    # 1. Register audio hook with modern_audio_worker so Lila's speech streams to mobile
    try:
        from core.modern_audio_worker import register_remote_audio_hook
        register_remote_audio_hook(_on_modern_audio_chunk)
        logger.info("[RemoteMobile] Hooked into ModernAudioPlaybackWorker for live mobile streaming.")
    except Exception as e:
        logger.warning(f"[RemoteMobile] Could not register modern_audio_worker hook: {e}")

    # 2. Register state listener with state_bridge
    try:
        from core.state_bridge import register_state_listener
        register_state_listener(_on_companion_state_update)
        logger.info("[RemoteMobile] Hooked into StateBridge for real-time status broadcasting.")
    except Exception as e:
        logger.warning(f"[RemoteMobile] Could not register state_bridge listener: {e}")

    # 3. Ensure Gemini Live Native Voice Thread is online so Mobile Lila has identical Aoede voice
    try:
        from live_voice import get_active_live_voice, LiveVoiceThread
        if not get_active_live_voice():
            logger.info("[RemoteMobile] Starting Gemini Live Native Voice Thread for Mobile Lila...")
            live_voice = LiveVoiceThread()
            live_voice.start()
            logger.info("[RemoteMobile] Gemini Live Native Voice active for mobile link.")
    except Exception as e:
        logger.warning(f"[RemoteMobile] Live voice auto-start notice: {e}")

    # 4. Start background telemetry broadcaster
    asyncio.create_task(_telemetry_broadcast_loop())

    urls = get_remote_urls(DEFAULT_PORT)
    print_ascii_qr(urls["https_lan"], urls["lan"])
    logger.info(f"[RemoteMobile] Server running on {urls['https_lan']} (HTTP: {urls['lan']})")


@app.on_event("shutdown")
async def on_shutdown():
    try:
        from core.modern_audio_worker import unregister_remote_audio_hook
        unregister_remote_audio_hook(_on_modern_audio_chunk)
    except Exception:
        pass

    try:
        from core.state_bridge import unregister_state_listener
        unregister_state_listener(_on_companion_state_update)
    except Exception:
        pass


def run_server(port: int = DEFAULT_PORT):
    """Starts high-performance Uvicorn ASGI server on port 8766."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                logger.info(f"[RemoteMobile] Port {port} already active. Skipping duplicate Uvicorn runner.")
                return
    except Exception:
        pass
    logger.info(f"[RemoteMobile] Starting server on 0.0.0.0:{port}...")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    run_server()
