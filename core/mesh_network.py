"""
mesh_network.py  —  JARVIS Multi-Agent Mesh Network v1.0

Multiple JARVIS instances across all your devices — sharing one brain, one memory.
The PC is the MASTER (Prime) node. All others (phone, laptop, tablet) are SLAVE nodes.

Architecture:
  MeshServer (FastAPI WebSocket, port 8765) — runs on Master
    - HMAC-SHA256 authenticated registration
    - Heartbeat tracking for all connected nodes
    - Broadcasts: memory updates, events, commands

  MeshClient — runs on Slave nodes (connect to Master)
    - Auto-reconnects on disconnect
    - Receives routed tasks and broadcasts
    - Reports capabilities: {voice, camera, screen, sensors}

  Mesh Protocol (JSON over WebSocket):
    REGISTER:   {type, node_id, device_name, platform, capabilities, token}
    HEARTBEAT:  {type, node_id, timestamp}
    TASK:       {type, payload, target_node_id, task_id}
    BROADCAST:  {type, payload, sender_id}
    MEMORY:     {type, key, value, operation}  — syncs memory writes
    EVENT:      {type, event_data}             — syncs ProactiveEvents
    VOICE:      {type, text, speaker}          — relay speech to device

  TaskRouter:
    "send to my phone" → routes to node with platform=android/ios
    "run on laptop"    → routes to node with hostname matching
    "broadcast all"    → sends to every connected node

  MemorySyncer:
    Hooks into jarvis_memory.db writes → broadcasts to all mesh nodes

"""

import os
import json
import time
import hmac
import hashlib
import asyncio
import socket
import platform
import threading
import sqlite3
import aiosqlite
import base64
from cryptography.fernet import Fernet
import traceback
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any

from core.jarvis_logger import log_error, log_warn, log_info
from core.monitors.base_monitor import BaseMonitor

# ── Paths ─────────────────────────────────────────────────────────────────────
_BASE   = Path(__file__).parent.parent
MESH_DB = str(_BASE / "jarvis_memory.db")

# ── Config ─────────────────────────────────────────────────────────────────────
MESH_PORT   = 8765
MESH_SECRET = os.environ.get("MESH_SECRET", "jarvis_mesh_prime_2025")
MASTER_HOST = os.environ.get("MESH_MASTER_HOST", "0.0.0.0")  # Override for slave nodes

# ── Singleton ─────────────────────────────────────────────────────────────────
_mesh_instance = None
_mesh_lock     = threading.Lock()

# ── Optional FastAPI / WebSockets ─────────────────────────────────────────────
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    print("[MESH]: fastapi/uvicorn not available — server mode disabled")

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False
    print("[MESH]: websockets not available — client mode disabled")


