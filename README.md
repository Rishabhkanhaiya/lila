# 🌸 Lila — AI Desktop Companion

<div align="center">
  <img src="lila_logo.png" width="120" alt="Lila Logo"/>
  <br/>
  <strong>Your always-on, emotionally intelligent AI companion for Windows</strong>
  <br/><br/>

  ![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)
  ![Gemini](https://img.shields.io/badge/AI-Gemini_2.5_Flash-orange?logo=google)
  ![Electron](https://img.shields.io/badge/Overlay-Electron_3D-9cf?logo=electron)
  ![License](https://img.shields.io/badge/License-MIT-green)
</div>

---

## What is Lila?

**Lila** is a persistent AI companion that lives on your Windows desktop as a beautiful animated 3D character. She listens, talks, understands Hindi + English, and controls your PC — all hands-free.

- 🎙️ **Always-on voice** — Gemini Live native audio, continuous conversation
- 🧠 **63+ tools** — Open apps, search web, control media, write code, automate tasks
- 📱 **Mobile remote** — Control from your phone via browser (no app install)
- 🌐 **Browser copilot** — Brave/Chrome extension to read pages and control browser
- 💃 **3D animated character** — VRM avatar with emotions, gestures, and expressions
- 🔒 **100% local** — All data stays on your machine. No cloud sync.

---

## Architecture

```
lila/
├── run_companion_bridge.py      ← Main entry: starts voice, bridge, mobile server
├── live_voice.py                ← Gemini Live audio thread
├── core/
│   ├── state_bridge.py          ← WebSocket bridge (ws://127.0.0.1:8765)
│   ├── fast_agent.py            ← Gemini agent + tool execution
│   ├── win_fast_voice.py        ← Voice command routing + all 63 tools
│   ├── remote_mobile_server.py  ← Mobile web UI server (port 8766)
│   ├── lila_cognitive_cortex.py ← Lila's mind: reasoning + awareness
│   ├── lila_emotion_engine.py   ← Real-time emotional state
│   ├── lila_lines.py            ← Personality dialog lines
│   ├── voice.py                 ← Text-to-speech (Edge-TTS / ElevenLabs)
│   ├── memory_engine.py         ← Long-term memory
│   └── ...                      ← 100+ more core modules
├── lila-overlay/                ← Electron 3D character overlay
│   ├── main.js                  ← Electron main process
│   ├── renderer/
│   │   ├── index.html           ← Overlay window
│   │   ├── app.js               ← Avatar controller
│   │   ├── three_avatar.js      ← Three.js + VRM renderer
│   │   └── style.css
│   └── assets/model/            ← VRM 3D character files
├── web_remote/                  ← Mobile browser UI
│   ├── index.html
│   ├── mobile.js
│   └── mobile.css
└── lila-browser-copilot/        ← Browser extension
    ├── manifest.json
    ├── background.js
    ├── content-script.js
    ├── offscreen.js
    └── popup.html
```

---

## Quick Start

### 1. Clone
```bash
git clone https://github.com/Rishabhkanhaiya/lila.git
cd lila
git lfs pull   # Download 3D VRM model files
```

### 2. Configure API Keys
```bash
cp .env.example .env
# Edit .env and add your Gemini API key (minimum required)
```

### 3. Install Python dependencies
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Install Electron overlay
```bash
cd lila-overlay
npm install
cd ..
```

### 5. Launch Lila
**Double-click** `Lila Companion.bat` or `start_lila_companion.bat`

Or run directly:
```bash
pythonw run_companion_bridge.py
```

---

## Mobile Remote

Once Lila is running, open your phone browser and go to:

```
http://<your-pc-ip>:8766
```

Find your PC IP with `ipconfig` on Windows. Works on local WiFi or Tailscale.

---

## Browser Copilot Extension

1. Open Brave/Chrome → `chrome://extensions/`
2. Enable **Developer mode**
3. Click **Load unpacked** → select the `lila-browser-copilot/` folder
4. Click the Lila icon in your toolbar

---

## Requirements

| Requirement | Version |
|---|---|
| Python | 3.11+ |
| Node.js | 18+ |
| Windows | 10/11 |
| RAM | 8 GB+ recommended |

**API Keys needed:**
- `GEMINI_API_KEY` — Required (get from [Google AI Studio](https://aistudio.google.com))
- Others are optional (for extended functionality)

---

## Configuration

All config is in your `.env` file (copy from `.env.example`). Key settings:

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Main AI brain |
| `GROQ_API_KEY` | Fast fallback LLM |
| `LILA_VAD_THRESHOLD` | Mic sensitivity (default: 260.0) |
| `MQTT_ENABLED` | Smart home control |

---

## Security

- ✅ All API keys are in `.env` (git-ignored, never pushed)
- ✅ All memory/databases are local only
- ✅ No telemetry, no cloud sync
- ✅ Browser extension uses local WebSocket only

---

## License

MIT — feel free to use, modify, and share.

---

*Made with 💜 by Rishabh Joshi*
