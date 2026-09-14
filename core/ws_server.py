from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List
import uvicorn
import threading
import json
import asyncio
import os
import hmac

WS_SECRET = os.environ.get("WS_SECRET", "jarvis_local_secret")

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    manager.loop = asyncio.get_running_loop()
    yield

app = FastAPI(title="JARVIS API", version="2.0", lifespan=lifespan)

# Allow dashboard HTML (file://) to fetch data without CORS block
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.loop = None

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def _broadcast_async(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_text(json.dumps(message))
            except Exception:
                self.disconnect(connection)

    def broadcast(self, message: dict):
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self._broadcast_async(message), self.loop)

manager = ConnectionManager()



@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = None):
    # Fix: use hmac.compare_digest for constant-time comparison (prevents timing attacks)
    if not token or not hmac.compare_digest(token, WS_SECRET):
        await websocket.close(code=1008)
        return

    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Echo back received commands as acknowledgement
            try:
                msg = json.loads(data)
                await websocket.send_text(json.dumps({"ack": True, "cmd": msg}))
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)

def get_free_port(start_port=8000):
    import socket
    port = start_port
    while port < 8100:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return port
        port += 1
    return 8000

def run_server():
    import os
    cert = "jarvis_cert.pem"
    key = "jarvis_key.pem"
    
    port = get_free_port(8000)
    
    if os.path.exists(cert) and os.path.exists(key):
        print(f"[JARVIS UI]: Securing WebSocket with TLS (wss://) on port {port}...")
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="error", ssl_keyfile=key, ssl_certfile=cert)
    else:
        print("[WARNING] No SSL certificates found (jarvis_cert.pem). Running UI WebSockets in INSECURE PLAINTEXT mode.")
        print(f"[JARVIS UI]: Started on port {port}")
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="error")

def start_ws_server():
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    return manager


# ── HTTP REST endpoints ───────────────────────────────────────────────

@app.get("/api/quota")
async def get_quota():
    """Live API quota dashboard data. Consumed by ui/api_dashboard.html."""
    try:
        from core.api_quota_tracker import get_dashboard_data
        data = get_dashboard_data()
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/dashboard")
async def serve_dashboard():
    """Serve the API quota dashboard HTML directly from the browser."""
    _ui_path = os.path.join(os.path.dirname(__file__), "..", "ui", "api_dashboard.html")
    _ui_path = os.path.abspath(_ui_path)
    if os.path.exists(_ui_path):
        return FileResponse(_ui_path, media_type="text/html")
    return JSONResponse(content={"error": "Dashboard not found"}, status_code=404)


@app.get("/api/health")
async def health():
    """Simple health check."""
    import datetime
    return {"status": "ok", "time": datetime.datetime.now().isoformat()}
