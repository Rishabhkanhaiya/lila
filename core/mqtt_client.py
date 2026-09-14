"""
core/mqtt_client.py — JARVIS IoT / Smart Home MQTT Engine v2.0
===============================================================
Full bidirectional MQTT client with:
  • Auto-connect + exponential backoff reconnect
  • Broker config from .env (MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS)
  • Device registry — name → topic mapping
  • Publish commands (on/off/set/toggle/query)
  • Subscribe to sensor data (temperature, humidity, motion, etc.)
  • Telemetry publishing for JARVIS internal state
  • Voice command integration via `handle_iot_command()`

Topic convention:
  jarvis/cmd/<device>       ← JARVIS publishes commands here
  jarvis/state/<device>     ← Devices publish state here (subscribed)
  jarvis/sensor/<device>    ← Sensor readings (subscribed)
  jarvis/telemetry/v1       ← JARVIS internal telemetry (published)
"""

import json
import time
import threading
import os
from datetime import datetime
from dotenv import load_dotenv
from core.jarvis_logger import log_error, log_warn, log_info

load_dotenv(override=True)  # override=True ensures .env always wins over system env vars

# ⚡ Broker config from environment ⚡
MQTT_BROKER   = os.environ.get("MQTT_BROKER",   "127.0.0.1")
MQTT_PORT     = int(os.environ.get("MQTT_PORT",  "1883"))
MQTT_USER     = os.environ.get("MQTT_USER",     "")
MQTT_PASS     = os.environ.get("MQTT_PASS",     "")
MQTT_ENABLED  = os.environ.get("MQTT_ENABLED",  "true").lower() == "true"

# ── Device Registry ───────────────────────────────────────────────────────────
# Maps friendly name → MQTT command topic
# Edit this to match your smart home device topics
DEVICE_REGISTRY = {
    # Lights
    "lights":          "jarvis/cmd/lights",
    "bedroom light":   "jarvis/cmd/bedroom_light",
    "living room":     "jarvis/cmd/living_room_light",
    "desk lamp":       "jarvis/cmd/desk_lamp",
    # Appliances
    "fan":             "jarvis/cmd/fan",
    "ac":              "jarvis/cmd/ac",
    "air conditioner": "jarvis/cmd/ac",
    "tv":              "jarvis/cmd/tv",
    "monitor":         "jarvis/cmd/monitor",
    # Sensors (read-only)
    "temperature":     "jarvis/sensor/temperature",
    "humidity":        "jarvis/sensor/humidity",
    "motion":          "jarvis/sensor/motion",
}

# ── Sensor data store (updated by subscriptions) ─────────────────────────────
_sensor_data: dict = {}
_sensor_lock  = threading.Lock()


