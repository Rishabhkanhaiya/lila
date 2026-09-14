"""
core/eyes.py — JARVIS Visual Cortex (Modern 2026 Engine)
=========================================================
High-speed in-memory vision processing using the modern google.genai SDK.
- Zero disk I/O: screenshots captured directly to in-memory JPEG streams
- Powered by Gemini 2.5 Flash
- Desktop accessibility tree extraction for precise UI coordinates
- Realtime frame extraction for Gemini Live WebSockets
"""

import os
import io
import base64
import json
import threading
from PIL import Image
try:
    import pyautogui
    HAS_PYAUTOGUI = True
except ImportError:
    pyautogui = None
    HAS_PYAUTOGUI = False

from dotenv import load_dotenv

# For Accessibility Tree Extraction (Cross-Platform)
try:
    import pygetwindow as gw
    HAS_GW = True
except ImportError:
    HAS_GW = False

try:
    import uiautomation as auto
    HAS_UIAUTOMATION = True
except ImportError:
    HAS_UIAUTOMATION = False

load_dotenv()

# ==========================================
# ⚡ MODERN GOOGLE GENAI CLIENT POOL
# ==========================================
_vision_key_idx = 0
_vision_key_lock = threading.Lock()
_genai_clients: dict = {}

def _get_client_for_key(key: str):
    if key not in _genai_clients:
        from google import genai
        _genai_clients[key] = genai.Client(api_key=key)
    return _genai_clients[key]

def _call_gemini_vision(img_b64: str, prompt: str) -> str | None:
    """Use modern google.genai with gemini-2.5-flash — rotates API keys gracefully."""
    global _vision_key_idx
    from google.genai import types

    keys = [
        os.environ.get("GEMINI_API_KEY", ""),
        os.environ.get("GEMINI_API_KEY_2", ""),
    ]
    keys = [k for k in keys if k]
    if not keys:
        return None

    vision_models = [
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-flash-latest",
        "gemini-2.5-flash",
    ]


    for _ in range(len(keys)):
        with _vision_key_lock:
            key = keys[_vision_key_idx % len(keys)]
            _vision_key_idx += 1

        try:
            client = _get_client_for_key(key)
            image_bytes = base64.b64decode(img_b64)
            part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

            for m in vision_models:
                try:
                    response = client.models.generate_content(
                        model=m,
                        contents=[prompt, part],
                    )
                    if response and response.text:
                        return response.text
                except Exception as me:
                    m_err = str(me).lower()
                    if "429" in m_err or "quota" in m_err or "resource_exhausted" in m_err:
                        continue
                    # Non-quota error on this model, try next
                    continue
        except Exception as e:
            err = str(e)
            if "429" in err or "quota" in err.lower():
                continue
            print(f"[⚠️ Gemini Vision Cortex Error]: {e}")
            return None
    return None


def call_vision(img_b64: str, prompt: str) -> str:
    """Primary vision entry point with zero-latency failover."""
    try:
        result = _call_gemini_vision(img_b64, prompt)
        if result:
            return result
    except Exception as e:
        print(f"[⚠️ Gemini Vision Error]: {e}")
    
    return "Visual cortex offline — unable to analyze image."


# ==========================================
# ⚡ IN-MEMORY FRAME CAPTURE (ZERO DISK I/O)
# ==========================================
def capture_screen_jpeg_bytes(quality: int = 80, max_dim: int = 1280) -> bytes:
    """
    Captures the desktop screen entirely in-memory as a compressed JPEG.
    Zero disk writes — ideal for live WebSocket streaming.
    Falls back gracefully if display context is unavailable (headless/locked session).
    """
    try:
        from core.cua_grounding import capture_raw_screen_pil
        screenshot = capture_raw_screen_pil()
    except Exception:
        screenshot = None

    if not screenshot:
        try:
            screenshot = pyautogui.screenshot()
        except Exception:
            screenshot = Image.new("RGB", (1280, 720), color=(15, 15, 20))

    if max_dim and (screenshot.width > max_dim or screenshot.height > max_dim):
        screenshot.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    
    buf = io.BytesIO()
    if screenshot.mode != "RGB":
        screenshot = screenshot.convert("RGB")
    screenshot.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


