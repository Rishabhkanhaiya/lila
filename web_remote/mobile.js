/**
 * web_remote/mobile.js — Lila Zero-ADB Mobile Remote Companion
 * =============================================================
 * Real-time bidirectional Web Audio streaming, live telemetry,
 * screen mirroring, and remote Google Antigravity execution.
 */

// ─── State & DOM Elements ───────────────────────────────────────────────────

const host = window.location.host;
const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const controlWsUrl = `${wsProtocol}//${host}/ws/control`;
const audioWsUrl = `${wsProtocol}//${host}/ws/audio`;

let controlWs = null;
let audioWs = null;
let audioCtx = null;
let micStream = null;
let micSource = null;
let micProcessor = null;

let isMicStreaming = false;
let isHandsFreeMode = true;
let isPTTHolding = false;
let isLilaSpeaking = false;

const isMobileClient = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || 
                       (window.matchMedia && window.matchMedia('(max-width: 768px)').matches);

const isLocalDesktop = (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') && !isMobileClient;

// Mute browser Web Audio on desktop to prevent duplicate speech with SoundDevice
let isPhoneSpeakerActive = !isLocalDesktop;
const savedLaptopMute = localStorage.getItem('jarvis_laptop_muted');
let isLaptopSoundMuted = savedLaptopMute !== null ? (savedLaptopMute === 'true') : false;
let lastDownlinkAudioTime = 0;
let autoRefreshTimer = null;

const isHttps = window.location.protocol === 'https:';
const sslPort = 8767;
const httpsRedirectUrl = `https://${window.location.hostname}:${sslPort}/`;

// DOM Elements
const statusOrb = document.getElementById('status-orb');
const profileIcon = document.getElementById('profile-icon');
const profileName = document.getElementById('profile-name');
const btnAudioProfile = document.getElementById('btn-audio-profile');
const btnPhoneSound = document.getElementById('btn-phone-sound');
const phoneSoundIcon = document.getElementById('phone-sound-icon');
const phoneSoundStatus = document.getElementById('phone-sound-status');
const btnLaptopSound = document.getElementById('btn-laptop-sound');
const laptopSoundIcon = document.getElementById('laptop-sound-icon');
const laptopSoundStatus = document.getElementById('laptop-sound-status');
const btnMuteToggle = document.getElementById('btn-mute-toggle');
const muteIcon = document.getElementById('mute-icon');
const btnUiCmd = document.getElementById('btn-ui-cmd');
const btnVoiceUiCmd = document.getElementById('btn-voice-ui-cmd');
const btnChatAttachPath = document.getElementById('btn-chat-attach-path');
const btnChatClip = document.getElementById('btn-chat-clip');




// Telemetry Elements
const valCpu = document.getElementById('val-cpu');
const valRam = document.getElementById('val-ram');
const valBat = document.getElementById('val-bat');
const valWin = document.getElementById('val-win');

// Voice Elements
const centralOrb = document.getElementById('central-orb');
const speechBubble = document.getElementById('speech-bubble');
const captionText = document.getElementById('caption-text');
const moodPill = document.getElementById('mood-pill');
const btnModeHandsfree = document.getElementById('btn-mode-handsfree');
const btnModePtt = document.getElementById('btn-mode-ptt');
const pttHint = document.getElementById('ptt-hint');
const canvas = document.getElementById('waveform-canvas');
const ctx = canvas.getContext('2d');

// Screen Elements
const desktopScreenshot = document.getElementById('desktop-screenshot');
const btnCaptureScreen = document.getElementById('btn-capture-screen');
const btnAutoRefresh = document.getElementById('btn-auto-refresh');
const screenLoading = document.getElementById('screen-loading');
const screenViewport = document.getElementById('screen-viewport');
const zoomWrapper = document.getElementById('zoom-wrapper');
const btnZoomOut = document.getElementById('btn-zoom-out');
const btnZoomReset = document.getElementById('btn-zoom-reset');
const btnZoomIn = document.getElementById('btn-zoom-in');
const btnZoomHd = document.getElementById('btn-zoom-hd');
const btnZoomFullscreen = document.getElementById('btn-zoom-fullscreen');

// Lightbox Zoom Modal Elements
const zoomModal = document.getElementById('zoom-modal');
const zoomModalHeading = document.getElementById('zoom-modal-heading');
const zoomModalImg = document.getElementById('zoom-modal-img');
const zoomModalViewport = document.getElementById('zoom-modal-viewport');
const zoomModalWrapper = document.getElementById('zoom-modal-wrapper');
const modalZoomBadge = document.getElementById('modal-zoom-badge');
const modalBtnZoomIn = document.getElementById('modal-btn-zoom-in');
const modalBtnZoomOut = document.getElementById('modal-btn-zoom-out');
const modalBtnZoomReset = document.getElementById('modal-btn-zoom-reset');
const modalBtnClose = document.getElementById('modal-btn-close');

// Antigravity Elements
const agyProjectSelect = document.getElementById('agy-project-select');
const agyTaskInput = document.getElementById('agy-task-input');
const btnExecuteAgy = document.getElementById('btn-execute-agy');
const agyResultCard = document.getElementById('agy-result-card');
const agyResultText = document.getElementById('agy-result-text');
const agyResultShot = document.getElementById('agy-result-shot');

// Chat Elements
const chatStream = document.getElementById('chat-stream');
const chatTextInput = document.getElementById('chat-text-input');
const btnChatSend = document.getElementById('btn-chat-send');

// ─── Tab Navigation ─────────────────────────────────────────────────────────

document.querySelectorAll('.nav-item').forEach(btn => {
  btn.addEventListener('click', () => {
    const tabId = btn.getAttribute('data-tab');
    document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    const panel = document.getElementById(tabId);
    if (panel) panel.classList.add('active');

    // Trigger instant screen capture when switching to screen tab
    if (tabId === 'tab-screen') {
      captureDesktopScreen();
    }
    // Refresh WOL status when switching to WOL tab
    if (tabId === 'tab-wol') {
      fetchWolInfo();
      probeWolStatus();
    }
  });
});

// ─── Web Audio API (Two-Way Mobile Voice Link) ───────────────────────────────

function initAudioContext() {
  if (!audioCtx) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return null;
    try {
      audioCtx = new AudioContextClass({ sampleRate: 24000 });
    } catch (e) {
      audioCtx = new AudioContextClass();
    }
  }
  if (audioCtx.state === 'suspended') {
    audioCtx.resume().catch(() => {});
  }
  return audioCtx;
}

// Unlock AudioContext on first user gesture (mobile browsers block audio until interaction)
document.addEventListener('touchstart', () => { initAudioContext(); }, { once: true, passive: true });
document.addEventListener('click', () => { initAudioContext(); }, { once: true, passive: true });

