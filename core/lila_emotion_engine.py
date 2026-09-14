"""
core/lila_emotion_engine.py -- Lila Real-Time Emotion & Lip-Sync Engine
=======================================================================
Real-time sentiment analysis of Lila's own spoken text -> avatar emotion states.
Zero external API calls. Pure local keyword/phoneme pattern matching.
"""

import re
import time
import random
from typing import Optional, Callable, Dict, Any, List
from core.jarvis_logger import log_info, log_warn


# --- Emotion Rules -----------------------------------------------------------
EMOTION_RULES = {
    'happy': {
        'triggers': ['haha', 'lol', 'kya baat', 'amazing', 'awesome', 'love it', 'yay',
                     'pyaar', 'cute', 'sweet', 'mast', 'bahut accha', 'wah', 'shabash',
                     'bilkul', 'of course', 'perfect', 'great', 'wonderful', 'yaar',
                     'mujhe bahut', 'so good', 'so cute', 'bachpan', 'hasi'],
        'weight': 0.7,
        'conversation_state': 'happy',
        'mood': 'excited',
        'reaction_beat': 'small_bounce',
    },
    'excited': {
        'triggers': ['omg', 'oh wow', 'seriously', 'no way', 'wait what', 'done it',
                     'ready', "let's go", 'chal', 'abhi karta', 'abhi karti', 'dekho',
                     'immediately', 'itna', 'ek second', 'bas abhi', 'right now',
                     'just did', 'oh my god', 'incredible', 'unbelievable'],
        'weight': 0.9,
        'conversation_state': 'excited',
        'mood': 'excited',
        'reaction_beat': 'small_bounce',
    },
    'proud': {
        'triggers': ['done', 'completed', 'finished', 'successfully', 'created', 'built',
                     'ho gaya', 'kar diya', 'execute', 'launched', 'deployed', 'shipped',
                     'fixed', 'solved', 'working', 'it works', 'kaam kar raha',
                     'mission accomplished', 'all set', 'ready to go'],
        'weight': 0.85,
        'conversation_state': 'proud',
        'mood': 'excited',
        'reaction_beat': 'chest_puff',
    },
    'teasing': {
        'triggers': ['sach mein', 'teri toh', 'hehe', 'haan haan sure', 'pata hai',
                     'obviously', 'duh', 'clearly', 'jaise', 'matlab', 'tu toh',
                     'aise', 'acha', 'tch tch', 'yeh lo', 'kya scene hai',
                     'sharma raha', 'itna bhi nahi', 'bakwaas', 'chill'],
        'weight': 0.75,
        'conversation_state': 'teasing',
        'mood': 'teasing',
        'reaction_beat': None,
    },
    'thinking': {
        'triggers': ['let me think', 'hmm', 'umm', 'acha suno', 'soch', 'samajh',
                     'interesting', 'basically', 'technically', 'actually', 'you see',
                     'dekho na', 'check kar', 'let me check', 'analyzing', 'processing',
                     'looking at', 'scanning', 'reading'],
        'weight': 0.6,
        'conversation_state': 'thinking',
        'mood': 'focused',
        'reaction_beat': None,
    },
    'worried': {
        'triggers': ['oh no', 'error', 'failed', 'crash', 'broke', 'nahi hua',
                     'problem', 'issue', 'sorry', 'unfortunately', 'oops',
                     'something went wrong', 'not working', 'kaam nahi kar raha',
                     'exception', 'traceback', 'timeout', 'could not'],
        'weight': 0.8,
        'conversation_state': 'error',
        'mood': 'idle',
        'reaction_beat': 'slump',
    },
    'surprised': {
        'triggers': ['what!', 'really?', 'seriously?', 'sach mein?', 'kya?',
                     'wait wait', 'hold on', 'ek second', 'oh!', 'whoa', 'huh?',
                     'are you sure', 'kya hua', 'kyun?'],
        'weight': 0.85,
        'conversation_state': 'surprised',
        'mood': 'excited',
        'reaction_beat': 'full_body_flinch',
    },
    'sleepy': {
        'triggers': ['thoda ruk', 'ek minute', 'searching', 'loading',
                     'downloading', 'wait karein', 'kuch time', 'processing',
                     'background mein', 'running', 'executing task'],
        'weight': 0.5,
        'conversation_state': 'sleepy',
        'mood': 'sleepy',
        'reaction_beat': None,
    },
    'greeting': {
        'triggers': ['hi', 'hello', 'namaste', 'hey', 'good morning', 'good evening',
                     'good afternoon', 'kya haal', 'kaise ho', 'supr', 'namaskar'],
        'weight': 0.9,
        'conversation_state': 'greeting',
        'mood': 'excited',
        'reaction_beat': 'chest_puff',
    },
    'farewell': {
        'triggers': ['bye', 'goodbye', 'alvida', 'take care', 'good night',
                     'raat ko', 'phir milenge', 'see you', 'jao thodi der',
                     'rest karo', 'so jao'],
        'weight': 0.8,
        'conversation_state': 'farewell',
        'mood': 'idle',
        'reaction_beat': None,
    },
}


