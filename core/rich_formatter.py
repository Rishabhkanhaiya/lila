"""
rich_formatter.py — JARVIS Rich Card & Link Renderer
=====================================================
Detects when JARVIS returns a list of items (hackathons, events, links,
products, jobs, etc.) and converts plain-text / markdown into beautiful
HTML cards with clickable Register/Visit buttons.

The desktop terminal already renders innerHTML when the text contains
< and > tags — so this output is instantly displayed as rich cards.

The Flutter app receives the same payload via the mesh and renders
clickable link buttons using its own card widget.

Usage:
    from core.rich_formatter import maybe_richify
    html = maybe_richify(plain_text, user_query)
    # returns rich HTML if applicable, original text otherwise
"""

import re
from typing import Optional


# ── Trigger detection ──────────────────────────────────────────────────────────
_RICH_TRIGGERS = [
    # Events / hackathons
    r'\bhackathon', r'\bcontest', r'\bcompetition', r'\bevent', r'\bsummit',
    r'\bconference', r'\bwebinar', r'\bworkshop',
    # Links / resources
    r'\bregister', r'\bsign.?up', r'\bapply', r'\bjoin',
    # Lists of things
    r'\blatest\b', r'\btop \d', r'\bbest \d', r'\blist of',
    # Jobs / products
    r'\bjob', r'\binternship', r'\bcourse', r'\bscholarship',
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _RICH_TRIGGERS]


def _should_richify(query: str, response: str) -> bool:
    """Return True if the response should be converted to rich HTML cards."""
    combined = (query + " " + response).lower()
    return any(p.search(combined) for p in _COMPILED)


# ── URL extractor ──────────────────────────────────────────────────────────────
_URL_RE = re.compile(
    r'https?://[^\s\)\]\"\'<>]+',
    re.IGNORECASE
)

_BOLD_RE  = re.compile(r'\*\*(.+?)\*\*')
_HEAD_RE  = re.compile(r'^#{1,4}\s+(.+)$', re.MULTILINE)
_BULL_RE  = re.compile(r'^\s*[-*•]\s+(.+)$', re.MULTILINE)
_NUM_RE   = re.compile(r'^\s*\d+\.\s+(.+)$', re.MULTILINE)


# ── Card colours cycling ──────────────────────────────────────────────────────
_ACCENT_COLOURS = [
    "#00FFCC",   # cyan
    "#0A84FF",   # blue
    "#BF5AF2",   # purple
    "#FF9F0A",   # orange
    "#30D158",   # green
    "#FF375F",   # red
]


def _colour_for(index: int) -> str:
    return _ACCENT_COLOURS[index % len(_ACCENT_COLOURS)]


# ── Section parser ─────────────────────────────────────────────────────────────
def _parse_sections(text: str) -> list[dict]:
    """
    Split markdown-style text into discrete card sections.
    Each section has:
      title    : str
      body     : str
      url      : str | None
      btn_label: str
    """
    sections = []

    # Strategy 1 — numbered list items  (1. Title\n   body\n   url)
    blocks = re.split(r'\n(?=\s*\d+\.)', text)
    if len(blocks) >= 2:
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            lines = block.splitlines()
            title_line = re.sub(r'^\d+\.\s*', '', lines[0]).strip()
            title_line = _BOLD_RE.sub(r'\1', title_line)
            body_lines = [l.strip() for l in lines[1:] if l.strip()]
            url = None
            clean_body = []
            for bl in body_lines:
                m = _URL_RE.search(bl)
                if m:
                    url = m.group(0)
                else:
                    clean_body.append(bl)
            sections.append({
                "title": title_line,
                "body": " ".join(clean_body)[:200],
                "url": url,
            })
        if len(sections) >= 2:
            return sections

    # Strategy 2 — markdown H2/H3 headers as card titles
    sections = []
    parts = _HEAD_RE.split(text)
    # parts alternates: [pre_text, title1, body1, title2, body2 ...]
    if len(parts) >= 3:
        it = iter(parts[1:])   # skip pre-text
        for title, body in zip(it, it):
            url = None
            m = _URL_RE.search(body)
            if m:
                url = m.group(0)
                body = body.replace(url, "").strip()
            body = _BOLD_RE.sub(r'\1', body).strip()
            sections.append({"title": title.strip(), "body": body[:200], "url": url})
        if len(sections) >= 2:
            return sections

    # Strategy 3 — bullet list items
    sections = []
    bullets = _BULL_RE.findall(text) or _NUM_RE.findall(text)
    for item in bullets:
        url = None
        m = _URL_RE.search(item)
        if m:
            url = m.group(0)
            item = item.replace(url, "").strip()
        item = _BOLD_RE.sub(r'\1', item)
        sections.append({"title": item[:80], "body": "", "url": url})
    if len(sections) >= 2:
        return sections

    return []


