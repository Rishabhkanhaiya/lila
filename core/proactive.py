import time
import threading
import psutil
import datetime
import json
from core.brain import call_groq_brain

class ProactiveDaemonThread(threading.Thread):
    def __init__(self, ui_signal=None):
        super().__init__()
        self.daemon = True
        self.ui_signal = ui_signal
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()
        
    def run(self):
        # Initial sleep interruptible by stop
        if self._stop_event.wait(60):
            return
        
        while not self._stop_event.is_set():
            try:
                # Gather state
                cpu = psutil.cpu_percent(interval=1)
                mem = psutil.virtual_memory().percent
                now = datetime.datetime.now().strftime("%I:%M %p")
                
                # ⚡ BIOMETRIC SENSOR ⚡
                bio_context = ""
                try:
                    from core.biometrics import analyze_emotion
                    import core.brain as brain_mod
                    
                    bio_data = analyze_emotion()
                    if bio_data and isinstance(bio_data, dict):
                        mood = bio_data.get("mood", "Neutral")
                        adj = bio_data.get("adjustment", "None")
                        
                        brain_mod.CURRENT_EMOTION = mood
                        brain_mod.CURRENT_TONE_ADJUSTMENT = adj
                        
                        bio_context = f"\nBiometric User Emotion: {mood}. Required tone adjustment: {adj}."
                except Exception as e:
                    pass
                
                context = f"Current Time: {now}. CPU Usage: {cpu}%. Memory Usage: {mem}%. {bio_context}"
                
                prompt = f"""
You are JARVIS's Proactive Subconscious.
{context}
Analyze this system state. If there is a CRITICAL anomaly (e.g. CPU > 90%, Memory > 90%), you should speak to the user to warn them.
If nothing is wrong and you don't need to speak, return exactly: {{"reply": "SILENT"}}

Return ONLY valid JSON. Example if warning:
{{"reply": "Umm, I notice a CPU spike of 95%. Should I check what's causing it?"}}
"""
                # Use logic_task=False so we don't force execute_jarvis_action tool
                res = call_groq_brain(prompt, phase="DIRECTIVE", is_logic_task=False)
                
                reply_text = ""
                if isinstance(res, str):
                    try:
                        import re
                        clean = re.sub(r'^```json\s*', '', res, flags=re.IGNORECASE)
                        clean = re.sub(r'^```\s*', '', clean)
                        clean = re.sub(r'\s*```$', '', clean).strip()
                        parsed = json.loads(clean)
                        reply_text = parsed.get("reply", "SILENT")
                    except Exception:
                        reply_text = res
                
                if "SILENT" not in reply_text.upper() and len(reply_text) > 5:
                    print(f"\n[🚨 PROACTIVE DAEMON]: {reply_text}")
                    if self.ui_signal:
                        self.ui_signal.emit("speaking", f"SYSTEM_REPLY:{reply_text}")
                    from core.voice import speak
                    speak(reply_text)
                    
            except Exception as e:
                print(f"[⚠️ PROACTIVE DAEMON ERROR]: {e}")
                
            # Wait 5 minutes before checking again
            if self._stop_event.wait(300): break