class MQTTClient:
    """
    Full-featured JARVIS MQTT client.
    Connects in background, auto-reconnects, handles commands and sensors.
    """

    RECONNECT_DELAYS = [2, 5, 10, 30, 60]   # seconds between reconnect attempts

    def __init__(self):
        self.is_connected  = False
        self._client       = None
        self._reconnect_idx = 0
        self._stop         = threading.Event()
        self._lock         = threading.Lock()

        if MQTT_ENABLED:
            self._start_connection()
        else:
            log_info("system", "system", "[MQTT]: Disabled via MQTT_ENABLED env var")

    # ── Connection management ─────────────────────────────────────────────────

    def _start_connection(self):
        threading.Thread(
            target=self._connect_loop, daemon=True, name="JARVIS-MQTT"
        ).start()

    def _connect_loop(self):
        """Keep trying to connect with exponential backoff."""
        import paho.mqtt.client as mqtt_lib

        while not self._stop.is_set():
            try:
                import uuid
                client = mqtt_lib.Client(client_id=f"JARVIS-v4-{uuid.uuid4().hex[:8]}")
                client.on_connect    = self._on_connect
                client.on_disconnect = self._on_disconnect
                client.on_message    = self._on_message

                if MQTT_USER:
                    client.username_pw_set(MQTT_USER, MQTT_PASS)

                log_info("system", "system", f"[MQTT]: Connecting to {MQTT_BROKER}:{MQTT_PORT}...")
                client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60, socket_keepalive=True)
                client._sock_keepalive = True

                with self._lock:
                    self._client = client

                client.loop_start()
                
                # Wait for connection to establish
                connect_wait = 0
                while not self.is_connected and connect_wait < 50 and not self._stop.is_set():
                    if self._stop.wait(0.1):
                        break
                    connect_wait += 1

                if self.is_connected:
                    self._reconnect_idx = 0   # Reset backoff on success
                    # Wait until disconnected or stop requested
                    while not self._stop.is_set() and self.is_connected:
                        if self._stop.wait(1.0):
                            break

                client.loop_stop()

            except Exception as e:
                log_warn("mqtt", f"Connection failed: {e}")

            if self._stop.is_set():
                break

            delay = self.RECONNECT_DELAYS[
                min(self._reconnect_idx, len(self.RECONNECT_DELAYS) - 1)
            ]
            self._reconnect_idx += 1
            log_info("system", "system", f"[MQTT]: Reconnecting in {delay}s...")
            self._stop.wait(delay)

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.is_connected = True
            log_info("system", "system", "[MQTT]: ✅ Connected to broker")

            # Subscribe to all device state and sensor topics
            for topic in ["jarvis/state/#", "jarvis/sensor/#", "jarvis/cmd/#"]:
                client.subscribe(topic)
                log_info("system", "system", f"[MQTT]: Subscribed to {topic}")
        else:
            log_warn("mqtt", f"Connection refused — code {rc}")

    def _on_disconnect(self, client, userdata, rc):
        self.is_connected = False
        if rc != 0:
            log_warn("mqtt", f"Unexpected disconnect (code {rc}) — will reconnect")

    def _on_message(self, client, userdata, msg):
        """Handle incoming messages from devices/sensors."""
        try:
            topic   = msg.topic
            payload = msg.payload.decode("utf-8", errors="replace")

            # Try to parse JSON payload
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                data = {"value": payload}

            # Store sensor readings
            if topic.startswith("jarvis/sensor/") or topic.startswith("jarvis/state/"):
                device_key = topic.split("/")[-1]
                with _sensor_lock:
                    _sensor_data[device_key] = {
                        "value":     data,
                        "timestamp": time.time(),
                        "topic":     topic,
                    }
                log_info("system", "system", f"[MQTT]: {topic} → {str(data)[:60]}")

        except Exception as e:
            log_error("mqtt_client", "error", e)

    # ── Publish interface ─────────────────────────────────────────────────────

    def publish(self, topic: str, payload: dict | str, qos: int = 1) -> bool:
        """Publish any payload to any topic."""
        if not self.is_connected or not self._client:
            log_warn("mqtt", "Not connected — command not sent")
            return False
        try:
            msg = json.dumps(payload) if isinstance(payload, dict) else str(payload)
            with self._lock:
                self._client.publish(topic, msg, qos=qos)
            return True
        except Exception as e:
            log_error("mqtt_client", "error", e)
            return False

    def publish_state(self, task_name: str, state: str, message: str):
        """Telemetry publisher — called by master_router.py."""
        payload = {
            "task":      task_name,
            "state":     state,
            "message":   message,
            "timestamp": datetime.now().isoformat(),   # Fixed: was threading.get_ident()
        }
        self.publish("jarvis/telemetry/v1", payload, qos=0)

    # ── Device control ────────────────────────────────────────────────────────

    def send_command(self, device: str, command: str, value=None) -> tuple[bool, str]:
        """
        Send a command to a named device.

        Args:
            device:  Friendly name from DEVICE_REGISTRY (e.g. "lights")
            command: "on" | "off" | "toggle" | "set" | "query"
            value:   Optional value for "set" (e.g. brightness 0-100)

        Returns:
            (success: bool, message: str)
        """
        device_lower = device.lower().strip()
        topic = DEVICE_REGISTRY.get(device_lower)

        if not topic:
            # Fuzzy match — try partial names
            for name, t in DEVICE_REGISTRY.items():
                if device_lower in name or name in device_lower:
                    topic = t
                    break

        if not topic:
            return False, f"Unknown device '{device}'. Known: {', '.join(DEVICE_REGISTRY.keys())}"

        payload = {
            "command":   command,
            "timestamp": datetime.now().isoformat(),
        }
        if value is not None:
            payload["value"] = value

        ok = self.publish(topic, payload)
        if ok:
            msg = f"Sent '{command}' to {device}"
            if value is not None:
                msg += f" (value: {value})"
            return True, msg
        else:
            return False, "MQTT not connected — command failed"

    def get_sensor_reading(self, sensor: str) -> dict | None:
        """Get the latest sensor reading for a sensor name."""
        with _sensor_lock:
            data = _sensor_data.get(sensor)
            if not data:
                # Try partial match
                for k, v in _sensor_data.items():
                    if sensor.lower() in k.lower():
                        data = v
                        break
        return data

    def get_all_device_states(self) -> dict:
        """Return all known device/sensor states."""
        with _sensor_lock:
            return dict(_sensor_data)

    def stop(self):
        self._stop.set()
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass


