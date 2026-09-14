/**
 * Lila Floating Desktop Companion & Reactive HUD
 * ===============================================
 * Subscribes to local WebSocket state bridge (ws://127.0.0.1:8765).
 * Features:
 * - Real-time 3D Anime Character Companion (Three.js WebGL)
 * - Smooth mood state-machine interpolation (Excited, Focused, Teasing, Sleepy, Idle)
 * - Audio-reactive circular frequency wave visualizer
 * - Glassmorphism fading speech bubble with streaming typewriter captions
 * - Hover-triggered radial quick action bar
 * - Pixel-accurate click-through hit testing
 */

import { initThreeAvatar, updateThreeAvatarState, setThreeAvatarVisible, playDance, stopDance, playGesture, switchModel } from './three_avatar.js';

// ─── Mood Palettes & State Machine Constants ─────────────────────────────────

const MOOD_PALETTES = {
  excited: {
    primary: [255, 46, 147],    // #ff2e93
    secondary: [255, 112, 214], // #ff70d6
    orbitSpeed: 0.045,
    pulseSpeed: 0.07,
    particleCount: 22,
    eyeStyle: 'sparkle'
  },
  focused: {
    primary: [0, 240, 255],     // #00f0ff
    secondary: [0, 136, 255],   // #0088ff
    orbitSpeed: 0.025,
    pulseSpeed: 0.04,
    particleCount: 16,
    eyeStyle: 'focused'
  },
  teasing: {
    primary: [181, 55, 242],    // #b537f2
    secondary: [255, 0, 127],   // #ff007f
    orbitSpeed: 0.038,
    pulseSpeed: 0.06,
    particleCount: 20,
    eyeStyle: 'wink'
  },
  sleepy: {
    primary: [255, 170, 0],     // #ffaa00
    secondary: [123, 67, 151],  // #7b4397
    orbitSpeed: 0.012,
    pulseSpeed: 0.02,
    particleCount: 10,
    eyeStyle: 'sleepy'
  },
  idle: {
    primary: [0, 255, 204],     // #00ffcc
    secondary: [0, 119, 255],   // #0077ff
    orbitSpeed: 0.02,
    pulseSpeed: 0.03,
    particleCount: 14,
    eyeStyle: 'normal'
  }
};

// Current interpolated visual values
let currentMoodName = 'excited';
let targetMoodName = 'excited';

let curColor1 = [...MOOD_PALETTES.excited.primary];
let curColor2 = [...MOOD_PALETTES.excited.secondary];
let curOrbitSpeed = MOOD_PALETTES.excited.orbitSpeed;
let curPulseSpeed = MOOD_PALETTES.excited.pulseSpeed;

let isSpeaking = false;
let audioLevel = 0.0;
let targetAudioLevel = 0.0;
let captionText = "";

let speechBubbleTimer = null;
let rotationAngle = 0;
let pulsePhase = 0;

// Avatar Mode: 3D Anime Character vs 2D Hologram HUD (defaults to 3D)
let is3DMode = localStorage.getItem('lila_avatar_mode') !== '2d';

export function setAvatarMode(is3D) {
  is3DMode = is3D;
  localStorage.setItem('lila_avatar_mode', is3D ? '3d' : '2d');
  setThreeAvatarVisible(is3D);
  const container = document.getElementById('three-container');
  if (container) {
    container.classList.toggle('hidden', !is3D);
  }
  if (speechBubble && bubbleCaption) {
    if (speechBubbleTimer) clearTimeout(speechBubbleTimer);
    speechBubble.classList.add('active');
    bubbleCaption.textContent = is3D ? "✨ Lila 3D Model Activated" : "🔮 2D Hologram HUD Activated";
    bumpBubbleWatchdog(3000);
  }
}

export function toggleAvatarMode() {
  setAvatarMode(!is3DMode);
}

// ─── DOM References ──────────────────────────────────────────────────────────

const canvas = document.getElementById('hud-canvas');
const ctx = canvas.getContext('2d');
const speechBubble = document.getElementById('speech-bubble');
const bubbleCaption = document.getElementById('bubble-caption');
const moodBadge = document.getElementById('mood-badge');

const btnMute = document.getElementById('btn-mute');
const btnMemory = document.getElementById('btn-memory');
const btnScreenshot = document.getElementById('btn-screenshot');
const btnChat = document.getElementById('btn-chat');

const chatDrawer = document.getElementById('chat-drawer');
const chatInput = document.getElementById('chat-input');
const btnSendChat = document.getElementById('btn-send-chat');
const btnMicMute = document.getElementById('btn-mic-mute');
const btnAudioDevice = document.getElementById('btn-audio-device');
const logoStatusDot = document.getElementById('logo-status-dot');
const lilaLogoBadge = document.getElementById('lila-logo-badge');
let isMicMuted = false;

// 3-Mode Audio Profiles: 1. Windows PC / 2. boAt Rockerz Headset / 3. Earbuds Any
const AUDIO_PROFILES = {
  pc: {
    name: 'pc',
    label: 'Windows PC (Speakers & Mic)',
    iconClass: 'icon-pc',
    title: 'Audio: Windows PC (Speakers & Mic) — Click to switch',
    toast: 'Audio Mode: Windows PC (Speakers & Mic) 💻',
    next: 'rockerz'
  },
  rockerz: {
    name: 'rockerz',
    label: 'boAt Rockerz Headset',
    iconClass: 'icon-rockerz',
    title: 'Audio: boAt Rockerz Headset (44.1kHz A2DP) — Click to switch',
    toast: 'Audio Mode: boAt Rockerz Headset 🎧',
    next: 'earbuds'
  },
  earbuds: {
    name: 'earbuds',
    label: 'Wireless Earbuds (Any)',
    iconClass: 'icon-earbuds',
    title: 'Audio: Wireless Earbuds (Noise, realme, TWS) — Click to switch',
    toast: 'Audio Mode: Wireless Earbuds (TWS) 🦻',
    next: 'pc'
  }
};
let currentAudioProfile = localStorage.getItem('lila_audio_profile') || 'rockerz';

export function updateAudioProfileUI(profile) {
  if (!AUDIO_PROFILES[profile]) {
    profile = 'rockerz';
  }
  currentAudioProfile = profile;
  localStorage.setItem('lila_audio_profile', profile);

  if (btnAudioDevice) {
    btnAudioDevice.className = `audio-toggle-btn interactive profile-${profile}`;
    btnAudioDevice.setAttribute('title', `Audio & Mic Settings (Click to select Microphone or Speakers)`);
    btnAudioDevice.setAttribute('aria-label', 'Audio & Microphone Settings');

    const icons = btnAudioDevice.querySelectorAll('.audio-dev-icon');
    icons.forEach(icon => {
      if (icon.classList.contains(AUDIO_PROFILES[profile].iconClass)) {
        icon.style.display = 'block';
      } else {
        icon.style.display = 'none';
      }
    });
  }
}

export function updateMicMuteUI(muted) {
  isMicMuted = Boolean(muted);
  if (btnMicMute) {
    btnMicMute.classList.toggle('is-muted', isMicMuted);
    btnMicMute.setAttribute('title', isMicMuted ? 'Unmute Microphone (Currently Muted)' : 'Mute Microphone (Currently Active)');
    btnMicMute.setAttribute('aria-label', isMicMuted ? 'Unmute Mic' : 'Mute Mic');
    const activeSvg = btnMicMute.querySelector('.mic-active-svg');
    const mutedSvg = btnMicMute.querySelector('.mic-muted-svg');
    if (activeSvg && mutedSvg) {
      activeSvg.style.display = isMicMuted ? 'none' : 'block';
      mutedSvg.style.display = isMicMuted ? 'block' : 'none';
    }
  }
  if (logoStatusDot) {
    logoStatusDot.style.background = isMicMuted ? '#ff4455' : '#00ffcc';
    logoStatusDot.style.boxShadow = isMicMuted ? '0 0 6px #ff4455' : '0 0 6px #00ffcc';
  }
}

