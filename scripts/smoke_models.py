#!/usr/bin/env python3
"""Smoke test for the model services (M0): hits each port and prints the measured latency.

  scripts/smoke_models.py                  every service, including one real request per model
  scripts/smoke_models.py --no-infer       ports and readiness endpoints only (no GPU work)
  scripts/smoke_models.py --only lightning,omni
  scripts/smoke_models.py --wait 3000      poll until everything is up (first-boot kernel compile)

Per service: TCP connect time, readiness GET time, then one small real request.
  lightning :8000  chat completion with response_format json_schema and thinking off (the agents' shape)
  omni      :8001  text-only chat completion (the video path is exercised by backend/omni.py)
  asr       :8002  POST /transcribe, 1 s of silence as audio/wav in the body, text out
  tts       :8003  POST /tts {"text": ...}, wav out
  backend   :7000  GET /health          frontend :5173  GET /

States: OK, STARTING (port open, not answering yet), DOWN (nothing listening), FAIL (answered wrongly).
Exit code 0 only if every checked service is OK. Standard library only; all calls go to HOST.
"""
from __future__ import annotations

import argparse
import io
import json
import socket
import sys
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass, field
from typing import Callable, Optional

HOST = "127.0.0.1"
# Loopback only: never route through a proxy from the environment.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class NotReady(Exception):
    """The port is open but the service did not answer (still loading or compiling)."""


@dataclass
class Row:
    name: str
    port: int
    state: str = "DOWN"
    tcp_ms: Optional[float] = None
    ready_ms: Optional[float] = None
    infer_ms: Optional[float] = None
    notes: list[str] = field(default_factory=list)


def http(method: str, port: int, path: str, body: Optional[bytes] = None,
         content_type: str = "application/json", timeout: float = 5.0) -> tuple[int, bytes, float]:
    """One request. Returns (status, body, milliseconds). Raises NotReady when nothing answers."""
    req = urllib.request.Request(f"http://{HOST}:{port}{path}", data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", content_type)
    t0 = time.perf_counter()
    try:
        with OPENER.open(req, timeout=timeout) as resp:
            data = resp.read()
            return resp.status, data, (time.perf_counter() - t0) * 1000
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), (time.perf_counter() - t0) * 1000
    except (urllib.error.URLError, OSError) as exc:  # reset, empty reply, timeout
        raise NotReady(str(getattr(exc, "reason", exc))) from exc


def tcp_ms(port: int) -> Optional[float]:
    t0 = time.perf_counter()
    try:
        with socket.create_connection((HOST, port), timeout=2.0):
            return (time.perf_counter() - t0) * 1000
    except OSError:
        return None


def silence_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


# ---- readiness (no inference) ---------------------------------------------------------------

def ready_vllm(row: Row) -> None:
    status, body, ms = http("GET", row.port, "/v1/models")
    row.ready_ms = ms
    if status != 200:
        raise NotReady(f"GET /v1/models -> {status}")
    ids = [m["id"] for m in json.loads(body).get("data", [])]
    if not ids:
        raise RuntimeError("GET /v1/models lists no model")
    row.notes.append(f"model={ids[0]}")


def ready_get(path: str, expect: Callable[[bytes], bool] = lambda b: True) -> Callable[[Row], None]:
    def check(row: Row) -> None:
        status, body, ms = http("GET", row.port, path)
        row.ready_ms = ms
        if status != 200 or not expect(body):
            raise RuntimeError(f"GET {path} -> {status}")
    return check


# ---- one real request per model -------------------------------------------------------------

def chat(row: Row, payload: dict, timeout: float) -> dict:
    model = row.notes[0].split("=", 1)[1]
    payload = {"model": model, "temperature": 0.2,
               "chat_template_kwargs": {"enable_thinking": False}, **payload}
    status, body, ms = http("POST", row.port, "/v1/chat/completions", json.dumps(payload).encode(), timeout=timeout)
    row.infer_ms = ms
    if status != 200:
        raise RuntimeError(f"chat completion -> {status}: {body[:160].decode(errors='replace')}")
    out = json.loads(body)
    tokens = (out.get("usage") or {}).get("completion_tokens")
    if tokens and ms > 0:
        row.notes.append(f"{tokens} tok, {tokens / (ms / 1000):.0f} tok/s incl. prompt")
    return out


def infer_lightning(row: Row, timeout: float) -> None:
    schema = {"type": "object", "additionalProperties": False, "required": ["status", "confidence"],
              "properties": {"status": {"type": "string", "enum": ["ready"]},
                             "confidence": {"type": "string", "enum": ["low", "medium", "high"]}}}
    out = chat(row, {
        "max_tokens": 40,
        "messages": [{"role": "user", "content": "Smoke test. Reply with status ready and any confidence."}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "smoke", "schema": schema, "strict": True}},
    }, timeout)
    choice = out["choices"][0]
    reply = json.loads(choice["message"]["content"])  # must be the schema, not prose or thinking
    if reply.get("status") != "ready" or reply.get("confidence") not in ("low", "medium", "high"):
        raise RuntimeError(f"structured output does not match the schema: {reply}")
    row.notes.append(f"json_schema ok, finish={choice.get('finish_reason')}")