// Adaptive continuous audio queue with microsecond transitions (eliminates 180ms jitter gaps)
let nextPlayTime = 0;
let activeAudioSources = [];
let lilaSpeakingTimer = null;
const INITIAL_CUSHION_SEC = 0.08; // 80ms cushion when starting playback from silence
const UNDERRUN_RAMP_SEC = 0.020;  // 20ms minimal transition ramp on underrun (prevents silence gaps)

function stopAllDownlinkAudio() {
  for (let i = 0; i < activeAudioSources.length; i++) {
    try {
      activeAudioSources[i].stop();
      activeAudioSources[i].disconnect();
    } catch (e) {}
  }
  activeAudioSources = [];
  nextPlayTime = 0;
  if (lilaSpeakingTimer) {
    clearTimeout(lilaSpeakingTimer);
    lilaSpeakingTimer = null;
  }
  isLilaSpeaking = false;
  if (statusOrb) statusOrb.classList.remove('speaking');
  if (speechBubble) speechBubble.classList.remove('speaking');
}

function playDownlinkPCM(arrayBuffer) {
  if (!isPhoneSpeakerActive) {
    return; // Phone audio muted: zero processing, zero playback
  }
  try {
    const actx = initAudioContext();
    if (!actx) return;

    // Detect binary interruption packet: "__INTERRUPT__" (13 bytes) or "STOP" (4 bytes)
    if (arrayBuffer.byteLength === 13) {
      try {
        const text = new TextDecoder().decode(arrayBuffer);
        if (text === '__INTERRUPT__') {
          stopAllDownlinkAudio();
          return;
        }
      } catch (e) {}
    } else if (arrayBuffer.byteLength === 4) {
      try {
        const text = new TextDecoder().decode(arrayBuffer);
        if (text === 'STOP') {
          stopAllDownlinkAudio();
          return;
        }
      } catch (e) {}
    }

    const int16 = new Int16Array(arrayBuffer);
    if (int16.length === 0) return;

    const wasIdle = (Date.now() - lastDownlinkAudioTime) > 350;

    // Immediately assert speech activity on mobile client (eliminates /ws/control race condition)
    isLilaSpeaking = true;
    lastDownlinkAudioTime = Date.now();
    if (statusOrb) statusOrb.classList.add('speaking');
    if (speechBubble) speechBubble.classList.add('speaking');

    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
      float32[i] = int16[i] / 32768.0;
    }

    const audioBuffer = actx.createBuffer(1, float32.length, 24000);
    audioBuffer.getChannelData(0).set(float32);

    const source = actx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(actx.destination);

    const currentTime = actx.currentTime;

    // Continuous Adaptive Queue Scheduling (Google-standard):
    // If the play cursor has fallen behind currentTime, recover smoothly with cushion/ramp.
    // Crucial: NEVER pull nextPlayTime backwards into the past, as that causes overlapping packets and speech mangling!
    if (nextPlayTime <= currentTime) {
      nextPlayTime = currentTime + (wasIdle ? INITIAL_CUSHION_SEC : UNDERRUN_RAMP_SEC);
    }

    source.start(nextPlayTime);
    activeAudioSources.push(source);

    source.onended = () => {
      const idx = activeAudioSources.indexOf(source);
      if (idx !== -1) activeAudioSources.splice(idx, 1);
      try { source.disconnect(); } catch (e) {}
    };

    nextPlayTime += audioBuffer.duration;

    // Debounce speaking state reset: keep isLilaSpeaking active until playback completes + 750ms echo cooldown
    if (lilaSpeakingTimer) clearTimeout(lilaSpeakingTimer);
    const playbackRemainingMs = Math.max(100, Math.round((nextPlayTime - currentTime) * 1000) + 750);
    lilaSpeakingTimer = setTimeout(() => {
      if (activeAudioSources.length === 0 && (Date.now() - lastDownlinkAudioTime >= 750)) {
        isLilaSpeaking = false;
        if (statusOrb) statusOrb.classList.remove('speaking');
        if (speechBubble) speechBubble.classList.remove('speaking');
      }
    }, playbackRemainingMs);

  } catch (err) {
    console.warn('[AudioDownlink] Play error:', err);
  }
}

// Mobile Phone Microphone Capture (16kHz PCM Uplink)
async function startMicrophoneStreaming() {
  if (isMicStreaming) return;

  // Check if Secure Context (HTTPS) is active for mediaDevices
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    console.warn('[RemoteMic] navigator.mediaDevices is undefined on insecure context.');
    captionText.textContent = `🔒 Mic needs HTTPS! Tap 'Enable Mic' banner or open port 8767.`;
    if (modalMicPermission) {
      if (linkSwitchHttps) linkSwitchHttps.href = httpsRedirectUrl;
      modalMicPermission.classList.remove('hidden');
    }
    return;
  }

  try {
    initAudioContext();
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        sampleRate: 16000,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      }
    });

    const micCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    micSource = micCtx.createMediaStreamSource(micStream);
    micProcessor = micCtx.createScriptProcessor(2048, 1, 1);

    micProcessor.onaudioprocess = (e) => {
      if (!isMicStreaming && !isPTTHolding && !isHandsFreeMode) return;
      if (isPTTHolding || isHandsFreeMode) {
        // Hardware Acoustic Half-Duplex Suppression:
        // If phone speaker is playing, or Lila is speaking, or within 850ms of speaker output,
        // suppress mic uplink completely so phone mic never captures Lila's voice coming from speaker!
        const isPhonePlaying = isPhoneSpeakerActive && (audioCtx && audioCtx.currentTime < (nextPlayTime + 0.15));
        const recentlyPlayed = (Date.now() - lastDownlinkAudioTime) < 850;
        if ((isLilaSpeaking || isPhonePlaying || recentlyPlayed) && !isPTTHolding) {
          return;
        }

        const inputData = e.inputBuffer.getChannelData(0);
        // Convert Float32 to 16-bit PCM
        const pcmData = new Int16Array(inputData.length);
        for (let i = 0; i < inputData.length; i++) {
          const s = Math.max(-1, Math.min(1, inputData[i]));
          pcmData[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }

        if (audioWs && audioWs.readyState === WebSocket.OPEN) {
          audioWs.send(pcmData.buffer);
        }
      }
    };

    micSource.connect(micProcessor);
    micProcessor.connect(micCtx.destination);
    isMicStreaming = true;
    centralOrb.classList.add('streaming');
    console.log('[RemoteMic] Streaming active at 16kHz.');

  } catch (err) {
    console.error('[RemoteMic] Failed to access phone mic:', err);
    captionText.textContent = `⚠️ Mic permission needed: ${err.message}`;
  }
}