function showFeedback(text, duration = 4000) {
  if (speechBubble && bubbleCaption) {
    if (speechBubbleTimer) clearTimeout(speechBubbleTimer);
    speechBubble.classList.add('active');
    bubbleCaption.textContent = text;
    bumpBubbleWatchdog(duration);
  }
}

async function handleModelSwitchCommand(arg) {
  const cleanArg = (arg || '').toLowerCase().trim();

  if (cleanArg === 'lila' || cleanArg === 'original' || cleanArg === 'default') {
    showFeedback("Switching to Lila (Original)... 👗");
    try {
      await switchModel('lila', 'Lila (Original)');
      showFeedback("✨ Active Model: Lila (Original)");
    } catch (err) {
      showFeedback(`⚠️ Failed to load Lila: ${err.message}`);
    }
  } else if (cleanArg === 'nyan' || cleanArg === 'nyan-chan') {
    showFeedback("Loading Nyan-Chan (VRoid)... 🐾");
    try {
      await switchModel('nyan', 'Nyan-Chan (VRoid)');
      showFeedback("✨ Active Model: Nyan-Chan! ♡");
    } catch (e) {
      showFeedback("⚠️ nyan_chan.vrm not found in assets/model/ yet. Drop the downloaded .vrm file onto me!");
    }
  } else if (cleanArg === 'ana' || cleanArg === 'pompom' || cleanArg.includes('7170519')) {
    showFeedback("Switching to Ana (Cheerleader)... ☀️");
    try {
      await switchModel('ana', 'Ana - Na Nare Hana Nare');
      showFeedback("✨ Active Model: Ana (Cheerleader)");
    } catch (err) {
      showFeedback(`⚠️ Failed to load Ana: ${err.message}`);
    }
  } else if (cleanArg === 'girl' || cleanArg === 'nextdoor' || cleanArg.includes('4787548')) {
    showFeedback("Switching to Girl Next Door... 🎀");
    try {
      await switchModel('girl', 'Girl Next Door');
      showFeedback("✨ Active Model: Girl Next Door");
    } catch (err) {
      showFeedback(`⚠️ Failed to load Girl Next Door: ${err.message}`);
    }
  } else if (cleanArg === 'model3' || cleanArg.includes('8830340')) {
    showFeedback("Switching to Anime Character 3... 💫");
    try {
      await switchModel('model3', 'Anime Character 3');
      showFeedback("✨ Active Model: Anime Character 3");
    } catch (err) {
      showFeedback(`⚠️ Failed to load Character 3: ${err.message}`);
    }
  } else if (cleanArg === 'fem' || cleanArg === 'jin') {
    showFeedback("Switching to Female VRoid (Jin)... 🌸");
    try {
      await switchModel('fem', 'Female VRoid (Jin)');
      showFeedback("✨ Active Model: Female VRoid (Jin)");
    } catch (err) {
      showFeedback(`⚠️ Failed to load Female VRoid: ${err.message}`);
    }
  } else if (cleanArg === 'switch' || cleanArg === 'next') {
    showFeedback("Cycling to next avatar model... 🔄");
    try {
      await switchModel('next');
      showFeedback(`✨ Active Model: ${window.currentModelName || 'New Avatar'}`);
    } catch (err) {
      showFeedback(`⚠️ Cycle failed: ${err.message}`);
    }
  } else {
    // Open file chooser via native Electron dialog or hidden file input
    if (window.lilaAPI && window.lilaAPI.openVRMDialog) {
      try {
        const filePath = await window.lilaAPI.openVRMDialog();
        if (filePath) {
          const fileUrl = 'file:///' + filePath.replace(/\\/g, '/');
          const name = filePath.split(/[\\/]/).pop().replace(/\.vrm$/i, '');
          showFeedback(`Loading ${name}... ✨`);
          await switchModel(fileUrl, name);
          showFeedback(`✨ Switched to: ${name}!`);
          return;
        }
      } catch (err) {
        console.warn('Native dialog failed, falling back to input:', err);
      }
    }
    const input = document.getElementById('vrm-file-input');
    if (input) input.click();
  }
}

function sendChatCommand() {
  if (!chatInput) return;
  const text = chatInput.value.trim();
  if (!text) return;

  // Handle local companion mode toggle and quick commands
  const lower = text.toLowerCase();
  if (lower.startsWith('/audio') || lower === 'switch audio' || lower === 'change audio') {
    const rawArg = lower.replace('/audio', '').replace('switch audio', '').replace('change audio', '').trim();
    let targetProf = 'rockerz';
    if (rawArg === 'pc' || rawArg === 'speakers' || rawArg === 'laptop') targetProf = 'pc';
    else if (rawArg === 'earbuds' || rawArg === 'tws' || rawArg === 'buds') targetProf = 'earbuds';
    else if (rawArg === 'rockerz' || rawArg === 'boat' || rawArg === 'headset' || rawArg === 'headphones') targetProf = 'rockerz';
    else targetProf = AUDIO_PROFILES[currentAudioProfile]?.next || 'rockerz';
    updateAudioProfileUI(targetProf);
    sendAction(`set_audio_profile:${targetProf}`);
    showFeedback(AUDIO_PROFILES[targetProf].toast, 3000);
    chatInput.value = '';
    return;
  }
  if (lower.startsWith('/model') || lower.startsWith('/avatar') || lower === 'switch model' || lower === 'change model') {
    const rawArg = lower.replace('/model', '').replace('/avatar', '').replace('switch model', '').replace('change model', '').trim();
    handleModelSwitchCommand(rawArg);
    chatInput.value = '';
    return;
  }
  if (lower === '/3d' || lower === '3d' || lower === '3d mode') {
    setAvatarMode(true);
    chatInput.value = '';
    return;
  }
  if (lower === '/2d' || lower === '2d' || lower === '2d mode') {
    setAvatarMode(false);
    chatInput.value = '';
    return;
  }
  if (lower === '/toggle' || lower === 'toggle' || lower === 'toggle avatar') {
    toggleAvatarMode();
    chatInput.value = '';
    return;
  }
  if (lower === '/wave' || lower === 'wave' || lower === '/hi' || lower === '/hello') {
    playGesture('greet');
    chatInput.value = '';
    return;
  }
  const DANCE_TRIGGERS = ['/dance', 'dance', 'nacho', 'naach', 'dance karo', 'thoda nacho', 'dance for me', 'nach ke dikhao'];
  if (DANCE_TRIGGERS.some(t => lower === t || lower.startsWith('/dance') || lower.includes('dance karo') || lower.includes('nach ke dikhao') || lower.includes('thoda nach'))) {
    // Check if user requested a specific dance (e.g. /dance samba, /dance hiphop, etc.)
    const cleaned = lower.replace('/dance', '').replace('dance', '').trim();
    const parts = cleaned.split(/\s+/);
    const requestedDance = parts.length > 0 && parts[0] ? parts[0] : null;
    playDance(requestedDance);

    targetMoodName = 'excited';
    if (moodBadge) moodBadge.textContent = 'Excited';
    if (speechBubble && bubbleCaption) {
      if (speechBubbleTimer) clearTimeout(speechBubbleTimer);
      speechBubble.classList.add('active');
      bubbleCaption.textContent = "💃 Dancing for Rishabh...";
      bumpBubbleWatchdog(6500);
    }
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'chat_command', text: text }));
    }
    chatInput.value = '';
    return;
  }
  if (lower === '/look' || lower === '/screenshot' || lower === 'look' || lower === 'screenshot') {
    sendAction('screenshot');
    if (speechBubble && bubbleCaption) {
      if (speechBubbleTimer) clearTimeout(speechBubbleTimer);
      speechBubble.classList.add('active');
      bubbleCaption.textContent = "Analyzing your screen... 📸";
      bumpBubbleWatchdog(4000);
    }
    chatInput.value = '';
    return;
  }
  if (lower === '/clear' || lower === 'clear' || lower === '/reset') {
    sendAction('clear_memory');
    if (speechBubble && bubbleCaption) {
      if (speechBubbleTimer) clearTimeout(speechBubbleTimer);
      speechBubble.classList.add('active');
      bubbleCaption.textContent = "Refreshing memory context... 🧹";
      bumpBubbleWatchdog(3000);
    }
    chatInput.value = '';
    return;
  }
  if (lower === '/mute' || lower === 'mute') {
    sendAction('mute');
    chatInput.value = '';
    return;
  }

  // Optimistic UI update
  if (speechBubble && bubbleCaption) {
    if (speechBubbleTimer) clearTimeout(speechBubbleTimer);
    speechBubble.classList.add('active');
    bubbleCaption.textContent = `Rishabh: ${text}`;
  }

  // Update mood
  targetMoodName = 'focused';
  if (moodBadge) moodBadge.textContent = 'Focused';

  // Send to Python backend via WebSocket
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'chat_command', text: text }));
    console.log('[Lila WS] Sent chat command:', text);
  } else {
    console.warn('[Lila WS] Cannot send command, bridge offline:', text);
  }

  chatInput.value = '';
}