# ── DB Init ───────────────────────────────────────────────────────────────────
def _init_mesh_tables():
    conn = sqlite3.connect(MESH_DB)
    cur  = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS mesh_nodes (
            node_id     TEXT PRIMARY KEY,
            device_name TEXT,
            platform    TEXT,
            hostname    TEXT,
            capabilities TEXT DEFAULT '[]',
            last_seen   TEXT DEFAULT (datetime('now','localtime')),
            status      TEXT DEFAULT 'offline',
            ip_address  TEXT
        );

        CREATE TABLE IF NOT EXISTS mesh_messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id   TEXT,
            target_id   TEXT,
            msg_type    TEXT,
            payload     TEXT,
            sent_at     TEXT DEFAULT (datetime('now','localtime')),
            delivered   INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS mesh_sync_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            operation   TEXT,
            key         TEXT,
            value_hash  TEXT,
            synced_at   TEXT DEFAULT (datetime('now','localtime'))
        );
    """)
    conn.commit()
    conn.close()


# ── Node dataclass ─────────────────────────────────────────────────────────────
@dataclass
class MeshNode:
    node_id:      str
    device_name:  str
    platform:     str       # windows / android / ios / linux / raspberry
    hostname:     str       = ""
    capabilities: List[str] = field(default_factory=list)
    ip_address:   str       = ""
    last_seen:    float     = field(default_factory=time.time)
    status:       str       = "online"
    websocket:    Any       = None  # WebSocket connection object

    @property
    def is_alive(self) -> bool:
        return time.time() - self.last_seen < 30  # 30s heartbeat window

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id, "device_name": self.device_name,
            "platform": self.platform, "hostname": self.hostname,
            "capabilities": self.capabilities, "status": self.status,
            "is_alive": self.is_alive
        }



# ── MN-3: E2EE Cipher ─────────────────────────────────────────────────────────
def _get_cipher() -> Fernet:
    h = hashlib.sha256(MESH_SECRET.encode()).digest()
    key = base64.urlsafe_b64encode(h)
    return Fernet(key)

def _encrypt_msg(payload_str: str) -> str:
    return _get_cipher().encrypt(payload_str.encode()).decode()

def _decrypt_msg(encrypted_str: str) -> str:
    return _get_cipher().decrypt(encrypted_str.encode()).decode()

# ── Auth ───────────────────────────────────────────────────────────────────────
def _generate_token(node_id: str) -> str:
    """Generate HMAC-SHA256 token for a node."""
    return hmac.new(
        MESH_SECRET.encode(), node_id.encode(), hashlib.sha256
    ).hexdigest()

def _verify_token(node_id: str, token: str) -> bool:
    expected = _generate_token(node_id)
    return hmac.compare_digest(expected, token)


# ── Task Router ────────────────────────────────────────────────────────────────
class TaskRouter:
    """
    Routes tasks to the correct node based on target specification.
    Supports: node_id, device_name, platform, or 'broadcast'.
    """

    def resolve_target(self, target: str, nodes: Dict[str, MeshNode]) -> List[str]:
        """Returns list of node_ids that match the target spec."""
        target = target.lower().strip()

        if target in ("all", "broadcast", "everyone"):
            return list(nodes.keys())

        matches = []
        for nid, node in nodes.items():
            if (target == nid.lower() or
                    target in node.device_name.lower() or
                    target == node.platform.lower() or
                    target in node.hostname.lower()):
                matches.append(nid)

        return matches

    def build_task_message(self, task_type: str, payload: Any,
                           sender_id: str = "prime") -> dict:
        return {
            "type":      "task",
            "task_type": task_type,
            "payload":   payload,
            "sender_id": sender_id,
            "task_id":   f"task_{int(time.time())}",
            "timestamp": time.time()
        }

    def build_broadcast(self, payload: Any, msg_type: str = "broadcast",
                        sender_id: str = "prime") -> dict:
        return {
            "type":      msg_type,
            "payload":   payload,
            "sender_id": sender_id,
            "timestamp": time.time()
        }


# ── Mesh Server (Master Node) ─────────────────────────────────────────────────
class MeshServer:
    """
    FastAPI WebSocket server running on the Master (Prime) node.
    Handles node registration, heartbeats, task routing, and broadcasts.
    """

    def __init__(self):
        self.nodes:   Dict[str, MeshNode] = {}
        self.router   = TaskRouter()
        self._loop    = None
        self._lock    = threading.Lock()
        self._app     = None
        self._server_thread = None

        if HAS_FASTAPI:
            self._app = FastAPI(title="JARVIS Mesh Network")
            self._setup_routes()

    def _setup_routes(self):
        app = self._app

        @app.on_event("startup")
        async def _startup():
            self._loop = asyncio.get_running_loop()

        @app.websocket("/mesh")
        async def mesh_ws(ws: WebSocket):
            await ws.accept()
            node: Optional[MeshNode] = None

            try:
                # Wait for registration
                raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
                try:
                    raw = _decrypt_msg(raw)
                except Exception:
                    pass  # plaintext fallback for web clients
                msg = json.loads(raw)

                if msg.get("type") != "register":
                    await ws.close(code=1008)
                    return

                node_id = msg.get("node_id", "")
                token   = msg.get("token", "")

                if not _verify_token(node_id, token):
                    await ws.send_text(json.dumps({"type": "error", "message": "Invalid token"}))
                    await ws.close(code=1008)
                    return

                node = MeshNode(
                    node_id=node_id,
                    device_name=msg.get("device_name", node_id),
                    platform=msg.get("platform", "unknown"),
                    hostname=msg.get("hostname", ""),
                    capabilities=msg.get("capabilities", []),
                    ip_address=ws.client.host if ws.client else "",
                    websocket=ws
                )

                with self._lock:
                    self.nodes[node_id] = node
                    mesh_size = len(self.nodes)
                    nodes_list = [n.to_dict() for n in self.nodes.values()]

                await self._persist_node_async(node)
                print(f"[MESH SERVER]: Node registered — {node.device_name} ({node.platform}) @ {node.ip_address}")

                # Send welcome + current node list
                await ws.send_text(json.dumps({
                    "type": "welcome",
                    "node_id": node_id,
                    "mesh_size": mesh_size,
                    "nodes": nodes_list,
                    "master": self._get_master_info()
                }))

                # Broadcast new node joined
                await self._flush_pending_messages(node)
                await self._broadcast_async({
                    "type": "node_joined",
                    "node": node.to_dict()
                }, exclude=[node_id])

                # Message loop
                while True:
                    raw = await asyncio.wait_for(ws.receive_text(), timeout=35)
                    try:
                        raw = _decrypt_msg(raw)
                    except Exception:
                        pass  # plaintext fallback for web clients
                    await self._handle_message(json.loads(raw), node)

            except asyncio.TimeoutError:
                print(f"[MESH SERVER]: Node {node.node_id if node else '?'} timed out (heartbeat missed)")
            except WebSocketDisconnect:
                print(f"[MESH SERVER]: Node {node.node_id if node else '?'} disconnected")
            except Exception as e:
                print(f"[MESH SERVER]: Connection error: {e}")
            finally:
                if node:
                    with self._lock:
                        if node.node_id in self.nodes:
                            del self.nodes[node.node_id]
                    await self._broadcast_async({
                        "type": "node_left",
                        "node_id": node.node_id,
                        "device_name": node.device_name
                    }, exclude=[])

    async def _handle_message(self, msg: dict, sender: MeshNode):
        msg_type = msg.get("type", "")

        if msg_type == "heartbeat":
            sender.last_seen = time.time()
            sender.status    = "online"

        elif msg_type == "broadcast":
            # Relay broadcast from slave to all other nodes
            await self._broadcast_async(msg, exclude=[sender.node_id])

        elif msg_type == "command":
            target = msg.get("target", "prime")
            if target == "prime":
                command_text = msg.get("payload", {}).get("command", "")
                if command_text:
                    print(f"[MESH SERVER]: Received remote command from {sender.device_name}: {command_text}")
                    try:
                        import threading
                        from core.master_router import delegate_to_swarm
                        threading.Thread(target=delegate_to_swarm, args=(command_text, None), daemon=True).start()
                    except Exception as e:
                        print(f"[MESH SERVER]: Command delegation failed: {e}")
            else:
                targets = self.router.resolve_target(target, self.nodes)
                for nid in targets:
                    if nid != sender.node_id:
                        await self._send_to_node(nid, msg)

        elif msg_type == "task":
            # Route task to target node
            target = msg.get("target", "broadcast")
            targets = self.router.resolve_target(target, self.nodes)
            for nid in targets:
                if nid != sender.node_id:
                    await self._send_to_node(nid, msg)

        elif msg_type == "memory":
            # Sync memory update to all nodes
            await self._broadcast_async(msg, exclude=[sender.node_id])
            await self._log_sync_async("memory", msg.get("key", ""), str(msg.get("value", ""))[:50])

        elif msg_type == "voice":
            # Route TTS command to target device
            target = msg.get("target", "broadcast")
            targets = self.router.resolve_target(target, self.nodes)
            for nid in targets:
                await self._send_to_node(nid, msg)

        elif msg_type == "module_toggle":
            # Toggle a JARVIS feature flag from remote client
            module_name = msg.get("module", "")
            enabled     = msg.get("enabled", False)
            try:
                from core.feature_flags import toggle as _toggle, is_enabled as _is_enabled
                # Map module display name → feature flag key
                _flag_map = {
                    "Brain / LLM Router":      "CASUAL_CHAT",
                    "Web Agent (Playwright)":  "WEB_AGENT",
                    "Deep Research":           "DEEP_RESEARCH",
                    "Dream Mode":              "DREAM_MODE",
                    "Finance Engine":          "FINANCE_ENGINE",
                    "Proactive Engine":        "PROACTIVE",
                }
                flag = _flag_map.get(module_name)
                if flag and (enabled != _is_enabled(flag)):
                    _toggle(flag)
                print(f"[MESH SERVER]: Module toggle — {module_name}: {enabled}")
            except Exception as e:
                print(f"[MESH SERVER]: module_toggle error: {e}")

    async def _broadcast_async(self, msg: dict, exclude: list = None):
        exclude = exclude or []
        with self._lock:
            nodes = list(self.nodes.values())
        payload = _encrypt_msg(json.dumps(msg))
        for node in nodes:
            if node.node_id not in exclude and node.websocket:
                try:
                    await node.websocket.send_text(payload)
                except Exception:
                    await self._store_message(msg, "prime", node.node_id)

    async def _send_to_node(self, node_id: str, msg: dict):
        with self._lock:
            node = self.nodes.get(node_id)
        if node and node.websocket:
            try:
                await node.websocket.send_text(_encrypt_msg(json.dumps(msg)))
            except Exception:
                await self._store_message(msg, "prime", node_id)
        else:
            await self._store_message(msg, "prime", node_id)

    def broadcast_sync(self, msg: dict):
        """Thread-safe synchronous broadcast from main thread."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._broadcast_async(msg), self._loop
            )

    def start(self):
        if not HAS_FASTAPI or self._app is None:
            print("[MESH SERVER]: FastAPI not available — server not started")
            return

        def _run():
            import os
            cert = "jarvis_cert.pem"
            key = "jarvis_key.pem"
            if os.path.exists(cert) and os.path.exists(key):
                uvicorn.run(self._app, host=MASTER_HOST, port=MESH_PORT, log_level="error", ssl_keyfile=key, ssl_certfile=cert)
            else:
                print("[WARNING] No SSL certificates found (jarvis_cert.pem). Mesh Network running in INSECURE PLAINTEXT mode.")
                uvicorn.run(self._app, host=MASTER_HOST, port=MESH_PORT, log_level="error")

        self._server_thread = threading.Thread(target=_run, daemon=True, name="JARVIS-MeshServer")
        self._server_thread.start()
        
        import os
        protocol = "wss" if os.path.exists("jarvis_cert.pem") else "ws"
        print(f"[MESH SERVER]: Listening locally on {protocol}://{MASTER_HOST}:{MESH_PORT}/mesh")
        
        try:
            from pyngrok import ngrok
            import qrcode
            
            print("[MESH SERVER]: Generating Ngrok secure tunnel for Remote Mobile Access...")
            public_url = ngrok.connect(MESH_PORT, bind_tls=True).public_url
            wss_url = public_url.replace("https://", "wss://") + "/mesh"
            
            print(f"\n[NEURAL LINK READY] Mobile App Host URL:\n{wss_url}\n")
            
            qr = qrcode.QRCode(border=2)
            qr.add_data(wss_url)
            print("╔════════════════════════════════════════════════════╗")
            print("║     SCAN THIS SECURE CODE IN YOUR JARVIS APP       ║")
            print("╚════════════════════════════════════════════════════╝")
            qr.print_ascii(invert=True)
            
        except ImportError:
            print("[WARNING] 'pyngrok' or 'qrcode' not installed. Remote Mobile Access disabled. Run 'pip install pyngrok qrcode'.")

    def get_connected_nodes(self) -> List[dict]:
        with self._lock:
            return [n.to_dict() for n in self.nodes.values() if n.is_alive]

    async def _persist_node_async(self, node: MeshNode):
        try:
            async with aiosqlite.connect(MESH_DB) as db:
                await db.execute("""
                    INSERT INTO mesh_nodes (node_id, device_name, platform, hostname, capabilities, status, ip_address)
                    VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(node_id) DO UPDATE SET
                        device_name=excluded.device_name, status='online',
                        last_seen=datetime('now','localtime'), ip_address=excluded.ip_address
                """, (node.node_id, node.device_name, node.platform, node.hostname,
                      json.dumps(node.capabilities), "online", node.ip_address))
                await db.commit()
        except Exception as e:
            log_warn("mesh_network", f"persist_node_async failed: {e}")

    async def _log_sync_async(self, operation: str, key: str, value_hash: str):
        try:
            async with aiosqlite.connect(MESH_DB) as db:
                await db.execute("INSERT INTO mesh_sync_log (operation, key, value_hash) VALUES (?,?,?)",
                            (operation, key, value_hash))
                await db.commit()
        except Exception as e:
            log_warn("mesh_network", f"log_sync_async failed: {e}")

    
    async def _store_message(self, msg: dict, sender_id: str, target_id: str):
        try:
            async with aiosqlite.connect(MESH_DB) as db:
                await db.execute(
                    "INSERT INTO mesh_messages (sender_id, target_id, msg_type, payload) VALUES (?,?,?,?)",
                    (sender_id, target_id, msg.get("type", "unknown"), json.dumps(msg))
                )
                await db.commit()
        except Exception as e:
            log_warn("mesh_network", f"store_message failed: {e}")

    async def _flush_pending_messages(self, node: MeshNode):
        try:
            async with aiosqlite.connect(MESH_DB) as db:
                async with db.execute("SELECT id, payload FROM mesh_messages WHERE target_id=? AND delivered=0", (node.node_id,)) as cursor:
                    rows = await cursor.fetchall()
                    for row in rows:
                        msg_id, payload = row
                        await node.websocket.send_text(_encrypt_msg(payload))
                        await db.execute("UPDATE mesh_messages SET delivered=1 WHERE id=?", (msg_id,))
                await db.commit()
        except Exception as e:
            print(f"[MESH] Flush error: {e}")

    def _get_master_info(self) -> dict:
        return {
            "hostname": socket.gethostname(),
            "platform": platform.system().lower(),
            "port": MESH_PORT
        }


