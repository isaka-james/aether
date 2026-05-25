#!/usr/bin/env bash
# Downloads the Kokoro TTS model files into the /models volume on first run.
# faster-whisper downloads its own models on first use (cached in /models/hf).
set -euo pipefail

MODEL_DIR=/models/kokoro
ONNX="$MODEL_DIR/kokoro-v1.0.onnx"
VOICES="$MODEL_DIR/voices-v1.0.bin"
BASE_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

mkdir -p "$MODEL_DIR" /models/hf

if [ ! -f "$ONNX" ]; then
  echo "[entrypoint] Downloading Kokoro model (~310MB)..."
  wget -q --show-progress -O "$ONNX" "$BASE_URL/kokoro-v1.0.onnx"
fi
if [ ! -f "$VOICES" ]; then
  echo "[entrypoint] Downloading Kokoro voices (~27MB)..."
  wget -q --show-progress -O "$VOICES" "$BASE_URL/voices-v1.0.bin"
fi

echo "[entrypoint] Models ready. Starting backend."
exec "$@"
