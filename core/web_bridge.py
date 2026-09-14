"""
core/web_bridge.py — Modern QWebChannel IPC Bridge for JARVIS PRO
===================================================================
Replaces the legacy 80ms document.title polling loop with native,
event-driven bidirectional signals and slots between Python and JavaScript.
"""

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot
import json
from concurrent.futures import ThreadPoolExecutor

_bridge_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="JarvisWebBridgeWorker")

class JarvisWebBridge(QObject):
    """Native QWebChannel bridge object exposed to window.pybridge in JavaScript."""

    # ── Signals emitted from Python to JavaScript ─────────────────────────────
    statusChanged = pyqtSignal(str, str)         # (status_string, message_string)
    transcriptReceived = pyqtSignal(str, str)    # (role, text)
    chatMessage = pyqtSignal(str, str)           # (role, text)
    flagToast = pyqtSignal(str, bool)            # (feature_name, state)
    micStateChanged = pyqtSignal(bool)           # (is_active)
    quotaDataReady = pyqtSignal(str)            # (json_string)
    nodeSpawned = pyqtSignal(str, int, int)      # (node_json, x, y)

    def __init__(self, main_window=None):
        super().__init__()
        self.main_window = main_window

    # ── Slots called directly from JavaScript ─────────────────────────────────

    @pyqtSlot(str)
    def sendCommand(self, cmd: str):
        """Called by UI input box when user submits a text command."""
        cmd = str(cmd).strip()
        if not cmd:
            return
        if self.main_window:
            self.main_window._dispatch_text_command(cmd)

    @pyqtSlot(str)
    def sendTextMessage(self, cmd: str):
        """Alias for sendCommand to ensure 100% compatibility with JS UI calls."""
        self.sendCommand(cmd)

    @pyqtSlot()
    def triggerVaultUpload(self):
        """Trigger file upload dialog for Vector Vault."""
        if self.main_window and hasattr(self.main_window, '_handle_title'):
            self.main_window._handle_title("M3:vault_upload")

    @pyqtSlot()
    def openQuotaDashboard(self):
        """Open API quota dashboard."""
        if self.main_window and hasattr(self.main_window, '_handle_title'):
            self.main_window._handle_title("M3:open_quota_dashboard")

    @pyqtSlot()
    def toggleVoice(self):
        """Called by UI mic button to toggle recording."""
        if self.main_window and hasattr(self.main_window, 'voice_thread') and self.main_window.voice_thread:
            self.main_window.voice_thread.toggle_recording()
        elif self.main_window:
            self.main_window.update_ui("listening", "VOICE LISTENING TOGGLED")

    @pyqtSlot()
    def startVoice(self):
        """Called when PTT key or mic is pressed down."""
        if self.main_window and hasattr(self.main_window, 'voice_thread') and self.main_window.voice_thread:
            self.main_window.voice_thread.start_recording()

    @pyqtSlot()
    def stopVoice(self):
        """Called when PTT key or mic is released."""
        if self.main_window and hasattr(self.main_window, 'voice_thread') and self.main_window.voice_thread:
            self.main_window.voice_thread.stop_recording()

    @pyqtSlot(str)
    def triggerAstraResearch(self, topic: str):
        """Triggers deep research for a given topic."""
        topic = str(topic).strip()
        if not topic or not self.main_window:
            return
        self.main_window.update_ui("thinking", f"🔬 ASTRA DEEP RESEARCH: {topic[:30]}...")
        def _run_astra():
            try:
                from core.astra_research import AstraResearchEngine
                engine = AstraResearchEngine()
                res = engine.deep_research(topic, breadth=3)
                exec_sum = res.get("executive_summary", "")
                vault_id = res.get("vault_id", "N/A")
                sources_count = res.get("sources_count", 0)
                msg = f"**Astra Executive Briefing: {topic}**\n\n{exec_sum}\n\n*Verified across {sources_count} sources and indexed into Neural Vault ({vault_id})*"
                self.main_window.update_ui("speaking", f"SYSTEM_REPLY:{msg}")
                try:
                    from core.voice import speak_async
                    first_sentence = exec_sum.split(". ")[0] if exec_sum else "Research complete."
                    speak_async(f"Rishabh, research complete ho gaya hai. {first_sentence}")
                except Exception:
                    pass
            except Exception as ae:
                self.main_window.update_ui("speaking", f"SYSTEM_REPLY:Astra research error: {ae}")
        _bridge_executor.submit(_run_astra)

    @pyqtSlot(str, bool)
    def setFlag(self, feature: str, value: bool):
        """Updates a feature flag in the system."""
        try:
            from core.feature_flags import set_flag
            set_flag(feature, value)
            self.flagToast.emit(feature, value)
        except Exception as e:
            print(f"[SET FLAG ERR]: {e}")

    @pyqtSlot()
    def getFlags(self):
        """Fetches all feature flags and pushes them to UI."""
        try:
            import os
            from core.feature_flags import get_all
            flags = get_all()
            features_path = os.path.join(os.path.dirname(__file__), "..", "jarvis_features.json")
            if os.path.exists(features_path):
                with open(features_path, "r", encoding="utf-8") as f:
                    fdata = json.load(f)
                    if "preferred_model" in fdata:
                        flags["preferred_model"] = fdata["preferred_model"]
            if self.main_window:
                self.main_window.run_js(f"if (typeof applyFeatureFlags === 'function') applyFeatureFlags({json.dumps(flags)});")
        except Exception as e:
            print(f"[GET FLAGS ERR]: {e}")

    @pyqtSlot()
    def minimizeWindow(self):
        if self.main_window:
            self.main_window.showMinimized()

    @pyqtSlot()
    def closeWindow(self):
        if self.main_window:
            self.main_window.close()

    @pyqtSlot()
    def restoreFullscreen(self):
        if self.main_window:
            if self.main_window.isMaximized():
                self.main_window.showNormal()
            else:
                self.main_window.showMaximized()