def infer_omni(row: Row, timeout: float) -> None:
    out = chat(row, {"max_tokens": 8,
                     "messages": [{"role": "user", "content": "Smoke test. Reply with the single word: ready"}]}, timeout)
    text = (out["choices"][0]["message"].get("content") or "").strip()
    if not text:
        raise RuntimeError("empty reply")
    row.notes.append(f"text-only reply {text[:20]!r}")


def infer_asr(row: Row, timeout: float) -> None:
    status, body, ms = http("POST", row.port, "/transcribe", silence_wav(), "audio/wav", timeout)
    row.infer_ms = ms
    if status != 200:
        raise RuntimeError(f"POST /transcribe -> {status}: {body[:120].decode(errors='replace')}")
    row.notes.append(f"1 s of silence -> {body[:40].decode(errors='replace')!r}")


def infer_tts(row: Row, timeout: float) -> None:
    status, body, ms = http("POST", row.port, "/tts", json.dumps({"text": "Smoke test."}).encode(), timeout=timeout)
    row.infer_ms = ms
    if status != 200 or body[:4] != b"RIFF":
        raise RuntimeError(f"POST /tts -> {status}, {len(body)} bytes, not a wav")
    with wave.open(io.BytesIO(body)) as w:
        row.notes.append(f"wav {w.getnframes() / w.getframerate():.2f} s at {w.getframerate()} Hz")


# name, port, readiness check, inference check (None = no model behind it)
SERVICES: list[tuple[str, int, Callable[[Row], None], Optional[Callable[[Row, float], None]]]] = [
    ("lightning", 8000, ready_vllm, infer_lightning),
    ("omni", 8001, ready_vllm, infer_omni),
    ("asr", 8002, ready_get("/health"), infer_asr),
    ("tts", 8003, ready_get("/health"), infer_tts),
    ("backend", 7000, ready_get("/health"), None),
    ("frontend", 5173, ready_get("/", lambda b: b"<html" in b.lower()), None),
]


def check(name: str, port: int, ready: Callable[[Row], None],
          infer: Optional[Callable[[Row, float], None]], do_infer: bool, timeout: float) -> Row:
    row = Row(name, port)
    row.tcp_ms = tcp_ms(port)
    if row.tcp_ms is None:
        row.notes.append("nothing listening")
        return row
    try:
        ready(row)
        if infer is not None and do_infer:
            infer(row, timeout)
        row.state = "OK"
    except NotReady as exc:
        row.state = "STARTING"
        row.notes.append(f"port open, no answer yet ({exc})")
    except Exception as exc:  # wrong status, bad JSON, schema mismatch
        row.state = "FAIL"
        row.notes.append(f"{type(exc).__name__}: {exc}")
    return row


def fmt(ms: Optional[float]) -> str:
    return "-" if ms is None else f"{ms:.2f}"


def report(rows: list[Row], do_infer: bool) -> None:
    print(f"{'service':<10}{'port':>5}  {'state':<9}{'tcp ms':>8}{'ready ms':>10}{'infer ms':>10}  detail")
    for r in rows:
        print(f"{r.name:<10}{r.port:>5}  {r.state:<9}{fmt(r.tcp_ms):>8}{fmt(r.ready_ms):>10}{fmt(r.infer_ms):>10}  "
              + "; ".join(r.notes))
    if not do_infer:
        print("--no-infer: no model request was sent, so there is no inference latency above.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke test for the Sentinel-OR services.")
    ap.add_argument("--only", help="comma-separated subset, e.g. lightning,omni")
    ap.add_argument("--no-infer", action="store_true", help="ports and readiness only; no model request")
    ap.add_argument("--wait", type=float, default=0, metavar="S", help="poll for up to S seconds until all are OK")
    ap.add_argument("--timeout", type=float, default=60, metavar="S", help="per inference request (default 60)")
    args = ap.parse_args()

    wanted = set(args.only.split(",")) if args.only else None
    known = {s[0] for s in SERVICES}
    if wanted and wanted - known:
        ap.error(f"unknown service(s): {', '.join(sorted(wanted - known))}")
    selected = [s for s in SERVICES if wanted is None or s[0] in wanted]

    deadline = time.monotonic() + args.wait
    while True:
        rows = [check(*s, do_infer=not args.no_infer, timeout=args.timeout) for s in selected]
        waiting = [r.name for r in rows if r.state in ("DOWN", "STARTING")]
        if not waiting or time.monotonic() >= deadline:
            break
        print(f"waiting for {', '.join(waiting)} ({deadline - time.monotonic():.0f} s left)", file=sys.stderr)
        time.sleep(10)

    report(rows, not args.no_infer)
    bad = [r.name for r in rows if r.state != "OK"]
    print("SMOKE OK" if not bad else f"SMOKE FAILED: {', '.join(bad)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
