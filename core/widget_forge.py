"""
core/widget_forge.py — JARVIS Widget Forge
==========================================
Spawns always-on-top, transparent, draggable desktop widgets
as separate frameless PyQt5 windows.

Available widgets:
  - clock:        Live date/time
  - timer:        Countdown timer (e.g. "25 minutes")
  - stock:        Live stock ticker (e.g. TSLA, AAPL)
  - weather:      Current weather for a city
  - note:         Sticky note widget

Voice usage:
  "Jarvis, spawn a clock widget"
  "Jarvis, open a timer widget for 25 minutes"
  "Jarvis, show a stock widget for Tesla"
  "Jarvis, create a weather widget for Delhi"
  "Jarvis, close all widgets"
  "Jarvis, close the clock widget"
"""

import sys
import json
import threading
from typing import Optional, Any
from concurrent.futures import ThreadPoolExecutor
import httpx
from core.jarvis_logger import log_error, log_warn, log_info

_widget_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="WidgetForgeWorker")

try:
    from PyQt5.QtWidgets import (
        QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QSizeGrip,
        QPushButton, QGraphicsDropShadowEffect
    )
    from PyQt5.QtCore import Qt, QTimer, QPoint, QObject, pyqtSignal
    from PyQt5.QtGui import QFont, QColor, QPainter, QBrush, QPen
    HAS_QT = True
except ImportError:
    HAS_QT = False
    log_warn("widget_forge", "PyQt5 not available — widgets disabled.")

# ── Widget registry — tracks all open widgets ────────────────────────────────
_widgets: dict[str, "BaseWidget"] = {}
_lock = threading.Lock()
_app_ref = None  # We reuse the existing QApplication


# ─────────────────────────────────────────────────────────────────────────────
# BASE WIDGET CLASS
# ─────────────────────────────────────────────────────────────────────────────

