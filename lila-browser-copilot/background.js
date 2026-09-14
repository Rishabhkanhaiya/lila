// background.js — MV3 service worker.
// This file holds ZERO socket state (spec §5.2).
// Its only jobs:
//   1. Keep the offscreen document alive (which holds the WebSocket).
//   2. Route inbound commands (from bridge via offscreen) to the correct tab.
//   3. Route outbound events (tab changes, content-script data) to offscreen.

'use strict';

// ─── Offscreen document management ───────────────────────────────────────────

/**
 * Creates the offscreen document if it doesn't already exist.
 * The offscreen doc holds the long-lived WebSocket to the Lila bridge.
 * Called on install and on browser startup so the socket is always live.
 */
async function ensureOffscreenDocument() {
  try {
    if (typeof chrome.runtime.getContexts === 'function') {
      const existing = await chrome.runtime.getContexts({
        contextTypes: ['OFFSCREEN_DOCUMENT'],
      });
      if (existing.length > 0) return;
    }
  } catch (_) {}

  try {
    await chrome.offscreen.createDocument({
      url: 'offscreen.html',
      reasons: ['WORKERS'],
      justification: 'Persistent WebSocket bridge to local Lila assistant service',
    });
  } catch (_) {
    try {
      await chrome.offscreen.createDocument({
        url: 'offscreen.html',
        reasons: ['AUDIO_PLAYBACK'],
        justification: 'Persistent WebSocket bridge to local Lila assistant service',
      });
    } catch (_) {}
  }
}

chrome.runtime.onInstalled.addListener(ensureOffscreenDocument);
chrome.runtime.onStartup.addListener(ensureOffscreenDocument);
// Ensure offscreen document is immediately alive whenever background worker executes
ensureOffscreenDocument().catch(() => {});