// ─── Particle System for Orbit Halo ──────────────────────────────────────────

const particles = [];
for (let i = 0; i < 30; i++) {
  particles.push({
    angle: Math.random() * Math.PI * 2,
    radius: 68 + Math.random() * 28,
    speed: (0.01 + Math.random() * 0.025) * (Math.random() > 0.5 ? 1 : -1),
    size: 1.5 + Math.random() * 2.5,
    alpha: 0.3 + Math.random() * 0.7
  });
}

// ─── WebSocket Client & Bridge ───────────────────────────────────────────────

let ws = null;
let reconnectDelay = 1500;

function connectBridge() {
  const wsUrl = "ws://127.0.0.1:8765";
  try {
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log("[Lila WS] Connected to state bridge at", wsUrl);
      reconnectDelay = 1500;
      sendAction('get_audio_devices');
      if (currentAudioProfile) {
        sendAction(`set_audio_profile:${currentAudioProfile}`);
      }
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'state_update') {
          handleStateUpdate(data);
        } else if (data.type === 'audio_devices_list') {
          handleAudioDevicesList(data.data || data);
        } else if (data.type === 'action_result') {
          console.log("[Lila WS] Action executed:", data);
        } else if (data.type === 'ui_show') {
          handleRealtimeUiShow(data);
        } else if (data.type === 'ui_dismiss') {
          handleRealtimeUiDismiss();
        }
      } catch (e) {
        console.error("[Lila WS] Message parse error:", e);
      }
    };

    ws.onclose = () => {
      console.warn(`[Lila WS] Disconnected. Reconnecting in ${reconnectDelay}ms...`);
      ws = null;
      if (speechBubble && !isSpeaking) {
        if (speechBubbleTimer) clearTimeout(speechBubbleTimer);
        speechBubbleTimer = setTimeout(() => {
          speechBubble.classList.remove('active');
        }, 1500);
      }
      setTimeout(connectBridge, reconnectDelay);
      reconnectDelay = Math.min(8000, reconnectDelay * 1.5);
    };

    ws.onerror = () => {
      if (ws) ws.close();
    };

  } catch (err) {
    console.error("[Lila WS] Connection initialization error:", err);
    setTimeout(connectBridge, reconnectDelay);
  }
}

function sendAction(actionName) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "action", action: actionName }));
    console.log("[Lila WS] Sent action:", actionName);
  } else {
    console.warn("[Lila WS] Cannot send action, bridge not connected:", actionName);
  }
}

// ─── Speech Bubble Sliding Watchdog & Safety Dismissal ───────────────────────

let watchdogTimer = null;

function bumpBubbleWatchdog(timeoutMs = 5000) {
  if (watchdogTimer) clearTimeout(watchdogTimer);
  watchdogTimer = setTimeout(() => {
    // Only auto-dismiss if speech is NOT actively playing
    if (!isSpeaking) {
      speechBubble.classList.remove('active');
    }
  }, timeoutMs);
}

// ─── State Update Handler ────────────────────────────────────────────────────

function handleStateUpdate(data) {
  if (data.speaking || data.caption) {
    try {
      if (window.lilaAPI && window.lilaAPI.bringToFront) {
        window.lilaAPI.bringToFront();
      }
    } catch (e) {}
  }

  // Mic Mute state synchronization
  if (typeof data.mic_muted === 'boolean') {
    updateMicMuteUI(data.mic_muted);
  }

  // Audio Profile synchronization
  if (data.audio_profile) {
    updateAudioProfileUI(data.audio_profile);
  }

  // 1. Mood update
  if (data.mood && MOOD_PALETTES[data.mood]) {
    targetMoodName = data.mood;
    moodBadge.textContent = data.mood.charAt(0).toUpperCase() + data.mood.slice(1);
  }

  // 2. Speaking & Audio Level
  if (typeof data.speaking === 'boolean') {
    const wasSpeakingBefore = isSpeaking;
    isSpeaking = data.speaking;

    if (isSpeaking) {
      // Clear any pending dismissal or watchdog while speaking
      if (speechBubbleTimer) {
        clearTimeout(speechBubbleTimer);
        speechBubbleTimer = null;
      }
      if (watchdogTimer) {
        clearTimeout(watchdogTimer);
        watchdogTimer = null;
      }
      if (captionText && captionText.trim()) {
        speechBubble.classList.add('active');
      }
    } else if (wasSpeakingBefore && !isSpeaking) {
      // Fade out and clear caption text 1.8s after speaking genuinely finishes
      if (speechBubbleTimer) clearTimeout(speechBubbleTimer);
      speechBubbleTimer = setTimeout(() => {
        if (!isSpeaking) {
          speechBubble.classList.remove('active');
          captionText = "";
          bubbleCaption.textContent = "";
        }
      }, 1800);
    }
  }

  if (typeof data.audio_level === 'number') {
    targetAudioLevel = Math.max(0.0, Math.min(1.0, data.audio_level));
    if (micLevelBar) {
      const pct = Math.min(100, Math.round(targetAudioLevel * 100));
      micLevelBar.style.width = `${pct}%`;
    }
    // If receiving live audio chunks, reset watchdog so it never cuts off
    if ((isSpeaking || targetAudioLevel > 0.05) && captionText && captionText.trim()) {
      speechBubble.classList.add('active');
      if (watchdogTimer) {
        clearTimeout(watchdogTimer);
        watchdogTimer = null;
      }
    }
  }

  // 3. Caption text
  if (data.caption && data.caption !== captionText) {
    captionText = data.caption;
    speechBubble.classList.add('active');
    streamCaption(captionText);
    // If not currently speaking (e.g. status text), bump the watchdog on each new text chunk
    if (!isSpeaking) {
      bumpBubbleWatchdog(5000);
    }
  } else if (!data.caption && captionText) {
    captionText = "";
    bubbleCaption.textContent = "";
    if (!isSpeaking) {
      speechBubble.classList.remove('active');
    }
  }

  if (data.gesture === 'stop_dance' || data.gesture === 'stop') {
    stopDance(0.35);
  }

  // 4. Synchronize 3D avatar mood, speech pulse, audio energy, gesture, head tilt, camera proximity, posture & breathing
  updateThreeAvatarState({
    mood: data.mood || targetMoodName,
    speaking: isSpeaking,
    audioLevel: targetAudioLevel,
    conversationState: data.conversation_state || null,
    gesture: data.gesture || null,
    headTilt: data.head_tilt || null,
    cameraProximity: data.camera_proximity || null,
    posture: data.posture || null,
    breathingRate: data.breathing_rate || null,
    reactionBeat: data.reaction_beat || null
  });
}