# ── Mesh Client (Slave Node) ───────────────────────────────────────────────────
class MeshClient:
    """
    WebSocket client for slave JARVIS instances (laptop, phone, RPi).
    Handles auto-reconnect, task execution, and memory sync.
    """

    def __init__(self, master_host: str, master_port: int = MESH_PORT,
                 device_name: str = "", node_id: str = ""):
        self.master_uri  = f"ws://{master_host}:{master_port}/mesh"
        self.device_name = device_name or socket.gethostname()
        self.node_id     = node_id or f"{platform.system().lower()}_{socket.gethostname()}"
        self.token       = _generate_token(self.node_id)
        self.capabilities = self._detect_capabilities()
        self._running    = False
        self._stop_event = threading.Event()
        self._thread     = None
        self._on_task    = None  # Callback for received tasks

    def _detect_capabilities(self) -> List[str]:
        caps = ["text", "memory_sync"]
        try:
            import pyautogui
            caps.append("screen")
        except ImportError:
            pass
        try:
            import sounddevice
            caps.append("audio")
        except ImportError:
            pass
        caps.append(f"platform:{platform.system().lower()}")
        return caps

    def set_task_handler(self, callback):
        """Set callback for received task messages: callback(task_dict) → result_str"""
        self._on_task = callback

    def start(self):
        self._running = True
        self._stop_event.clear()
        self._thread  = threading.Thread(
            target=self._connection_loop, daemon=True, name="JARVIS-MeshClient"
        )
        self._thread.start()

    def stop(self):
        self._running = False
        self._stop_event.set()

    def _connection_loop(self):
        delay = 2
        while self._running:
            start_time = time.time()
            try:
                asyncio.run(self._connect())
            except Exception as e:
                if HAS_WEBSOCKETS and isinstance(e, websockets.exceptions.ConnectionClosed):
                    print(f"[MESH CLIENT]: Server dropped connection: {e}")
                else:
                    print(f"[MESH CLIENT]: Connection failed: {e}")
            
            if time.time() - start_time > 10:
                delay = 2
                
            if self._running:
                print(f"[MESH CLIENT]: Reconnecting in {delay}s...")
                if self._stop_event.wait(delay):
                    break
                delay = min(delay * 2, 60)

    async def _connect(self):
        if not HAS_WEBSOCKETS:
            print("[MESH CLIENT]: websockets library not available")
            await asyncio.sleep(60)
            return

        print(f"[MESH CLIENT]: Connecting to {self.master_uri}...")
        async with websockets.connect(self.master_uri, ping_interval=20) as ws:
            # Register
            await ws.send(json.dumps({
                "type":         "register",
                "node_id":      self.node_id,
                "token":        self.token,
                "device_name":  self.device_name,
                "platform":     platform.system().lower(),
                "hostname":     socket.gethostname(),
                "capabilities": self.capabilities
            }))

            # Start heartbeat
            heartbeat_task = asyncio.ensure_future(self._heartbeat_loop(ws))

            try:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        await self._handle_message(msg, ws)
                    except Exception as e:
                        print(f"[MESH CLIENT]: Message error: {e}")
            finally:
                heartbeat_task.cancel()

    async def _heartbeat_loop(self, ws):
        while True:
            try:
                await ws.send(json.dumps({
                    "type":    "heartbeat",
                    "node_id": self.node_id,
                    "timestamp": time.time()
                }))
                await asyncio.sleep(10)
            except Exception:
                break

    async def _handle_message(self, msg: dict, ws):
        msg_type = msg.get("type", "")

        if msg_type == "welcome":
            print(f"[MESH CLIENT]: Connected to mesh. {msg.get('mesh_size', 0)} nodes online.")

        elif msg_type == "task":
            task_type = msg.get("task_type", "")
            payload   = msg.get("payload", {})
            print(f"[MESH CLIENT]: Received task: {task_type}")

            result = "Task received"
            if self._on_task:
                try:
                    result = self._on_task(msg) or "Done"
                except Exception as e:
                    result = f"Task failed: {e}"

            # Send result back
            await ws.send(json.dumps({
                "type":    "broadcast",
                "payload": {"task_id": msg.get("task_id"), "result": result}
            }))

        elif msg_type == "voice":
            text    = msg.get("payload", {}).get("text", "")
            speaker = msg.get("payload", {}).get("speaker", "jarvis")
            if text and "audio" in self.capabilities:
                try:
                    from core.voice import speak_async
                    speak_async(text, speaker)
                except Exception as e:
                    log_warn("mesh_network", f"voice relay speak failed: {e}")

        elif msg_type == "node_joined":
            node = msg.get("node", {})
            print(f"[MESH CLIENT]: Node joined: {node.get('device_name')} ({node.get('platform')})")

        elif msg_type == "node_left":
            print(f"[MESH CLIENT]: Node left: {msg.get('device_name')}")

        elif msg_type == "memory":
            # Apply memory sync
            key   = msg.get("key", "")
            value = msg.get("value", "")
            if key and value:
                try:
                    from core.memory import save_memory
                    save_memory(key, str(value))
                    print(f"[MESH CLIENT]: Memory synced: {key}")
                except Exception as e:
                    log_warn("mesh_network", f"memory sync save failed: {e}")

        elif msg_type == "event":
            event_data = msg.get("payload", {})
            print(f"[MESH CLIENT]: Event received: {event_data.get('title', '?')}")


