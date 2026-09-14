"""
response_guard.py — JARVIS Response Quality Guard (POLISH P3)
=============================================================
Post-processes EVERY LLM response before it reaches voice or UI.
Strips markdown artifacts, JSON leaks, thought tags, double spaces,
numbered lists that shouldn't be spoken, and other LLM artifacts.

Usage:
    from core.response_guard import clean_response
    clean = clean_response(raw_llm_output)
"""

import re
from core.jarvis_logger import log_info


STAGE_WORDS = {
    'encouragement', 'encouraging', 'happy', 'happiness', 'excited', 'excitement',
    'playful', 'playfulness', 'teasing', 'affectionate', 'affection', 'loving', 'love',
    'supportive', 'comforting', 'comfort', 'reassurance', 'reassuring', 'greeting',
    'enthusiastic', 'enthusiasm', 'curious', 'curiosity', 'thoughtful', 'pride', 'proud',
    'sympathetic', 'sympathy', 'empathy', 'empathetic', 'cheerful', 'cheerfulness',
    'warm', 'warmth', 'friendly', 'gentle', 'caring', 'sweet', 'surprised', 'surprise',
    'amused', 'amusement', 'giggle', 'giggles', 'laugh', 'laughs', 'smile', 'smiles',
    'chuckle', 'chuckles', 'sigh', 'sighs', 'blush', 'blushes', 'wink', 'winks',
    'gasp', 'gasps', 'grin', 'grins', 'whisper', 'whispers', 'softly', 'pout', 'pouts',
    'nod', 'nods', 'snicker', 'snickers', 'yawn', 'yawns', 'clear', 'throat',
    'cough', 'coughs', 'shrug', 'shrugs', 'relieved',
    'sadness', 'sad', 'confusion', 'confused', 'neutral', 'anger', 'angry', 'upset',
    'annoyed', 'serious', 'expression', 'expressions', 'mood', 'moods', 'emotion', 'emotions', 'tone', 'tones'
}

_BRACKETED_EXPRESSION_TAG = re.compile(
    r'(?:\[|\*|\()\s*(?:Expression|Emotion|Mood|Tone)\s*:\s*[^\]*)]+\s*(?:\]|\*|\))\s*[:,\-!.]*',
    re.IGNORECASE
)

_UNBRACKETED_EXPRESSION_TAG = re.compile(
    r'\b(?:Expression|Emotion|Mood|Tone)\s*:\s*[a-zA-Z_]+\s*[:,\-!.]*',
    re.IGNORECASE
)

_SORTED_STAGE_WORDS = sorted(list(STAGE_WORDS), key=len, reverse=True)
_EMOTION_WORDS_REGEX = '|'.join(re.escape(w) for w in _SORTED_STAGE_WORDS)

_EMOTION_PREFIX_PATTERN = re.compile(
    r'^\s*(?:\[|\*|\()?\s*(?:' + _EMOTION_WORDS_REGEX + r')\s*(?:\]|\*|\))?\s*[:,\-!.]+\s*',
    re.IGNORECASE
)