// ─── Smooth Caption Streaming / Typewriter ───────────────────────────────────

let typeInterval = null;
let currentTargetText = "";

function streamCaption(fullText) {
  if (!fullText) {
    if (typeInterval) clearInterval(typeInterval);
    typeInterval = null;
    currentTargetText = "";
    if (bubbleCaption) bubbleCaption.textContent = "";
    return;
  }

  const currentShown = (bubbleCaption && bubbleCaption.textContent) ? bubbleCaption.textContent : "";
  let idx = 0;

  // If incoming fullText extends currently shown text, seamlessly continue from current position
  if (currentShown.length > 0 && fullText.startsWith(currentShown)) {
    idx = currentShown.length;
  } else if (currentTargetText && fullText.startsWith(currentTargetText) && currentShown.length > 0) {
    idx = currentShown.length;
  } else {
    // New utterance / different sentence: clear and start fresh
    if (bubbleCaption) bubbleCaption.textContent = "";
    idx = 0;
  }

  currentTargetText = fullText;
  if (typeInterval) {
    clearInterval(typeInterval);
    typeInterval = null;
  }

  if (idx >= fullText.length) {
    if (bubbleCaption) bubbleCaption.textContent = fullText;
    return;
  }

  const remaining = fullText.length - idx;
  const speed = Math.max(6, Math.min(20, Math.floor(400 / Math.max(1, remaining))));

  typeInterval = setInterval(() => {
    if (!bubbleCaption) {
      clearInterval(typeInterval);
      typeInterval = null;
      return;
    }
    if (idx < currentTargetText.length) {
      bubbleCaption.textContent += currentTargetText.charAt(idx);
      idx++;
      bubbleCaption.scrollTop = bubbleCaption.scrollHeight;
    } else {
      clearInterval(typeInterval);
      typeInterval = null;
    }
  }, speed);
}

// ─── Math & Interpolation Utilities ──────────────────────────────────────────

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function lerpColor(c1, c2, t) {
  return [
    Math.round(lerp(c1[0], c2[0], t)),
    Math.round(lerp(c1[1], c2[1], t)),
    Math.round(lerp(c1[2], c2[2], t))
  ];
}

function rgb(c, alpha = 1.0) {
  return `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${alpha})`;
}

// ─── 60 FPS Canvas HUD Render Loop ───────────────────────────────────────────