# ── Memory Syncer ──────────────────────────────────────────────────────────────
class MemorySyncer:
    """
    Hooks into memory saves and broadcasts updates across the mesh.
    Integrates with MeshServer.broadcast_sync().
    """

    def __init__(self, server: MeshServer):
        self.server = server

    def sync_memory(self, key: str, value: str):
        """Call when any memory key is written."""
        self.server.broadcast_sync({
            "type":  "memory",
            "key":   key,
            "value": value[:500],
            "timestamp": time.time()
        })

    def sync_event(self, event_dict: dict):
        """Broadcast a ProactiveEvent to all mesh nodes."""
        self.server.broadcast_sync({
            "type":    "event",
            "payload": event_dict,
            "timestamp": time.time()
        })

    def sync_voice(self, text: str, target: str = "broadcast", speaker: str = "jarvis"):
        """Send TTS command to a specific device."""
        self.server.broadcast_sync({
            "type":   "voice",
            "target": target,
            "payload": {"text": text, "speaker": speaker},
            "timestamp": time.time()
        })


# ── QR Token Generator ────────────────────────────────────────────────────────
def generate_connection_info() -> dict:
    """Generates connection info for pairing new devices."""
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception as e:
        log_warn("mesh_network", f"get local IP failed: {e}")

    new_node_id = f"device_{int(time.time())}"
    token       = _generate_token(new_node_id)

    info = {
        "master_ip":   local_ip,
        "master_port": MESH_PORT,
        "node_id":     new_node_id,
        "token":       token,
        "ws_uri":      f"ws://{local_ip}:{MESH_PORT}/mesh",
        "connect_cmd": (
            f"python -c \"from core.mesh_network import MeshClient; "
            f"c = MeshClient('{local_ip}', {MESH_PORT}, node_id='{new_node_id}'); "
            f"import hmac, hashlib; c.token='{token}'; c.start(); import time; time.sleep(9999)\""
        )
    }
    return info


