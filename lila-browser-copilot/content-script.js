// =============================================================================
// Lila Browser Copilot — content-script.js
// Runs in every tab/frame (all_frames: true).
// Implements spec §2.2, §2.3, §4, §5.4
// =============================================================================

'use strict';

// ---------------------------------------------------------------------------
// §5.4 — Accessible Name Resolver
// ---------------------------------------------------------------------------

/**
 * Returns the accessible name of an element, following ARIA priority order.
 * @param {Element} el
 * @returns {string}
 */
function getAccessibleName(el) {
  return (
    el.getAttribute('aria-label') ||
    (el.getAttribute('aria-labelledby') &&
      document.getElementById(el.getAttribute('aria-labelledby'))?.textContent) ||
    el.textContent?.trim() ||
    el.getAttribute('title') ||
    el.getAttribute('placeholder') ||
    el.getAttribute('name') ||
    el.getAttribute('id') ||
    ''
  ).trim().toLowerCase();
}

/**
 * Recursively walks all elements in the DOM, including shadow roots.
 * @param {Document|ShadowRoot|Element} root
 * @param {Element[]} out
 * @returns {Element[]}
 */
function walkAllElements(root, out = []) {
  for (const el of root.querySelectorAll('*')) {
    out.push(el);
    if (el.shadowRoot) walkAllElements(el.shadowRoot, out);
  }
  return out;
}

/**
 * Resolves a human-readable description to a DOM element using accessible names.
 * Prefers exact matches, falls back to partial (includes) matches.
 * Supports role awareness (prefers inputs for fill, buttons/links for click).
 * @param {string} description
 * @param {'input'|'button'|null} preferredRole
 * @returns {Element|undefined}
 */
