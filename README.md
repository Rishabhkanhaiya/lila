# 🌸 Lila — AI Desktop Companion

<div align="center">
  <img src="lila_logo.png" width="160" alt="Lila (Ana)"/>
  <br/><br/>
  <strong>A fully local, emotionally intelligent AI companion that lives on your Windows desktop as a beautiful 3D animated character.</strong>
  <br/>Featuring <strong>Ana</strong> as Lila's canonical 3D avatar. She listens, talks, understands Hindi + English, controls your PC, and connects to your phone — all hands-free.
  <br/><br/>

  ![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)
  ![Gemini](https://img.shields.io/badge/Gemini_2.5_Flash-Live_Audio-orange?logo=google&logoColor=white)
  ![Electron](https://img.shields.io/badge/Electron_34-3D_Overlay-9cf?logo=electron)
  ![Three.js](https://img.shields.io/badge/Three.js-VRM_Avatar-black?logo=three.js)
  ![License](https://img.shields.io/badge/License-MIT-green)
  ![Platform](https://img.shields.io/badge/Platform-Windows_10%2F11-blue?logo=windows)
</div>

---

## What is Lila?

Lila is a **real, working AI companion** — not a chatbot, not a widget. She is a multi-process system combining:

- A **Python supervisor** (`run_companion_bridge.py`) that orchestrates voice, tools, memory, and remote connections
- A **Gemini Live native audio** thread for continuous, natural voice conversation
- An **Electron 3D overlay** rendering an animated VRM character that floats on your desktop
- A **FastAPI mobile server** so you can talk to Lila from your phone browser — no app install
- A **Brave/Chrome browser extension** that gives Lila full browser awareness and control
- **63+ registered tools** covering Windows control, web browsing, file operations, screen vision, code, memory, and more

Everything runs locally on your PC. No cloud sync, no account, no data leaves your machine.

---

## System Architecture

```
start_lila_companion.bat  ←  Windows launcher (resolves Python, cleans stale processes, picks VRM model)
  │
  ├──▶ run_companion_bridge.py  (Python supervisor — coordinates everything)
  │       │
  │       ├──▶ core/state_bridge.py          ws://127.0.0.1:8765  ← Local RPC & UI state channel
  │       ├──▶ live_voice.LiveVoiceThread     Gemini Live native audio (16kHz mic → 24kHz speaker)
  │       ├──▶ core/live_tools.py             63+ Gemini function declarations & dispatcher
  │       ├──▶ core/omniforge.py              http://localhost:5252  ← Local app-builder server
  │       └──▶ core/remote_mobile_server.py   http://0.0.0.0:8766  ← Mobile companion API
  │
  └──▶ lila-overlay/  (Electron 34 — transparent always-on-top window)
          │
          ├──▶ main.js              Single-instance, transparent frameless window, persists position
          ├──▶ preload.js           Restricted window.lilaAPI bridge (contextIsolation: true)
          └──▶ renderer/
                  ├──▶ app.js          Connects to state_bridge, renders HUD, captions, quick actions
                  ├──▶ three_avatar.js Three.js + @pixiv/three-vrm + Kalidokit — VRM animation engine
                  ├──▶ index.html      Overlay window shell
                  ├──▶ style.css       Dark glassmorphism UI
                  └──▶ assets/dances/ 10 dance animation routines (JSON keyframes)

Optional integrations wired into the bridge:
  ┌─────────────────────────────────────────────────────────────────┐
  │ Browser Extension ←→ WebSocket bridge ←→ Python tool registry  │
  │ Mobile browser    ←→ FastAPI/WebSocket ←→ Python state & tools  │
  │ BrowserOS neo     ←→ MCP bridge        ←→ browser automation    │
  └─────────────────────────────────────────────────────────────────┘
```

---

## Key Subsystems

### 1. Local State & Command Bridge (`core/state_bridge.py`)

The central nervous system. An always-on WebSocket server at `ws://127.0.0.1:8765` that:

- Maintains real-time UI state: `mood`, `speaking`, `caption`, `audio_level`, `gesture`, `head_tilt`, `camera_proximity`, `posture`, `breathing_rate`, `mic_muted`, `audio_profile`, `desktop_context`
- Broadcasts state updates to all connected clients (Electron overlay, browser extension, any custom listener)
- Routes typed chat commands into the agent or tool pipeline
- Handles browser extension pairing tokens and request/response correlation
- Has fast-path intercepts for: URLs, companion open/close, screen clicks, media playback, Windows Settings, dances, screenshots, Antigravity status

---

### 2. Gemini Live Voice (`live_voice.py`)

The primary voice engine — NOT a wake-word system. Lila is always listening:

- Captures **16 kHz mono PCM** microphone input continuously
- Streams to **Gemini 2.5 Flash Native Audio** (Live API)
- Receives **24 kHz PCM** audio output, played locally and optionally forwarded to mobile
- Emits transcript signals to state_bridge for captions on the overlay
- Supports **barge-in / interruption** — say "stop/ruko/bas" mid-speech
- Reconnect handling with exponential backoff when provider is unavailable
- Registers all 63+ tool declarations via `core/live_tools.ALL_DECLARATIONS`
- Voice callbacks wire into: `on_voice_status`, `on_voice_transcript`, `on_voice_error`

Related modules: `core/voice.py` (TTS output), `core/ears.py` (STT fallback), `core/smart_interrupt.py`, `core/shared_mic.py`, `core/modern_audio_worker.py`

---

### 3. Electron 3D Overlay (`lila-overlay/`)

A transparent, always-on-top floating window powered by:

| Library | Version | Role |
|---|---|---|
| Electron | ^34.2.0 | Desktop shell, IPC, window management |
| Three.js | ^0.185.1 | WebGL renderer |
| @pixiv/three-vrm | ^3.5.5 | VRM character loading and animation |
| Kalidokit | ^1.1.5 | Inverse kinematics for body motion |

**What the overlay renders:**
- Animated VRM 3D character that reacts to Lila's mood, speech, and tool activity
- Speech captions in real time
- Audio level indicator (waveform)
- Quick action buttons (dance, mute, model switch, settings)
- Chat input panel for typed commands
- Emotion-driven poses and expressions from `lila_emotion_engine`

**Available VRM Characters:**
| File | Character | Description |
|---|---|---|
| `ana.vrm` | **Lila (Ana)** ⭐ | **Official Canonical Avatar** — Yellow dress, blonde wavy hair, blue eyes |
| `lila.vrm` | Lila (Alt Outfit) | Alternative costume — Bomber jacket, crop top, purple twin tails |
| `girl.vrm` / `girl_next_door.vrm` | Girl Next Door | Casual companion with off-shoulder cardigan & jeans |
| `nyan_chan.vrm` | Nyan Chan | Playful cat-ear anime avatar with glasses & school skirt |
| `character3.vrm`, `model3.vrm` | Character 3 / Model 3 | Stylized anime character with kimono sleeves & red sash |

**Built-in Dance Routines:**
`hiphop`, `wave_hiphop`, `samba`, `twist`, `jazz`, `party`, `rumba`, `tut_hiphop`, `step_hiphop`, `breakdance_uprock`, `random`

---

### 4. Browser Copilot Extension (`lila-browser-copilot/`)

A **Manifest V3** Brave/Chrome extension that gives Lila full browser awareness and control:

| File | Role |
|---|---|
| `manifest.json` | MV3 registration, permissions: `tabs`, `scripting`, `storage`, `offscreen` |
| `background.js` | Service-worker dispatcher — tabs, media, DOM, lifecycle |
| `offscreen.js` | **Persistent WebSocket** to Python bridge, reconnect logic, token pairing |
| `content-script.js` | Runs in every tab: accessible-name resolver, click/fill, article extraction, YouTube transcript |
| `popup.html/js` | Connection status UI — green/red dot, paired/not-paired |

**What Lila can do in your browser:**

| Command | What happens |
|---|---|
| `tabs.list` | List all open tabs with title + URL |
| `tabs.switch` | Switch to a specific tab |
| `tabs.close` | Close a tab |
| `dom.click "button"` | Click any element by accessible name (aria-label, text, placeholder) |
| `dom.fill "input" "text"` | Fill any form field |
| `dom.context.request` | Get page URL, title, selected text, article text |
| `page.summarizeTranscript` | Extract full YouTube transcript (clicks "Show transcript" automatically) |
| `media.play/pause/seek/volume` | Control video/audio without stealing browser focus |
| Tab change events | Lila auto-detects when you switch tabs |
| Selection change | Lila sees text you highlight (debounced 300ms) |

**Pairing:** On first install, the extension generates a 32-char random token and registers with the Python bridge. All subsequent commands carry this token.

---

### 5. Mobile Companion (`core/remote_mobile_server.py` + `web_remote/`)

Open your phone browser to `http://<your-pc-ip>:8766` — no app install needed.

**REST API:**
| Endpoint | Function |
|---|---|
| `GET /` | Mobile web UI |
| `GET /api/info` | PC info, Lila status, available models |
| `GET /api/telemetry` | System stats (CPU, RAM, uptime) |
| `GET /api/screenshot` | Live screenshot of your PC screen |
| `GET /api/qr` | QR code for easy mobile access |
| `POST /api/power/sleep` | Put PC to sleep from phone |
| `/api/antigravity/*` | Trigger Antigravity projects/actions remotely |

**WebSocket endpoints:**
| Endpoint | Protocol |
|---|---|
| `ws://<ip>:8766/ws/audio` | 16 kHz mic uplink + 24 kHz Lila voice downlink (full duplex) |
| `ws://<ip>:8766/ws/control` | Chat, state sync, screenshots, mute, audio profiles, Antigravity dispatch |

**Mobile UI features:**
- Chat with Lila by text
- Live voice conversation from phone (Web Audio API capture)
- Half-duplex echo suppression
- Real-time screenshot viewer with pinch zoom
- Audio profile switcher
- Remote mute PC microphone
- Remote PC power controls

Works on **local WiFi** or **Tailscale VPN** (for remote access from anywhere).

---

## Tool Capabilities (63+ Registered Tools)

Lila's tools are declared in `core/live_tools.py` and dispatched through Gemini's function-calling mechanism:

| Category | Tools | Key Modules |
|---|---|---|
| **Companion** | Open/close overlay, switch VRM model, list/trigger dances | `lila_companion_launcher.py`, `lila-overlay/` |
| **Windows Control** | Launch/close/focus apps, minimize/maximize/snap, Start menu search | `win_os_agent.py`, `win_fast_voice.py` |
| **Computer Use** | Screen read/analyze, click by description, type text, hotkeys, scroll | `eyes.py`, `cua_grounding.py`, `cua_driver.py`, `computer_use_agent.py` |
| **Browser (autonomous)** | Full web browsing, search, click elements, scrape, research, download | `web_agent.py`, `modern_browser.py`, `web_reader.py`, `astra_research.py` |
| **Browser Copilot** | Tab control, DOM context, accessible-name click/fill, YouTube transcript | `state_bridge.py`, `lila-browser-copilot/` |
| **Media** | YouTube control, streaming launch, play/pause/next/previous, volume | `youtube_driver.py`, `media_streaming.py`, `netmirror_driver.py` |
| **Files & Documents** | Read/write/open files, list directories, create documents, organize | `doc_forge.py`, `file_organizer.py`, `hands.py` |
| **Code & IDE** | Semantic codebase query, repo ingestion, VS Code actions | `codebase_oracle.py`, `skills_registry.py` |
| **Memory** | Remember facts, recall episodes, daily diary, user profile | `lila_cognitive_cortex.py`, `modern_memory.py`, `memory_engine.py` |
| **App Building** | Generate and serve web applications locally (OmniForge) | `omniforge.py`, `omniforge_server.py`, `omniforge_scaffolder.py` |
| **Vision** | Screenshot analysis, persistent screen watcher, visual grounding | `eyes.py`, `persistent_vision.py` |
| **Web Search** | Tavily, Serper, DuckDuckGo, Exa search with fallback | `web_reader.py`, `brain.py` |
| **Antigravity** | Control Antigravity AI assistant: execute tasks, list projects, get artifacts | `antigravity_agent.py` |
| **Background Work** | Queue tasks, proactive monitoring, scheduled alerts | `queue_db.py`, `proactive_orchestrator.py` |
| **Network** | Tailscale management, Wake-on-LAN, tunnel creation | `tailscale_manager.py`, `wake_on_lan.py`, `wormhole.py` |
| **Swarm/Mesh** | Multi-agent coordination across nodes | `swarm.py`, `mesh_network.py` |
| **Self-Evolution** | Monitor errors, auto-generate patches, apply fixes | `self_evolution.py` |
| **Calls & IoT** | Twilio outbound calls, MQTT smart home (lights/fan/AC/sensors) | `outbound_call_orchestrator.py`, `mqtt_client.py` |
| **Analytics** | System health, API quota tracking, performance metrics | `analytics_engine.py`, `health_check.py`, `api_quota_tracker.py` |
| **Mobile** | ADB-style Android device control | `mobile_agent.py` |

---

## Memory Architecture

Lila has a multi-layer memory system — she remembers you across conversations and restarts:

```
core/lila_cognitive_cortex.py  ←  Primary: SQLite WAL + FTS5
  ├── User Profile (name, preferences, facts)
  ├── Episodes (conversation history with timestamps)
  ├── Daily Diary (what happened today)
  ├── Relationship Milestones
  └── Deliverables (tasks you gave her)

core/memory_engine.py     ←  LLM fact extraction (extracts facts from every reply)
core/modern_memory.py     ←  Compatibility facade
core/vector_vault.py      ←  ChromaDB + Gemini embeddings (semantic search)
core/graph_memory.py      ←  Relationship graph between people/topics
core/trajectory_memory.py ←  Browser/action replay memory
core/response_cache.py    ←  Fast response deduplication cache
```

Memory is extracted automatically after every conversation turn — she learns your name, preferences, habits, and ongoing tasks without you having to explicitly tell her.

---

## Proactive & Autonomous Systems

When enabled (via `proactive_config.json`), Lila runs continuous background monitors:

| Monitor | What it watches |
|---|---|
| Health monitor | System CPU/RAM/disk |
| Battery monitor | Low battery alerts |
| Weather monitor | Weather changes |
| Market monitor | Stock/crypto prices |
| File monitor | Important folder changes |
| Network monitor | Connectivity status |
| Time monitor | Calendar reminders |
| Dream mode | Background memory consolidation while idle |
| Daily briefing | Morning summary of news/weather/tasks |
| Persistent vision | Screen watcher — auto-analyzes if errors appear on screen |

---

## Intelligence Pipeline

How a voice command or typed message flows through Lila:

```
🎤 Microphone / ⌨️ Chat input
        │
        ▼
Gemini Live / state_bridge command handler
        │
        ▼
Model selects a function declaration (from live_tools.ALL_DECLARATIONS)
        │
        ▼
core/live_tools.dispatch_tool_call()
        │
        ├──▶ Concrete Python module (e.g., win_os_agent, web_agent, eyes)
        │
        ▼
Tool result returned to Gemini
        │
        ▼
Gemini synthesizes voice response (24kHz PCM audio)
        │
        ├──▶ Spoken aloud on PC speakers
        ├──▶ Forwarded to mobile client (if connected)
        ▼
state_bridge broadcast → Electron overlay (caption, mood, gesture)
```

---

## Quick Start

### 1. Clone

```bash
git clone https://github.com/Rishabhkanhaiya/lila.git
cd lila
git lfs pull    # Downloads VRM 3D character model files (~140 MB)
```

### 2. Configure API Keys

```bash
copy .env.example .env
# Open .env in Notepad and add your keys
# Minimum required: GEMINI_API_KEY
```

Get a free Gemini API key from [Google AI Studio](https://aistudio.google.com).

### 3. Install Python Dependencies

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Install Electron Overlay

```bash
cd lila-overlay
npm install
cd ..
```

### 5. Launch Lila

**Double-click** `start_lila_companion.bat`

Or select a specific character:
```bash
# Default character (Ana)
start_lila_companion.bat

# Choose character
start_lila_companion.bat lila
start_lila_companion.bat girl
start_lila_companion.bat nyan_chan
```

Or run the Python backend directly (no overlay):
```bash
pythonw run_companion_bridge.py
```

---

## Browser Extension Setup

1. Open **Brave** or **Chrome** → go to `chrome://extensions/`
2. Enable **Developer mode** (top right toggle)
3. Click **"Load unpacked"**
4. Select the `lila-browser-copilot/` folder
5. Click the 🌸 Lila icon in your toolbar — green dot = connected

The extension auto-pairs with Lila on first load. As long as Lila is running, the extension stays connected.

**What you can say to Lila after loading the extension:**
- *"What tabs do I have open?"*
- *"Switch to the YouTube tab"*
- *"Click the Subscribe button"*
- *"Get the transcript of this video"*
- *"Fill in the search box with machine learning"*
- *"What's on this page?"*

---

## Mobile Remote Setup

1. Make sure Lila is running (bridge on port 8766)
2. Connect your phone to the **same WiFi** as your PC
3. Find your PC's IP: run `ipconfig` in cmd, look for IPv4 Address
4. Open your phone browser: `http://<your-pc-ip>:8766`

For remote access from **anywhere** (not just home WiFi), install [Tailscale](https://tailscale.com) on both PC and phone, then use your Tailscale IP.

---

## Requirements

### System
| Requirement | Minimum | Recommended |
|---|---|---|
| OS | Windows 10 | Windows 11 |
| Python | 3.11 | 3.11+ |
| RAM | 8 GB | 16 GB |
| Node.js | 18 | 20+ |
| Microphone | Any USB/3.5mm | Headset (less echo) |

### Python Dependencies (key ones)
```
google-generativeai    Gemini Live voice + text
openai                 GPT fallback
groq                   Fast LLM fallback
fastapi + uvicorn      Mobile server
websockets             State bridge
edge-tts               Text to speech
PyAutoGUI + pywin32    Windows automation
chromadb               Vector memory
sentence-transformers  Embeddings
sounddevice            Audio I/O
```

### Electron Dependencies
```
electron ^34.2.0         Desktop shell
three ^0.185.1           3D WebGL renderer
@pixiv/three-vrm ^3.5.5  VRM character loader
kalidokit ^1.1.5         Body inverse kinematics
```

---

## API Keys Reference

| Key | Required? | Get from |
|---|---|---|
| `GEMINI_API_KEY` | ✅ Required | [aistudio.google.com](https://aistudio.google.com) |
| `GROQ_API_KEY` | Optional (fast fallback) | [console.groq.com](https://console.groq.com) |
| `OPENAI_API_KEY` | Optional | [platform.openai.com](https://platform.openai.com) |
| `OPENROUTER_API_KEY` | Optional (multi-model gateway) | [openrouter.ai](https://openrouter.ai) |
| `TAVILY_API_KEY` | Optional (web search) | [tavily.com](https://tavily.com) |
| `SERPER_API_KEY` | Optional (web search) | [serper.dev](https://serper.dev) |
| `EXA_API_KEY` | Optional (research search) | [exa.ai](https://exa.ai) |
| `TOGETHER_API_KEY` | Optional | [together.ai](https://together.ai) |

---

## Voice Commands (Examples)

Lila understands natural Hindi + English. You don't need to say specific keywords — just talk:

**Companion control:**
- *"Apna UI dikhao"* / *"Come on screen"* → Opens the 3D overlay
- *"Nacho Lila"* / *"Do the robot dance"* → Triggers dance animation
- *"Ana ko lao"* / *"Switch to girl model"* → Changes VRM character

**PC control:**
- *"Notepad kholo"* / *"Open Spotify"* → Launches any app
- *"Volume 50 karo"* / *"Mute the system"* → Audio control
- *"Screenshot lo"* → Captures and analyzes screen
- *"Ye error kya hai"* → Analyzes current screen for errors

**Browser:**
- *"YouTube pe lo-fi hip hop chalao"* → Searches and plays
- *"Tab switch karo Stack Overflow pe"* → Switches browser tabs
- *"Is page ka summary do"* → Summarizes current page

**Productivity:**
- *"Aaj maine kya kiya?"* → Recalls today's diary
- *"Mera naam yaad hai?"* → Recalls your profile
- *"Is code ko explain karo"* → Reads and explains screen content

**General:**
- *"Ruk ja"* / *"Stop"* → Interrupts Lila mid-speech
- *"Mobile se sun"* → Routes mic input from your phone

---

## Security Notes

> [!WARNING]
> **Local-first design** — Lila is designed for single-user, trusted-network use.

| Area | Current Status |
|---|---|
| Electron | `contextIsolation: true`, `nodeIntegration: false` ✅ |
| Extension | Pairing token in `.copilot_token` and extension storage ✅ |
| API keys | Git-ignored, local only, never pushed ✅ |
| Mobile server | CORS `allow_origins=["*"]` — firewall/VPN recommended ⚠️ |
| WebSocket bridge | Local loopback (127.0.0.1) — any local process can connect ⚠️ |
| Self-evolution | Auto-patch mode requires careful supervision ⚠️ |

**Recommendation:** Do not expose port 8766 to the public internet without authentication. Use Tailscale or a VPN for remote access.

---

## Project Structure

```
lila/
├── run_companion_bridge.py      Main entry point — starts everything
├── live_voice.py                Gemini Live native audio thread
├── start_lila_companion.bat     Windows launcher (recommended)
├── Lila Companion.bat           Alternate launcher
├── Lila Companion (Silent).vbs  Silent background launcher
├── requirements.txt             Python dependencies
├── .env.example                 Config template (copy to .env)
├── .gitignore                   Excludes keys, databases, caches
├── .gitattributes               Git LFS for VRM/GLB model files
│
├── core/                        Python backend (105 modules)
│   ├── state_bridge.py          WebSocket state server
│   ├── live_tools.py            63+ tool declarations & dispatcher
│   ├── fast_agent.py            Gemini agent core
│   ├── win_fast_voice.py        Voice command routing
│   ├── remote_mobile_server.py  Mobile API (port 8766)
│   ├── lila_cognitive_cortex.py Memory & personality brain
│   ├── lila_emotion_engine.py   Real-time emotional state machine
│   ├── lila_lines.py            Personality dialog bank
│   ├── lila_companion_launcher.py Overlay launch/close logic
│   ├── voice.py                 TTS (Edge-TTS / ElevenLabs)
│   ├── omniforge.py             Local web app generator
│   ├── self_evolution.py        Auto error detection & patching
│   └── ...                      (100 more modules)
│
├── lila-overlay/                Electron 3D desktop overlay
│   ├── main.js                  Electron main process
│   ├── preload.js               Restricted IPC bridge
│   ├── package.json             Electron 34, Three.js, VRM, Kalidokit
│   └── renderer/
│       ├── app.js               HUD & state bridge client
│       ├── three_avatar.js      VRM avatar animation engine
│       ├── index.html           Overlay window
│       ├── style.css            Dark glassmorphism UI
│       └── assets/
│           ├── dances/          10 dance animation JSON files
│           └── model/           VRM character files (Git LFS)
│
├── web_remote/                  Mobile browser UI
│   ├── index.html               Mobile companion web app
│   ├── mobile.js                WebSocket client, audio, chat, controls
│   └── mobile.css               Mobile-optimized dark UI
│
└── lila-browser-copilot/        Browser extension (MV3)
    ├── manifest.json            Extension registration
    ├── background.js            Service worker dispatcher
    ├── offscreen.js             Persistent WebSocket to Python
    ├── content-script.js        DOM interaction in every tab
    ├── popup.html               Extension popup UI
    └── popup.js                 Status display
```

---

## Contributing

Pull requests welcome. If you add new tools, register them in `core/live_tools.py` under `ALL_DECLARATIONS`.

---

## License

MIT — free to use, modify, and build on.

---

<div align="center">
  Made with 💜 by <strong>Rishabh Joshi</strong> & Antigravity AI
  <br/>
  <em>"She's not just an assistant. She's a companion."</em>
</div>
