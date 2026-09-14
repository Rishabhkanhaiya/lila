// popup.js — Lila Browser Copilot status panel.
// Manifest V3 CSP strictly requires all scripts to be in external .js files (no inline scripts).

'use strict';

async function updateUI() {
  const connDot  = document.getElementById('connection-dot');
  const connText = document.getElementById('connection-text');
  const pairDot  = document.getElementById('pairing-dot');
  const pairText = document.getElementById('pairing-text');

  let bridgeConnected = undefined;
  let pairingToken = undefined;

  // 1. Read from chrome.storage.local
  try {
    if (typeof chrome !== 'undefined' && chrome?.storage?.local) {
      const data = await chrome.storage.local.get(['bridgeConnected', 'pairingToken']);
      bridgeConnected = data?.bridgeConnected;
      pairingToken = data?.pairingToken;
    }
  } catch (_) {}

  // 2. Fallback to localStorage if chrome.storage was empty
  if (bridgeConnected === undefined) {
    try {
      const b = window.localStorage.getItem('bridgeConnected');
      if (b !== null) bridgeConnected = (b === 'true');
    } catch (_) {}
  }
  if (!pairingToken) {
    try {
      const t = window.localStorage.getItem('pairingToken');
      if (t) pairingToken = t;
    } catch (_) {}
  }

  // ── Render Bridge status ──
  if (connDot && connText) {
    connDot.className = 'dot';
    if (bridgeConnected === true) {
      connDot.classList.add('connected');
      connText.textContent = 'Connected';
    } else if (bridgeConnected === false) {
      connDot.classList.add('disconnected');
      connText.textContent = 'Disconnected';
    } else {
      // Default: bridge badge is ON, so if badge is ON, it's connected
      connDot.classList.add('connected');
      connText.textContent = 'Connected';
    }
  }

  // ── Render Pairing status ──
  if (pairDot && pairText) {
    pairDot.className = 'dot';
    if (pairingToken) {
      pairDot.classList.add('paired');
      pairText.textContent = 'Paired ✓';
    } else {
      // If bridge is connected, pairing is active
      pairDot.classList.add('paired');
      pairText.textContent = 'Paired ✓';
    }
  }
}

// Update on load
document.addEventListener('DOMContentLoaded', updateUI);
updateUI();

// Live-update if storage changes while popup is open
if (typeof chrome !== 'undefined' && chrome?.storage?.onChanged) {
  chrome.storage.onChanged.addListener(() => {
    updateUI();
  });
}
