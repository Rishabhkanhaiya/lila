"""
cua_grounding.py — Generation-4 Visual Grounding & Set-of-Marks Engine (2026)
=============================================================================
Powers Tier-1 Computer Use with zero coordinate guessing:
  • Per-Monitor v2 DPI calibration (eliminates 125%/150% scaling click drift)
  • Windows UI Automation (UIA) control extraction (buttons, inputs, menus, tabs)
  • OpenCV adaptive contour fallback for canvas, Electron, and web controls
  • High-contrast Set-of-Marks (SoM) visual badge overlay ([1], [2], [3])
  • Visual Delta verification via cv2.absdiff to confirm screen state change
"""

import io
import os
import sys
import time
import ctypes
from typing import Optional, List, Dict, Tuple, Any
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import cv2

# Windows DPI & UIA imports
try:
    import uiautomation as auto
    HAS_UIA = True
except ImportError:
    HAS_UIA = False

try:
    import win32gui
    import win32con
    import win32api
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

from core.jarvis_logger import log_info, log_warn, log_error

# ─────────────────────────────────────────────────────────────────────────────
# 1. PER-MONITOR DPI CALIBRATION
# ─────────────────────────────────────────────────────────────────────────────

_DPI_INITIALIZED = False

def init_dpi_awareness():
    """Initializes Per-Monitor v2 DPI awareness to ensure 1:1 physical-to-logical pixel parity."""
    global _DPI_INITIALIZED
    if _DPI_INITIALIZED or sys.platform != "win32":
        return
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        _DPI_INITIALIZED = True
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            _DPI_INITIALIZED = True
        except Exception as e:
            log_warn("cua_grounding", f"DPI awareness init: {e}")

init_dpi_awareness()

def get_dpi_scale_factor(hwnd: int = 0) -> float:
    """Returns the current DPI scale factor (e.g. 1.0 for 100%, 1.25 for 125%, 1.5 for 150%)."""
    if sys.platform != "win32":
        return 1.0
    try:
        if hwnd and hasattr(ctypes.windll.user32, "GetDpiForWindow"):
            dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
            if dpi > 0:
                return dpi / 96.0
        # System DPI fallback
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return dpi / 96.0 if dpi > 0 else 1.0
    except Exception:
        return 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. SCREENSHOT CAPTURE IN-MEMORY
# ─────────────────────────────────────────────────────────────────────────────

def capture_raw_screen_pil() -> Optional[Image.Image]:
    """Captures the real interactive desktop in-memory as a PIL Image."""
    captured = [None]

    def _worker():
        try:
            import win32service
            winsta0 = win32service.OpenWindowStation('winsta0', False, 0x037F)
            winsta0.SetProcessWindowStation()
            hdesk = win32service.OpenDesktop('default', 0, False, 0x01FF)
            hdesk.SetThreadDesktop()
        except Exception:
            pass

        if HAS_MSS:
            try:
                import mss
                with mss.mss() as sct:
                    mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                    raw = sct.grab(mon)
                    captured[0] = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                    return
            except Exception as e:
                log_warn("cua_grounding", f"MSS capture fallback: {e}")

        try:
            from PIL import ImageGrab
            captured[0] = ImageGrab.grab().convert("RGB")
        except Exception as ex:
            log_error("cua_grounding", "ImageGrab fallback failed", ex)

    import threading
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=3.0)
    return captured[0]


# ─────────────────────────────────────────────────────────────────────────────
# 3. UI ELEMENT EXTRACTION (HYBRID UIA + OPENCV CONTOURS)
# ─────────────────────────────────────────────────────────────────────────────

INTERACTIVE_CONTROL_TYPES = {
    "ButtonControl", "EditControl", "MenuItemControl", "CheckBoxControl",
    "RadioButtonControl", "TabItemControl", "ComboBoxControl", "HyperlinkControl",
    "TreeItemControl", "ListItemControl", "SplitButtonControl", "ToolBarControl"
}