// ─── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Returns the tabId of the active tab.
 * Robust across background execution: prioritizes normal scriptable web pages
 * over internal browser pages (brave:// or chrome://).
 */
async function getActiveTabId() {
  try {
    const isScriptable = (t) =>
      t?.id &&
      t.url &&
      !t.url.startsWith('chrome://') &&
      !t.url.startsWith('brave://') &&
      !t.url.startsWith('about:') &&
      !t.url.startsWith('chrome-extension://');

    // 1. Check active tab in last focused window
    const lastFocused = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (lastFocused.length > 0 && isScriptable(lastFocused[0])) {
      return lastFocused[0].id;
    }

    // 2. Check active tab in current window
    const current = await chrome.tabs.query({ active: true, currentWindow: true });
    if (current.length > 0 && isScriptable(current[0])) {
      return current[0].id;
    }

    // 3. Fallback: find any active scriptable tab across all windows
    const allActive = await chrome.tabs.query({ active: true });
    const normalActive = allActive.find(isScriptable);
    if (normalActive?.id) return normalActive.id;

    // 4. Fallback: find any search/content page open in browser (e.g. Wikipedia, Google, YouTube)
    const allTabs = await chrome.tabs.query({});
    const wikiOrSearchTab = allTabs.find((t) =>
      isScriptable(t) &&
      (t.url.includes('wikipedia') || t.url.includes('google') || t.url.includes('youtube') ||
       (t.title && t.title.toLowerCase().includes('wikipedia')))
    );
    if (wikiOrSearchTab?.id) return wikiOrSearchTab.id;

    // 5. Fallback: find ANY scriptable tab in the entire browser
    const anyScriptable = allTabs.find(isScriptable);
    if (anyScriptable?.id) return anyScriptable.id;

    // 6. Ultimate fallback if all else fails
    return lastFocused[0]?.id ?? current[0]?.id ?? allTabs[0]?.id ?? null;
  } catch (_) {
    return null;
  }
}

/**
 * Sends a message to the offscreen document.
 * Used to forward content-script events and command replies up to the bridge.
 */
async function sendToOffscreen(msg) {
  try {
    if (typeof chrome.runtime.getContexts === 'function') {
      const existing = await chrome.runtime.getContexts({
        contextTypes: ['OFFSCREEN_DOCUMENT'],
      });
      if (existing.length === 0) {
        await ensureOffscreenDocument();
      }
    }
  } catch (_) {}
  return chrome.runtime.sendMessage(msg).catch(() => {});
}

// ─── Music tab management (spec §2.2 Option A) ────────────────────────────────

/**
 * Handles media.play {url, tabId?}:
 *   - Reuses an existing `lilaMusicTabId` if the tab is still alive.
 *   - Otherwise creates a new background tab (active: false).
 *   - After 2500ms, injects a muted-play-unmute sequence so autoplay policy
 *     is satisfied: muted autoplay is always permitted by Chromium.
 * Returns an object with the used tabId.
 */
async function handleMediaPlay(msg) {
  let { url } = msg.payload;

  // Ensure autoplay=1 for YouTube URLs
  if (url.includes('youtube.com/watch') && !url.includes('autoplay=1')) {
    url += (url.includes('?') ? '&' : '?') + 'autoplay=1';
  }

  // ── Resolve or create the music tab ──
  let musicTabId = null;
  const { lilaMusicTabId, warmedDomains = [] } = await chrome.storage.local.get(['lilaMusicTabId', 'warmedDomains']);

  if (lilaMusicTabId != null) {
    try {
      await chrome.tabs.get(lilaMusicTabId); // throws if tab was closed
      musicTabId = lilaMusicTabId;
    } catch (_) {
      await chrome.storage.local.remove('lilaMusicTabId');
    }
  }

  // Check if domain has been warmed up per spec §2.2 Option B
  let domain = 'youtube.com';
  try { domain = new URL(url).hostname; } catch (_) {}
  const isWarmed = Array.isArray(warmedDomains) && warmedDomains.includes(domain);

  // Get current active tab so we can instantly restore it if warmup is needed
  const [currentActiveTab] = await chrome.tabs.query({ active: true, currentWindow: true });

  if (musicTabId != null) {
    // Reuse existing tab — never steal focus
    await chrome.tabs.update(musicTabId, { url });
  } else {
    // Spec §2.2 Option B: If domain is not yet warmed, briefly activate for 350ms to register MEI,
    // then immediately restore the user's active tab. All subsequent plays are 100% active:false.
    const createActive = !isWarmed;
    const newTab = await chrome.tabs.create({ url, active: createActive });
    musicTabId = newTab.id;
    await chrome.storage.local.set({ lilaMusicTabId: musicTabId });

    if (createActive) {
      setTimeout(async () => {
        try {
          if (currentActiveTab?.id) {
            await chrome.tabs.update(currentActiveTab.id, { active: true });
          }
          const updatedWarmed = Array.isArray(warmedDomains) ? [...warmedDomains, domain] : [domain];
          await chrome.storage.local.set({ warmedDomains: updatedWarmed });
        } catch (_) {}
      }, 350);
    }
  }

  // Active polling function injected into tab to ensure playback starts
  const injectStarter = async () => {
    try {
      await chrome.scripting.executeScript({
        target: { tabId: musicTabId },
        func: () => {
          let count = 0;
          const poll = setInterval(() => {
            count++;
            // 1. YouTube native player API
            const p = document.getElementById('movie_player');
            if (p && typeof p.playVideo === 'function') {
              try {
                p.playVideo();
                if (typeof p.unMute === 'function') p.unMute();
                if (typeof p.setVolume === 'function') p.setVolume(100);
              } catch (_) {}
            }
            // 2. HTML5 video element (Spec §2.2 Option A)
            const v = document.querySelector('video');
            if (v) {
              v.muted = false;
              v.play().catch(() => {
                // If direct unmuted play blocked, do muted-play-then-unmute
                v.muted = true;
                v.play().then(() => {
                  setTimeout(() => { v.muted = false; }, 300);
                }).catch(() => {});
              });
              if (!v.paused && v.currentTime > 0) {
                clearInterval(poll);
                return;
              }
            }
            // 3. Click play button
            const btn = document.querySelector('.ytp-play-button');
            if (btn && btn.getAttribute('data-title-no-tooltip') === 'Play') {
              btn.click();
            }
            if (count >= 25) clearInterval(poll);
          }, 350);
        },
      });
    } catch (err) {
      console.warn('[background] scripting.executeScript failed:', err.message);
    }
  };

  // Run starter at 1200ms and 3000ms to catch fast and deferred tab renders
  setTimeout(injectStarter, 1200);
  setTimeout(injectStarter, 3000);

  return { ok: true, tabId: musicTabId, id: msg.id };
}

async function sendMessageWithInjection(tabId, message) {
  try {
    return await chrome.tabs.sendMessage(tabId, message);
  } catch (firstErr) {
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ['content-script.js'],
      });
      return await chrome.tabs.sendMessage(tabId, message);
    } catch (_) {
      throw firstErr;
    }
  }
}

