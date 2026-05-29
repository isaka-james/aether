"""Text-to-speech using Kokoro (ONNX, CPU) with the 'sky' voice.

Kokoro degrades on long inputs — it batches at arbitrary phoneme cuts and the joins
come out clipped or run-together. So for anything beyond a sentence or two we split
the text at natural sentence boundaries ourselves, synthesize each chunk, and
concatenate the audio with a short silence between chunks.

Robustness matters more than purity here: one chunk Kokoro can't phonemize (a stray
unicode glyph, a long URL, an unusual proper noun) must NOT silence the whole reply.
Each chunk is tried as-is; on failure it's retried with a stripped, ASCII-shaped
fallback; if both fail the chunk is dropped and we keep going. As long as ANY chunk
synthesises we return audio — partial speech beats dead air.

Produces 16-bit PCM WAV bytes at the model's native 24kHz sample rate, suitable both
for playback on the host (via the host agent) and for the browser.
"""
import io
import logging
import re
import unicodedata

import numpy as np
import soundfile as sf

from .config import get_settings

log = logging.getLogger("aether.tts")
_kokoro = None

# Keep each synthesis chunk well under Kokoro's ~510-phoneme limit. Characters are a
# rough proxy for phonemes, so we stay conservative to leave headroom.
MAX_CHUNK_CHARS = 260
# Short pause inserted between chunks so concatenated sentences don't run together.
GAP_SECONDS = 0.18

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*\s*")

# Strip emoji, pictographs, dingbats, regional flags, variation selectors, arrows,
# misc symbols — espeak-ng either errors out or produces silence on these.
_UNSPEAKABLE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U0000FE00-\U0000FE0F\U00002190-\U000021FF\U00002B00-\U00002BFF\U0000200D\U0000FE0F]"
)
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_URL = re.compile(r"https?://\S+|www\.[^\s]+")
_HASHTAG = re.compile(r"#(\w+)")
_HANDLE = re.compile(r"(?<![\w@])@(\w+)")
_MULTISPACE = re.compile(r"[ \t]{2,}")
_MULTIPERIOD = re.compile(r"\.{2,}")

# Smart punctuation → ASCII equivalents espeak handles cleanly. Anything decorative that
# isn't speech-load-bearing gets normalised toward a comma or a space.
_TRANSLITERATE = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "«": '"', "»": '"', "‹": "'", "›": "'",
    "–": ", ", "—": ", ", "−": "-",  # en/em dash, minus
    "…": ", ", " ": " ", " ": " ", " ": " ", "​": "",
    "•": ", ", "·": ", ",                # bullets
    "°": " degrees ",                          # °
    "±": " plus or minus ",
    "×": " by ", "÷": " divided by ",
    "€": " euros ", "£": " pounds ", "¥": " yen ", "¢": " cents ",
    "™": "", "®": "", "©": "",
}


def _transliterate(text: str) -> str:
    for src, dst in _TRANSLITERATE.items():
        if src in text:
            text = text.replace(src, dst)
    return text


def _speakable(text: str) -> str:
    """Reduce text to a clean, speakable form for Kokoro/espeak.

    Goal: never lose content that matters; always neutralise content that the
    phonemizer will choke on. Order matters — URLs/handles/hashtags are turned into
    spoken phrases BEFORE we lose their delimiters."""
    if not text:
        return ""
    # NFKC: maps full-width digits, ligatures, compat forms to their canonical ASCII-ish
    # cousins ("①" → "1", "ﬁ" → "fi") so espeak doesn't trip over them.
    text = unicodedata.normalize("NFKC", text)
    text = _transliterate(text)
    text = _URL.sub(" a link ", text)
    text = _HASHTAG.sub(r"hashtag \1", text)
    text = _HANDLE.sub(r"at \1", text)
    text = _UNSPEAKABLE.sub(" ", text)
    text = _CTRL.sub(" ", text)
    # & between words → " and "; standalone $ etc. handled by espeak in most locales.
    text = re.sub(r"\s*&\s*", " and ", text)
    # Collapse multi-dot ellipsis-likes (a comma reads better than a long pause).
    text = _MULTIPERIOD.sub(", ", text)
    text = _MULTISPACE.sub(" ", text)
    return text.strip()