function stopMicrophoneStreaming() {
  if (micProcessor) {
    micProcessor.disconnect();
    micProcessor = null;
  }
  if (micSource) {
    micSource.disconnect();
    micSource = null;
  }
  if (micStream) {
    micStream.getTracks().forEach(t => t.stop());
    micStream = null;
  }
  isMicStreaming = false;
  centralOrb.classList.remove('streaming');
}

// ─── WebSocket Audio Connection (/ws/audio) ──────────────────────────────────

function connectAudioWebSocket() {
  audioWs = new WebSocket(audioWsUrl);
  audioWs.binaryType = 'arraybuffer';

  audioWs.onopen = () => {
    console.log('[AudioWS] Connected to live voice stream.');
    if (isHandsFreeMode) {
      startMicrophoneStreaming();
    }
  };

  audioWs.onmessage = (event) => {
    if (event.data instanceof ArrayBuffer) {
      playDownlinkPCM(event.data);
    }
  };

  audioWs.onclose = () => {
    console.warn('[AudioWS] Audio socket closed. Reconnecting in 2s...');
    setTimeout(connectAudioWebSocket, 2000);
  };

  audioWs.onerror = (e) => {
    console.error('[AudioWS] Socket error:', e);
  };
}

// ─── Control WebSocket Connection (/ws/control) ──────────────────────────────

function connectControlWebSocket() {
  controlWs = new WebSocket(controlWsUrl);

  controlWs.onopen = () => {
    console.log('[ControlWS] Connected to Lila Remote Hub.');
    statusOrb.classList.remove('offline');
    const userPref = localStorage.getItem('jarvis_laptop_muted');
    if (userPref !== null) {
      controlWs.send(JSON.stringify({ action: 'toggle_laptop_sound', muted: (userPref === 'true') }));
    }
  };

  controlWs.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleControlMessage(msg);
    } catch (e) {
      console.error('[ControlWS] JSON parse error:', e);
    }
  };

  controlWs.onclose = () => {
    console.warn('[ControlWS] Control socket closed. Reconnecting in 2s...');
    statusOrb.classList.add('offline');
    setTimeout(connectControlWebSocket, 2000);
  };

  controlWs.onerror = (e) => {
    console.error('[ControlWS] Error:', e);
  };
}

function handleControlMessage(msg) {
  const type = msg.type;

  if (type === 'init') {
    if (msg.state) handleStateUpdate(msg.state);
    if (msg.telemetry) updateTelemetryUI(msg.telemetry);
    if (typeof msg.laptop_muted === 'boolean') {
      const userPref = localStorage.getItem('jarvis_laptop_muted');
      if (userPref === null) {
        updateLaptopSoundUI(msg.laptop_muted);
      } else {
        updateLaptopSoundUI(userPref === 'true');
      }
    }
  }
  else if (type === 'telemetry') {
    updateTelemetryUI(msg.data);
  }
  else if (type === 'interrupt') {
    stopAllDownlinkAudio();
  }
  else if (type === 'state_update') {
    handleStateUpdate(msg);
  }
  else if (type === 'screenshot') {
    if (msg.image) {
      desktopScreenshot.src = msg.image;
      screenLoading.classList.remove('active');
    }
  }
  else if (type === 'antigravity_result') {
    agyResultCard.classList.remove('hidden');
    agyResultText.textContent = msg.result || 'Task completed successfully.';
    if (msg.screenshot) {
      agyResultShot.src = msg.screenshot;
      document.getElementById('agy-result-shot-wrap').style.display = 'block';
    } else {
      document.getElementById('agy-result-shot-wrap').style.display = 'none';
    }
    btnExecuteAgy.textContent = '⚡ Execute in Google Antigravity';
    btnExecuteAgy.disabled = false;
  }
  else if (type === 'pipeline_progress') {
    const mark = msg.status === 'DONE' ? '✅' : (msg.status === 'FAILED' ? '❌' : '⏳');
    const badgeText = `${mark} Step ${msg.step_index}/${msg.total_steps}: ${msg.title}`;
    captionText.textContent = badgeText;
    if (msg.status === 'RUNNING' && msg.step_index === 1) {
      appendChatMessage('lila', `🚀 *Starting Mission Pipeline:* ${msg.mission}`);
    } else if (msg.status === 'DONE') {
      appendChatMessage('lila', `✅ *Step ${msg.step_index}/${msg.total_steps} Complete:* ${msg.title}`);
    }
  }
  else if (type === 'notification') {
    captionText.textContent = msg.message;
  }
  else if (type === 'chat_ack') {
    // User message already rendered optimistically in sendChat
  }
  else if (type === 'error') {
    console.warn('[ControlWS] Server error:', msg.message);
    appendChatMessage('lila', `⚠️ ${msg.message || 'Action failed'}`);
  }
  else if (type === 'laptop_sound_state') {
    updateLaptopSoundUI(msg.laptop_muted);
  }
  else if (type === 'action_result') {
    if (msg.action === 'toggle_mic') {
      updateMuteUI(msg.mic_muted);
    } else if (msg.action === 'set_audio_profile') {
      updateProfileUI(msg.audio_profile);
    } else if (msg.action === 'toggle_laptop_sound') {
      updateLaptopSoundUI(msg.laptop_muted);
    } else if (msg.action === 'launch_ui_shortcut' || msg.action === 'open_ui') {
      if (captionText && msg.message) {
        captionText.textContent = msg.message;
      }
    }
  }
}

// ─── UI State Updaters ───────────────────────────────────────────────────────

let activeLilaBubble = null;

function handleStateUpdate(state) {
  if (typeof state.speaking === 'boolean') {
    isLilaSpeaking = state.speaking;
    statusOrb.classList.toggle('speaking', isLilaSpeaking);
    speechBubble.classList.toggle('speaking', isLilaSpeaking);
  }

  if (state.caption) {
    if (captionText) captionText.textContent = `"${state.caption}"`;
    const wolLilaText = document.getElementById('wol-lila-text');
    if (wolLilaText && !state.caption.startsWith('Rishabh:')) {
      wolLilaText.textContent = `"${state.caption}"`;
    }
    const cleanCap = state.caption.trim();

    if (cleanCap.startsWith('Rishabh:')) {
      // User speech turn: finalize previous Lila response so next response gets a new bubble
      activeLilaBubble = null;
    } else if (cleanCap && !cleanCap.startsWith('Working on:')) {
      // Lila's speech turn: render or update in-place in ONE single bubble
      if (!activeLilaBubble) {
        activeLilaBubble = appendChatMessage('lila', cleanCap);
      } else {
        activeLilaBubble.textContent = cleanCap;
        chatStream.scrollTop = chatStream.scrollHeight;
      }
    }
  }

  if (state.mood) {
    moodPill.textContent = state.mood;
  }

  if (typeof state.mic_muted === 'boolean') {
    updateMuteUI(state.mic_muted);
  }

  if (state.audio_profile) {
    updateProfileUI(state.audio_profile);
  }
}