// ─── Message handler ──────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  const { source, type } = msg;

  // ── Guard: only handle messages from offscreen doc or content scripts ──
  if (source !== 'offscreen' && source !== 'content-script') return;

  // ── Content-script events → forward to offscreen for bridge relay ──
  if (source === 'content-script') {
    sendToOffscreen({ ...msg, source: 'background' })
      .then(() => sendResponse({ ok: true }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true; // async
  }

  // ── Commands arriving from the bridge (via offscreen) ──
  // source === 'offscreen'
  handleCommand(msg)
    .then((result) => {
      const fullReply = { source: 'background_reply', id: msg.id, ...(result || {}) };
      // Dual-relay: direct sendToOffscreen guarantees delivery even if MV3 closed sendResponse port
      sendToOffscreen(fullReply);
      sendResponse(fullReply);
    })
    .catch((err) => {
      console.error('[background] handleCommand error for', type, ':', err.message);
      const errReply = { source: 'background_reply', id: msg.id, ok: false, error: err.message };
      sendToOffscreen(errReply);
      sendResponse(errReply);
    });
  return true; // keep channel open for async response
});

/**
 * Routes a command message to the appropriate Chrome API or tab.
 * Every branch echoes the original message's `id` so the bridge can
 * correlate responses to requests (spec §3.1).
 */
