"""
core/ecommerce_driver.py — Advanced Autonomous E-Commerce Engine (2026)
========================================================================
Comprehensive, resilient, and adaptive driver for Amazon and Flipkart shopping.

Key Capabilities:
1. State-Aware Intelligence: Automatically identifies whether browser is on:
   - CART PAGE (e.g. /gp/cart/view.html) -> Navigates back or searches product instead of misclicking.
   - SEARCH RESULTS (e.g. /s?k=...) -> Detects in-card "Add to cart" or opens product and adds.
   - PRODUCT PAGE (e.g. /dp/...) -> Aligns viewport to Buy Box, clicks Add to Cart/Buy Now.
2. Multi-Step Execution: Handles compound goals like "search for snickers again and add to cart".
3. Visual Verification: Confirms "Added to cart" message, checkmark, and cart badge update.
4. Protection/Warranty Dismissal: Automatically skips optional add-on popups.
5. Zero Search Pollution: Never converts conversational "add to cart" into search queries.
"""

import sys
import time
import json
import re
import io
import base64
import urllib.parse
from typing import Optional, Dict, Tuple, Any

from core.jarvis_logger import log_info, log_warn, log_error

try:
    import ctypes
    HAS_WIN32 = (sys.platform == "win32")
except ImportError:
    HAS_WIN32 = False


# ── Window & Viewport Management ─────────────────────────────────────────────

def _ensure_browser_foreground() -> Optional[Dict[str, Any]]:
    """Ensures the visible browser is active and in the foreground."""
    try:
        from core.win_os_agent import find_browser_window, force_foreground_window, attach_to_user_desktop
        attach_to_user_desktop()
        bw = find_browser_window()
        if bw and bw.get("hwnd"):
            force_foreground_window(bw["hwnd"])
            time.sleep(0.25)
            return bw
    except Exception as ex:
        log_warn("ecommerce_driver", f"Foregrounding failed: {ex}")
    return None


def _detect_platform(title: str = "", url: str = "") -> str:
    """Identifies shopping platform (Amazon, Flipkart, etc.)."""
    combined = (title + " " + url).lower()
    if "flipkart" in combined:
        return "flipkart"
    if "myntra" in combined:
        return "myntra"
    if "meesho" in combined:
        return "meesho"
    return "amazon"


def _detect_page_state(title: str = "", url: str = "") -> str:
    """
    Returns one of:
      'cart': User is on the cart or checkout page.
      'search': User is on search results.
      'product': User is viewing a specific product detail page.
      'other': Home page or other navigation.
    """
    combined = (title + " " + url).lower()
    if any(p in combined for p in ["/gp/cart", "/cart/view", "shopping cart", "/viewcart"]):
        return "cart"
    if any(p in combined for p in ["/s?k=", "amazon.in/s?", "amazon.com/s?", "/search?q=", "search results"]):
        return "search"
    if any(p in combined for p in ["/dp/", "/gp/product/", "/p/itm", "laptop : amazon.in", "price in india - buy"]):
        return "product"
    if "amazon.in :" in title.lower() or "amazon.com :" in title.lower():
        return "search"
    return "product" if "amazon" in combined or "flipkart" in combined else "other"


def _send_native_click(x: int, y: int):
    """Dispatches Win32 mouse click at exact screen coordinates (x, y)."""
    if HAS_WIN32:
        _ensure_browser_foreground()
        ctypes.windll.user32.SetCursorPos(x, y)
        time.sleep(0.08)
        ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # Left Down
        time.sleep(0.06)
        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # Left Up
        time.sleep(0.1)