function updateTelemetryUI(data) {
  if (!data) return;
  if (typeof data.cpu === 'number') valCpu.textContent = `${Math.round(data.cpu)}%`;
  if (typeof data.ram === 'number') valRam.textContent = `${Math.round(data.ram)}%`;
  if (typeof data.battery === 'number') valBat.textContent = `${data.battery}%`;
  if (data.active_window) valWin.textContent = data.active_window;
}

function updateMuteUI(isMuted) {
  btnMuteToggle.classList.toggle('is-muted', Boolean(isMuted));
  muteIcon.textContent = isMuted ? '🤫' : '🎤';
}

const PROFILE_META = {
  pc: { icon: '💻', name: 'PC Realtek' },
  rockerz: { icon: '🎧', name: 'boAt Rockerz' },
  earbuds: { icon: '🦻', name: 'Earbuds (TWS)' },
  mobile: { icon: '📱', name: 'Phone Speaker' }
};

function updateProfileUI(profile) {
  const meta = PROFILE_META[profile] || PROFILE_META.rockerz;
  profileIcon.textContent = meta.icon;
  profileName.textContent = meta.name;
}

function updateLaptopSoundUI(muted) {
  isLaptopSoundMuted = Boolean(muted);
  if (!btnLaptopSound) return;
  if (isLaptopSoundMuted) {
    btnLaptopSound.classList.remove('is-active-speaker');
    if (laptopSoundIcon) laptopSoundIcon.textContent = '💻🔇';
    if (laptopSoundStatus) laptopSoundStatus.textContent = 'Laptop Muted';
    btnLaptopSound.title = 'Laptop Sound: Muted (Voice on mobile only)';
  } else {
    btnLaptopSound.classList.add('is-active-speaker');
    if (laptopSoundIcon) laptopSoundIcon.textContent = '💻🔊';
    if (laptopSoundStatus) laptopSoundStatus.textContent = 'Laptop Audio';
    btnLaptopSound.title = 'Laptop Sound: Active (Both UI and Mobile Lila speaking)';
  }
}

// ─── Voice Controls & PTT Handlers ──────────────────────────────────────────

btnModeHandsfree.addEventListener('click', () => {
  isHandsFreeMode = true;
  btnModeHandsfree.classList.add('active');
  btnModePtt.classList.remove('active');
  pttHint.classList.remove('show');
  startMicrophoneStreaming();
});

btnModePtt.addEventListener('click', () => {
  isHandsFreeMode = false;
  btnModeHandsfree.classList.remove('active');
  btnModePtt.classList.add('active');
  pttHint.textContent = 'Hold down the central orb to talk...';
  pttHint.classList.add('show');
  stopMicrophoneStreaming();
});

// Press to Talk (PTT) on Central Orb
function onOrbPressStart(e) {
  if (isHandsFreeMode) return;
  isPTTHolding = true;
  stopAllDownlinkAudio();
  centralOrb.classList.add('streaming');
  startMicrophoneStreaming();
}

function onOrbPressEnd(e) {
  if (isHandsFreeMode) return;
  if (isPTTHolding) {
    isPTTHolding = false;
    centralOrb.classList.remove('streaming');
    stopMicrophoneStreaming();
  }
}

centralOrb.addEventListener('pointerdown', onOrbPressStart);
window.addEventListener('pointerup', onOrbPressEnd);
window.addEventListener('pointercancel', onOrbPressEnd);

// Header Buttons
btnAudioProfile.addEventListener('click', () => {
  const current = (profileName.textContent || '').toLowerCase();
  let next = 'rockerz';
  if (current.includes('pc')) next = 'rockerz';
  else if (current.includes('rockerz')) next = 'earbuds';
  else if (current.includes('earbud')) next = 'mobile';
  else next = 'pc';

  updateProfileUI(next);
  if (controlWs && controlWs.readyState === WebSocket.OPEN) {
    controlWs.send(JSON.stringify({ action: 'set_audio_profile', profile: next }));
  }
});

btnMuteToggle.addEventListener('click', () => {
  if (controlWs && controlWs.readyState === WebSocket.OPEN) {
    controlWs.send(JSON.stringify({ action: 'toggle_mic' }));
  }
});

// Phone Speaker Mute Toggle Button
if (btnPhoneSound) {
  btnPhoneSound.addEventListener('click', () => {
    isPhoneSpeakerActive = !isPhoneSpeakerActive;
    if (isPhoneSpeakerActive) {
      btnPhoneSound.classList.add('is-active-speaker');
      phoneSoundIcon.textContent = '📱🔊';
      phoneSoundStatus.textContent = 'Phone Audio';
      captionText.textContent = '📱 Phone speaker active: Voice will stream to this phone!';
    } else {
      btnPhoneSound.classList.remove('is-active-speaker');
      phoneSoundIcon.textContent = '📱🔇';
      phoneSoundStatus.textContent = 'Phone Muted';
      captionText.textContent = '📱 Phone speaker muted: Voice will NOT play on this phone.';
    }
  });
}

if (btnLaptopSound) {
  // Sync initial UI state from saved preference
  updateLaptopSoundUI(isLaptopSoundMuted);

  btnLaptopSound.addEventListener('click', () => {
    const nextState = !isLaptopSoundMuted;
    updateLaptopSoundUI(nextState);
    localStorage.setItem('jarvis_laptop_muted', String(nextState));
    if (nextState) {
      if (captionText) captionText.textContent = '💻 Laptop sound muted: Voice will play ONLY on this phone!';
    } else {
      if (captionText) captionText.textContent = '💻 Laptop sound active: Voice will also play on laptop speakers.';
    }
    if (controlWs && controlWs.readyState === WebSocket.OPEN) {
      controlWs.send(JSON.stringify({
        action: 'toggle_laptop_sound',
        muted: nextState
      }));
    }
  });
}

// ─── Desktop Screen Capture & Zoom Engine ──────────────────────────────────

class ZoomableImageController {
  constructor({
    viewport,
    wrapper,
    img,
    badge = null,
    btnIn = null,
    btnOut = null,
    btnReset = null,
    minScale = 1.0,
    maxScale = 6.0,
    onScaleChange = null
  }) {
    this.viewport = viewport;
    this.wrapper = wrapper;
    this.img = img;
    this.badge = badge;
    this.btnIn = btnIn;
    this.btnOut = btnOut;
    this.btnReset = btnReset;
    this.minScale = minScale;
    this.maxScale = maxScale;
    this.onScaleChange = onScaleChange;

    this.scale = 1.0;
    this.translateX = 0;
    this.translateY = 0;

    this.isDragging = false;
    this.dragStartX = 0;
    this.dragStartY = 0;
    this.initialTranslateX = 0;
    this.initialTranslateY = 0;

    // Multi-touch pinch
    this.initialPinchDistance = 0;
    this.initialPinchScale = 1.0;
    this.isPinching = false;

    // Double-tap tracking
    this.lastTapTime = 0;
    this.lastTapX = 0;
    this.lastTapY = 0;

    this.init();
  }