# --- Viseme Map --------------------------------------------------------------
VISEME_MAP = {
    # Vowels -> open mouth shapes
    'a': 'aa', 'A': 'aa',
    'e': 'ee', 'E': 'ee', 'i': 'ih', 'I': 'ih',
    'o': 'ou', 'O': 'ou', 'u': 'ou', 'U': 'ou',
    # Diphthongs / special combos
    'ai': 'aa', 'ay': 'aa', 'au': 'ou', 'aw': 'ou',
    'ee': 'ee', 'ea': 'ee', 'ie': 'ih',
    'oo': 'ou', 'ou': 'ou', 'ow': 'oh', 'oe': 'oh',
    'oh': 'oh',
    # Consonants -> brief aa at low weight
    'b': 'aa', 'p': 'aa', 'm': 'aa',    # bilabial
    'f': 'ih', 'v': 'ih',               # labiodental
    'th': 'ih', 'd': 'aa', 't': 'aa',   # dental/alveolar
    'n': 'ih', 'l': 'ih', 'r': 'ih',
    's': 'ih', 'z': 'ih', 'sh': 'ih', 'zh': 'ih',
    'k': 'aa', 'g': 'aa', 'h': 'aa',
    'ch': 'ih', 'j': 'ih', 'y': 'ih', 'w': 'ou',
}

_VOWELS = set('aeiouAEIOU')