def extract_uia_interactive_elements(max_elements: int = 40) -> List[Dict[str, Any]]:
    """
    Extracts interactive controls from the foreground window using Windows UI Automation.
    Returns list of dicts: {"rect": (x1, y1, x2, y2), "name": str, "type": str, "control": ctrl}
    """
    if not HAS_UIA or not HAS_WIN32:
        return []

    elements = []
    try:
        fg_hwnd = win32gui.GetForegroundWindow()
        if not fg_hwnd:
            return []

        fg_ctrl = auto.ControlFromHandle(fg_hwnd)
        if not fg_ctrl:
            return []

        # Walk descendants up to depth 5
        for ctrl, depth in auto.WalkControl(fg_ctrl, maxDepth=5):
            try:
                ctype = ctrl.ControlTypeName
                if ctype in INTERACTIVE_CONTROL_TYPES:
                    rect = ctrl.BoundingRectangle
                    if rect and (rect.right - rect.left >= 14) and (rect.bottom - rect.top >= 10):
                        name = (ctrl.Name or "").strip()
                        elements.append({
                            "rect": (rect.left, rect.top, rect.right, rect.bottom),
                            "name": name,
                            "type": ctype.replace("Control", ""),
                            "control": ctrl
                        })
                        if len(elements) >= max_elements:
                            break
            except Exception:
                continue
    except Exception as e:
        log_warn("cua_grounding", f"UIA control extraction: {e}")

    return elements


def extract_opencv_visual_candidates(image_pil: Image.Image, max_candidates: int = 35) -> List[Dict[str, Any]]:
    """
    Fallback visual candidate detector using OpenCV edge & contour detection.
    Finds button-like rectangles, input boxes, and clickable UI tiles.
    """
    try:
        cv_img = cv2.cvtColor(np.array(image_pil), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
        
        # Adaptive thresholding to isolate UI borders
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2)
        
        # Morphological dilation to connect button edges
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (4, 3))
        dilated = cv2.dilate(thresh, kernel, iterations=1)
        
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        img_h, img_w = gray.shape
        candidates = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            # Filter realistic button / input box dimensions
            if 18 <= w <= (img_w * 0.7) and 14 <= h <= (img_h * 0.3) and (w * h) >= 300:
                candidates.append({
                    "rect": (x, y, x + w, y + h),
                    "name": f"UI_Element_{len(candidates)+1}",
                    "type": "VisualBox",
                    "control": None
                })
                if len(candidates) >= max_candidates:
                    break

        return candidates
    except Exception as e:
        log_warn("cua_grounding", f"OpenCV visual candidate extraction: {e}")
        return []


def deduplicate_boxes(boxes: List[Dict[str, Any]], iou_threshold: float = 0.5) -> List[Dict[str, Any]]:
    """Deduplicates overlapping bounding boxes using Intersection-over-Union (IoU)."""
    if not boxes:
        return []

    def iou(box1, box2):
        x1 = max(box1[0], box2[0])
        y1 = max(box1[1], box2[1])
        x2 = min(box1[2], box2[2])
        y2 = min(box1[3], box2[3])
        inter_area = max(0, x2 - x1) * max(0, y2 - y1)
        area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
        area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
        denom = float(area1 + area2 - inter_area)
        return inter_area / denom if denom > 0 else 0.0

    filtered = []
    for b in boxes:
        overlap = False
        for f in filtered:
            if iou(b["rect"], f["rect"]) > iou_threshold:
                overlap = True
                break
        if not overlap:
            filtered.append(b)
    return filtered