  init() {
    if (!this.viewport || !this.wrapper) return;

    if (this.btnIn) {
      this.btnIn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.zoomBy(0.5);
      });
    }

    if (this.btnOut) {
      this.btnOut.addEventListener('click', (e) => {
        e.stopPropagation();
        this.zoomBy(-0.5);
      });
    }

    if (this.btnReset) {
      this.btnReset.addEventListener('click', (e) => {
        e.stopPropagation();
        this.reset();
      });
    }

    // Wheel zoom
    this.viewport.addEventListener('wheel', (e) => {
      e.preventDefault();
      const delta = e.deltaY < 0 ? 0.3 : -0.3;
      this.zoomBy(delta);
    }, { passive: false });

    // Touch events for pinch & pan
    this.viewport.addEventListener('touchstart', (e) => this.handleTouchStart(e), { passive: false });
    this.viewport.addEventListener('touchmove', (e) => this.handleTouchMove(e), { passive: false });
    this.viewport.addEventListener('touchend', (e) => this.handleTouchEnd(e), { passive: false });
    this.viewport.addEventListener('touchcancel', (e) => this.handleTouchEnd(e), { passive: false });

    // Mouse drag for desktop testing
    this.viewport.addEventListener('mousedown', (e) => this.handleMouseDown(e));
    window.addEventListener('mousemove', (e) => this.handleMouseMove(e));
    window.addEventListener('mouseup', (e) => this.handleMouseUp(e));
  }

  getTouchDistance(t1, t2) {
    return Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY);
  }

  clampX(x) {
    const vpW = this.viewport ? this.viewport.clientWidth : 360;
    const maxPanX = Math.max(0, (vpW * (this.scale - 1)) / 2);
    return Math.max(-maxPanX, Math.min(maxPanX, x));
  }

  clampY(y) {
    const vpH = this.viewport ? this.viewport.clientHeight : 260;
    const maxPanY = Math.max(0, (vpH * (this.scale - 1)) / 2);
    return Math.max(-maxPanY, Math.min(maxPanY, y));
  }

  clampTranslations() {
    this.translateX = this.clampX(this.translateX);
    this.translateY = this.clampY(this.translateY);
  }

  handleTouchStart(e) {
    if (e.touches.length === 2) {
      // Start multi-touch pinch
      e.preventDefault();
      this.isPinching = true;
      this.isDragging = false;
      this.wrapper.classList.remove('panning');
      this.initialPinchDistance = this.getTouchDistance(e.touches[0], e.touches[1]);
      this.initialPinchScale = this.scale;
    } else if (e.touches.length === 1) {
      const now = Date.now();
      const touch = e.touches[0];
      const rect = this.viewport.getBoundingClientRect();
      const tapX = touch.clientX - rect.left;
      const tapY = touch.clientY - rect.top;

      // Detect double-tap
      if (now - this.lastTapTime < 300 && Math.hypot(tapX - this.lastTapX, tapY - this.lastTapY) < 35) {
        e.preventDefault();
        this.lastTapTime = 0;
        if (this.scale > 1.05) {
          this.reset();
        } else {
          this.zoomTo(2.5, tapX - rect.width / 2, tapY - rect.height / 2);
        }
        return;
      }
      this.lastTapTime = now;
      this.lastTapX = tapX;
      this.lastTapY = tapY;

      if (this.scale > 1.0) {
        e.preventDefault();
        this.isDragging = true;
        this.wrapper.classList.add('panning');
        this.dragStartX = touch.clientX;
        this.dragStartY = touch.clientY;
        this.initialTranslateX = this.translateX;
        this.initialTranslateY = this.translateY;
      }
    }
  }

  handleTouchMove(e) {
    if (this.isPinching && e.touches.length === 2) {
      e.preventDefault();
      const currentDist = this.getTouchDistance(e.touches[0], e.touches[1]);
      if (this.initialPinchDistance > 0) {
        const factor = currentDist / this.initialPinchDistance;
        this.setScale(this.initialPinchScale * factor);
      }
    } else if (this.isDragging && e.touches.length === 1 && this.scale > 1.0) {
      e.preventDefault();
      const touch = e.touches[0];
      const deltaX = touch.clientX - this.dragStartX;
      const deltaY = touch.clientY - this.dragStartY;
      this.translateX = this.clampX(this.initialTranslateX + deltaX);
      this.translateY = this.clampY(this.initialTranslateY + deltaY);
      this.applyTransform();
    }
  }

  handleTouchEnd(e) {
    if (e.touches.length < 2) {
      this.isPinching = false;
    }
    if (e.touches.length === 0) {
      this.isDragging = false;
      this.wrapper.classList.remove('panning');
      if (this.scale <= 1.05) {
        this.reset();
      } else {
        this.clampTranslations();
        this.applyTransform();
      }
    }
  }

  handleMouseDown(e) {
    if (e.button !== 0) return;
    if (this.scale > 1.0) {
      e.preventDefault();
      this.isDragging = true;
      this.wrapper.classList.add('panning');
      this.dragStartX = e.clientX;
      this.dragStartY = e.clientY;
      this.initialTranslateX = this.translateX;
      this.initialTranslateY = this.translateY;
    }
  }

  handleMouseMove(e) {
    if (this.isDragging && this.scale > 1.0) {
      e.preventDefault();
      const deltaX = e.clientX - this.dragStartX;
      const deltaY = e.clientY - this.dragStartY;
      this.translateX = this.clampX(this.initialTranslateX + deltaX);
      this.translateY = this.clampY(this.initialTranslateY + deltaY);
      this.applyTransform();
    }
  }

  handleMouseUp() {
    if (this.isDragging) {
      this.isDragging = false;
      this.wrapper.classList.remove('panning');
      this.clampTranslations();
      this.applyTransform();
    }
  }

  setScale(newScale) {
    this.scale = Math.max(this.minScale, Math.min(this.maxScale, newScale));
    if (this.scale <= 1.02) {
      this.scale = 1.0;
      this.translateX = 0;
      this.translateY = 0;
    } else {
      this.clampTranslations();
    }
    this.applyTransform();
    this.updateUI();
  }

  zoomBy(step) {
    this.setScale(this.scale + step);
  }

  zoomTo(targetScale, offsetX = 0, offsetY = 0) {
    this.scale = Math.max(this.minScale, Math.min(this.maxScale, targetScale));
    if (this.scale > 1.0) {
      this.translateX = this.clampX(-offsetX * (this.scale - 1));
      this.translateY = this.clampY(-offsetY * (this.scale - 1));
    } else {
      this.translateX = 0;
      this.translateY = 0;
    }
    this.applyTransform();
    this.updateUI();
  }

  reset() {
    this.scale = 1.0;
    this.translateX = 0;
    this.translateY = 0;
    this.isDragging = false;
    this.isPinching = false;
    if (this.wrapper) {
      this.wrapper.classList.remove('panning');
    }
    this.applyTransform();
    this.updateUI();
  }

  applyTransform() {
    if (!this.wrapper) return;
    this.wrapper.style.transform = `translate3d(${this.translateX.toFixed(1)}px, ${this.translateY.toFixed(1)}px, 0px) scale(${this.scale.toFixed(3)})`;
  }

  updateUI() {
    const percent = Math.round(this.scale * 100);
    if (this.badge) {
      this.badge.textContent = `${percent}%`;
    }
    if (this.viewport) {
      if (this.scale > 1.0) {
        this.viewport.classList.add('is-zoomed');
      } else {
        this.viewport.classList.remove('is-zoomed');
      }
    }
    if (this.onScaleChange) {
      this.onScaleChange(this.scale);
    }
  }
}