function renderHUD() {
  const targetPalette = MOOD_PALETTES[targetMoodName] || MOOD_PALETTES.excited;

  // Smoothly blend colors and parameters towards target mood
  curColor1 = lerpColor(curColor1, targetPalette.primary, 0.08);
  curColor2 = lerpColor(curColor2, targetPalette.secondary, 0.08);
  curOrbitSpeed = lerp(curOrbitSpeed, targetPalette.orbitSpeed, 0.05);
  curPulseSpeed = lerp(curPulseSpeed, targetPalette.pulseSpeed, 0.05);

  // Smoothly blend audio level
  audioLevel = lerp(audioLevel, isSpeaking ? Math.max(0.15, targetAudioLevel) : 0.0, 0.25);

  // Update CSS theme colors dynamically for glow effects
  document.documentElement.style.setProperty('--theme-glow', rgb(curColor1, 1));
  document.documentElement.style.setProperty('--theme-secondary', rgb(curColor2, 1));

  // Advance phases
  rotationAngle += curOrbitSpeed * (1.0 + audioLevel * 1.5);
  pulsePhase += curPulseSpeed;

  const w = canvas.width;
  const h = canvas.height;
  const cx = w / 2;
  const cy = h / 2;

  ctx.clearRect(0, 0, w, h);

  if (is3DMode) {
    // Pure transparent character mode: no circles, no rings, no HUD wave animations
    return;
  } else {
    // ─── 2D Mode: Hologram Core Sphere & Face Expressions ─────────────────────
    // 1. Ambient Glow Aura
    const baseRadius = 55 + Math.sin(pulsePhase) * 4 + audioLevel * 14;
    const auraGrad = ctx.createRadialGradient(cx, cy, 20, cx, cy, baseRadius * 1.6);
    auraGrad.addColorStop(0, rgb(curColor1, 0.35 + audioLevel * 0.3));
    auraGrad.addColorStop(0.6, rgb(curColor2, 0.15 + audioLevel * 0.15));
    auraGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');

    ctx.fillStyle = auraGrad;
    ctx.beginPath();
    ctx.arc(cx, cy, baseRadius * 1.6, 0, Math.PI * 2);
    ctx.fill();

    // 2. Audio-Reactive Waveform Outer Ring
    const waveSegments = 32;
    ctx.lineWidth = 2 + audioLevel * 3;
    ctx.strokeStyle = rgb(curColor1, 0.7 + audioLevel * 0.3);

    ctx.beginPath();
    for (let i = 0; i <= waveSegments; i++) {
      const theta = (i / waveSegments) * Math.PI * 2;
      const waveOffset = Math.sin(theta * 6 + pulsePhase * 3) * (4 + audioLevel * 16) +
                         Math.cos(theta * 3 - rotationAngle * 2) * (2 + audioLevel * 8);
      const r = baseRadius + 18 + waveOffset;
      const x = cx + Math.cos(theta) * r;
      const y = cy + Math.sin(theta) * r;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.stroke();

    // 3. Orbiting Rings & Techno Accents
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(rotationAngle);

    // Primary Elliptical Orbit
    ctx.lineWidth = 1.8;
    ctx.strokeStyle = rgb(curColor2, 0.6);
    ctx.beginPath();
    ctx.ellipse(0, 0, baseRadius + 8, (baseRadius + 8) * 0.75, Math.PI / 4, 0, Math.PI * 2);
    ctx.stroke();

    // Secondary Counter-Rotating Dash Ring
    ctx.rotate(-rotationAngle * 2.2);
    ctx.lineWidth = 1.5;
    ctx.setLineDash([8, 12, 3, 12]);
    ctx.strokeStyle = rgb(curColor1, 0.5);
    ctx.beginPath();
    ctx.arc(0, 0, baseRadius + 28, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();

    // 4. Floating Energy Particle Halo
    particles.forEach(p => {
      p.angle += p.speed * (1.0 + audioLevel * 2.0);
      const px = cx + Math.cos(p.angle) * (p.radius + audioLevel * 12);
      const py = cy + Math.sin(p.angle) * (p.radius + audioLevel * 12);

      ctx.fillStyle = rgb(curColor2, p.alpha);
      ctx.beginPath();
      ctx.arc(px, py, p.size, 0, Math.PI * 2);
      ctx.fill();
    });

    // 5. Central Holographic Core Sphere
    const coreGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, baseRadius);
    coreGrad.addColorStop(0, rgb(curColor1, 0.96));
    coreGrad.addColorStop(0.70, rgb(curColor1, 0.90));
    coreGrad.addColorStop(0.88, rgb(curColor2, 0.85));
    coreGrad.addColorStop(1, 'rgba(10, 15, 30, 0.95)');

    ctx.fillStyle = coreGrad;
    ctx.beginPath();
    ctx.arc(cx, cy, baseRadius, 0, Math.PI * 2);
    ctx.fill();

    // 6. Lila's Cybernetic Facial Indicators / Mood Expressions
    drawLilaFace(cx, cy, baseRadius, targetPalette.eyeStyle);
  }

  requestAnimationFrame(renderHUD);
}

function drawLilaFace(cx, cy, r, eyeStyle) {
  ctx.fillStyle = '#ffffff';
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 2.5;
  ctx.lineCap = 'round';

  const eyeSpacing = r * 0.36;
  const eyeY = cy - r * 0.12;

  // Left & Right Eye Render
  if (eyeStyle === 'sparkle' || isSpeaking) {
    // Excited sparkle eyes (+)
    [cx - eyeSpacing, cx + eyeSpacing].forEach(ex => {
      const s = 5 + audioLevel * 3;
      ctx.beginPath();
      ctx.moveTo(ex - s, eyeY);
      ctx.lineTo(ex + s, eyeY);
      ctx.moveTo(ex, eyeY - s);
      ctx.lineTo(ex, eyeY + s);
      ctx.stroke();
    });
  } else if (eyeStyle === 'wink') {
    // Teasing wink (left wink ^, right dot)
    ctx.beginPath();
    ctx.moveTo(cx - eyeSpacing - 5, eyeY + 2);
    ctx.lineTo(cx - eyeSpacing, eyeY - 4);
    ctx.lineTo(cx - eyeSpacing + 5, eyeY + 2);
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(cx + eyeSpacing, eyeY, 3, 0, Math.PI * 2);
    ctx.fill();
  } else if (eyeStyle === 'sleepy') {
    // Gentle curved sleeping eyes (⌒ ⌒)
    [cx - eyeSpacing, cx + eyeSpacing].forEach(ex => {
      ctx.beginPath();
      ctx.arc(ex, eyeY + 3, 6, Math.PI, 0, false);
      ctx.stroke();
    });
  } else {
    // Focused / Normal eyes
    [cx - eyeSpacing, cx + eyeSpacing].forEach(ex => {
      ctx.beginPath();
      ctx.arc(ex, eyeY, 3.2, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  // Mouth / Sound Visualizer
  const mouthY = cy + r * 0.28;
  if (isSpeaking) {
    // Animated speaking mouth opening reacting to audio
    const mouthW = 8 + audioLevel * 10;
    const mouthH = 3 + audioLevel * 12;
    ctx.beginPath();
    ctx.ellipse(cx, mouthY, mouthW, mouthH, 0, 0, Math.PI * 2);
    ctx.fillStyle = '#ffffff';
    ctx.fill();
  } else {
    // Gentle cute smile
    ctx.beginPath();
    ctx.arc(cx, mouthY - 3, 8, 0.2 * Math.PI, 0.8 * Math.PI, false);
    ctx.stroke();
  }
}

// ─── Radial Quick Action Handlers ────────────────────────────────────────────

function setupActionButton(btn, actionName, feedbackText) {
  if (!btn) return;

  const triggerAction = (e) => {
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }
    console.log(`[Lila] Action button pressed: ${actionName}`);

    // 1. Send via WebSocket
    sendAction(actionName);

    // 2. Also send via Electron IPC if available
    if (window.lilaAPI && window.lilaAPI.sendAction) {
      try {
        window.lilaAPI.sendAction(actionName);
      } catch (err) {}
    }

    // 3. Visual button bounce
    btn.classList.add('clicked');
    setTimeout(() => btn.classList.remove('clicked'), 250);

    // 4. Subtle status in caption with sliding watchdog
    if (speechBubble && bubbleCaption) {
      if (speechBubbleTimer) clearTimeout(speechBubbleTimer);
      speechBubble.classList.add('active');
      const isOnline = (ws && ws.readyState === WebSocket.OPEN);
      bubbleCaption.textContent = isOnline ? feedbackText : 'Connecting to Lila backend... 🔌';

      // Sliding watchdog: resets on any state_update/chunk, dismisses only if nothing arrives
      bumpBubbleWatchdog(isOnline ? 5000 : 2500);
    }
  };

  btn.addEventListener('click', triggerAction);
  btn.addEventListener('pointerdown', (e) => {
    e.stopPropagation();
  });
}

setupActionButton(btnMute, 'mute', 'Silencing...');
setupActionButton(btnMemory, 'clear_memory', 'Refreshing memory...');
setupActionButton(btnScreenshot, 'screenshot', 'Analyzing screen...');
setupActionButton(btnChat, 'open_chat', 'Opening chat...');

// ─── Chat Drawer Listeners & Keyboard Hook ───────────────────────────────────

if (btnMicMute) {
  btnMicMute.addEventListener('click', (e) => {
    e.stopPropagation();
    isMicMuted = !isMicMuted;
    updateMicMuteUI(isMicMuted);
    sendAction('toggle_mic');
    showFeedback(isMicMuted ? "Mic muted 🤫 (Lila won't listen to mic)" : "Mic active 🎤 (Lila is listening!)", 3000);
  });

  // Right-click on mic button also opens Audio & Mic hardware settings
  btnMicMute.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    e.stopPropagation();
    toggleAudioSettingsModal();
  });
}

// ─── Audio Hardware & Devices Modal Management ──────────────────────────────

const audioModal = document.getElementById('audio-settings-modal');
const btnCloseAudioModal = document.getElementById('btn-close-audio-modal');
const selectOutputDevice = document.getElementById('select-output-device');
const selectInputDevice = document.getElementById('select-input-device');
const micLevelBar = document.getElementById('mic-level-bar');
const btnRefreshDevices = document.getElementById('btn-refresh-devices');
const btnTestSound = document.getElementById('btn-test-sound');

let isAudioModalOpen = false;
let audioDevicesCatalog = null;

function toggleAudioSettingsModal(open = null) {
  if (!audioModal) return;
  isAudioModalOpen = (open !== null) ? open : !isAudioModalOpen;
  if (isAudioModalOpen) {
    audioModal.classList.add('active');
    sendAction('get_audio_devices');
  } else {
    audioModal.classList.remove('active');
  }
}

function handleAudioDevicesList(catalog) {
  if (!catalog) return;
  audioDevicesCatalog = catalog;

  // 1. Populate Output Devices
  if (selectOutputDevice && catalog.output_devices) {
    selectOutputDevice.innerHTML = '';
    catalog.output_devices.forEach((dev) => {
      const opt = document.createElement('option');
      opt.value = dev.index;
      const isHP = (dev.display_name && dev.display_name.includes('Headphone')) || dev.name.toLowerCase().includes('rockerz');
      const icon = isHP ? '🎧 ' : '🔊 ';
      opt.textContent = `${icon}${dev.display_name || dev.name}`;
      if (dev.active || Number(dev.index) === Number(catalog.current_output)) {
        opt.selected = true;
      }
      selectOutputDevice.appendChild(opt);
    });
    if (catalog.current_output !== undefined && catalog.current_output !== null) {
      selectOutputDevice.value = catalog.current_output;
    }
  }

  // 2. Populate Input Devices
  if (selectInputDevice && catalog.input_devices) {
    selectInputDevice.innerHTML = '';
    catalog.input_devices.forEach((dev) => {
      const opt = document.createElement('option');
      opt.value = dev.index;
      const isHP = (dev.display_name && (dev.display_name.includes('Headphone') || dev.display_name.includes('Headset'))) ||
                   dev.name.toLowerCase().includes('rockerz') || dev.name.toLowerCase().includes('headset');
      const icon = isHP ? '🎧 ' : '💻 ';
      opt.textContent = `${icon}${dev.display_name || dev.name}`;
      if (dev.active || Number(dev.index) === Number(catalog.current_input)) {
        opt.selected = true;
      }
      selectInputDevice.appendChild(opt);
    });
    if (catalog.current_input !== undefined && catalog.current_input !== null) {
      selectInputDevice.value = catalog.current_input;
    }
  }
}

if (selectOutputDevice) {
  selectOutputDevice.addEventListener('change', (e) => {
    const chosenIdx = e.target.value;
    sendAction(`set_output_device:${chosenIdx}`);
    try { localStorage.setItem('lila_pref_output_idx', chosenIdx); } catch(ex) {}
    const chosenName = e.target.options[e.target.selectedIndex]?.text || chosenIdx;
    showFeedback(`Output switched to: ${chosenName} 🔊`, 3000);
  });
}

if (selectInputDevice) {
  selectInputDevice.addEventListener('change', (e) => {
    const chosenIdx = e.target.value;
    sendAction(`set_input_device:${chosenIdx}`);
    try { localStorage.setItem('lila_pref_input_idx', chosenIdx); } catch(ex) {}
    const chosenName = e.target.options[e.target.selectedIndex]?.text || chosenIdx;
    showFeedback(`Microphone switched to: ${chosenName} 🎤`, 3000);
  });
}

if (btnCloseAudioModal) {
  btnCloseAudioModal.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleAudioSettingsModal(false);
  });
}