async function handleCommand(msg) {
  const { type, payload = {}, id } = msg;

  switch (type) {
    // ── Safe storage relay for Brave/Chromium offscreen document ─────────────

    case 'storage.get': {
      const { key } = payload;
      const data = await chrome.storage.local.get(key);
      return { id, ok: true, value: data?.[key] ?? null };
    }

    case 'storage.set': {
      const { key, value } = payload;
      await chrome.storage.local.set({ [key]: value });
      return { id, ok: true };
    }

    case 'bridge.status': {
      const connected = payload.connected;
      await chrome.storage.local.set({ bridgeConnected: connected });
      try {
        if (chrome.action?.setBadgeText) {
          chrome.action.setBadgeText({ text: connected ? 'ON' : '!' });
          chrome.action.setBadgeBackgroundColor({ color: connected ? '#3dd68c' : '#e05a5a' });
        }
      } catch (_) {}
      return { id, ok: true };
    }

    case 'extension.reload': {
      setTimeout(() => { chrome.runtime.reload(); }, 100);
      return { id, ok: true, reloading: true };
    }

    // ── Tab management ──────────────────────────────────────────────────────

    case 'tabs.list': {
      const tabs = await chrome.tabs.query({});
      return {
        id,
        type: 'tabs.list.result',
        payload: tabs.map((t) => ({
          id: t.id,
          title: t.title,
          url: t.url,
          active: t.active,
          audible: t.audible,
          muted: t.mutedInfo?.muted ?? false,
        })),
      };
    }

    case 'tabs.switch': {
      await chrome.tabs.update(payload.tabId, { active: true });
      return { id, type: 'tabs.switch.result', payload: { ok: true } };
    }

    case 'tabs.close': {
      await chrome.tabs.remove(payload.tabId);
      return { id, type: 'tabs.close.result', payload: { ok: true } };
    }

    // ── Media control ───────────────────────────────────────────────────────

    case 'media.play': {
      const result = await handleMediaPlay(msg);
      return { id, type: 'media.play.result', payload: result };
    }

    case 'media.pause': {
      const { tabId } = payload;
      await chrome.scripting.executeScript({
        target: { tabId },
        func: () => {
          document.querySelectorAll('video, audio').forEach((el) => el.pause());
        },
      });
      return { id, type: 'media.pause.result', payload: { ok: true } };
    }

    case 'media.seek': {
      const { tabId, value } = payload; // value in seconds
      await chrome.scripting.executeScript({
        target: { tabId },
        func: (seconds) => {
          document.querySelectorAll('video, audio').forEach((el) => {
            el.currentTime = seconds;
          });
        },
        args: [value],
      });
      return { id, type: 'media.seek.result', payload: { ok: true } };
    }

    case 'media.volume': {
      const { tabId, value } = payload; // value 0.0–1.0
      await chrome.scripting.executeScript({
        target: { tabId },
        func: (vol) => {
          document.querySelectorAll('video, audio').forEach((el) => {
            el.volume = Math.max(0, Math.min(1, vol));
          });
        },
        args: [value],
      });
      return { id, type: 'media.volume.result', payload: { ok: true } };
    }

    // ── System messages from bridge (ignore/no-op) ──────────────────────────
    case 'copilot.auth.ok':
    case 'copilot.auth.rejected':
    case 'state_update':
    case 'audio_devices_list':
    case 'pong':
      return { id, ok: true };

    // ── DOM / content-script commands ───────────────────────────────────────

    case 'dom.context.request': {
      const tabId = payload.tabId ?? (await getActiveTabId());
      if (tabId == null) return { id, type: 'dom.context.result', payload: { ok: false, error: 'No active tab' } };
      try {
        const result = await sendMessageWithInjection(tabId, { ...msg, source: 'background' });
        return { id, type: 'dom.context.result', payload: result };
      } catch (err) {
        return { id, type: 'dom.context.result', payload: { ok: false, error: err.message } };
      }
    }

    case 'dom.click': {
      const tabId = payload.tabId ?? (await getActiveTabId());
      if (tabId == null) return { id, type: 'dom.click.result', payload: { ok: false, error: 'No active tab' } };
      try {
        const result = await sendMessageWithInjection(tabId, { ...msg, source: 'background' });
        return { id, type: 'dom.click.result', payload: result };
      } catch (err) {
        return { id, type: 'dom.click.result', payload: { ok: false, error: err.message } };
      }
    }

    case 'dom.fill': {
      const tabId = payload.tabId ?? (await getActiveTabId());
      if (tabId == null) return { id, type: 'dom.fill.result', payload: { ok: false, error: 'No active tab' } };
      try {
        const result = await sendMessageWithInjection(tabId, { ...msg, source: 'background' });
        return { id, type: 'dom.fill.result', payload: result };
      } catch (err) {
        return { id, type: 'dom.fill.result', payload: { ok: false, error: err.message } };
      }
    }

    case 'dom.search':
    case 'dom.fill_and_click': {
      const tabId = payload.tabId ?? (await getActiveTabId());
      if (tabId == null) return { id, type: `${msg.type}.result`, payload: { ok: false, error: 'No active tab' } };
      try {
        const result = await sendMessageWithInjection(tabId, { ...msg, source: 'background' });
        return { id, type: `${msg.type}.result`, payload: result };
      } catch (err) {
        return { id, type: `${msg.type}.result`, payload: { ok: false, error: err.message } };
      }
    }

    case 'page.summarizeTranscript': {
      const tabId = payload.tabId ?? (await getActiveTabId());
      if (tabId == null) return { id, type: 'page.summarizeTranscript.result', payload: { ok: false, error: 'No active tab' } };
      try {
        const result = await sendMessageWithInjection(tabId, { ...msg, source: 'background' });
        return { id, type: 'page.summarizeTranscript.result', payload: result };
      } catch (err) {
        return { id, type: 'page.summarizeTranscript.result', payload: { ok: false, error: err.message } };
      }
    }

    // ── Fallthrough: forward everything else to the active tab ─────────────

    default: {
      const tabId = payload?.tabId ?? (await getActiveTabId());
      if (tabId == null) {
        return { id, ok: false, error: 'No active tab found' };
      }
      try {
        const result = await chrome.tabs.sendMessage(tabId, { ...msg, source: 'background' });
        return { id, ...result };
      } catch (err) {
        return { id, ok: false, error: err.message };
      }
    }
  }
}

// ─── Tab event forwarding ─────────────────────────────────────────────────────

/**
 * When the active tab changes, emit tabs.changed to the bridge
 * (via offscreen relay). The bridge can use this to track context.
 * Spec §3.3.
 */
chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  try {
    const tab = await chrome.tabs.get(tabId);
    await sendToOffscreen({
      source: 'background',
      type: 'tabs.changed',
      payload: { url: tab.url, title: tab.title, tabId },
    });
  } catch (err) {
    console.warn('[background] tabs.onActivated relay failed:', err.message);
  }
});