# ── Mesh Orchestrator ──────────────────────────────────────────────────────────
class MeshOrchestrator:
    """Master controller: manages the server + exposes clean API."""

    def __init__(self):
        _init_mesh_tables()
        self.server  = MeshServer()
        self.router  = TaskRouter()
        self.syncer  = MemorySyncer(self.server)
        self._client: Optional[MeshClient] = None

        print(f"[MESH NETWORK]: Orchestrator online. Master host: {socket.gethostname()}")

    def start_as_master(self):
        """Start as the Prime (master) node with WebSocket server."""
        self.server.start()
        print(f"[MESH NETWORK]: Running as MASTER. Waiting for nodes...")

    def start_as_slave(self, master_host: str, node_id: str = "", device_name: str = ""):
        """Start as slave node, connecting to a master."""
        self._client = MeshClient(
            master_host=master_host,
            device_name=device_name,
            node_id=node_id
        )
        self._client.start()
        print(f"[MESH NETWORK]: Running as SLAVE → {master_host}:{MESH_PORT}")

    def get_mesh_status(self) -> str:
        nodes = self.server.get_connected_nodes()
        if not nodes:
            return "Rishabh, no devices are currently connected to the mesh network."
        lines = [f"Rishabh, {len(nodes)} device(s) connected to JARVIS mesh:"]
        for n in nodes:
            lines.append(f"  • {n['device_name']} ({n['platform']}) — {n['status']}")
        return "\n".join(lines)

    def send_to_device(self, target: str, task_type: str, payload: Any):
        msg = self.router.build_task_message(task_type, payload)
        msg["target"] = target
        self.server.broadcast_sync(msg)

    def broadcast_event(self, event_data: dict):
        self.syncer.sync_event(event_data)

    def speak_on_device(self, text: str, target: str = "broadcast", speaker: str = "jarvis"):
        self.syncer.sync_voice(text, target, speaker)

    def get_pairing_info(self) -> dict:
        return generate_connection_info()

    def get_history(self, limit: int = 10) -> list:
        try:
            conn = sqlite3.connect(MESH_DB)
            conn.row_factory = sqlite3.Row
            cur  = conn.cursor()
            cur.execute("""
                SELECT node_id, device_name, platform, status, last_seen
                FROM mesh_nodes ORDER BY last_seen DESC LIMIT ?
            """, (limit,))
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception:
            return []


