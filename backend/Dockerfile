# Aether backend — FastAPI + faster-whisper (STT) + kokoro-onnx (TTS)
# CPU-only. Models are cached in mounted volumes, not baked into the image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
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
RUN pip install -r requirements.txt

COPY app ./app
COPY web ./web
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