if (btnRefreshDevices) {
  btnRefreshDevices.addEventListener('click', (e) => {
    e.stopPropagation();
    sendAction('get_audio_devices');
    showFeedback('Refreshing audio devices... 🔄', 2000);
  });
}

if (btnTestSound) {
  btnTestSound.addEventListener('click', (e) => {
    e.stopPropagation();
    sendAction('test_audio_output');
    showFeedback('Playing audio test chime... 🔊', 2500);
  });
}

if (btnAudioDevice) {
  btnAudioDevice.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleAudioSettingsModal();
  });
}

// Dismiss modal when clicking anywhere outside
document.addEventListener('pointerdown', (e) => {
  if (isAudioModalOpen && audioModal && !audioModal.contains(e.target) && btnAudioDevice && !btnAudioDevice.contains(e.target)) {
    toggleAudioSettingsModal(false);
  }
});

if (lilaLogoBadge) {
  lilaLogoBadge.addEventListener('click', (e) => {
    e.stopPropagation();
    if (chatInput) chatInput.focus();
    showFeedback("Hi babe! Lila is here with you ✨", 2500);
  });
}

if (btnSendChat) {
  btnSendChat.addEventListener('click', (e) => {
    e.stopPropagation();
    sendChatCommand();
  });
}

const qvBtn = document.getElementById('btn-quick-vision');
if (qvBtn) {
  qvBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    sendAction('screenshot');
    if (speechBubble && bubbleCaption) {
      if (speechBubbleTimer) clearTimeout(speechBubbleTimer);
      speechBubble.classList.add('active');
      bubbleCaption.textContent = "Analyzing your screen... 📸";
      bumpBubbleWatchdog(4000);
    }
  });
}

// Interactive command suggestion chips (if present)
document.querySelectorAll('.chip-btn').forEach((btn) => {
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const cmd = btn.getAttribute('data-cmd');
    if (cmd && chatInput) {
      chatInput.value = cmd;
      sendChatCommand();
    }
  });
});

if (chatInput) {
  chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      sendChatCommand();
    }
  });

  chatInput.addEventListener('focus', () => {
    isMouseOverInteractive = true;
    if (window.lilaAPI && window.lilaAPI.setIgnoreMouseEvents) {
      window.lilaAPI.setIgnoreMouseEvents(false);
    }
  });

  chatInput.addEventListener('mousedown', (e) => {
    e.stopPropagation();
    isMouseOverInteractive = true;
    if (window.lilaAPI && window.lilaAPI.setIgnoreMouseEvents) {
      window.lilaAPI.setIgnoreMouseEvents(false);
    }
    chatInput.focus();
  });

  chatInput.addEventListener('click', (e) => {
    e.stopPropagation();
    isMouseOverInteractive = true;
    if (window.lilaAPI && window.lilaAPI.setIgnoreMouseEvents) {
      window.lilaAPI.setIgnoreMouseEvents(false);
    }
    chatInput.focus();
  });

  chatInput.addEventListener('pointerdown', (e) => {
    e.stopPropagation();
    isMouseOverInteractive = true;
    if (window.lilaAPI && window.lilaAPI.setIgnoreMouseEvents) {
      window.lilaAPI.setIgnoreMouseEvents(false);
    }
  });
}

if (chatDrawer) {
  chatDrawer.addEventListener('mousedown', (e) => {
    e.stopPropagation();
    isMouseOverInteractive = true;
    if (window.lilaAPI && window.lilaAPI.setIgnoreMouseEvents) {
      window.lilaAPI.setIgnoreMouseEvents(false);
    }
    if (chatInput) chatInput.focus();
  });
  chatDrawer.addEventListener('mouseenter', () => {
    isMouseOverInteractive = true;
    if (window.lilaAPI && window.lilaAPI.setIgnoreMouseEvents) {
      window.lilaAPI.setIgnoreMouseEvents(false);
    }
  });
}

// When chat button is clicked, open/focus the text box
if (btnChat && chatInput) {
  btnChat.addEventListener('click', (e) => {
    e.stopPropagation();
    if (window.lilaAPI && window.lilaAPI.setIgnoreMouseEvents) {
      window.lilaAPI.setIgnoreMouseEvents(false);
    }
    if (chatDrawer) chatDrawer.classList.remove('hidden');
    setTimeout(() => {
      chatInput.focus();
    }, 50);
  });
}

// Ensure all interactive elements capture mouse immediately on hover
document.querySelectorAll('.interactive, .action-btn, #chat-drawer, #chat-input, #btn-send-chat, #hud-canvas, #hud-viewport, #speech-bubble').forEach((el) => {
  el.addEventListener('mouseenter', () => {
    isMouseOverInteractive = true;
    if (window.lilaAPI && window.lilaAPI.setIgnoreMouseEvents) {
      window.lilaAPI.setIgnoreMouseEvents(false);
    }
  });
});

// ─── Direct Dragging Support on Center Canvas, Viewport & Bubble ─────────────

let isDragging = false;
let startScreenX = 0;
let startScreenY = 0;
let isMouseOverInteractive = true;

function handleDragStart(e) {
  if (e.button !== 0) return; // Left click only
  // Don't drag if clicking interactive buttons or inputs
  if (e.target.closest('.action-btn') || 
      e.target.closest('#chat-drawer') || 
      e.target.closest('input') || 
      e.target.closest('button')) {
    return;
  }

  isDragging = true;
  startScreenX = e.screenX;
  startScreenY = e.screenY;
  document.body.classList.add('is-dragging');

  if (window.lilaAPI && window.lilaAPI.setIgnoreMouseEvents) {
    window.lilaAPI.setIgnoreMouseEvents(false);
  }
}

const hudViewport = document.getElementById('hud-viewport');
[canvas, hudViewport, speechBubble].forEach((el) => {
  if (el) {
    el.addEventListener('mousedown', handleDragStart);
  }
});

// Double-click on Lila's viewport to toggle between 3D Model and 2D Hologram HUD
if (hudViewport) {
  hudViewport.addEventListener('dblclick', (e) => {
    e.stopPropagation();
    toggleAvatarMode();
  });
}