function resolveTarget(description, preferredRole = null) {
  const target = description.trim().toLowerCase();
  const all = walkAllElements(document);

  const candidates = all.filter((el) =>
    ['button', 'a', 'input', 'textarea', '[role=button]', '[role=search]', '[contenteditable=true]'].some(
      (sel) => el.matches?.(sel)
    )
  );

  // When resolving an input field (e.g. for fill_element)
  if (preferredRole === 'input') {
    const inputs = candidates.filter((el) =>
      el.matches?.('input:not([type=button]):not([type=submit]):not([type=reset]):not([type=checkbox]):not([type=radio]), textarea, [contenteditable=true]')
    );
    if (inputs.length > 0) {
      const match =
        inputs.find((el) => getAccessibleName(el) === target) ||
        inputs.find((el) => getAccessibleName(el).includes(target)) ||
        inputs.find((el) => target.includes(getAccessibleName(el)) && getAccessibleName(el).length > 2) ||
        (target.includes('search') ? inputs.find((el) => el.type === 'search' || getAccessibleName(el).includes('search') || (el.getAttribute('name') && el.getAttribute('name').toLowerCase().includes('search')) || (el.id && el.id.toLowerCase().includes('search'))) : null);
      if (match) return match;
    }
  }

  // When resolving a clickable element (e.g. for click_element)
  if (preferredRole === 'button') {
    // 1. Prioritize real button elements first
    const realButtons = candidates.filter((el) =>
      el.matches?.('button, input[type=submit], input[type=button], [role=button]')
    );
    if (realButtons.length > 0) {
      const match =
        realButtons.find((el) => getAccessibleName(el) === target) ||
        realButtons.find((el) => getAccessibleName(el).includes(target)) ||
        realButtons.find((el) => target.includes(getAccessibleName(el)) && getAccessibleName(el).length > 2) ||
        (target.includes('search') ? realButtons.find((el) => getAccessibleName(el).includes('search') || el.type === 'submit' || (el.className && typeof el.className === 'string' && el.className.toLowerCase().includes('search'))) : null);
      if (match) return match;
    }

    // 2. Fallback to links only if no real button matched
    const links = candidates.filter((el) => el.matches?.('a'));
    if (links.length > 0) {
      const match =
        links.find((el) => getAccessibleName(el) === target) ||
        links.find((el) => getAccessibleName(el).includes(target)) ||
        links.find((el) => target.includes(getAccessibleName(el)) && getAccessibleName(el).length > 2);
      if (match) return match;
    }
  }

  return (
    candidates.find((el) => getAccessibleName(el) === target) ||
    candidates.find((el) => getAccessibleName(el).includes(target)) ||
    candidates.find((el) => target.includes(getAccessibleName(el)) && getAccessibleName(el).length > 2)
  );
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

/**
 * Simple promise-based sleep helper.
 * @param {number} ms  Milliseconds to wait.
 * @returns {Promise<void>}
 */
function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// ---------------------------------------------------------------------------
// §2.2 — Media Controls (Option A: mute-play-unmute)
// ---------------------------------------------------------------------------

/**
 * Finds the first <video> element in the document, including inside shadow DOM.
 * @returns {HTMLVideoElement|undefined}
 */
function findVideoElement() {
  return walkAllElements(document).find((el) => el.tagName?.toLowerCase() === 'video');
}

/**
 * Ensures video playback starts reliably using:
 * 1. YouTube movie_player API (mute -> playVideo -> unMute)
 * 2. HTML5 video element mute-play-unmute (Spec §2.2 Option A)
 * 3. Polling retry loop up to 10 seconds to handle background tab deferral.
 * @param {number} maxWaitMs
 * @returns {Promise<{ok: boolean, error?: string, method?: string}>}
 */
async function mutePlayUnmute(maxWaitMs = 10000) {
  const start = Date.now();
  while (Date.now() - start < maxWaitMs) {
    // 1. YouTube internal player API (most robust on YouTube)
    const player = document.getElementById('movie_player');
    if (player && typeof player.playVideo === 'function') {
      try {
        if (typeof player.mute === 'function') player.mute();
        player.playVideo();
        setTimeout(() => {
          try {
            if (typeof player.unMute === 'function') player.unMute();
            if (typeof player.setVolume === 'function') player.setVolume(100);
          } catch (_) {}
        }, 500);
        return { ok: true, method: 'movie_player' };
      } catch (_) {}
    }

    // 2. HTML5 video element
    const video = findVideoElement();
    if (video) {
      if (!video.paused && video.currentTime > 0) {
        video.muted = false;
        return { ok: true, method: 'already_playing' };
      }
      try {
        video.muted = true;
        await video.play();
        setTimeout(() => { video.muted = false; }, 400);
        return { ok: true, method: 'html5_video' };
      } catch (_) {}
    }

    // 3. YouTube play button
    const playBtn = document.querySelector('.ytp-play-button');
    if (playBtn && playBtn.getAttribute('data-title-no-tooltip') === 'Play') {
      playBtn.click();
    }

    await sleep(400);
  }

  return { ok: false, error: 'Video element or player not ready after timeout' };
}

// ---------------------------------------------------------------------------
// §4 — DOM Context: Article Text Extractor
// ---------------------------------------------------------------------------

/**
 * Extracts the main article text from the current page using a simplified
 * Readability-style approach — clones the document, strips noise, and reads
 * the innerText of the most relevant container.
 * @returns {string}
 */
function extractArticleText() {
  // Clone doc to avoid mutating live DOM
  const clone = document.cloneNode(true);
  // Remove noise elements
  [
    'script', 'style', 'nav', 'header', 'footer', 'aside',
    '[role=banner]', '[role=navigation]', '[role=complementary]', '[aria-hidden=true]'
  ].forEach((sel) => clone.querySelectorAll(sel).forEach((el) => el.remove()));
  // Try main content element
  const main = clone.querySelector('main, [role=main], article, .article, #content, #main');
  const source = main || clone.body;
  const rawText = source?.innerText || source?.textContent || '';
  return rawText.replace(/\s{3,}/g, '\n\n').trim().slice(0, 8000);
}

// ---------------------------------------------------------------------------
// §4 — DOM Context: YouTube Transcript Extractor
// ---------------------------------------------------------------------------

/**
 * Extracts the YouTube transcript for the current video.
 * Opens the transcript panel if it is not already visible, reads all segment
 * text, then closes the panel.
 * @returns {Promise<{ok: boolean, transcript?: string, error?: string}>}
 */
async function extractYouTubeTranscript() {
  // Check if transcript panel already open
  let segments = document.querySelectorAll('ytd-transcript-segment-renderer');

  if (segments.length === 0) {
    // Try to find and click 'Show transcript' button directly
    const btn = resolveTarget('show transcript') || resolveTarget('transcript');

    if (!btn) {
      // Try opening the '...' more-options menu first
      const moreBtn = resolveTarget('more actions') || resolveTarget('...');
      if (moreBtn) {
        moreBtn.click();
        await sleep(600);
        const transcriptOpt =
          resolveTarget('show transcript') || resolveTarget('open transcript');
        if (transcriptOpt) {
          transcriptOpt.click();
        } else {
          return { ok: false, error: 'transcript button not found in menu' };
        }
      } else {
        return { ok: false, error: 'could not locate transcript button' };
      }
    } else {
      btn.click();
    }

    // Wait for segments to render
    await sleep(1500);
    segments = document.querySelectorAll('ytd-transcript-segment-renderer');
  }

  if (segments.length === 0) {
    return { ok: false, error: 'transcript segments not rendered' };
  }

  const text = Array.from(segments)
    .map(
      (s) =>
        s.querySelector('.segment-text, [class*=text]')?.textContent?.trim() ||
        s.textContent?.trim()
    )
    .filter(Boolean)
    .join(' ');

  // Close the transcript panel
  const closeBtn = resolveTarget('close transcript') || resolveTarget('collapse');
  if (closeBtn) closeBtn.click();

  return { ok: true, transcript: text };
}

// ---------------------------------------------------------------------------
// §4 — Selection Change Listener (debounced 300 ms)
// ---------------------------------------------------------------------------

let _selectionDebounce = null;

document.addEventListener('selectionchange', () => {
  clearTimeout(_selectionDebounce);
  _selectionDebounce = setTimeout(() => {
    try {
      const sel = window.getSelection()?.toString() ?? '';
      if (sel.length > 0) {
        chrome.runtime.sendMessage({
          source: 'content-script',
          type: 'dom.context.changed',
          payload: {
            url: location.href,
            title: document.title,
            selection: sel,
          },
        }).catch(() => {});
      }
    } catch (_) {}
  }, 300);
});

// ---------------------------------------------------------------------------
// §2.3 / §4 — Message Router
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    switch (msg.type) {
      // -------------------------------------------------------------------
      // DOM context snapshot
      // -------------------------------------------------------------------
      case 'dom.context.request': {
        sendResponse({
          ok: true,
          url: location.href,
          title: document.title,
          selection: window.getSelection()?.toString() ?? '',
          articleText: extractArticleText(),
        });
        break;
      }

      // -------------------------------------------------------------------
      // Click an element by accessible-name description
      // -------------------------------------------------------------------
      case 'dom.click': {
        const el = resolveTarget(msg.payload?.targetDescription ?? '', 'button');
        if (el) {
          try {
            el.focus();
            el.click();
            const formToSubmit = el.form || (el.tagName?.toLowerCase() === 'form' ? el : null);
            if (formToSubmit) {
              try {
                if (typeof formToSubmit.requestSubmit === 'function') {
                  formToSubmit.requestSubmit(el.tagName?.toLowerCase() === 'form' ? undefined : el);
                } else {
                  formToSubmit.submit();
                }
              } catch (_) {}
            }
            sendResponse({ ok: true, tag: el.tagName?.toLowerCase() });
          } catch (e) {
            sendResponse({ ok: false, error: e.message });
          }
        } else {
          sendResponse({ ok: false, error: 'target not found' });
        }
        break;
      }

      // -------------------------------------------------------------------
      // Fill a form field by accessible-name description
      // -------------------------------------------------------------------
      case 'dom.fill': {
        const el = resolveTarget(msg.payload?.targetDescription ?? '', 'input');
        if (el) {
          try {
            el.focus();
            const prototype = Object.getPrototypeOf(el);
            const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
            if (descriptor && descriptor.set) {
              descriptor.set.call(el, msg.payload.value);
            } else {
              el.value = msg.payload.value;
            }
            el.dispatchEvent(new Event('focus', { bubbles: true, composed: true }));
            el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
            el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
            sendResponse({ ok: true, tag: el.tagName?.toLowerCase() });
          } catch (e) {
            sendResponse({ ok: false, error: e.message });
          }
        } else {
          sendResponse({ ok: false, error: 'target not found' });
        }
        break;
      }

      // -------------------------------------------------------------------
      // Autonomous search & submit / fill & click (Atomic single-shot)
      // -------------------------------------------------------------------
      case 'dom.search':
      case 'dom.fill_and_click': {
        const inputDesc = msg.payload?.targetDescription || msg.payload?.inputDescription || 'search';
        const buttonDesc = msg.payload?.clickTarget || msg.payload?.buttonDescription || 'search';
        const val = msg.payload?.value ?? '';

        const inputEl = resolveTarget(inputDesc, 'input');
        if (!inputEl) {
          sendResponse({ ok: false, error: `Input field '${inputDesc}' not found` });
          break;
        }

        try {
          inputEl.focus();
          const prototype = Object.getPrototypeOf(inputEl);
          const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
          if (descriptor && descriptor.set) {
            descriptor.set.call(inputEl, val);
          } else {
            inputEl.value = val;
          }
          inputEl.dispatchEvent(new Event('focus', { bubbles: true, composed: true }));
          inputEl.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
          inputEl.dispatchEvent(new Event('change', { bubbles: true, composed: true }));

          await sleep(150);

          // 1. Look for a submit button directly inside the input's own form first!
          let btnEl = null;
          if (inputEl.form) {
            btnEl = inputEl.form.querySelector('button[type=submit], input[type=submit], button:not([type=button])') ||
                    inputEl.form.querySelector('button');
          }

          // 2. If not found in form, resolve by description prioritizing real buttons
          if (!btnEl) {
            btnEl = resolveTarget(buttonDesc, 'button');
          }

          let submitted = false;
          if (btnEl) {
            btnEl.focus();
            btnEl.click();
            const formToSubmit = btnEl.form || inputEl.form;
            if (formToSubmit) {
              try {
                if (typeof formToSubmit.requestSubmit === 'function') {
                  formToSubmit.requestSubmit(btnEl);
                } else {
                  formToSubmit.submit();
                }
                submitted = true;
              } catch (_) {}
            }
          }

          // 3. If form still hasn't submitted, submit inputEl.form directly
          if (!submitted && inputEl.form) {
            try {
              if (typeof inputEl.form.requestSubmit === 'function') {
                inputEl.form.requestSubmit();
              } else {
                inputEl.form.submit();
              }
              submitted = true;
            } catch (_) {}
          }

          // 4. Always dispatch Enter key events as well (for React/Vue/SPA apps that don't use standard forms)
          const enterOpts = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true };
          inputEl.dispatchEvent(new KeyboardEvent('keydown', enterOpts));
          inputEl.dispatchEvent(new KeyboardEvent('keypress', enterOpts));
          inputEl.dispatchEvent(new KeyboardEvent('keyup', enterOpts));

          sendResponse({
            ok: true,
            filled: val,
            clicked: btnEl ? (btnEl.tagName?.toLowerCase() || 'button') : (submitted ? 'form_submit' : 'enter_key')
          });
        } catch (e) {
          sendResponse({ ok: false, error: e.message });
        }
        break;
      }

      // -------------------------------------------------------------------
      // Media — play (mute-play-unmute strategy)
      // -------------------------------------------------------------------
      case 'media.play': {
        const result = await mutePlayUnmute();
        sendResponse(result);
        break;
      }

      // -------------------------------------------------------------------
      // Media — pause
      // -------------------------------------------------------------------
      case 'media.pause': {
        const v = findVideoElement();
        if (v) {
          v.pause();
          sendResponse({ ok: true });
        } else {
          sendResponse({ ok: false, error: 'no video element found' });
        }
        break;
      }

      // -------------------------------------------------------------------
      // Media — seek to timestamp (seconds)
      // -------------------------------------------------------------------
      case 'media.seek': {
        const v = findVideoElement();
        if (v) {
          v.currentTime = msg.payload.value;
          sendResponse({ ok: true });
        } else {
          sendResponse({ ok: false, error: 'no video element found' });
        }
        break;
      }

      // -------------------------------------------------------------------
      // Media — set volume (0.0 – 1.0)
      // -------------------------------------------------------------------
      case 'media.volume': {
        const v = findVideoElement();
        if (v) {
          v.volume = Math.max(0, Math.min(1, msg.payload.value));
          v.muted = msg.payload.value === 0;
          sendResponse({ ok: true });
        } else {
          sendResponse({ ok: false, error: 'no video element found' });
        }
        break;
      }

      // -------------------------------------------------------------------
      // Page — extract YouTube transcript
      // -------------------------------------------------------------------
      case 'page.summarizeTranscript': {
        const result = await extractYouTubeTranscript();
        sendResponse(result);
        break;
      }

      // -------------------------------------------------------------------
      // Default — unknown message type
      // -------------------------------------------------------------------
      default: {
        sendResponse({ ok: false, error: 'unknown message type' });
        break;
      }
    }
  })();

  // Return true to keep the message channel open for async sendResponse calls.
  return true;
});

// Auto-start background playback on YouTube watch pages with autoplay=1
if (location.href.includes('youtube.com/watch') && location.href.includes('autoplay=1')) {
  setTimeout(() => { mutePlayUnmute(12000); }, 1000);
}