# ─────────────────────────────────────────────────────────────────────────────
# 4. SET-OF-MARKS (SoM) IMAGE TAGGING & REGISTRY BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_som_tagged_screen() -> Tuple[bytes, Dict[int, Dict[str, Any]], str]:
    """
    Captures the screen, extracts UI elements, and draws numbered Set-of-Marks badges.
    Returns:
      1. tagged_jpeg_bytes (for Gemini Vision prompt)
      2. element_registry: dict mapping element_id -> {center, rect, name, type, control}
      3. element_list_text (human-readable list of visible numbered elements)
    """
    pil_img = capture_raw_screen_pil()
    if not pil_img:
        raise RuntimeError("Failed to capture screen image for Set-of-Marks tagging.")

    img_w, img_h = pil_img.size

    # 1. Extract UIA controls first (highest semantic precision)
    uia_elements = extract_uia_interactive_elements(max_elements=30)
    
    # 2. Extract visual contours if UIA elements are sparse (e.g. web/canvas apps)
    if len(uia_elements) < 8:
        cv_elements = extract_opencv_visual_candidates(pil_img, max_candidates=25)
        combined = uia_elements + cv_elements
    else:
        combined = uia_elements

    elements = deduplicate_boxes(combined, iou_threshold=0.45)[:45]

    # Draw badges onto a copy of the image
    tagged_img = pil_img.copy()
    draw = ImageDraw.Draw(tagged_img)

    registry: Dict[int, Dict[str, Any]] = {}
    lines_desc = []

    badge_font = None
    try:
        # Use default PIL font or Arial if available
        badge_font = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        badge_font = ImageFont.load_default()

    # Color palette for badges (vibrant high contrast)
    box_color = (255, 50, 50)      # Bright Red
    badge_bg = (230, 20, 20)       # Solid Red Badge
    text_color = (255, 255, 255)   # Crisp White Text

    for idx, el in enumerate(elements, start=1):
        x1, y1, x2, y2 = el["rect"]
        # Clamp to screen bounds
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(img_w - 1, x2), min(img_h - 1, y2)
        if x2 <= x1 or y2 <= y1:
            continue

        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2

        # 1. Draw bounding box around element
        draw.rectangle([x1, y1, x2, y2], outline=box_color, width=2)

        # 2. Draw badge label [N] in the top-left corner
        tag_str = f"[{idx}]"
        badge_w = 26 + (len(str(idx)) - 1) * 8
        badge_h = 18
        bx1 = x1
        by1 = max(0, y1 - badge_h)
        bx2 = min(img_w - 1, x1 + badge_w)
        by2 = by1 + badge_h

        draw.rectangle([bx1, by1, bx2, by2], fill=badge_bg)
        draw.text((bx1 + 3, by1 + 1), str(idx), fill=text_color, font=badge_font)

        # Store in registry
        name = el.get("name") or "unlabeled"
        ctype = el.get("type") or "element"
        registry[idx] = {
            "id": idx,
            "center": (cx, cy),
            "rect": (x1, y1, x2, y2),
            "name": name,
            "type": ctype,
            "control": el.get("control")
        }
        lines_desc.append(f"[{idx}] {ctype}: '{name}' at ({cx}, {cy})")

    # Encode tagged image as JPEG bytes
    buffer = io.BytesIO()
    tagged_img.save(buffer, format="JPEG", quality=85)
    tagged_bytes = buffer.getvalue()

    element_list_str = "\n".join(lines_desc) if lines_desc else "No interactive elements detected."
    return tagged_bytes, registry, element_list_str


# ─────────────────────────────────────────────────────────────────────────────
# 5. VISUAL DELTA VERIFICATION
# ─────────────────────────────────────────────────────────────────────────────

def compute_visual_delta(before_bytes: bytes, after_bytes: bytes, min_threshold: int = 25) -> float:
    """
    Computes the percentage of pixels that changed between two screenshots (0.0 to 1.0).
    Used to verify if a click or typing action actually changed the on-screen UI state.
    """
    if not before_bytes or not after_bytes:
        return 0.0
    try:
        arr1 = np.frombuffer(before_bytes, dtype=np.uint8)
        arr2 = np.frombuffer(after_bytes, dtype=np.uint8)
        img1 = cv2.imdecode(arr1, cv2.IMREAD_GRAYSCALE)
        img2 = cv2.imdecode(arr2, cv2.IMREAD_GRAYSCALE)

        if img1 is None or img2 is None or img1.shape != img2.shape:
            return 0.0

        diff = cv2.absdiff(img1, img2)
        changed_pixels = np.count_nonzero(diff > min_threshold)
        total_pixels = img1.shape[0] * img1.shape[1]
        return float(changed_pixels) / float(total_pixels)
    except Exception as e:
        log_warn("cua_grounding", f"compute_visual_delta error: {e}")
        return 0.0