# ── Button label heuristic ────────────────────────────────────────────────────
def _btn_label(url: Optional[str], title: str) -> str:
    if not url:
        return None
    t = (title + " " + url).lower()
    if any(k in t for k in ["register", "sign up", "signup", "join"]):
        return "REGISTER NOW"
    if any(k in t for k in ["apply", "application"]):
        return "APPLY NOW"
    if any(k in t for k in ["github", "repo", "code"]):
        return "VIEW CODE"
    if any(k in t for k in ["youtube", "video", "watch"]):
        return "WATCH VIDEO"
    if any(k in t for k in ["job", "career", "hire"]):
        return "APPLY NOW"
    return "VISIT LINK"


# ── HTML Card renderer ────────────────────────────────────────────────────────
_CARD_CSS = """
<style>
.jv-rich-wrap {
    font-family: 'Inter', 'Segoe UI', sans-serif;
    padding: 4px 0;
}
.jv-rich-header {
    font-size: 11px;
    letter-spacing: 2px;
    color: rgba(255,255,255,0.35);
    margin-bottom: 12px;
    text-transform: uppercase;
}
.jv-cards {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
}
.jv-card {
    background: rgba(255,255,255,0.03);
    border: 1px solid var(--c);
    border-radius: 12px;
    padding: 16px 18px;
    flex: 1 1 260px;
    min-width: 220px;
    max-width: 340px;
    position: relative;
    box-shadow: 0 0 18px -8px var(--c);
    transition: transform 0.15s, box-shadow 0.15s;
}
.jv-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 0 30px -6px var(--c);
}
.jv-num {
    position: absolute;
    top: -10px; left: 14px;
    background: var(--c);
    color: #06080F;
    font-size: 10px;
    font-weight: 900;
    border-radius: 20px;
    padding: 2px 10px;
    letter-spacing: 1px;
}
.jv-title {
    font-size: 14px;
    font-weight: 700;
    color: #fff;
    margin: 8px 0 6px;
    line-height: 1.3;
}
.jv-body {
    font-size: 12px;
    color: rgba(255,255,255,0.55);
    line-height: 1.5;
    margin-bottom: 12px;
}
.jv-btn {
    display: inline-block;
    padding: 7px 18px;
    border-radius: 8px;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 1.5px;
    text-decoration: none;
    color: var(--c);
    border: 1px solid var(--c);
    background: rgba(255,255,255,0.04);
    cursor: pointer;
    transition: background 0.15s, box-shadow 0.15s;
}
.jv-btn:hover {
    background: rgba(255,255,255,0.1);
    box-shadow: 0 0 12px -4px var(--c);
}
.jv-no-url {
    font-size: 11px;
    color: rgba(255,255,255,0.25);
    font-style: italic;
}
</style>
"""


def _build_html(sections: list[dict], query: str) -> str:
    n = len(sections)
    topic_guess = re.sub(r'(latest|top\s+\d+|list of|give me|find|show|tell me about)\s*', '',
                         query, flags=re.IGNORECASE).strip().title()

    cards_html = ""
    for i, sec in enumerate(sections):
        colour = _colour_for(i)
        btn_label = _btn_label(sec.get("url"), sec.get("title", ""))

        if btn_label and sec.get("url"):
            btn_html = (
                f'<a class="jv-btn" href="{sec["url"]}" target="_blank" rel="noopener">'
                f'{btn_label}</a>'
            )
        elif sec.get("url"):
            btn_html = (
                f'<a class="jv-btn" href="{sec["url"]}" target="_blank" rel="noopener">'
                f'OPEN LINK</a>'
            )
        else:
            btn_html = '<span class="jv-no-url">No link available</span>'

        body_html = f'<div class="jv-body">{sec["body"]}</div>' if sec.get("body") else ""

        cards_html += f"""
<div class="jv-card" style="--c:{colour}">
  <span class="jv-num">#{i+1}</span>
  <div class="jv-title">{sec['title']}</div>
  {body_html}
  {btn_html}
</div>"""

    header = f'<div class="jv-rich-header">⚡ {n} Results — {topic_guess}</div>' if topic_guess else ""

    return (
        f"{_CARD_CSS}"
        f'<div class="jv-rich-wrap">'
        f"{header}"
        f'<div class="jv-cards">{cards_html}</div>'
        f'</div>'
    )


# ── Main entry point ──────────────────────────────────────────────────────────
def maybe_richify(response: str, query: str = "") -> str:
    """
    Given a plain/markdown LLM response and the original user query,
    return a rich HTML card layout if applicable, or the original text.
    """
    if not response or len(response) < 80:
        return response

    if not _should_richify(query, response):
        return response

    sections = _parse_sections(response)
    if len(sections) < 2:
        # Even if we can't split into cards, at least linkify URLs
        return _linkify(response)

    return _build_html(sections, query)


def _linkify(text: str) -> str:
    """Convert bare URLs in plain text to clickable anchor tags."""
    def _replace(m):
        url = m.group(0)
        short = url.split("//")[-1][:40]
        return f'<a href="{url}" target="_blank" style="color:#00FFCC; text-decoration:underline;">{short}</a>'
    return _URL_RE.sub(_replace, text)