// Expose globally for testing & modularity
window.ZoomableImageController = ZoomableImageController;
window.playDownlinkPCM = playDownlinkPCM;
window.stopAllDownlinkAudio = stopAllDownlinkAudio;
try {
  Object.defineProperty(window, 'isLilaSpeaking', {
    get: () => isLilaSpeaking,
    set: (val) => { isLilaSpeaking = val; },
    configurable: true
  });
} catch (e) {}

// ─── Initialize Controllers & Screen Capture ───────────────────────────────

let isScreenshotHD = false;
let desktopZoomController = null;
let modalZoomController = null;

if (screenViewport && zoomWrapper && desktopScreenshot) {
  desktopZoomController = new ZoomableImageController({
    viewport: screenViewport,
    wrapper: zoomWrapper,
    img: desktopScreenshot,
    badge: btnZoomReset,
    btnIn: btnZoomIn,
    btnOut: btnZoomOut,
    btnReset: btnZoomReset,
    minScale: 1.0,
    maxScale: 6.0
  });
}

function captureDesktopScreen() {
  if (!screenLoading || !desktopScreenshot) return;
  screenLoading.classList.add('active');
  const quality = isScreenshotHD ? 95 : 80;
  const maxDim = isScreenshotHD ? 2560 : 1920;
  desktopScreenshot.src = `/api/screenshot?quality=${quality}&max_dim=${maxDim}&t=${Date.now()}`;
}

if (desktopScreenshot) {
  desktopScreenshot.onload = () => {
    if (screenLoading) screenLoading.classList.remove('active');
  };
}

if (btnCaptureScreen) {
  btnCaptureScreen.addEventListener('click', captureDesktopScreen);
}

if (btnZoomHd) {
  btnZoomHd.addEventListener('click', () => {
    isScreenshotHD = !isScreenshotHD;
    btnZoomHd.classList.toggle('active', isScreenshotHD);
    captureDesktopScreen();
  });
}

// ─── Fullscreen Zoom Lightbox Modal Logic ──────────────────────────────────

function openZoomModal(imgSrc, title = 'Screen Inspector') {
  if (!zoomModal || !zoomModalImg) return;
  if (zoomModalHeading) zoomModalHeading.textContent = title;
  zoomModalImg.src = imgSrc;
  zoomModal.classList.remove('hidden');

  if (!modalZoomController && zoomModalViewport && zoomModalWrapper) {
    modalZoomController = new ZoomableImageController({
      viewport: zoomModalViewport,
      wrapper: zoomModalWrapper,
      img: zoomModalImg,
      badge: modalZoomBadge,
      btnIn: modalBtnZoomIn,
      btnOut: modalBtnZoomOut,
      btnReset: modalBtnZoomReset,
      minScale: 1.0,
      maxScale: 6.0
    });
  } else if (modalZoomController) {
    modalZoomController.reset();
  }
}

function closeZoomModal() {
  if (!zoomModal) return;
  zoomModal.classList.add('hidden');
  if (modalZoomController) {
    modalZoomController.reset();
  }
}

if (btnZoomFullscreen && desktopScreenshot) {
  btnZoomFullscreen.addEventListener('click', () => {
    openZoomModal(desktopScreenshot.src, 'Desktop Screen Inspector');
  });
}

if (modalBtnClose) {
  modalBtnClose.addEventListener('click', closeZoomModal);
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && zoomModal && !zoomModal.classList.contains('hidden')) {
    closeZoomModal();
  }
});

// Click Antigravity snapshot to open in zoom modal
if (agyResultShot) {
  agyResultShot.style.cursor = 'zoom-in';
  agyResultShot.addEventListener('click', () => {
    if (agyResultShot.src) {
      openZoomModal(agyResultShot.src, 'Antigravity Result Snapshot');
    }
  });
}

if (btnAutoRefresh) {
  btnAutoRefresh.addEventListener('click', () => {
    if (autoRefreshTimer) {
      clearInterval(autoRefreshTimer);
      autoRefreshTimer = null;
      btnAutoRefresh.classList.remove('active');
      btnAutoRefresh.textContent = '⚡ Auto (2s)';
    } else {
      autoRefreshTimer = setInterval(captureDesktopScreen, 2000);
      btnAutoRefresh.classList.add('active');
      btnAutoRefresh.textContent = '⏹️ Stop Auto';
      captureDesktopScreen();
    }
  });
}

// ─── Google Antigravity Execution ───────────────────────────────────────────

async function loadAntigravityProjects() {
  try {
    const res = await fetch('/api/antigravity/projects');
    const data = await res.json();
    if (data.projects && data.projects.length > 0) {
      agyProjectSelect.innerHTML = '<option value="">(Auto-detect active workspace)</option>';
      data.projects.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.name;
        opt.textContent = `${p.name} (${p.tech || 'Dev'})`;
        agyProjectSelect.appendChild(opt);
      });
    }
  } catch (e) {
    console.warn('[AGY] Failed to load projects:', e);
  }
}

document.querySelectorAll('.chip').forEach(chip => {
  chip.addEventListener('click', () => {
    const task = chip.getAttribute('data-task');
    if (task) agyTaskInput.value = task;
  });
});

btnExecuteAgy.addEventListener('click', () => {
  const task = agyTaskInput.value.trim();
  if (!task) return;

  const project = agyProjectSelect.value || null;
  btnExecuteAgy.textContent = '⏳ Injecting into Antigravity...';
  btnExecuteAgy.disabled = true;

  if (controlWs && controlWs.readyState === WebSocket.OPEN) {
    controlWs.send(JSON.stringify({
      action: 'antigravity_execute',
      task: task,
      project: project
    }));
  }
});

