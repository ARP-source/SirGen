"""Offline speech service for Sentinel-OR: Piper text-to-speech (and, when installed,
faster-whisper speech-to-text). CPU only; nothing here touches the GPU.

    GET  /health      -> {"ok": true, "voice": "<name>", "engine": "piper", "stt": bool}
    POST /tts         -> body {"text": "..."} (<= 600 chars) -> audio/wav (16-bit PCM, voice's native rate)
    POST /transcribe  -> body = wav bytes -> {"text": "...", "latency_ms": n}   (503 if STT is not installed)

Environment:
    SENTINEL_TTS_VOICE   path to the voice .onnx (the .onnx.json must sit next to it)
    SENTINEL_TTS_PORT    listen port (default 8003); always binds 127.0.0.1
    SENTINEL_STT_MODEL   directory holding a CTranslate2 faster-whisper model (default ~/sentinel-speech/whisper/small.en)
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

MAX_TEXT = 600
DEFAULT_VOICE = Path.home() / "sentinel-speech" / "voices" / "en_US-lessac-medium.onnx"
DEFAULT_STT = Path.home() / "sentinel-speech" / "whisper" / "small.en"

log = logging.getLogger("sentinel.speech")


class Speech:
    """One loaded Piper voice (and optionally one Whisper model) shared by all request threads."""

    def __init__(self, voice_path: Path, stt_dir: Optional[Path]) -> None:
        from piper import PiperVoice  # imported here so a missing install fails loudly at start-up

        t0 = time.perf_counter()
        self.voice_name = voice_path.name.removesuffix(".onnx")
        self.voice = PiperVoice.load(str(voice_path), config_path=str(voice_path) + ".json", use_cuda=False)
        self.sample_rate = int(self.voice.config.sample_rate)
        # onnxruntime sessions are thread-safe for run(), but serialise anyway: a single CPU model
        # finishing one sentence at a time is faster overall than interleaving two.
        self._tts_lock = threading.Lock()
        log.info("voice %s loaded (%d Hz) in %.2fs", self.voice_name, self.sample_rate, time.perf_counter() - t0)

        self.stt = None
        self._stt_lock = threading.Lock()
        if stt_dir is not None and stt_dir.is_dir():
            try:
                from faster_whisper import WhisperModel

                t0 = time.perf_counter()
                self.stt = WhisperModel(str(stt_dir), device="cpu", compute_type="int8")
                log.info("stt model %s loaded in %.2fs", stt_dir, time.perf_counter() - t0)
            except Exception as exc:  # noqa: BLE001 - STT is optional; TTS must still come up
                log.warning("stt unavailable: %s", exc)
        else:
            log.info("stt disabled (no model dir at %s)", stt_dir)

    def tts_wav(self, text: str) -> bytes:
        buf = io.BytesIO()
        with self._tts_lock, wave.open(buf, "wb") as wf:
            self.voice.synthesize_wav(text, wf)
        return buf.getvalue()

    def transcribe(self, wav_bytes: bytes) -> str:
        if self.stt is None:
            raise RuntimeError("stt not installed")
        import numpy as np

        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            if wf.getsampwidth() != 2:
                raise ValueError("wav must be 16-bit PCM")
            rate, channels = wf.getframerate(), wf.getnchannels()
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        if channels > 1:
            pcm = pcm.reshape(-1, channels).mean(axis=1)
        audio = pcm.astype(np.float32) / 32768.0
        if rate != 16000:  # whisper wants 16 kHz; linear resample is fine for speech
            n = int(round(len(audio) * 16000 / rate))
            audio = np.interp(np.linspace(0, len(audio), n, endpoint=False), np.arange(len(audio)), audio).astype(np.float32)
        with self._stt_lock:
            segments, _info = self.stt.transcribe(audio, language="en", beam_size=1, vad_filter=False)
            return " ".join(s.text.strip() for s in segments).strip()


SPEECH: Optional[Speech] = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silence the default per-request stderr line; we log our own
        return

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n > 0 else b""

    def do_GET(self):  # noqa: N802
        if self.path.split("?")[0] == "/health":
            assert SPEECH is not None
            self._json(200, {"ok": True, "voice": SPEECH.voice_name, "engine": "piper",
                             "sample_rate": SPEECH.sample_rate, "stt": SPEECH.stt is not None})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/tts":
            self._tts()
        elif path == "/transcribe":
            self._transcribe()
        else:
            self._json(404, {"error": "not found"})

    def _tts(self) -> None:
        assert SPEECH is not None
        t0 = time.perf_counter()
        try:
            payload = json.loads(self._body() or b"{}")
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "body must be JSON"})
            return
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str) or not text.strip():
            self._json(400, {"error": "missing text"})
            return
        if len(text) > MAX_TEXT:
            self._json(400, {"error": f"text longer than {MAX_TEXT} chars"})
            return
        try:
            wav = SPEECH.tts_wav(text.strip())
        except Exception as exc:  # noqa: BLE001
            log.exception("tts failed")
            self._json(500, {"error": f"synthesis failed: {exc}"})
            return
        self._send(200, wav, "audio/wav")
        ms = (time.perf_counter() - t0) * 1000
        log.info("tts %d chars -> %d bytes in %.0f ms", len(text), len(wav), ms)

    def _transcribe(self) -> None:
        assert SPEECH is not None
        if SPEECH.stt is None:
            self._json(503, {"error": "stt unavailable"})
            return
        t0 = time.perf_counter()
        body = self._body()
        if len(body) < 44 or body[:4] != b"RIFF":
            self._json(400, {"error": "body must be a wav file"})
            return
        try:
            text = SPEECH.transcribe(body)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("transcribe failed")
            self._json(500, {"error": f"transcription failed: {exc}"})
            return
        ms = round((time.perf_counter() - t0) * 1000)
        self._json(200, {"text": text, "latency_ms": ms})
        log.info("stt %d bytes -> %r in %d ms", len(body), text, ms)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    voice_path = Path(os.environ.get("SENTINEL_TTS_VOICE") or DEFAULT_VOICE).expanduser()
    if not voice_path.is_file():
        log.error("voice not found: %s", voice_path)
        sys.exit(2)
    port = int(os.environ.get("SENTINEL_TTS_PORT") or 8003)
    stt_dir = Path(os.environ.get("SENTINEL_STT_MODEL") or DEFAULT_STT).expanduser()

    global SPEECH
    SPEECH = Speech(voice_path, stt_dir)
    SPEECH.tts_wav("Sentinel ready.")  # warm up onnxruntime so the first real request is not slow

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    log.info("speech service listening on 127.0.0.1:%d", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