# ── Voice command handler ─────────────────────────────────────────────────────

def handle_iot_command(text: str) -> tuple[bool, str]:
    """
    Parse a natural language IoT command and execute it.
    Returns (handled: bool, response: str)

    Supports:
      "turn on/off <device>"
      "toggle <device>"
      "set <device> to <value>"
      "what is the temperature/humidity"
      "device status"
    """
    t = text.lower().strip()

    # ── Sensor queries ────────────────────────────────────────────────────────
    if any(x in t for x in ["temperature", "temp", "how hot", "how cold"]):
        data = telemetry_client.get_sensor_reading("temperature")
        if data:
            val = data["value"]
            temp = val.get("temperature", val.get("value", val)) if isinstance(val, dict) else val
            return True, f"Current temperature is {temp}°C, Rishabh."
        return True, "Abhi koi temperature sensor data available nahi hai, Rishabh."

    if "humidity" in t:
        data = telemetry_client.get_sensor_reading("humidity")
        if data:
            val = data["value"]
            hum = val.get("humidity", val.get("value", val)) if isinstance(val, dict) else val
            return True, f"Humidity is at {hum}%, Rishabh."
        return True, "Abhi koi humidity data available nahi hai, Rishabh."

    if any(x in t for x in ["device status", "iot status", "smart home status"]):
        states = telemetry_client.get_all_device_states()
        if not states:
            return True, "Abhi koi device data receive nahi hua hai, Rishabh. Check MQTT broker connection."
        summary = f"I have data from {len(states)} devices: " + \
                  ", ".join(f"{k}" for k in list(states.keys())[:5])
        return True, summary + "."

    # ── On/Off commands ───────────────────────────────────────────────────────
    for keyword in ["turn on", "switch on", "enable", "activate"]:
        if keyword in t:
            device = t.replace(keyword, "").strip()
            if device:
                ok, msg = telemetry_client.send_command(device, "on")
                return True, msg + ", Rishabh." if ok else msg

    for keyword in ["turn off", "switch off", "disable", "deactivate"]:
        if keyword in t:
            device = t.replace(keyword, "").strip()
            if device:
                ok, msg = telemetry_client.send_command(device, "off")
                return True, msg + ", Rishabh." if ok else msg

    if "toggle" in t:
        device = t.replace("toggle", "").strip()
        if device:
            ok, msg = telemetry_client.send_command(device, "toggle")
            return True, msg + ", Rishabh." if ok else msg

    # ── Set value commands ("set fan to 3", "set brightness to 80") ──────────
    if "set " in t and " to " in t:
        parts = t.replace("set ", "").split(" to ")
        if len(parts) == 2:
            device, value = parts[0].strip(), parts[1].strip()
            try:
                value = int(value)
            except ValueError:
                pass
            ok, msg = telemetry_client.send_command(device, "set", value)
            return True, msg + ", Rishabh." if ok else msg

    return False, ""   # Not an IoT command


# ── Singleton ─────────────────────────────────────────────────────────────────
telemetry_client = MQTTClient()