// ─── Chat Input ─────────────────────────────────────────────────────────────

function appendChatMessage(role, text) {
  const msgEl = document.createElement('div');
  msgEl.className = `msg ${role === 'user' ? 'user-msg' : 'lila-msg'}`;
  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  bubble.textContent = text;
  msgEl.appendChild(bubble);
  chatStream.appendChild(msgEl);
  chatStream.scrollTop = chatStream.scrollHeight;
  return bubble;
}

function sendChat(customText) {
  const text = (typeof customText === 'string' ? customText : chatTextInput.value).trim();
  if (!text) return;

  // Unlock AudioContext on user action so Lila's spoken reply will play
  initAudioContext();

  // Immediately display user message in chat stream!
  appendChatMessage('user', text);
  if (typeof customText !== 'string') {
    chatTextInput.value = '';
  }
  activeLilaBubble = null; // Prepare for fresh reply bubble

  if (controlWs && controlWs.readyState === WebSocket.OPEN) {
    controlWs.send(JSON.stringify({
      action: 'chat_command',
      text: text
    }));
  } else {
    appendChatMessage('lila', '⚠️ Connection to Lila is reconnecting... please wait 1 second and retry.');
    connectControlWebSocket();
  }
}

btnChatSend.addEventListener('click', () => sendChat());
chatTextInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    sendChat();
  }
});

const LILA_COMPANION_SHORTCUT_PATH = 'C:\\Users\\Rishabh_Joshi\\Downloads\\jarvis_project\\Lila Companion.lnk';

function sendUICommand() {
  console.log('[MobileUI] UI button clicked: sending chat command to open Lila Companion...');
  sendChat(`open "${LILA_COMPANION_SHORTCUT_PATH}"`);
  if (captionText) {
    captionText.textContent = '✨ Opening Lila 3D Companion UI on PC screen...';
  }
}

if (btnUiCmd) btnUiCmd.addEventListener('click', sendUICommand);
if (btnVoiceUiCmd) btnVoiceUiCmd.addEventListener('click', sendUICommand);
if (btnChatAttachPath) btnChatAttachPath.addEventListener('click', sendUICommand);
if (btnChatClip) {
  btnChatClip.addEventListener('click', () => {
    chatTextInput.value = `open "${LILA_COMPANION_SHORTCUT_PATH}"`;
    chatTextInput.focus();
  });
}

// ─── Realtime Waveform Animation ────────────────────────────────────────────

let wavePhase = 0;
function drawWaveform() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const cx = canvas.width / 2;
  const cy = canvas.height / 2;

  if (isLilaSpeaking || isMicStreaming) {
    const ripples = isLilaSpeaking ? 4 : 2;
    const baseColor = isLilaSpeaking ? 'rgba(255, 42, 133, ' : 'rgba(0, 255, 204, ';

    for (let r = 1; r <= ripples; r++) {
      const radius = 70 + r * 16 + Math.sin(wavePhase + r) * 6;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.strokeStyle = `${baseColor}${0.6 / r})`;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    wavePhase += 0.08;
  }
  requestAnimationFrame(drawWaveform);
}

// ─── Wake-on-LAN & Tailscale Power Management ───────────────────────────────

const wolStatusPill = document.getElementById('wol-status-pill');
const statusIndicatorDot = document.getElementById('status-indicator-dot');
const statusMainTitle = document.getElementById('status-main-title');
const statusSubDesc = document.getElementById('status-sub-desc');
const wolTargetName = document.getElementById('wol-target-name');
const wolTargetMac = document.getElementById('wol-target-mac');
const wolTargetIp = document.getElementById('wol-target-ip');
const wolTailscaleIp = document.getElementById('wol-tailscale-ip');
const wolRelayUrl = document.getElementById('wol-relay-url');
const btnWolWake = document.getElementById('btn-wol-wake');
const btnWolWakeWait = document.getElementById('btn-wol-wake-wait');
const btnWolPostWake = document.getElementById('btn-wol-post-wake');
const btnWolSleep = document.getElementById('btn-wol-sleep');
const btnWolClearLogs = document.getElementById('btn-wol-clear-logs');
const wolConsoleStream = document.getElementById('wol-console-stream');

function logWolConsole(msg, type = 'info') {
  if (!wolConsoleStream) return;
  const line = document.createElement('div');
  line.className = `console-line ${type}`;
  const time = new Date().toLocaleTimeString();
  line.textContent = `[${time}] ${msg}`;
  wolConsoleStream.appendChild(line);
  wolConsoleStream.scrollTop = wolConsoleStream.scrollHeight;
}

if (btnWolClearLogs) {
  btnWolClearLogs.addEventListener('click', () => {
    if (wolConsoleStream) wolConsoleStream.innerHTML = '';
  });
}

async function fetchWolInfo() {
  try {
    const res = await fetch('/api/wol/info');
    if (!res.ok) return;
    const data = await res.json();
    const cfg = data.config || {};
    const ts = data.tailscale || {};

    if (wolTargetName) wolTargetName.textContent = cfg.target_name || 'Rishabh-Laptop';
    if (wolTargetMac) wolTargetMac.textContent = cfg.primary_mac || '4C:23:38:76:0D:BF';
    if (wolTargetIp) wolTargetIp.textContent = cfg.target_lan_ip || '192.168.88.204';
    if (wolTailscaleIp) {
      wolTailscaleIp.textContent = ts.self_ip ? `${ts.self_ip} (Online)` : (ts.running ? '100.72.18.12 (Connected)' : 'Offline');
    }
    if (wolRelayUrl && cfg.always_on_relay_url) {
      wolRelayUrl.textContent = cfg.always_on_relay_url;
    }
  } catch (e) {
    console.debug('[WOL] Info fetch error:', e);
  }
}

async function probeWolStatus() {
  if (wolStatusPill) {
    wolStatusPill.className = 'wol-status-pill checking';
    wolStatusPill.textContent = 'CHECKING';
  }

  try {
    const res = await fetch('/api/wol/status');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (data.target_awake || data.awake) {
      if (wolStatusPill) {
        wolStatusPill.className = 'wol-status-pill awake';
        wolStatusPill.textContent = 'ONLINE';
      }
      if (statusIndicatorDot) statusIndicatorDot.className = 'status-indicator-dot online';
      if (statusMainTitle) statusMainTitle.textContent = '🟢 Computer is ON (Lila Ready)';
      if (statusSubDesc) statusSubDesc.textContent = 'Lila and all AI services are running and responsive.';
      logWolConsole(`Laptop verified ONLINE (${data.target_host})`, 'ok');
    } else {
      if (wolStatusPill) {
        wolStatusPill.className = 'wol-status-pill sleeping';
        wolStatusPill.textContent = 'SLEEPING';
      }
      if (statusIndicatorDot) statusIndicatorDot.className = 'status-indicator-dot offline';
      if (statusMainTitle) statusMainTitle.textContent = '🔴 Computer is Asleep';
      if (statusSubDesc) statusSubDesc.textContent = 'Computer is in low-power sleep mode. Tap below to turn ON.';
      logWolConsole(`Laptop is in sleep mode (${data.target_host})`, 'warn');
    }
  } catch (e) {
    if (wolStatusPill) {
      wolStatusPill.className = 'wol-status-pill awake';
      wolStatusPill.textContent = 'ACTIVE';
    }
    if (statusIndicatorDot) statusIndicatorDot.className = 'status-indicator-dot online';
    if (statusMainTitle) statusMainTitle.textContent = '🟢 Computer is ON (Lila Ready)';
    if (statusSubDesc) statusSubDesc.textContent = 'Connected via active session.';
  }
}

