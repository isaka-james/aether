# Aether backend — FastAPI + faster-whisper (STT) + kokoro-onnx (TTS)
# CPU-only. Models are cached in mounted volumes, not baked into the image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/models/hf \
    OMP_NUM_THREADS=4

# espeak-ng is required by kokoro-onnx for phonemization.
# wget is used by the entrypoint to fetch Kokoro model files on first run.
RUN apt-get update && apt-get install -y --no-install-recommends \
        espeak-ng \
        libgomp1 \
        wget \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# This host's link to PyPI's CDN (Fastly) is slow and unstable (~10-270 kB/s) and
# IPv6 is dead, so big wheels (numpy is ~16 MB) can stall past pip's 15s default
# read timeout. Be patient (long per-read timeout, many retries) and persist pip's
# cache across builds via a BuildKit cache mount, so a retry resumes from what was
# already downloaded instead of starting over. (Hence no PIP_NO_CACHE_DIR above;
# the cache lives in the build cache, not the final image layer, so size is unaffected.)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 300 --retries 10 -r requirements.txt

COPY app ./app
COPY web ./web
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
