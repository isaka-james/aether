"""Play WAV audio on the host speakers. Stdlib only.

Tries PipeWire/PulseAudio first (paplay), then ffplay, then aplay — whichever is
present. Playback is synchronous so the backend knows when speech has finished.
"""
import shutil
import subprocess
import tempfile


def play_wav(wav_bytes: bytes) -> bool:
    if not wav_bytes:
        return False
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
        f.write(wav_bytes)
        f.flush()
        for argv in (
            ["paplay", f.name],
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", f.name],
            ["aplay", "-q", f.name],
        ):
            if shutil.which(argv[0]):
                try:
                    p = subprocess.run(argv, timeout=60)
                    if p.returncode == 0:
                        return True
                except subprocess.TimeoutExpired:
                    return False
    return False