window.addEventListener('mousemove', (e) => {
  // 1. Actively dragging window
  if (isDragging) {
    const deltaX = Math.round(e.screenX - startScreenX);
    const deltaY = Math.round(e.screenY - startScreenY);
    if (deltaX !== 0 || deltaY !== 0) {
      startScreenX = e.screenX;
      startScreenY = e.screenY;
      if (window.lilaAPI && window.lilaAPI.moveWindow) {
        window.lilaAPI.moveWindow(deltaX, deltaY);
      }
    }
    return;
  }

  // 2. Click-Through Hit Testing (Pass-through for transparent regions)
  if (window.lilaAPI && window.lilaAPI.setIgnoreMouseEvents) {
    const isChatFocused = (document.activeElement === chatInput);
    if (isChatFocused) {
      if (!isMouseOverInteractive) {
        isMouseOverInteractive = true;
        window.lilaAPI.setIgnoreMouseEvents(false);
      }
      return;
    }

    const targetEl = document.elementFromPoint(e.clientX, e.clientY);
    const isInteractive = targetEl && (
      targetEl.closest('.interactive') ||
      targetEl.closest('.action-btn') ||
      targetEl.closest('#chat-drawer') ||
      targetEl.closest('#hud-viewport') ||
      targetEl.closest('#speech-bubble') ||
      targetEl.closest('#audio-settings-modal') ||
      targetEl.closest('button') ||
      targetEl.closest('input') ||
      targetEl.closest('select') ||
      targetEl.closest('option') ||
      (isAudioModalOpen && audioModal && audioModal.contains(targetEl))
    );

    const shouldCapture = Boolean(isInteractive);
    if (shouldCapture !== isMouseOverInteractive) {
      isMouseOverInteractive = shouldCapture;
      window.lilaAPI.setIgnoreMouseEvents(!shouldCapture);
    }
  }
});

function handleDragEnd() {
  if (isDragging) {
    isDragging = false;
    document.body.classList.remove('is-dragging');
  }
}

window.addEventListener('mouseup', handleDragEnd);
window.addEventListener('blur', handleDragEnd);

// ─── Drag & Drop VRM Model Loading ───────────────────────────────────────────

window.addEventListener('dragover', (e) => {
  e.preventDefault();
  e.stopPropagation();
  if (e.dataTransfer) {
    e.dataTransfer.dropEffect = 'copy';
  }
});

window.addEventListener('drop', async (e) => {
  e.preventDefault();
  e.stopPropagation();
  const files = e.dataTransfer?.files;
  if (files && files.length > 0) {
    const file = files[0];
    if (file.name.toLowerCase().endsWith('.vrm')) {
      console.log('[Lila UI] 📦 Dropped VRM model:', file.name);
      showFeedback(`Loading custom avatar: ${file.name}... ✨`);
      try {
        await switchModel(file, file.name.replace(/\.vrm$/i, ''));
        showFeedback(`✨ Avatar switched to: ${file.name}!`);
      } catch (err) {
        showFeedback(`⚠️ Failed to load model: ${err.message}`);
      }
    }
  }
});

const vrmFileInput = document.getElementById('vrm-file-input');
if (vrmFileInput) {
  vrmFileInput.addEventListener('change', async (e) => {
    const file = e.target.files?.[0];
    if (file) {
      showFeedback(`Loading avatar: ${file.name}... ✨`);
      try {
        await switchModel(file, file.name.replace(/\.vrm$/i, ''));
        showFeedback(`✨ Avatar switched to: ${file.name}!`);
      } catch (err) {
        showFeedback(`⚠️ Failed to load model: ${err.message}`);
      }
      vrmFileInput.value = '';
    }
  });
}

// Send goodbye before window unloads
window.addEventListener('beforeunload', () => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    try {
      ws.send(JSON.stringify({ type: 'action', action: 'goodbye' }));
    } catch (e) {}
  }
});

// ─── Boot Sequence ───────────────────────────────────────────────────────────

window.addEventListener('DOMContentLoaded', () => {
  const threeContainer = document.getElementById('three-container');
  if (threeContainer) {
    initThreeAvatar(threeContainer);
    if (!is3DMode) {
      setThreeAvatarVisible(false);
      threeContainer.classList.add('hidden');
    }
  }
  updateAudioProfileUI(currentAudioProfile);
  connectBridge();
  requestAnimationFrame(renderHUD);
});


// ─── Realtime Interactive Multimodal UI Controller (Show, Don't Recite) ───────

const realtimeUiTray = document.getElementById('realtime-ui-tray');
const uiTrayIcon = document.getElementById('ui-tray-icon');
const uiTrayTitle = document.getElementById('ui-tray-title');
const uiTraySubtitle = document.getElementById('ui-tray-subtitle');
const btnCloseUiTray = document.getElementById('btn-close-ui-tray');
const uiCategoryPills = document.getElementById('ui-category-pills');
const uiCardsContainer = document.getElementById('ui-cards-container');
const uiTimerProgress = document.getElementById('ui-timer-progress');

let currentUiData = null;
let uiDismissTimer = null;
let uiProgressInterval = null;

if (btnCloseUiTray) {
  btnCloseUiTray.addEventListener('click', () => {
    handleRealtimeUiDismiss();
  });
}

function handleRealtimeUiDismiss() {
  if (uiDismissTimer) {
    clearTimeout(uiDismissTimer);
    uiDismissTimer = null;
  }
  if (uiProgressInterval) {
    clearInterval(uiProgressInterval);
    uiProgressInterval = null;
  }
  if (realtimeUiTray) {
    realtimeUiTray.classList.remove('active');
    setTimeout(() => {
      if (!realtimeUiTray.classList.contains('active')) {
        realtimeUiTray.style.display = 'none';
      }
    }, 280);
  }
  currentUiData = null;
}

function handleRealtimeUiShow(data) {
  if (!realtimeUiTray) return;
  currentUiData = data;

  if (uiDismissTimer) clearTimeout(uiDismissTimer);
  if (uiProgressInterval) clearInterval(uiProgressInterval);

  if (uiTrayIcon) uiTrayIcon.textContent = data.icon || '✨';
  if (uiTrayTitle) uiTrayTitle.textContent = data.title || 'Choose An Option';
  if (uiTraySubtitle) uiTraySubtitle.textContent = data.subtitle || 'Tap an option or just speak your choice';

  const categories = data.categories || [];
  let activeCat = data.active_category || (categories.length > 0 ? (categories[0].id || categories[0].name) : null);
  renderUiCategories(categories, activeCat);
  renderUiCategoryCards(data, activeCat);

  realtimeUiTray.style.display = 'flex';
  requestAnimationFrame(() => {
    realtimeUiTray.classList.add('active');
  });

  const durationSec = data.auto_dismiss_sec || 18;
  startUiCountdown(durationSec);
}

function renderUiCategories(categories, activeCat) {
  if (!uiCategoryPills) return;
  uiCategoryPills.innerHTML = '';
  if (!categories || categories.length === 0) {
    uiCategoryPills.style.display = 'none';
    return;
  }
  uiCategoryPills.style.display = 'flex';

  categories.forEach(cat => {
    const pill = document.createElement('div');
    const catId = cat.id || cat.name;
    const isActive = (catId === activeCat);
    pill.className = `ui-pill interactive ${isActive ? 'active' : ''}`;
    pill.innerHTML = `<span>${cat.icon || '🏷️'}</span> <span>${cat.name || cat.title || catId}</span>`;
    pill.addEventListener('click', () => {
      uiCategoryPills.querySelectorAll('.ui-pill').forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      renderUiCategoryCards(currentUiData, catId);
    });
    uiCategoryPills.appendChild(pill);
  });
}