# ── Singleton ─────────────────────────────────────────────────────────────────
def get_mesh() -> MeshOrchestrator:
    global _mesh_instance
    if _mesh_instance is None:
        with _mesh_lock:
            if _mesh_instance is None:
                _mesh_instance = MeshOrchestrator()
    return _mesh_instance


# ── BaseMonitor wrapper ───────────────────────────────────────────────────────
class MeshNetworkMonitor(BaseMonitor):
    """
    Proactive mesh monitor. Reports node health every 2 minutes.
    Fires alerts on node disconnect/reconnect events.
    """
    name = "MeshNetwork"
    interval_seconds = 120

    def __init__(self, event_queue=None, config=None):
        super().__init__(event_queue, config)
        self._mesh    = get_mesh()
        self._prev_nodes: set = set()
        # Start mesh server as master by default
        try:
            self._mesh.start_as_master()
        except Exception as e:
            print(f"[MESH]: Could not start server: {e}")

    def check(self):
        events = []
        try:
            connected = self._mesh.server.get_connected_nodes()
            current_ids = {n["node_id"] for n in connected}

            # Detect new connections
            new_nodes = current_ids - self._prev_nodes
            lost_nodes = self._prev_nodes - current_ids

            for nid in new_nodes:
                node = next((n for n in connected if n["node_id"] == nid), {})
                try:
                    from core.proactive_orchestrator import ProactiveEvent
                    events.append(ProactiveEvent(
                        priority=4,
                        category="mesh",
                        title=f"Mesh Node Connected: {node.get('device_name', nid)}",
                        message=(
                            f"Rishabh, {node.get('device_name', nid)} "
                            f"({node.get('platform', 'unknown')}) has joined the mesh network. "
                            f"Total connected: {len(current_ids)} device(s)."
                        ),
                        data={"node": node},
                        timestamp=time.time(),
                    ))
                except ImportError:
                    pass

            for nid in lost_nodes:
                try:
                    from core.proactive_orchestrator import ProactiveEvent
                    events.append(ProactiveEvent(
                        priority=3,
                        category="mesh",
                        title=f"Mesh Node Disconnected: {nid}",
                        message=f"Rishabh, node {nid} has disconnected from the mesh network.",
                        data={"node_id": nid},
                        timestamp=time.time(),
                    ))
                except ImportError:
                    pass

            self._prev_nodes = current_ids

            # Status log
            history = self._mesh.get_history(limit=3)
            print(
                f"[MESH NETWORK]: {len(current_ids)} node(s) active | "
                f"Ever seen: {len(history)} devices | "
                f"Server: {'running' if HAS_FASTAPI else 'fastapi_missing'}"
            )

        except Exception as e:
            print(f"[MESH NETWORK]: Monitor check error: {e}")
        return events


