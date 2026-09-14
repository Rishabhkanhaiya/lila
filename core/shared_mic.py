import sounddevice as sd
import numpy as np
import threading

class SharedMic:
    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(SharedMic, cls).__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self):
        self.samplerate = 16000
        self.channels = 1
        self.stream = None
        self.is_running = False
        self.subscribers = []
        
    def start(self):
        with self._lock:
            if self.is_running:
                return
            self.is_running = True
            
        def _callback(indata, frames, time, status):
            chunk = indata.copy()
            with self._lock:
                subs = list(self.subscribers)
            for cb in subs:
                try:
                    cb(chunk)
                except Exception as e:
                    pass
                
        # Select non-Bluetooth microphone to keep Bluetooth headphones in high-res A2DP mode
        device_idx = None
        try:
            devices = sd.query_devices()
            for idx, d in enumerate(devices):
                if d.get("max_input_channels", 0) > 0:
                    name = d.get("name", "").lower()
                    if any(k in name for k in ["microphone array", "mic array", "realtek"]) and not any(k in name for k in ["headset", "hands-free", "bthhfenum"]):
                        device_idx = idx
                        break
        except Exception:
            device_idx = None

        self.stream = sd.InputStream(device=device_idx, samplerate=self.samplerate, channels=self.channels, dtype=np.int16, callback=_callback)
        self.stream.start()

    def stop(self):
        with self._lock:
            if not self.is_running: return
            self.is_running = False
            
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    def subscribe(self, callback):
        with self._lock:
            if callback not in self.subscribers:
                self.subscribers.append(callback)
                if not self.is_running:
                    self.start()

    def unsubscribe(self, callback):
        with self._lock:
            if callback in self.subscribers:
                self.subscribers.remove(callback)
                if not self.subscribers and self.is_running:
                    self.stop()

shared_mic = SharedMic()