function renderUiCategoryCards(data, activeCat) {
  if (!uiCardsContainer) return;
  uiCardsContainer.innerHTML = '';

  const layout = data.layout || 'cards';

  // 1. Code Snippet & Review Card Layout
  if (layout === 'code' && data.code_snippet) {
    const codeCard = document.createElement('div');
    codeCard.className = 'ui-code-card interactive';
    codeCard.innerHTML = `
      <div class="ui-code-header">
        <span>${data.language || 'CODE'} SNIPPET</span>
        <span style="color: rgba(220,226,248,0.5);">${data.file_path || ''}</span>
      </div>
      <pre class="ui-code-pre"><code>${escapeHtml(data.code_snippet)}</code></pre>
      <div class="ui-code-actions">
        <button type="button" class="ui-btn-action secondary" id="btn-copy-code">📋 Copy</button>
        <button type="button" class="ui-btn-action primary" id="btn-run-code">▶ ${data.action_label || 'Run Action'}</button>
      </div>
    `;
    codeCard.querySelector('#btn-copy-code').addEventListener('click', (e) => {
      e.stopPropagation();
      navigator.clipboard.writeText(data.code_snippet);
      codeCard.querySelector('#btn-copy-code').textContent = '✅ Copied!';
      setTimeout(() => { codeCard.querySelector('#btn-copy-code').textContent = '📋 Copy'; }, 1500);
    });
    codeCard.querySelector('#btn-run-code').addEventListener('click', () => {
      sendUiAction(data.action || 'vscode_action', data.query || data.code_snippet, activeCat, data);
      handleRealtimeUiDismiss();
    });
    uiCardsContainer.appendChild(codeCard);
    return;
  }

  // 2. Metric / System Health Gauge Layout
  if (layout === 'metric' && (data.metrics || data.percent !== undefined)) {
    const metrics = data.metrics || [{ label: data.title || 'System Metric', percent: data.percent || 75, desc: data.desc || '', action: data.action || 'clean_storage' }];
    metrics.forEach(m => {
      const metricCard = document.createElement('div');
      metricCard.className = 'ui-metric-card interactive';
      metricCard.innerHTML = `
        <div class="ui-metric-top">
          <div class="ui-metric-label"><span>${m.icon || '📊'}</span> <span>${m.label || 'Metric'}</span></div>
          <div class="ui-metric-val">${m.percent}%</div>
        </div>
        <div class="ui-metric-track">
          <div class="ui-metric-fill" style="width: ${m.percent}%;"></div>
        </div>
        ${m.desc ? `<div style="font-size: 10px; color: rgba(220,226,248,0.65);">${m.desc}</div>` : ''}
        ${m.action_label ? `<button type="button" class="ui-btn-action primary" style="align-self: flex-end; margin-top: 4px;">${m.action_label}</button>` : ''}
      `;
      if (m.action_label) {
        metricCard.querySelector('button').addEventListener('click', () => {
          sendUiAction(m.action || 'run_command', m.query || m.label, activeCat, m);
          handleRealtimeUiDismiss();
        });
      }
      uiCardsContainer.appendChild(metricCard);
    });
    return;
  }

  // 3. Slider Control Layout (Volume / Brightness / Setting)
  if (layout === 'slider') {
    const sliderCard = document.createElement('div');
    sliderCard.className = 'ui-slider-card interactive';
    const initVal = data.slider_val !== undefined ? data.slider_val : 50;
    sliderCard.innerHTML = `
      <div class="ui-slider-top">
        <span>${data.slider_label || 'Adjustment'}</span>
        <span id="slider-val-display" style="color: #00f0ff;">${initVal}%</span>
      </div>
      <input type="range" class="ui-slider-input interactive" min="0" max="100" value="${initVal}" />
    `;
    const input = sliderCard.querySelector('input');
    const display = sliderCard.querySelector('#slider-val-display');
    let sliderDebounce = null;
    input.addEventListener('input', (e) => {
      const val = e.target.value;
      display.textContent = `${val}%`;
      if (sliderDebounce) clearTimeout(sliderDebounce);
      sliderDebounce = setTimeout(() => {
        sendUiAction(data.action || 'set_volume', val, activeCat, { value: val });
      }, 150);
    });
    uiCardsContainer.appendChild(sliderCard);
    return;
  }

  // 4. Decision Matrix / Split Options Layout
  if (layout === 'decision' && data.decisions) {
    const decRow = document.createElement('div');
    decRow.className = 'ui-decision-row interactive';
    data.decisions.forEach(d => {
      const btn = document.createElement('div');
      btn.className = `ui-decision-btn interactive ${d.recommended ? 'recommended' : ''}`;
      btn.innerHTML = `
        <div style="font-size: 20px;">${d.icon || '✨'}</div>
        <div class="ui-decision-title">${d.title}</div>
        <div class="ui-decision-desc">${d.desc || ''}</div>
      `;
      btn.addEventListener('click', () => {
        sendUiAction(d.action || 'chat_command', d.query || d.title, activeCat, d);
        handleRealtimeUiDismiss();
      });
      decRow.appendChild(btn);
    });
    uiCardsContainer.appendChild(decRow);
    return;
  }

  // 5. Chips Cloud Layout
  if (layout === 'chips') {
    const chipsCloud = document.createElement('div');
    chipsCloud.className = 'ui-chips-cloud interactive';
    const items = data.chips || data.cards || [];
    items.forEach(c => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `ui-chip-btn interactive ${c.recommended ? 'recommended' : ''}`;
      btn.innerHTML = `<span>${c.icon || '⚡'}</span> <span>${c.title || c.name || c}</span>`;
      btn.addEventListener('click', () => {
        sendUiAction(c.action || data.default_action || 'chat_command', c.query || c.title || c, activeCat, c);
        handleRealtimeUiDismiss();
      });
      chipsCloud.appendChild(btn);
    });
    uiCardsContainer.appendChild(chipsCloud);
    return;
  }

  // 6. Standard Option Cards Layout (Music, Movies, Files, Apps)
  let cards = [];
  if (data.category_cards && activeCat && data.category_cards[activeCat]) {
    cards = data.category_cards[activeCat];
  } else if (data.cards && Array.isArray(data.cards)) {
    cards = data.cards;
  }

  if (cards.length === 0) {
    uiCardsContainer.innerHTML = '<div style="color: rgba(220,226,248,0.5); font-size: 11px; padding: 12px; text-align: center;">No options in this category</div>';
    return;
  }

  cards.forEach((card, idx) => {
    const cardEl = document.createElement('div');
    cardEl.className = 'ui-card interactive';
    const badgeNum = idx + 1;
    cardEl.innerHTML = `
      <div class="ui-card-left">
        <div class="ui-card-badge">${card.icon || badgeNum}</div>
        <div class="ui-card-info">
          <div class="ui-card-title">${card.title || 'Option ' + badgeNum}</div>
          <div class="ui-card-desc">${card.desc || card.artist || card.subtitle || ''}</div>
        </div>
      </div>
      <div class="ui-card-action-btn">${card.action_icon || '▶'}</div>
    `;

    cardEl.addEventListener('click', () => {
      sendUiAction(card.action || data.default_action || 'play_youtube', card.query || card.title, activeCat, card);
      handleRealtimeUiDismiss();
    });

    uiCardsContainer.appendChild(cardEl);
  });
}

function sendUiAction(action, query, category, item) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    const plat = (item && item.platform) ? item.platform : ((action === 'stream_movie') ? 'netmirror' : 'youtube');
    ws.send(JSON.stringify({
      type: 'ui_action',
      action: action,
      query: query,
      category: category,
      item: item,
      platform: plat
    }));
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

function startUiCountdown(durationSec) {
  if (!uiTimerProgress) return;
  uiTimerProgress.style.width = '100%';
  const startTime = Date.now();
  const totalMs = durationSec * 1000;

  uiProgressInterval = setInterval(() => {
    const elapsed = Date.now() - startTime;
    const remaining = Math.max(0, 1.0 - (elapsed / totalMs));
    uiTimerProgress.style.width = `${(remaining * 100).toFixed(1)}%`;
    if (remaining <= 0) {
      clearInterval(uiProgressInterval);
      uiProgressInterval = null;
    }
  }, 100);

  uiDismissTimer = setTimeout(() => {
    handleRealtimeUiDismiss();
  }, totalMs);
}

// Expose on window for diagnostics, external triggers, and devtools
if (typeof window !== 'undefined') {
  window.handleRealtimeUiShow = handleRealtimeUiShow;
  window.handleRealtimeUiDismiss = handleRealtimeUiDismiss;
  window.renderUiCategoryCards = renderUiCategoryCards;
}
