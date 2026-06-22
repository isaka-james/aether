"""Speech-to-text with an online-first strategy and a local fallback.

Primary: Google's keyless web-speech endpoint — fast, and notably better at spelling
proper nouns (song/app names) than a small local model, with no API key to manage.
Fallback: local faster-whisper (CTranslate2, CPU/int8) for when the network or the
shared endpoint is unavailable.

Both providers consume the same 16 kHz mono PCM, so the browser's webm/opus is decoded
exactly once via faster-whisper's bundled decoder (PyAV) — no temp files, no system
ffmpeg. Provider is chosen by AETHER_STT_PROVIDER:
  "auto"   – online, then local on any failure (default)
  "google" – online only
  "local"  – local whisper only

Note: with "auto"/"google" the recorded audio is sent to Google's public speech API.
"""
import io
import json
import logging

import numpy as np
import requests
import soundfile as sf
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

from .config import get_settings

log = logging.getLogger("aether.stt")
_model: WhisperModel | None = None

# Google's public web-speech key, embedded in many open-source clients (e.g. the
# SpeechRecognition library). Keyless from our side, but shared and rate-limited — hence
# the local fallback always stays available.
_GOOGLE_KEY = "AIzaSyBOti4mM-6x9WDnZIjIeyEU21OpBXqWBgw"
_GOOGLE_URL = "https://www.google.com/speech-api/v2/recognize"  # https so audio isn't sent in clear text
SAMPLE_RATE = 16000


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        s = get_settings()
        log.info("Loading Whisper model '%s' (%s)...", s.whisper_model, s.whisper_compute_type)
        _model = WhisperModel(s.whisper_model, device="cpu", compute_type=s.whisper_compute_type)
    return _model


def _google_lang(whisper_lang: str) -> str:
    """faster-whisper uses 'en'; Google wants a BCP-47 tag like 'en-US'."""
    if not whisper_lang:
        return "en-US"
    return "en-US" if whisper_lang.lower() == "en" else whisper_lang


def _transcribe_google(pcm: np.ndarray, lang: str, timeout: float) -> str | None:
    """Best-effort online transcription. Returns text, or None on any failure so the
    caller can fall back to local."""
    try:
        flac = io.BytesIO()
        sf.write(flac, pcm, SAMPLE_RATE, format="FLAC")
        r = requests.post(
            _GOOGLE_URL,
            params={"output": "json", "lang": lang, "key": _GOOGLE_KEY},
            data=flac.getvalue(),
            headers={"Content-Type": f"audio/x-flac; rate={SAMPLE_RATE}"},
            timeout=timeout,
        )
        if r.status_code != 200:
            log.warning("Google STT returned HTTP %s", r.status_code)
            return None
        # Response is newline-delimited JSON; keep the last line that carries a result.
        best: str | None = None
        for line in r.text.splitlines():
            line = line.strip()
            if not line:
                continue
            for res in json.loads(line).get("result", []):
                alts = res.get("alternative") or []
                if alts and alts[0].get("transcript"):
                    best = alts[0]["transcript"]
        return best.strip() if best else None
    except Exception:  # noqa: BLE001 - any failure -> fall back to local
        log.warning("Google STT failed; falling back to local", exc_info=True)
        return None


def _transcribe_local(pcm: np.ndarray) -> str:
    s = get_settings()
    segments, _info = _get_model().transcribe(
        pcm,
        language=s.whisper_language,
        beam_size=5,                       # beam search: more accurate than greedy
        condition_on_previous_text=False,  # short commands: no hallucinated carry-over
        vad_filter=True,                   # drop silence
        initial_prompt=s.whisper_prompt or None,  # optional vocabulary hint
    )
    return "".join(seg.text for seg in segments).strip()


def transcribe(audio_bytes: bytes, suffix: str = ".webm") -> str:
    """Transcribe recorded audio to text. `suffix` is ignored — PyAV sniffs the format."""
    s = get_settings()
    pcm = decode_audio(io.BytesIO(audio_bytes), sampling_rate=SAMPLE_RATE)
    provider = (s.stt_provider or "auto").lower()

    if provider in ("auto", "google"):
        text = _transcribe_google(pcm, _google_lang(s.whisper_language), s.stt_online_timeout)
        if text:
            log.info("STT (google): %r", text)
            return text
        if provider == "google":
            return ""  # online-only: nothing to fall back to
        log.info("Google STT unavailable; using local whisper")

    text = _transcribe_local(pcm)
    log.info("STT (local): %r", text)
    return text
