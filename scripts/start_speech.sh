#!/usr/bin/env bash
# Start the offline speech service (Piper TTS, plus faster-whisper STT when installed) on 127.0.0.1:8003.
# CPU only. Uses its own venv (~/sentinel-speech/.venv); the app talks to it over HTTP and degrades without it.
#   SENTINEL_TTS_VOICE   path to the voice .onnx (default ~/sentinel-speech/voices/en_US-lessac-medium.onnx)
#   SENTINEL_TTS_PORT    listen port (default 8003)
#   SENTINEL_STT_MODEL   faster-whisper model dir (default ~/sentinel-speech/whisper/small.en)
#   SPEECH_VENV          venv with piper-tts (default ~/sentinel-speech/.venv)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${SPEECH_VENV:-$HOME/sentinel-speech/.venv}"
export SENTINEL_TTS_VOICE="${SENTINEL_TTS_VOICE:-$HOME/sentinel-speech/voices/en_US-lessac-medium.onnx}"
export SENTINEL_TTS_PORT="${SENTINEL_TTS_PORT:-8003}"
exec "$VENV/bin/python" "$ROOT/speech/tts_server.py"