async function triggerWolWake(wait = false) {
  initAudioContext();
  const btn = wait ? btnWolWakeWait : btnWolWake;
  if (btn) btn.disabled = true;

  logWolConsole(wait ? 'Dispatching WOL burst and awaiting host wake...' : 'Sending Local Wake-on-LAN Magic Packet burst...', 'info');

  try {
    const res = await fetch('/api/wol/wake', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wait: wait, timeout_sec: 45.0 })
    });
    const data = await res.json();

    if (data.status === 'ok') {
      const result = data.result || {};
      if (result.wol_sent) {
        logWolConsole(`Magic packets sent to ${result.target_mac} via ${result.targets ? result.targets.length : 1} targets!`, 'ok');
      }
      if (wait) {
        logWolConsole(`Laptop verified ONLINE! Lila is ready!`, 'ok');
        if (wolStatusPill) {
          wolStatusPill.className = 'wol-status-pill awake';
          wolStatusPill.textContent = 'ONLINE';
        }
        if (statusIndicatorDot) statusIndicatorDot.className = 'status-indicator-dot online';
        if (statusMainTitle) statusMainTitle.textContent = '🟢 Computer is ON (Lila Ready)';
        if (statusSubDesc) statusSubDesc.textContent = 'Lila and all AI services are running and responsive.';

        // Display Lila greeting in chat
        appendChatMessage('lila', 'Haan Rishabh! Main online hoon aur bilkul ready hoon! Batao kya kaam hai? ✨');
        if (captionText) captionText.textContent = '"Haan Rishabh! Main online hoon aur bilkul ready hoon! ✨"';

        // Automatically switch to Voice tab after 1 second so user can speak immediately!
        setTimeout(() => {
          const voiceTabBtn = document.querySelector('[data-tab="tab-voice"]');
          if (voiceTabBtn) voiceTabBtn.click();
        }, 1200);
      } else {
        setTimeout(probeWolStatus, 3000);
      }
    } else {
      logWolConsole(`WOL failed: ${data.error || 'Unknown error'}`, 'err');
    }
  } catch (e) {
    logWolConsole(`Network error: ${e.message}`, 'err');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function triggerWolPostWake() {
  if (btnWolPostWake) btnWolPostWake.disabled = true;
  logWolConsole('Triggering configured Post-Wake execution actions...', 'info');

  try {
    const res = await fetch('/api/wol/post-wake', { method: 'POST' });
    const data = await res.json();

    if (data.status === 'ok') {
      const exec = data.execution || {};
      if (exec.executed) {
        const successes = (exec.results || []).filter(r => r.success).length;
        const total = (exec.results || []).length;
        logWolConsole(`Post-wake executed ${successes}/${total} actions successfully!`, 'ok');
      } else {
        logWolConsole(`Post-wake skipped: ${exec.reason || 'No actions or debounced'}`, 'warn');
      }
    } else {
      logWolConsole(`Post-wake error: ${data.detail || 'Execution failed'}`, 'err');
    }
  } catch (e) {
    logWolConsole(`Post-wake network error: ${e.message}`, 'err');
  } finally {
    if (btnWolPostWake) btnWolPostWake.disabled = false;
  }
}

async function triggerLaptopSleep() {
  if (!confirm('Put laptop to sleep now?')) return;
  if (btnWolSleep) btnWolSleep.disabled = true;
  logWolConsole('Sending sleep command to laptop...', 'warn');

  try {
    const res = await fetch('/api/power/sleep', { method: 'POST' });
    const data = await res.json();
    if (data.status === 'ok') {
      logWolConsole('Laptop is entering sleep mode now (goodnight!).', 'ok');
      if (wolStatusPill) {
        wolStatusPill.className = 'wol-status-pill sleeping';
        wolStatusPill.textContent = 'SLEEPING';
      }
    } else {
      logWolConsole(`Sleep command failed: ${data.detail || 'Error'}`, 'err');
    }
  } catch (e) {
    logWolConsole(`Laptop entered sleep mode (connection closed).`, 'ok');
    if (wolStatusPill) {
      wolStatusPill.className = 'wol-status-pill sleeping';
      wolStatusPill.textContent = 'SLEEPING';
    }
  } finally {
    if (btnWolSleep) btnWolSleep.disabled = false;
  }
}

if (btnWolWake) btnWolWake.addEventListener('click', () => triggerWolWake(false));
if (btnWolWakeWait) btnWolWakeWait.addEventListener('click', () => triggerWolWake(true));
if (btnWolPostWake) btnWolPostWake.addEventListener('click', triggerWolPostWake);
if (btnWolSleep) btnWolSleep.addEventListener('click', triggerLaptopSleep);

// ─── Boot Sequence ───────────────────────────────────────────────────────────

window.addEventListener('DOMContentLoaded', () => {
  connectControlWebSocket();
  connectAudioWebSocket();
  loadAntigravityProjects();
  fetchWolInfo();
  drawWaveform();

  // Tab routing from URL: /chat, ?tab=chat, or #chat
  try {
    const path = window.location.pathname.toLowerCase();
    const params = new URLSearchParams(window.location.search);
    const hash = window.location.hash.toLowerCase().replace('#', '');
    let target = params.get('tab') || hash;
    if (!target) {
      if (path.includes('/chat')) target = 'tab-chat';
      else if (path.includes('/screen')) target = 'tab-screen';
      else if (path.includes('/antigravity')) target = 'tab-antigravity';
      else if (path.includes('/wol')) target = 'tab-wol';
      else if (path.includes('/voice')) target = 'tab-voice';
    }
    if (target) {
      if (!target.startsWith('tab-')) target = 'tab-' + target;
      const tabBtn = document.querySelector(`[data-tab="${target}"]`);
      if (tabBtn) tabBtn.click();
    }
  } catch (e) {
    console.warn('[Lila Mobile] Tab routing notice:', e);
  }

  console.log('[Lila Mobile] Booted successfully.');
});