# ==========================================
# ⚡ ACCESSIBILITY TREE
# ==========================================
def get_accessibility_tree() -> dict:
    """
    Extracts the structured layout of the current active window.
    Provides precise X/Y coordinates for UI elements.
    """
    print("[👁️ CORTEX]: Extracting Desktop Accessibility Tree...")
    tree_data = {"active_window": None, "elements": []}
    
    if not HAS_GW:
        return tree_data

    try:
        active_win = gw.getActiveWindow()
        if active_win:
            tree_data["active_window"] = {
                "title": active_win.title,
                "box": {"left": active_win.left, "top": active_win.top, "width": active_win.width, "height": active_win.height}
            }
            
        if HAS_UIAUTOMATION and active_win:
            win_control = auto.WindowControl(searchDepth=1, Name=active_win.title)
            if not win_control.Exists(1, 0):
                win_control = auto.WindowControl(searchDepth=1, SubName=active_win.title)
            
            if win_control.Exists(0, 0):
                def _extract(control, depth, max_depth=3):
                    if depth > max_depth:
                        return
                    try:
                        for child in control.GetChildren():
                            name = child.Name or ""
                            if name.strip():
                                rect = child.BoundingRectangle
                                tree_data["elements"].append({
                                    "name": name,
                                    "type": child.ControlTypeName,
                                    "center_x": (rect.left + rect.right) // 2,
                                    "center_y": (rect.top + rect.bottom) // 2
                                })
                            _extract(child, depth + 1, max_depth)
                    except Exception:
                        pass
                
                _extract(win_control, 0)
    except Exception as e:
        print(f"[⚠️ AX Tree Extraction Failed]: {e}")
        
    return tree_data


# ==========================================
# ⚡ SCREEN SCAN (MASTER IN-MEMORY PIPELINE)
# ==========================================
def scan_screen(prompt: str = None) -> str:
    """
    The Master Vision Pipeline.
    Captures screen in memory, combines with accessibility tree, and analyzes.
    """
    try:
        print("[👁️ EYES]: Capturing high-res screen state (in-memory)...")
        
        # 1. In-memory JPEG capture
        jpeg_bytes = capture_screen_jpeg_bytes(quality=85, max_dim=1920)
        img_b64 = base64.b64encode(jpeg_bytes).decode("utf-8")

        # 2. Structured Bounding Boxes
        ax_tree = get_accessibility_tree()
        ax_context = f"\n[STRUCTURED UI ELEMENTS]:\n{json.dumps(ax_tree, indent=2)}" if ax_tree.get("elements") else ""

        # 3. Analyze via Gemini 2.5 Flash
        user_prompt = prompt or (
            "You are JARVIS Astra's visual cortex. Describe what is on the screen concisely. "
            "If the user is asking to click or interact with an element, use the provided Structured UI Elements "
            "to find the exact center_x and center_y coordinates."
        )
        
        result = call_vision(img_b64, user_prompt + ax_context)
        return result
    except Exception as e:
        print(f"[⚠️ VISION ERROR]: {e}")
        return f"Visual cortex error: {e}"


# ==========================================
# ⚡ FILE/IMAGE ANALYSIS
# ==========================================
def analyze_image_with_fallback(image_path: str, prompt: str) -> str:
    """Analyzes a specific image file provided by path."""
    try:
        if not os.path.exists(image_path):
            return f"Image file not found: {image_path}"

        # In-memory read & resize if needed
        with Image.open(image_path) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        
        return call_vision(img_b64, prompt)
    except Exception as e:
        print(f"[⚠️ IMAGE ANALYSIS ERROR]: {e}")
        return f"Image analysis failed: {e}"