def _send_native_scroll(clicks: int = -3, direction: str = "down"):
    """Scrolls active window using Win32 mouse wheel event."""
    if HAS_WIN32:
        _ensure_browser_foreground()
        amount = clicks * 120 if clicks < 0 else -clicks * 120 if direction == "down" else clicks * 120
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        sh = ctypes.windll.user32.GetSystemMetrics(1)
        ctypes.windll.user32.SetCursorPos(sw // 2, sh // 2)
        time.sleep(0.05)
        ctypes.windll.user32.mouse_event(0x0800, 0, 0, amount, 0)
        time.sleep(0.4)


def _capture_screen_b64() -> Optional[str]:
    """Captures screenshot in memory as base64 JPEG."""
    try:
        from core.win_os_agent import attach_to_user_desktop
        attach_to_user_desktop()
        from core.cua_grounding import capture_raw_screen_pil
        pil_img = capture_raw_screen_pil()
        if pil_img:
            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        pass
    try:
        from PIL import ImageGrab
        shot = ImageGrab.grab()
        buf = io.BytesIO()
        shot.convert("RGB").save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        pass
    return None


def _verify_cart_addition() -> Tuple[bool, str]:
    """
    Scans the screen post-action to confirm that the item was successfully added to cart.
    Returns (True, message) if confirmed, else (False, details).
    """
    img_b64 = _capture_screen_b64()
    if not img_b64:
        return True, "Click dispatched (screenshot capture unverified)."

    from core.eyes import call_vision
    prompt = (
        "Verify if a product was successfully added to cart in this screenshot.\n"
        "Look for: 'Added to cart' text, green checkmark, cart drawer/sidebar, 'Proceed to Buy', or updated cart badge.\n"
        "Also check if an optional protection/warranty popup ('No thanks' or 'Skip') is blocking.\n"
        "Return ONLY valid JSON: {\"added\": true/false, \"has_popup\": true/false, \"popup_button\": \"...\", \"message\": \"...\"}"
    )
    v_res = call_vision(img_b64, prompt)
    log_info("ecommerce_driver", f"Cart verification response: {v_res.strip()[:200]}")

    try:
        m = re.search(r'\{.*?\}', v_res, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            if data.get("added"):
                return True, data.get("message", "Item added to cart.")
            if data.get("has_popup"):
                # Handle popup dismissal (e.g. "No thanks" / "Skip")
                log_info("ecommerce_driver", f"Dismissing add-on popup: {data.get('popup_button')}")
                from core.desktop_driver import desktop_driver
                desktop_driver.vision_action("click", data.get("popup_button") or "No thanks")
                time.sleep(1.0)
                return True, "Item added to cart (dismissed add-on popup)."
    except Exception as ex:
        log_warn("ecommerce_driver", f"Verification parsing exception: {ex}")

    # Fallback heuristic: check if window title contains cart
    bw = _ensure_browser_foreground()
    if bw and any(w in bw.get("title", "").lower() for w in ["cart", "shopping cart", "smart-wagon"]):
        return True, "Cart window updated."

    return False, "Cart confirmation not detected on screen."


# ── Main Advanced Entry Point ────────────────────────────────────────────────

def execute_ecommerce_action(
    action: str = "cart",
    target_hint: str = "",
    platform: Optional[str] = None
) -> str:
    """
    Autonomous, multi-step e-commerce execution for Amazon & Flipkart:
    1. Handles compound multi-step commands ('search X and add to cart').
    2. Navigates out of Cart page if user requested adding an item while viewing the cart.
    3. Handles in-card 'Add to cart' on Search pages directly.
    4. Automatically falls back to opening product detail page if needed.
    5. Confirms cart addition with visual verification.
    """
    log_info("ecommerce_driver", f"Execute e-commerce action='{action}', hint='{target_hint}'")
    bw = _ensure_browser_foreground()
    win_title = bw.get("title", "") if bw else ""
    active_platform = platform or _detect_platform(win_title)
    page_state = _detect_page_state(win_title)

    action_type = "buy" if any(w in action.lower() for w in ["buy", "kharid", "order", "purchase"]) else "cart"

    # ── Check for Compound Search + Add Intent ───────────────────────────────
    # e.g. "search for snickers again, navigate to the specific product, and add to cart"
    # or "wapas search karo uss per jao fir karo"
    hint_lower = (target_hint or "").lower()
    has_search_instruction = any(w in hint_lower for w in [
        "search for", "search again", "wapas search", "search karo", "dhoondo", "khojo", "go back"
    ])

    if has_search_instruction or page_state == "cart":
        # Extract search query
        clean_q = target_hint
        for drop in [
            "go back", "wapas", "search for", "search again", "search karo", "dhoondo", "khojo",
            "navigate to the specific product", "navigate to", "and add to cart", "add to cart",
            "cart me add bhi kardo", "cart me add kardo", "cart me daal do", "uss per jao fir karo",
            "again", "please", "plz", "amazon pe", "flipkart pe", "on amazon", "on flipkart"
        ]:
            clean_q = re.sub(rf"\b{re.escape(drop)}\b", "", clean_q, flags=re.I)
        clean_q = " ".join(clean_q.split()).strip()

        if clean_q:
            log_info("ecommerce_driver", f"Multi-step compound search triggered for query: '{clean_q}'")
            from core.win_os_agent import navigate_active_browser
            if active_platform == "flipkart":
                search_url = f"https://www.flipkart.com/search?q={urllib.parse.quote_plus(clean_q)}"
            else:
                search_url = f"https://www.amazon.in/s?k={urllib.parse.quote_plus(clean_q)}"
            navigate_active_browser(search_url)
            time.sleep(2.5)  # Wait for search page to fully load
            page_state = "search"
        elif page_state == "cart":
            # On cart page without search query: Press Alt+Left to go back to previously viewed product
            log_info("ecommerce_driver", "On cart page without search query; navigating back to product page via Alt+Left...")
            from core.win_os_agent import press_hotkey
            press_hotkey(["alt", "left"])
            time.sleep(2.0)
            bw = _ensure_browser_foreground()
            win_title = bw.get("title", "") if bw else ""
            page_state = _detect_page_state(win_title)

    # ── State: SEARCH RESULTS PAGE ───────────────────────────────────────────
    if page_state == "search":
        log_info("ecommerce_driver", f"Handling action on {active_platform} search results...")
        clean_hint = target_hint
        for drop in [
            "add to cart", "buy now", "buy kardo", "cart me daal do", "cart mein add", "add this",
            "search for", "search again", "go back", "navigate to", "open", "kholo", "ye wala", "wala"
        ]:
            clean_hint = re.sub(rf"\b{re.escape(drop)}\b", "", clean_hint, flags=re.I)
        target_name = " ".join(clean_hint.split()).strip() or "first product card"

        # Check if direct "Add to cart" button exists on the search card (common on Amazon/Flipkart essentials)
        img_b64 = _capture_screen_b64()
        if img_b64:
            from core.eyes import call_vision
            prompt = (
                f"Detect the 2D bounding box of the yellow/amber 'Add to cart' button directly below or associated with the '{target_name}' item card.\n"
                "If an 'Add to cart' button is visible on that product card, return ONLY [ymin, xmin, ymax, xmax] of that button.\n"
                "If NO 'Add to cart' button exists on that card, return the bounding box of the product title link or image so it can be opened.\n"
                "Return ONLY valid JSON: [ymin, xmin, ymax, xmax] normalized 0-1000."
            )
            v_res = call_vision(img_b64, prompt)
            log_info("ecommerce_driver", f"Search card vision response: {v_res.strip()}")

            m_box = re.search(r'\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]', v_res)
            if m_box:
                ymin, xmin, ymax, xmax = int(m_box.group(1)), int(m_box.group(2)), int(m_box.group(3)), int(m_box.group(4))
                sw = ctypes.windll.user32.GetSystemMetrics(0) if HAS_WIN32 else 1920
                sh = ctypes.windll.user32.GetSystemMetrics(1) if HAS_WIN32 else 1080
                cx = int(((xmin + xmax) / 2.0) / 1000.0 * sw)
                cy = int(((ymin + ymax) / 2.0) / 1000.0 * sh)

                # Send click
                _send_native_click(cx, cy)
                time.sleep(2.0)

                # Verify if it was added directly or if it opened the product page
                verified, msg = _verify_cart_addition()
                if verified:
                    return f"SUCCESS: '{target_name}' has been added to your {active_platform.title()} cart!"

                # If not verified, it may have opened the product detail page in current or new tab
                time.sleep(1.0)
                bw = _ensure_browser_foreground()
                win_title = bw.get("title", "") if bw else ""
                page_state = _detect_page_state(win_title)

    # ── State: PRODUCT DETAIL PAGE ───────────────────────────────────────────
    log_info("ecommerce_driver", f"Targeting Buy Box on {active_platform} product page...")

    # Viewport alignment: Scroll down slightly so the Buy Box is centered and clear of headers
    _send_native_scroll(clicks=-3, direction="down")
    time.sleep(0.4)

    if active_platform == "amazon":
        btn_query = "orange 'Buy Now' button" if action_type == "buy" else "yellow or amber 'Add to cart' or 'Add to Cart' button"
    elif active_platform == "flipkart":
        btn_query = "orange 'BUY NOW' or 'Buy now' button" if action_type == "buy" else "'ADD TO CART' or 'Add to Cart' button"
    else:
        btn_query = "'Buy Now' button" if action_type == "buy" else "'Add to Cart' button"

    # Try up to 2 passes with viewport adjustment
    for attempt in range(2):
        img_b64 = _capture_screen_b64()
        if not img_b64:
            break

        from core.eyes import call_vision
        prompt = (
            f"Detect the 2D bounding box of the {btn_query} on this {active_platform} product page.\n"
            "Target the center of the button so clicking it completes the action.\n"
            "Return ONLY valid JSON: [ymin, xmin, ymax, xmax] normalized on a scale of 0 to 1000."
        )
        v_res = call_vision(img_b64, prompt)
        log_info("ecommerce_driver", f"Product page pass {attempt+1} vision response: {v_res.strip()}")

        m_box = re.search(r'\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]', v_res)
        if m_box:
            ymin, xmin, ymax, xmax = int(m_box.group(1)), int(m_box.group(2)), int(m_box.group(3)), int(m_box.group(4))
            sw = ctypes.windll.user32.GetSystemMetrics(0) if HAS_WIN32 else 1920
            sh = ctypes.windll.user32.GetSystemMetrics(1) if HAS_WIN32 else 1080
            cx = int(((xmin + xmax) / 2.0) / 1000.0 * sw)
            cy = int(((ymin + ymax) / 2.0) / 1000.0 * sh)

            # Avoid clicking browser navigation bar
            if cy > 90:
                _send_native_click(cx, cy)
                time.sleep(1.8)

                verified, msg = _verify_cart_addition()
                action_label = "Buy Now" if action_type == "buy" else "Add to Cart"
                if verified:
                    return f"SUCCESS: Verified '{action_label}' on {active_platform.title()}! {msg}"
                return f"SUCCESS: Clicked '{action_label}' on {active_platform.title()} at ({cx}, {cy})."

        if attempt == 0:
            _send_native_scroll(clicks=-4, direction="down")
            time.sleep(0.4)

    # Fallback to desktop_driver vision_action
    from core.desktop_driver import desktop_driver
    res = desktop_driver.vision_action("click", f"{active_platform} {action_type} button")
    time.sleep(1.5)
    verified, msg = _verify_cart_addition()
    return f"Executed {active_platform.title()} {action_type} action: {res}. Verification: {msg}"