class BaseWidget(QWidget):
    """A draggable, frameless, always-on-top HUD widget."""

    WIDGET_W = 280
    WIDGET_H = 120

    # Dark glass theme
    BG_COLOR   = "rgba(3, 6, 15, 220)"
    BORDER_CSS = "1px solid rgba(0, 240, 255, 0.4)"
    CYAN       = "#00f0ff"
    GREEN      = "#00ff9d"
    DIM        = "rgba(200, 230, 255, 0.45)"

    def __init__(self, widget_id: str):
        super().__init__()
        self.widget_id = widget_id
        self._drag_pos = QPoint()

        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.WIDGET_W, self.WIDGET_H)

        # Glass container
        self._container = QWidget(self)
        self._container.setGeometry(0, 0, self.WIDGET_W, self.WIDGET_H)
        self._container.setStyleSheet(f"""
            QWidget {{
                background: {self.BG_COLOR};
                border: {self.BORDER_CSS};
                border-radius: 8px;
            }}
        """)

        # Drop shadow
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 240, 255, 60))
        shadow.setOffset(0, 0)
        self._container.setGraphicsEffect(shadow)

        # Close button
        self._close_btn = QPushButton("✕", self)
        self._close_btn.setGeometry(self.WIDGET_W - 24, 6, 18, 18)
        self._close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {self.DIM};
                border: none;
                font-size: 10px;
            }}
            QPushButton:hover {{
                color: {self.CYAN};
            }}
        """)
        self._close_btn.clicked.connect(self._destroy)

        # Layout inside container
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(12, 8, 12, 8)
        self._layout.setSpacing(4)

        self.build_ui()

        # Default position: bottom-right stack
        with _lock:
            offset = len(_widgets) * (self.WIDGET_H + 10)
        from PyQt5.QtWidgets import QDesktopWidget
        screen = QDesktopWidget().availableGeometry()
        self.move(screen.width() - self.WIDGET_W - 20, screen.height() - self.WIDGET_H - 60 - offset)

    def build_ui(self):
        """Override in subclasses to build widget-specific UI."""
        pass

    def _label(self, text: str, size: int = 11, color: str = None, bold: bool = False) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"""
            QLabel {{
                color: {color or self.DIM};
                background: transparent;
                border: none;
                font-size: {size}px;
                {"font-weight: bold;" if bold else ""}
            }}
        """)
        return lbl

    def _destroy(self):
        with _lock:
            _widgets.pop(self.widget_id, None)
        self.close()

    # Dragging
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)


# ─────────────────────────────────────────────────────────────────────────────
# CLOCK WIDGET
# ─────────────────────────────────────────────────────────────────────────────

class ClockWidget(BaseWidget):
    WIDGET_H = 110

    def build_ui(self):
        header = self._label("[ JARVIS CLOCK ]", 8, self.CYAN)
        self._layout.addWidget(header)

        self._time_lbl = self._label("--:--:--", 32, self.GREEN, bold=True)
        self._time_lbl.setAlignment(Qt.AlignCenter)
        self._layout.addWidget(self._time_lbl)

        self._date_lbl = self._label("", 10, self.DIM)
        self._date_lbl.setAlignment(Qt.AlignCenter)
        self._layout.addWidget(self._date_lbl)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    def _tick(self):
        from datetime import datetime
        now = datetime.now()
        self._time_lbl.setText(now.strftime("%H:%M:%S"))
        self._date_lbl.setText(now.strftime("%A, %d %B %Y"))


# ─────────────────────────────────────────────────────────────────────────────
# COUNTDOWN TIMER WIDGET
# ─────────────────────────────────────────────────────────────────────────────

class TimerWidget(BaseWidget):
    WIDGET_H = 130

    def __init__(self, widget_id: str, seconds: int, label: str = ""):
        self._total_secs = seconds
        self._remaining  = seconds
        self._timer_label = label
        self._running    = True
        super().__init__(widget_id)

    def build_ui(self):
        header = self._label(f"[ TIMER: {self._timer_label or 'COUNTDOWN'} ]", 8, self.CYAN)
        self._layout.addWidget(header)

        self._time_lbl = self._label("", 32, self.GREEN, bold=True)
        self._time_lbl.setAlignment(Qt.AlignCenter)
        self._layout.addWidget(self._time_lbl)

        btn_row = QHBoxLayout()
        for txt, fn in [("⏸", self._toggle), ("⟳", self._reset)]:
            btn = QPushButton(txt)
            btn.setStyleSheet(f"QPushButton{{background:transparent;color:{self.CYAN};border:1px solid rgba(0,240,255,0.3);padding:2px 8px;border-radius:3px;}}QPushButton:hover{{color:{self.GREEN};}}")
            btn.clicked.connect(fn)
            btn_row.addWidget(btn)
        self._layout.addLayout(btn_row)

        self._qt_timer = QTimer(self)
        self._qt_timer.timeout.connect(self._tick)
        self._qt_timer.start(1000)
        self._update_display()

    def _update_display(self):
        m, s = divmod(max(0, self._remaining), 60)
        h, m = divmod(m, 60)
        if h:
            self._time_lbl.setText(f"{h:02d}:{m:02d}:{s:02d}")
        else:
            self._time_lbl.setText(f"{m:02d}:{s:02d}")
        color = self.GREEN if self._remaining > 60 else "#ff4d6d"
        self._time_lbl.setStyleSheet(f"QLabel{{color:{color};background:transparent;border:none;font-size:32px;font-weight:bold;}}")

    def _tick(self):
        if self._running and self._remaining > 0:
            self._remaining -= 1
            self._update_display()
        elif self._remaining == 0:
            self._qt_timer.stop()
            self._time_lbl.setText("DONE!")
            try:
                from core.voice import speak
                speak(f"Timer {self._timer_label} is done.")
            except Exception:
                pass

    def _toggle(self):
        self._running = not self._running

    def _reset(self):
        self._remaining = self._total_secs
        self._running = True
        self._qt_timer.start(1000)
        self._update_display()


# ─────────────────────────────────────────────────────────────────────────────
# STOCK TICKER WIDGET
# ─────────────────────────────────────────────────────────────────────────────

class StockWidget(BaseWidget):
    WIDGET_H = 130

    def __init__(self, widget_id: str, symbol: str):
        self._symbol = symbol.upper()
        super().__init__(widget_id)

    def build_ui(self):
        header = self._label(f"[ STOCK: {self._symbol} ]", 8, self.CYAN)
        self._layout.addWidget(header)

        self._price_lbl = self._label("Loading...", 28, self.GREEN, bold=True)
        self._price_lbl.setAlignment(Qt.AlignCenter)
        self._layout.addWidget(self._price_lbl)

        self._change_lbl = self._label("", 10, self.DIM)
        self._change_lbl.setAlignment(Qt.AlignCenter)
        self._layout.addWidget(self._change_lbl)

        self._qt_timer = QTimer(self)
        self._qt_timer.timeout.connect(self._fetch)
        self._qt_timer.start(30000)  # refresh every 30s
        _widget_executor.submit(self._fetch_bg)

    def _fetch(self):
        _widget_executor.submit(self._fetch_bg)

    def _fetch_bg(self):
        try:
            import yfinance as yf
            tick = yf.Ticker(self._symbol)
            info = tick.fast_info
            price = info.last_price
            prev  = info.previous_close
            change = price - prev
            pct    = (change / prev) * 100 if prev else 0
            arrow  = "▲" if change >= 0 else "▼"
            color  = "#00ff9d" if change >= 0 else "#ff4d6d"
            self._price_lbl.setText(f"${price:.2f}")
            self._price_lbl.setStyleSheet(f"QLabel{{color:{color};background:transparent;border:none;font-size:28px;font-weight:bold;}}")
            self._change_lbl.setText(f"{arrow} {change:+.2f} ({pct:+.2f}%)")
            self._change_lbl.setStyleSheet(f"QLabel{{color:{color};background:transparent;border:none;font-size:10px;}}")
        except Exception as e:
            self._price_lbl.setText("Error")
            log_warn("widget_forge", f"Stock fetch failed ({self._symbol}): {e}")


# ─────────────────────────────────────────────────────────────────────────────
# WEATHER WIDGET
# ─────────────────────────────────────────────────────────────────────────────

class WeatherWidget(BaseWidget):
    WIDGET_H = 130

    def __init__(self, widget_id: str, city: str):
        self._city = city
        super().__init__(widget_id)

    def build_ui(self):
        header = self._label(f"[ WEATHER: {self._city.upper()} ]", 8, self.CYAN)
        self._layout.addWidget(header)

        self._temp_lbl = self._label("Loading...", 26, self.GREEN, bold=True)
        self._temp_lbl.setAlignment(Qt.AlignCenter)
        self._layout.addWidget(self._temp_lbl)

        self._desc_lbl = self._label("", 10, self.DIM)
        self._desc_lbl.setAlignment(Qt.AlignCenter)
        self._layout.addWidget(self._desc_lbl)

        self._qt_timer = QTimer(self)
        self._qt_timer.timeout.connect(self._fetch)
        self._qt_timer.start(600000)  # refresh every 10 min
        _widget_executor.submit(self._fetch_bg)

    def _fetch(self):
        _widget_executor.submit(self._fetch_bg)

    def _fetch_bg(self):
        try:
            url = f"https://wttr.in/{self._city}?format=j1"
            r = httpx.get(url, timeout=8.0)
            data = r.json()
            current = data["current_condition"][0]
            temp_c  = current["temp_C"]
            feels   = current["FeelsLikeC"]
            desc    = current["weatherDesc"][0]["value"]
            humidity= current["humidity"]
            self._temp_lbl.setText(f"{temp_c}°C  (feels {feels}°C)")
            self._desc_lbl.setText(f"{desc} | Humidity: {humidity}%")
        except Exception as e:
            self._temp_lbl.setText("Unavailable")
            log_warn("widget_forge", f"Weather fetch failed: {e}")


class NoteWidget(BaseWidget):
    """A floating sticky note widget for quick reminders."""
    WIDGET_H = 140

    def __init__(self, widget_id: str, note_text: str = "JARVIS Sticky Note"):
        self._note_text = note_text
        super().__init__(widget_id)

    def build_ui(self):
        header = self._label("[ STICKY NOTE ]", 8, self.CYAN)
        self._layout.addWidget(header)

        self._content_lbl = self._label(self._note_text, 12, "#e2e8f0")
        self._content_lbl.setWordWrap(True)
        self._content_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._layout.addWidget(self._content_lbl)


# ─────────────────────────────────────────────────────────────────────────────
# Qt Cross-Thread Bridge & PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

class _WidgetBridge(QObject):
    """Queued connection bridge to ensure all widget creation runs on main GUI thread."""
    spawn_signal = pyqtSignal(object, str, tuple)

    def __init__(self):
        super().__init__()
        self.spawn_signal.connect(self._do_spawn)

    def _do_spawn(self, cls, wid, args):
        try:
            w = cls(wid, *args)
            with _lock:
                _widgets[wid] = w
            w.show()
            log_info("widget_forge", f"Widget {wid} shown on main GUI thread.")
        except Exception as e:
            log_error("widget_forge", f"_do_spawn_{wid}", e)

_bridge: Optional[_WidgetBridge] = None

def _get_bridge() -> Optional[_WidgetBridge]:
    global _bridge, _app_ref
    if _bridge is None and HAS_QT:
        _app_ref = QApplication.instance()
        if _app_ref is None:
            _app_ref = QApplication(sys.argv)
        _bridge = _WidgetBridge()
        _bridge.moveToThread(_app_ref.thread())
    return _bridge

def _spawn_on_main_thread(cls, widget_id: str, *args):
    """Spawns a widget safely from any worker/voice thread onto the Qt GUI thread."""
    try:
        bridge = _get_bridge()
        if bridge:
            bridge.spawn_signal.emit(cls, widget_id, args)
        else:
            # Fallback if no bridge
            w = cls(widget_id, *args)
            with _lock:
                _widgets[widget_id] = w
            w.show()
    except Exception as e:
        log_error("widget_forge", "_spawn_on_main_thread", e)


def spawn_widget(widget_type: str, config: Optional[Any] = None, **kwargs) -> str:
    """
    Spawns a desktop widget. widget_type: 'stocks'|'stock'|'clock'|'timer'|'weather'|'notes'|'note'
    Accepts config as dictionary, JSON string, or kwargs.
    """
    if not HAS_QT:
        return "PyQt5 not available — widgets disabled."

    # Parse config if passed as 2nd positional or dict
    if isinstance(config, dict):
        kwargs = {**config, **kwargs}
    elif isinstance(config, str) and config.strip():
        try:
            parsed = json.loads(config)
            if isinstance(parsed, dict):
                kwargs = {**parsed, **kwargs}
        except Exception:
            pass

    wtype = str(widget_type).lower().strip()

    if wtype in ("stock", "stocks", "ticker", "graph", "chart"):
        symbol = kwargs.get("symbol") or kwargs.get("ticker") or kwargs.get("stock") or "AAPL"
        symbol = str(symbol).upper().strip()
        wid    = f"stock_{symbol}"
        if wid in _widgets:
            return f"{symbol} stock widget already open hai, Rishabh."
        _spawn_on_main_thread(StockWidget, wid, symbol)
        return f"Real-time graph widget for {symbol} spawned on desktop, Rishabh."

    elif wtype in ("clock", "watch", "time"):
        wid = "clock"
        if wid in _widgets:
            return "Clock widget already open hai, Rishabh."
        _spawn_on_main_thread(ClockWidget, wid)
        return "Clock widget spawned on desktop, Rishabh."

    elif wtype in ("timer", "stopwatch", "countdown"):
        seconds = int(kwargs.get("seconds", 300))
        label   = kwargs.get("label", "")
        wid     = f"timer_{seconds}"
        _spawn_on_main_thread(TimerWidget, wid, seconds, label)
        m, s = divmod(seconds, 60)
        return f"Timer set for {m}m {s}s, Rishabh."

    elif wtype in ("weather", "forecast", "temp"):
        city = kwargs.get("city", "Delhi")
        wid  = f"weather_{str(city).lower().replace(' ', '_')}"
        if wid in _widgets:
            return f"Weather widget for {city} already open hai, Rishabh."
        _spawn_on_main_thread(WeatherWidget, wid, str(city))
        return f"Weather widget for {city} spawned on desktop, Rishabh."

    elif wtype in ("note", "notes", "todo", "sticky"):
        text = kwargs.get("text") or kwargs.get("content") or "JARVIS Active Note"
        wid = f"note_{len(_widgets) + 1}"
        _spawn_on_main_thread(NoteWidget, wid, str(text))
        return "Sticky Note widget spawned on desktop, Rishabh."

    return f"Unknown widget type: {widget_type}. Available: 'stocks', 'clock', 'timer', 'weather', 'notes'."


def close_widget(widget_type: str) -> str:
    """Closes a specific type of widget."""
    if not HAS_QT:
        return "PyQt5 not available."

    wtype = widget_type.lower().strip()
    closed = []
    with _lock:
        to_remove = [wid for wid in list(_widgets.keys()) if wtype in wid]
    for wid in to_remove:
        w = _widgets.get(wid)
        if w:
            QTimer.singleShot(0, w.close)
            with _lock:
                _widgets.pop(wid, None)
            closed.append(wid)

    if closed:
        return f"Closed: {', '.join(closed)}, Rishabh."
    return f"No {widget_type} widget is open."


def close_all_widgets() -> str:
    """Closes all open widgets."""
    if not HAS_QT:
        return "PyQt5 not available."

    with _lock:
        all_ids = list(_widgets.keys())
    for wid in all_ids:
        w = _widgets.get(wid)
        if w:
            QTimer.singleShot(0, w.close)
    with _lock:
        _widgets.clear()

    if all_ids:
        return f"Closed {len(all_ids)} widget(s), Rishabh."
    return "No widgets are currently open."


def get_active_widgets() -> list[str]:
    """Returns list of currently active widget IDs."""
    with _lock:
        return list(_widgets.keys())