# ── LAN Node Discovery ────────────────────────────────────────────────────────
def discover_lan_nodes(timeout_s: float = 1.0) -> list[dict]:
    """
    Scan the local subnet for JARVIS mesh nodes by probing port 8765.
    Returns list of {"ip": str, "port": int} for responsive hosts.
    Fast parallel scan — completes in ~timeout_s seconds.
    """
    import socket as _sock
    import concurrent.futures as _cf

    # Get local subnet
    local_ip = "127.0.0.1"
    try:
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        return []

    # Build IP range (e.g., 192.168.1.1 → 192.168.1.254)
    subnet = ".".join(local_ip.split(".")[:3])
    candidates = [f"{subnet}.{i}" for i in range(1, 255)]
    found = []

    def _probe(ip: str) -> dict | None:
        try:
            s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
            s.settimeout(timeout_s)
            if s.connect_ex((ip, MESH_PORT)) == 0:
                s.close()
                return {"ip": ip, "port": MESH_PORT}
            s.close()
        except Exception as e:
            log_warn("mesh_network", f"LAN probe to {ip} failed: {e}")
        return None

    with _cf.ThreadPoolExecutor(max_workers=64) as pool:
        futures = [pool.submit(_probe, ip) for ip in candidates]
        for f in _cf.as_completed(futures, timeout=timeout_s + 2):
            r = f.result()
            if r:
                found.append(r)

    return found