def clean_response(text: str) -> str:
    """
    Clean raw LLM output for human consumption.
    Designed to be idempotent — safe to call multiple times.
    """
    if not text or not isinstance(text, str):
        return text or ""

    original = text

    # 1. Strip <thought>...</thought> tags (Gemma/DeepSeek chain-of-thought)
    text = re.sub(r'<thought>.*?</thought>', '', text, flags=re.DOTALL)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)

    # 2. Strip markdown code fences that leaked into speech
    text = re.sub(r'```[\w]*\n?', '', text)

    # 2b. Strip explicit Expression/Emotion/Mood tags anywhere in text
    text = _BRACKETED_EXPRESSION_TAG.sub('', text)
    text = _UNBRACKETED_EXPRESSION_TAG.sub('', text)

    # 3. Strip stage directions & emotion tags in asterisks, brackets, or parentheses
    def _asterisk_sub(m):
        inner = m.group(1).strip()
        words = [w.lower() for w in re.findall(r'[a-zA-Z]+', inner)]
        if any(w in STAGE_WORDS for w in words):
            return ''
        return inner
    text = re.sub(r'\*([^*]+)\*', _asterisk_sub, text)
    text = re.sub(r'\[([^\]]+)\]', lambda m: '' if any(w in STAGE_WORDS for w in re.findall(r'[a-zA-Z]+', m.group(1).lower())) else m.group(0), text)
    text = re.sub(r'\(([^)]+)\)', lambda m: '' if any(w in STAGE_WORDS for w in re.findall(r'[a-zA-Z]+', m.group(1).lower())) else m.group(0), text)

    # 3b. Strip starting emotion labels (e.g. "Encouragement:", "Happy:", "Sadness:", "[Happy]")
    for _ in range(5):
        m_emo = _EMOTION_PREFIX_PATTERN.match(text)
        if m_emo:
            text = text[m_emo.end():].strip()
        else:
            break

    # 3c. Strip **bold** and *italic* markdown
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)

    # 4. Strip markdown headers (# ## ###)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)

    # 5. Strip bullet points (- or *)
    text = re.sub(r'^\s*[-*]\s+', '', text, flags=re.MULTILINE)

    # 6. Strip numbered lists for voice (1. 2. 3.)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)

    # 7. Strip leftover HTML tags
    text = re.sub(r'<[^>]+>', '', text)

    # 8. Strip emoji clusters that TTS can't pronounce well
    # Keep single emoji but strip clusters of 3+
    text = re.sub(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF]{3,}', '', text)

    # 8b. Strip model internal chain-of-thought or meta-commentary preambles
    blocks = [b.strip() for b in text.split('\n\n') if b.strip()]
    if len(blocks) > 1:
        meta_indicators = [
            'my initial thought', 'i began with', "i've crafted", "i've determined",
            "so i'm putting together", 'aiming for a charming', 'as instructed',
            'no tools were needed', 'listing capabilities', 'initiating contact',
            'greeting the user', 'calculating the answer'
        ]
        cleaned_blocks = [b for b in blocks if not any(ind in b.lower() for ind in meta_indicators)]
        if cleaned_blocks:
            text = '\n\n'.join(cleaned_blocks)

    # 8c-1. Title-cased meta headers (e.g. Planning Another Demonstration, Evaluating Tool Availability, Generate a Graph, Investigating Antigravity)
    title_pattern = (
        r'^(?:(?:[A-Z][a-z0-9\'-]+(?:\s+(?:[A-Z][a-z0-9\'-]+|[a-z]{1,3}\s+[A-Z][a-z0-9\'-]+|\'[^\']+\'|"[^"]+")){0,6})\s*)'
        r'(?:[:.!?]|\s+(?=(?:I[\'’]m|I[\'’]ve|I\b|The\b|My\b|This\b|We\b|It\b|Currently\b|However\b|As\b|To\b|Let\b|Generating\b|Now\b|Hopefully\b|Oh\b|[A-Z\u0900-\u097F]))|\n)\s*'
    )

    # 8c-2. Meta sentence thought preambles
    sentence_meta_pattern = (
        r'^(?:'
        r'Targeting The Specifics|Locating Output Directory|Locating a File|'
        r'Initiating Contact|Calculating The Answer|Listing Capabilities|'
        r'I[\'’]m dissatisfied with|I need to showcase|My approach will be|I aim to create|'
        r'I[\'’]m working on explaining|It seems there[\'’]s a misunderstanding|'
        r'I[\'’]m focusing on clarifying|Managing expectations about|'
        r'I[\'’]m now zeroing in on|The user wants something|'
        r'The previous [^.\n]+ was a miss|I[\'’]m leaning toward|'
        r'It offers direct|I need to make the|Refining [^.\n]+|'
        r'I[\'’]ve got a clearer focus|The user[\'’]s feedback makes it imperative|'
        r'The goal is to make it|I intend to enhance|This avoids background processes|'
        r'I realized my output|It seems there was an error|Now,\s*I will apologize|'
        r'Then,\s*I plan to retry|Hopefully,\s*it runs successfully|'
        r'I[\'’]ve checked the tool definitions|Unfortunately,\s*it[\'’]s not present|'
        r'I[\'’]m pivoting to alternative options|My thinking is to|'
        r'I[\'’]ve decided to leverage|Specifically,\s*I[\'’]ll use|'
        r'I[\'’]ll also specify whether|The search for [^.\n]+ didn[\'’]t find|'
        r'I[\'’]ve been stuck|The previous attempts|I[\'’]m going to have to use|'
        r'My initial thought|I began with|I[\'’]ve determined|Okay,\s*so I[\'’]m|'
        r'I need a targeted approach|I[\'’]m now focusing on|The user wanted it saved|'
        r'I[\'’]m assuming the tool|Let[\'’]s try an alternative|Navigating to|'
        r'Looking at the results|Searching for|Determining the next step|'
        r'I understand the frustration|It seems [\'"][^\'"]+[\'"] is still missing|'
        r'To confirm,\s*I saved|I[\'’]ve decided to proceed|This time,\s*I[\'’]m leaning|'
        r'My plan is to define|This seems like a strong|I[\'’]m currently verifying|'
        r'The initial launch was confirmed|It[\'’]s likely still processing|'
        r'I lack a direct|I[\'’]ll provide an estimated|I understand your eagerness|'
        r'Currently,\s*the [\'"]?run_swarm|However,\s*rest assured|Generating a detailed|'
        r'I[\'’]ll inform you when|Due to its background nature'
        r')'
        r'.*?(?:[.!?](?=\s+[A-Z0-9\u0900-\u097F]|\s*$))\s*'
    )

    for _ in range(25):
        m_t = re.match(title_pattern, text)
        if m_t:
            text = text[m_t.end():].strip()
            continue

        m = re.match(sentence_meta_pattern, text, flags=re.IGNORECASE)
        if m:
            text = text[m.end():].strip()
            continue

        break

    # Strip any leading quotes or stray punctuation left from stripped clauses
    text = re.sub(r"^['\"\s,.:;—-]+", "", text).strip()

    # If remaining string is solely a stage word or emotion tag (e.g. "confusion", "sadness")
    if text.strip(' .,!?:;-*[]()').lower() in STAGE_WORDS:
        return ""

    # 9. Clean up whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)     # Max 2 newlines
    text = re.sub(r'  +', ' ', text)            # No double spaces
    text = re.sub(r' ([.,!?;:])', r'\1', text)  # No space before punctuation

    # 10. Strip trailing/leading whitespace
    text = text.strip()

    # 11. Persona guard — import if available
    try:
        from core.brain import persona_guard
        text = persona_guard(text)
    except Exception:
        pass

    if text != original:
        log_info("response_guard", f"Cleaned response ({len(original)} → {len(text)} chars)")

    return text


def clean_for_voice(text: str, max_chars: int = 500) -> str:
    """
    Extra cleaning specifically for TTS output.
    - Truncates very long responses
    - Removes parenthetical asides
    - Converts abbreviations TTS might mangle
    """
    text = clean_response(text)

    # Remove URLs (TTS reads them character by character)
    text = re.sub(r'https?://\S+', 'a link', text)

    # Remove parenthetical content for cleaner speech
    text = re.sub(r'\([^)]{50,}\)', '', text)

    # Truncate for TTS (don't speak essays)
    if len(text) > max_chars:
        # Cut at last sentence boundary before limit
        truncated = text[:max_chars]
        last_period = max(truncated.rfind('.'), truncated.rfind('!'), truncated.rfind('?'))
        if last_period > max_chars // 2:
            text = truncated[:last_period + 1]
        else:
            text = truncated.rstrip() + "..."

    return text.strip()