# --- LilaEmotionEngine -------------------------------------------------------
class LilaEmotionEngine:
    """
    Real-time local emotion engine for Lila's avatar states.

    Analyzes Lila's own spoken text using keyword/pattern matching and emits
    avatar-compatible emotion + conversation state updates with zero API calls.
    """

    def __init__(self):
        self._last_conversation_state = None
        log_info('LilaEmotionEngine', 'Initialized -- zero-API local emotion engine ready')

    def analyze_text(self, text):
        """
        Analyze text for dominant emotion, intensity, and avatar state mappings.

        Returns:
            dict with keys: emotion, intensity, conversation_state, mood,
                            reaction_beat, dominant_viseme
        """
        lower = text.lower()
        scores = {}

        for emotion_name, rule in EMOTION_RULES.items():
            count = sum(1 for trigger in rule['triggers'] if trigger in lower)
            scores[emotion_name] = count * rule['weight'] if count > 0 else 0.0

        # Boost: '!' boosts all scores by 0.2
        if '!' in text:
            for k in scores:
                scores[k] += 0.2

        # Boost: '?' boosts surprised by 0.1
        if '?' in text:
            scores['surprised'] = scores.get('surprised', 0.0) + 0.1

        # Boost: long text boosts thinking by 0.15
        if len(text) > 80:
            scores['thinking'] = scores.get('thinking', 0.0) + 0.15

        best_emotion = max(scores, key=lambda k: scores[k]) if scores else None
        best_score = scores.get(best_emotion, 0.0) if best_emotion else 0.0

        if best_score < 0.15:
            return {
                'emotion': 'idle',
                'intensity': 0.2,
                'conversation_state': 'idle',
                'mood': 'idle',
                'reaction_beat': None,
                'dominant_viseme': 'aa',
            }

        rule = EMOTION_RULES[best_emotion]
        intensity = min(1.0, max(0.2, best_score / 2.0))
        dominant_viseme = self._dominant_viseme(lower)

        return {
            'emotion': best_emotion,
            'intensity': intensity,
            'conversation_state': rule['conversation_state'],
            'mood': rule['mood'],
            'reaction_beat': rule.get('reaction_beat'),
            'dominant_viseme': dominant_viseme,
        }

    def get_lipsync_visemes(self, text, duration_seconds):
        """
        Generate a timestamped viseme sequence for lip-sync animation.

        Returns:
            List of dicts with keys: time_offset_ms, viseme, weight, duration_ms
        """
        clean = re.sub(r'[^\w\s]', '', text)
        words = clean.split()

        vowel_groups = []
        i = 0
        chars = clean.replace(' ', '').lower()
        while i < len(chars):
            ch = chars[i]
            if ch in _VOWELS:
                two = chars[i:i + 2] if i + 1 < len(chars) else ch
                if two in VISEME_MAP and len(two) == 2:
                    vowel_groups.append(two)
                    i += 2
                    continue
                vowel_groups.append(ch)
            i += 1

        syllables = len(vowel_groups)
        if syllables < 1:
            syllables = max(1, int(len(words) * 1.3))
            vowel_groups = []
            for w in words:
                for c in w.lower():
                    if c in _VOWELS:
                        vowel_groups.append(c)
            if not vowel_groups:
                vowel_groups = ['a'] * syllables

        if duration_seconds <= 0:
            duration_seconds = 0.1

        ms_per_syllable = (duration_seconds * 1000.0) / max(1, syllables)
        result = []
        current_ms = 0.0

        for vg in vowel_groups:
            viseme_name = VISEME_MAP.get(vg, VISEME_MAP.get(vg[0] if vg else 'a', 'aa'))
            weight = 0.55 + random.random() * 0.35
            dur_ms = ms_per_syllable * 0.7

            result.append({
                'time_offset_ms': round(current_ms),
                'viseme': viseme_name,
                'weight': round(weight, 3),
                'duration_ms': round(dur_ms),
            })

            current_ms += ms_per_syllable * 0.7
            result.append({
                'time_offset_ms': round(current_ms),
                'viseme': 'aa',
                'weight': 0.0,
                'duration_ms': round(ms_per_syllable * 0.3),
            })
            current_ms += ms_per_syllable * 0.3

            if len(result) >= 80:
                break

        return result[:80]

    def stream_emotion_update(self, text_chunk, bridge_sender_fn):
        """
        Analyze text and call bridge_sender_fn with avatar state update.
        Only fires if the conversation_state changed (debounces identical states).
        """
        try:
            result = self.analyze_text(text_chunk)
            new_state = result['conversation_state']

            if new_state == self._last_conversation_state:
                return

            self._last_conversation_state = new_state

            payload = {
                'type': 'state_update',
                'conversationState': new_state,
                'mood': result['mood'],
                'reactionBeat': result['reaction_beat'],
                'emotionIntensity': result['intensity'],
            }

            bridge_sender_fn(payload)
            log_info('LilaEmotionEngine', f'Emitted state: {new_state} | mood: {result["mood"]} | intensity: {result["intensity"]:.2f}')

        except Exception as exc:
            log_warn('LilaEmotionEngine', f'stream_emotion_update error: {exc}')

    def _dominant_viseme(self, lower_text):
        """Return the most frequently occurring vowel viseme in the text."""
        counts = {}
        for ch in lower_text:
            if ch in _VOWELS:
                v = VISEME_MAP.get(ch, 'aa')
                counts[v] = counts.get(v, 0) + 1
        if not counts:
            return 'aa'
        return max(counts, key=lambda k: counts[k])


# --- Standalone test ---------------------------------------------------------
if __name__ == '__main__':
    engine = LilaEmotionEngine()
    tests = [
        'Oh wow that is amazing! Ho gaya completely!',
        'hmm let me think about this carefully and analyze the code',
        'Oh no, error hua! Something went wrong with the compilation',
        'Haha tu toh bahut funny hai, acha suno na',
        'Done! Successfully built and deployed your project!',
        'Hi Rishabh! Good morning! Kaise ho aaj?',
    ]
    for t in tests:
        r = engine.analyze_text(t)
        print(f'Text: {t[:50]}...')
        print(f'  -> {r["emotion"]} ({r["intensity"]:.2f}) | state={r["conversation_state"]} | mood={r["mood"]}')
        print()
