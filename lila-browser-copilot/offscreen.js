// offscreen.js — persistent context (offscreen document).
// This is the ONLY place the WebSocket to the local Lila bridge lives.
// It never gets torn down for idleness the way a service worker does.
// Spec refs: §1 (architecture), §2.1 (reconnect backoff), §2.4 (auth token).

'use strict';

// ─── State ────────────────────────────────────────────────────────────────────

let socket = null;
let backoffMs = 500;
const BACKOFF_MAX = 10_000;
const WS_URL = 'ws://127.0.0.1:8765';

/** The persisted pairing token (32 hex chars), loaded from storage at startup. */
let authToken = null;

// ─── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Returns a UUID v4 string.
 * Uses crypto.randomUUID() when available (Chrome 92+); falls back to
 * building one manually from crypto.getRandomValues for older runtimes.
 */
function uuid() {
  if (typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  // Polyfill: RFC 4122 §4.4 UUID v4
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  // Set version bits (4) at nibble 12-15 of octet 6
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  // Set variant bits (10) at nibble 6-7 of octet 8
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes).map((b) => b.toString(16).padStart(2, '0')).join('');
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join('-');
}

/**
 * Generates a cryptographically random 32-hex-character token.
 * Used on first-connect to create the pairing secret (spec §2.4).
 */
function generatePairingToken() {
  const bytes = new Uint8Array(16); // 16 bytes → 32 hex chars
  crypto.getRandomValues(bytes);
  return Array.from(bytes).map((b) => b.toString(16).padStart(2, '0')).join('');
}

// ─── Storage Helpers (Offscreen MV3 safe) ────────────────────────────────────

/**
 * Reads a value from storage safely.
 * Chromium offscreen documents do not have chrome.storage.local enabled directly.
 * We fall back to window.localStorage (synchronous, origin-scoped)
 * and query background.js via runtime messaging.
 */
async function getStorageItem(key) {
  try {
    const val = window.localStorage.getItem(key);
    if (val !== null) return val;
  } catch (_) {}

  try {
    if (typeof chrome !== 'undefined' && chrome?.storage?.local) {
      const res = await chrome.storage.local.get(key);
      if (res && res[key] !== undefined) return res[key];
    }
  } catch (_) {}

  try {
    const res = await chrome.runtime.sendMessage({
      source: 'offscreen',
      type: 'storage.get',
      payload: { key }
    });
    if (res && res.value !== undefined) return res.value;
  } catch (_) {}

  return null;
}

/**
 * Saves a key-value pair to storage safely across window.localStorage,
 * chrome.storage.local (if present), and background.js relay.
 */
async function setStorageItem(key, value) {
  try {
    window.localStorage.setItem(key, typeof value === 'string' ? value : JSON.stringify(value));
  } catch (_) {}

  try {
    if (typeof chrome !== 'undefined' && chrome?.storage?.local) {
      await chrome.storage.local.set({ [key]: value });
    }
  } catch (_) {}

  try {
    await chrome.runtime.sendMessage({
      source: 'offscreen',
      type: 'storage.set',
      payload: { key, value }
    });
  } catch (_) {}
}

/**
 * Loads `pairingToken`.
 * If none exists, generates one, persists it, and sets authToken.
 * This is called once at startup before the first connect().
 */
async function initToken() {
  const pairingToken = await getStorageItem('pairingToken');
  if (pairingToken) {
    authToken = pairingToken;
    _tokenWasJustGenerated = false;
  } else {
    authToken = generatePairingToken();
    _tokenWasJustGenerated = true;
    await setStorageItem('pairingToken', authToken);
  }
}

// ─── Status notification ──────────────────────────────────────────────────────

/**
 * Broadcasts connection status to background.js (which can update the badge)
 * and persists the state so popup.html can read it from storage.
 * Spec §3.3: emits `bridge.status` event.
 */
function notifyStatus(connected) {
  // Persist for popup.html to read from storage
  setStorageItem('bridgeConnected', connected);
  // Broadcast to background (thin relay)
  chrome.runtime.sendMessage({
    source: 'offscreen',
    type: 'bridge.status',
    payload: { connected },
  }).catch(() => {
    // Background may be momentarily unavailable during startup; suppress error.
  });
}

// ─── Auth handshake ───────────────────────────────────────────────────────────

/**
 * Sends the appropriate auth message over the open socket:
 * - First connect (freshly generated token): action='register'
 * - Reconnect (token already existed in storage): action='verify'
 *
 * Spec §2.4 and the user prompt: every outbound message includes the token.
 */