# ── Voice Command Handler ─────────────────────────────────────────────────────
def handle_mesh_command(text: str, signal=None) -> tuple[bool, str]:
    """
    Parse natural language mesh commands.
    Returns (handled: bool, response: str)

    Supports:
      "mesh status" / "how many nodes"
      "connect to <device>"
      "discover nodes" / "scan network"
      "speak on <device> <text>"
      "send to <device> <task>"
      "pairing info"
    """
    t = text.lower().strip()
    mesh = get_mesh()

    # ── Status ────────────────────────────────────────────────────────────────
    if any(x in t for x in ["mesh status", "how many nodes", "connected devices",
                              "mesh network status", "nodes online"]):
        status = mesh.get_mesh_status()
        history = mesh.get_history(limit=5)
        if history:
            seen = ", ".join(set(h["device_name"] for h in history))
            status += f"\nDevices seen historically: {seen}."
        return True, status

    # ── Discovery ─────────────────────────────────────────────────────────────
    if any(x in t for x in ["discover nodes", "scan network", "find devices",
                              "scan for jarvis", "find nodes"]):
        if signal:
            signal.emit("thinking", "SYSTEM_REPLY:<i>[🔭 MESH]: Scanning LAN for JARVIS nodes...</i>")
        found = discover_lan_nodes(timeout_s=1.5)
        if not found:
            return True, ("Local network par koi JARVIS mesh nodes nahi mile, Rishabh. "
                          "Make sure other devices have JARVIS running with mesh enabled.")
        ips = ", ".join(n["ip"] for n in found)
        return True, f"Found {len(found)} mesh node(s) at: {ips}, Rishabh."

    # ── Connect to slave ──────────────────────────────────────────────────────
    if "connect to" in t and any(x in t for x in ["laptop", "phone", "tablet", "pi", "server"]):
        # Extract IP or device name after "connect to"
        parts = t.split("connect to")
        target = parts[-1].strip() if len(parts) > 1 else ""
        # Try to discover and connect
        if signal:
            signal.emit("thinking", f"SYSTEM_REPLY:<i>[🔭 MESH]: Connecting to {target}...</i>")
        nodes = discover_lan_nodes(timeout_s=2.0)
        if nodes:
            ip = nodes[0]["ip"]
            mesh.start_as_slave(ip)
            return True, f"Connecting to mesh node at {ip}, Rishabh."
        return True, f"Could not find '{target}' on the network, Rishabh. Is it running JARVIS?"

    # ── Pairing info ──────────────────────────────────────────────────────────
    if any(x in t for x in ["pairing info", "pair device", "connection info", "add device"]):
        info = mesh.get_pairing_info()
        return True, (f"Rishabh, your mesh connection info: "
                      f"IP {info['master_ip']}, Port {info['master_port']}. "
                      f"Run the connect command on the other device.")

    # ── Speak on device ───────────────────────────────────────────────────────
    if "speak on" in t or "say on" in t:
        for keyword in ["speak on", "say on"]:
            if keyword in t:
                rest = t.split(keyword, 1)[-1].strip()
                # First word = device, rest = text
                parts = rest.split(" ", 1)
                if len(parts) == 2:
                    device, text_to_say = parts[0], parts[1]
                    mesh.speak_on_device(text_to_say, target=device)
                    return True, f"Sending speech to {device}, Rishabh."
        return True, "Please specify a device and text, Rishabh."

    return False, ""


# ── Auto-start helper (called from main.py) ───────────────────────────────────
def start_mesh_master() -> MeshOrchestrator:
    """
    Boot the mesh server as master node.
    Safe to call even if fastapi/uvicorn is not installed.
    Returns the MeshOrchestrator singleton.
    """
    mesh = get_mesh()
    try:
        mesh.start_as_master()
        log_info("system", "system", f"[MESH NETWORK]: Master server started on port {MESH_PORT}")
    except Exception as e:
        log_warn("mesh", f"Could not start server: {e}")
    return mesh