def _ascii_fallback(text: str) -> str:
    """Last-resort sanitiser for a chunk Kokoro refused: strip to printable ASCII.
    Loses accents on names ('Beyoncé' → 'Beyonce'), which Kokoro voices fine."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = _MULTISPACE.sub(" ", text).strip()
    return text


def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro  # imported lazily; heavy
        s = get_settings()
        log.info("Loading Kokoro TTS (voice=%s)...", s.kokoro_voice)
        _kokoro = Kokoro(s.kokoro_onnx_path, s.kokoro_voices_path)
    return _kokoro


def _chunk(text: str) -> list[str]:
    """Split text into chunks at sentence boundaries, each <= MAX_CHUNK_CHARS.
    A single sentence longer than the budget is hard-wrapped on whitespace."""
    chunks: list[str] = []
    cur = ""
    for piece in _SENTENCE.findall(text) or [text]:
        sent = piece.strip()
        if not sent:
            continue
        if len(sent) > MAX_CHUNK_CHARS:          # oversize sentence -> wrap on words
            if cur:
                chunks.append(cur)
                cur = ""
            line = ""
            for w in sent.split():
                if len(line) + len(w) + 1 > MAX_CHUNK_CHARS:
                    chunks.append(line)
                    line = w
                else:
                    line = f"{line} {w}".strip()
            cur = line
            continue
        if len(cur) + len(sent) + 1 > MAX_CHUNK_CHARS:
            chunks.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def _render_chunk(kokoro, chunk: str, voice: str, speed: float, lang: str):
    """Try to synthesize one chunk; on failure retry once with an ASCII fallback.
    Returns (samples, sample_rate) or (None, None) if both attempts failed."""
    try:
        samples, sample_rate = kokoro.create(chunk, voice=voice, speed=speed, lang=lang)
        samples = np.asarray(samples, dtype=np.float32)
        if samples.size > 0:
            return samples, sample_rate
        log.warning("TTS chunk produced empty audio (len=%d); trying ASCII fallback.", len(chunk))
    except Exception as e:  # noqa: BLE001 - any phonemizer error -> degrade, don't crash
        log.warning("TTS chunk failed (len=%d): %s; trying ASCII fallback.", len(chunk), e)

    safe = _ascii_fallback(chunk)
    if not safe or safe == chunk:
        return None, None
    try:
        samples, sample_rate = kokoro.create(safe, voice=voice, speed=speed, lang=lang)
        samples = np.asarray(samples, dtype=np.float32)
        if samples.size > 0:
            return samples, sample_rate
    except Exception as e:  # noqa: BLE001
        log.warning("TTS ASCII fallback also failed (len=%d): %s", len(safe), e)
    return None, None


def synthesize(text: str) -> bytes:
    """Render `text` to WAV bytes, concatenating per-sentence audio. Returns b'' on
    empty input or when every chunk failed to synthesize."""
    raw_len = len(text or "")
    text = _speakable(text or "")
    if not text:
        log.warning("TTS: input collapsed to empty after sanitisation (raw len=%d).", raw_len)
        return b""
    s = get_settings()
    kokoro = _get_kokoro()

    chunks = _chunk(text)
    log.info("TTS: synthesising %d char(s) in %d chunk(s).", len(text), len(chunks))
    sample_rate = 24000
    parts: list[np.ndarray] = []
    failed = 0
    for chunk in chunks:
        samples, sr = _render_chunk(kokoro, chunk, s.kokoro_voice, s.kokoro_speed, s.kokoro_lang)
        if samples is None:
            failed += 1
            continue
        sample_rate = sr
        if parts:  # silence gap between chunks
            parts.append(np.zeros(int(sample_rate * GAP_SECONDS), dtype=np.float32))
        parts.append(samples)

    if not parts:
        log.warning("TTS: every chunk failed (text len=%d, chunks=%d).", len(text), len(chunks))
        return b""
    if failed:
        log.info("TTS: %d chunk(s) dropped; %d rendered.", failed, len(parts))
    buf = io.BytesIO()
    sf.write(buf, np.concatenate(parts), sample_rate, format="WAV", subtype="PCM_16")
    wav = buf.getvalue()
    log.info("TTS: produced %d byte(s) of audio.", len(wav))
    return wav