function sendAuth(isFirstRegister) {
  const msg = {
    type: 'copilot.auth',
    id: uuid(),
    token: authToken,
    action: isFirstRegister ? 'register' : 'verify',
  };
  socket.send(JSON.stringify(msg));
}

// ─── Outbound socket relay with deduplication ───────────────────────────────

const _sentMessageIds = new Set();

function sendOutboundToSocket(data) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  if (!data) return;
  const id = data.id;
  if (id) {
    if (_sentMessageIds.has(id)) return;
    _sentMessageIds.add(id);
    if (_sentMessageIds.size > 300) {
      const iter = _sentMessageIds.values();
      _sentMessageIds.delete(iter.next().value);
    }
  }
  const outbound = { ...data };
  if (authToken && !outbound.token) {
    outbound.token = authToken;
  }
  try {
    socket.send(JSON.stringify(outbound));
  } catch (err) {
    console.warn('[offscreen] socket send error:', err);
  }
}

// ─── WebSocket lifecycle ───────────────────────────────────────────────────────

/** Tracks whether this is the very first time we've ever generated the token. */
let _tokenWasJustGenerated = false;

/**
 * Opens the WebSocket and sets up all event handlers.
 * Implements exponential backoff on close/error (spec §2.1):
 *   start=500ms, doubles each attempt, cap=10,000ms.
 */
function connect() {
  // Ensure token is valid before connecting
  if (!authToken) {
    authToken = generatePairingToken();
    _tokenWasJustGenerated = true;
    setStorageItem('pairingToken', authToken);
  }

  // Clean up any lingering socket object
  if (socket) {
    try { socket.close(); } catch (_) {}
    socket = null;
  }

  socket = new WebSocket(WS_URL);

  socket.onopen = () => {
    // Reset backoff on successful connection
    backoffMs = 500;

    // Send auth handshake
    sendAuth(_tokenWasJustGenerated);
    // After first registration, all future connects are 'verify'
    _tokenWasJustGenerated = false;

    notifyStatus(true);
  };

  socket.onmessage = (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (err) {
      console.error('[offscreen] Received non-JSON frame from bridge:', event.data, err);
      return;
    }

    // Handle bridge auth responses locally in the offscreen document
    if (msg.type === 'copilot.auth.ok') {
      console.log('[offscreen] Lila bridge authenticated successfully.');
      notifyStatus(true);
      return;
    }
    if (msg.type === 'copilot.auth.rejected') {
      console.info('[offscreen] Lila bridge rejected auth token; generating new pairing token.');
      authToken = generatePairingToken();
      _tokenWasJustGenerated = true;
      setStorageItem('pairingToken', authToken);
      notifyStatus(false);
      return;
    }

    // Ignore overlay broadcast messages like state_update or audio_devices_list
    if (msg.type === 'state_update' || msg.type === 'audio_devices_list' || msg.type === 'pong') {
      return;
    }

    // Relay inbound command to background.js for tab routing.
    // When background returns a result, echo it back over the WebSocket to the bridge (Spec §3.1)
    chrome.runtime.sendMessage({ source: 'offscreen', ...msg })
      .then((response) => {
        if (response && (response.id || response.type || response.payload)) {
          sendOutboundToSocket(response);
        }
      })
      .catch((err) => {
        console.warn('[offscreen] background command error:', err);
      });
  };

  socket.onclose = () => {
    notifyStatus(false);
    scheduleReconnect();
  };

  socket.onerror = () => {
    // Normal during bridge restarts or reconnection attempts; handled by onclose
    notifyStatus(false);
  };
}

function scheduleReconnect() {
  const delay = backoffMs;
  backoffMs = Math.min(backoffMs * 2, BACKOFF_MAX);
  console.log(`[offscreen] Reconnecting in ${delay}ms …`);
  setTimeout(connect, delay);
}

// ─── Inbound relay (background → socket) ──────────────────────────────────────

/**
 * Messages from background.js (source==='background' or 'background_reply')
 * and content scripts are events/replies to be forwarded over WebSocket.
 */
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.source === 'offscreen') return false;
  if (msg.source === 'background_reply' || msg.source === 'background' || msg.source === 'content-script') {
    sendOutboundToSocket(msg);
  }
  return false;
});

// ─── Bootstrap ────────────────────────────────────────────────────────────────

(async () => {
  try {
    await initToken();
  } catch (err) {
    console.error('[offscreen] Token init error:', err);
    if (!authToken) {
      authToken = generatePairingToken();
      _tokenWasJustGenerated = true;
    }
  }
  connect();
})();
