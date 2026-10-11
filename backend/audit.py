"""Tamper-evident audit log (M4a).

An append-only JSON-lines file in which every entry carries the SHA-256 of the entry before it
(a hash chain). Changing, deleting, inserting or reordering any line breaks the chain, and
`verify()` finds the first broken entry by re-reading the file from disk. Dropping lines from the
END is caught by comparing the file's last hash with the head hash the running process holds
(and with the head hash shown on screen 2).

What this is and is not (say it exactly like this when asked):
  - It is TAMPER-EVIDENT: edits are detectable. It is not tamper-proof and not a legal record.
  - Someone with write access to the file AND the process could rewrite the whole chain, so the
    head hash is shown on screen 2 for the team to note, which anchors the chain outside the file.
  - It records what the system observed and said, with the source ids it cited. It does not judge
    whether anyone was right.

Writing never blocks alerts or video: one small synchronous append (flush + fsync) per entry. If the
write fails the log is marked "degraded" on screen 2 and the entry stays in memory; nothing crashes.

Entry fields (hashed, canonical JSON, sorted keys):
  seq, id ("log-<seq>"), run, wall_time (UTC), clock_s, scenario_id, kind, actor, summary, data,
  prev_hash, hash (hash = sha256 of the entry without "hash").
Kinds: session_start, run_start, control, alert, baseline_alarm, drug_given, nibp_reading,
phase_change, agent_message, agent_unavailable, deliberation_result, metrics_snapshot (the measured numbers at
the end of each deliberation, for the pitch), voice_qa, segment_end, report_generated.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("sentinel.audit")

GENESIS = "0" * 64
KINDS = (
    "session_start", "run_start", "control", "alert", "baseline_alarm", "drug_given", "nibp_reading",
    "phase_change", "agent_message", "agent_unavailable", "deliberation_result", "voice_qa",
    "segment_end", "report_generated", "metrics_snapshot",
)


def canonical(entry: dict[str, Any]) -> str:
    """The exact string that is hashed: sorted keys, no spaces, `hash` excluded."""
    body = {k: v for k, v in entry.items() if k != "hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entry_hash(entry: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(entry).encode("utf-8")).hexdigest()


@dataclass
class Verification:
    ok: bool
    entries: int
    head_hash: str
    first_bad_seq: Optional[int] = None
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "entries": self.entries, "head_hash": self.head_hash,
                "first_bad_seq": self.first_bad_seq, "reason": self.reason}


def verify_file(path: Path, expected_head: Optional[str] = None, expected_count: Optional[int] = None) -> Verification:
    """Re-read `path` from disk and check every link. `expected_head`/`expected_count` (what the
    running process believes) additionally catch a truncated tail."""
    prev = GENESIS
    n = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return Verification(False, 0, prev, None, f"cannot read the log file: {exc.strerror or exc}")
    for lineno, line in enumerate(lines, start=1):
        if not line.strip():
            return Verification(False, n, prev, n + 1, f"blank line at line {lineno}")
        try:
            e = json.loads(line)
        except ValueError:
            return Verification(False, n, prev, n + 1, f"line {lineno} is not valid JSON")
        seq = e.get("seq")
        if seq != n + 1:
            return Verification(False, n, prev, n + 1, f"entry numbering breaks at line {lineno} (found {seq}, expected {n + 1})")
        if e.get("prev_hash") != prev:
            return Verification(False, n, prev, seq, f"entry {seq} does not link to the entry before it")
        if entry_hash(e) != e.get("hash"):
            return Verification(False, n, prev, seq, f"entry {seq} was modified (its hash does not match its content)")
        prev = e["hash"]
        n += 1
    if expected_head is not None and (prev != expected_head or (expected_count is not None and n != expected_count)):
        return Verification(False, n, prev, n + 1,
                            f"the file ends at entry {n} but the running system holds entry {expected_count} (entries removed from the end)")
    return Verification(True, n, prev)


class AuditLog:
    """Single writer. `context` returns (clock_s, scenario_id) for the entry being written."""

    def __init__(self, directory: Path, context: Optional[Callable[[], tuple[Optional[float], Optional[str]]]] = None,
                 session_id: Optional[str] = None, on_change: Optional[Callable[["AuditLog"], None]] = None) -> None:
        self.session_id = session_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.dir = Path(directory)
        self.path = self.dir / f"audit-{self.session_id}.jsonl"
        self.context = context
        self.on_change = on_change
        self.run = 0
        self.status = "ok"  # ok | degraded
        self.error = ""
        self.last_verification: Optional[Verification] = None
        self._lock = threading.Lock()
        self._entries: list[dict[str, Any]] = []
        self._head = GENESIS
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.path, "a", encoding="utf-8")
        except OSError as exc:
            self._fh = None
            self._degrade(f"cannot open {self.path}: {exc.strerror or exc}")

    # ---- writing ---------------------------------------------------------------------------
    def record(self, kind: str, summary: str, data: Optional[dict[str, Any]] = None, actor: str = "system",
               clock_s: Optional[float] = None, scenario_id: Optional[str] = None) -> dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"unknown audit kind {kind}")
        ctx_clock, ctx_scn = self.context() if self.context else (None, None)
        with self._lock:
            seq = len(self._entries) + 1
            entry: dict[str, Any] = {
                "seq": seq, "id": f"log-{seq}", "run": self.run,
                "wall_time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "clock_s": round(clock_s if clock_s is not None else ctx_clock, 2)
                if (clock_s is not None or ctx_clock is not None) else None,
                "scenario_id": scenario_id if scenario_id is not None else ctx_scn,
                "kind": kind, "actor": actor, "summary": summary, "data": data or {},
                "prev_hash": self._head,
            }
            entry["hash"] = entry_hash(entry)
            self._write(entry)
            self._entries.append(entry)
            self._head = entry["hash"]
        self._changed()
        return entry

    def _write(self, entry: dict[str, Any]) -> None:
        if self._fh is None:
            return
        try:
            self._fh.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except OSError as exc:
            self._degrade(f"write failed: {exc.strerror or exc}")

    def _degrade(self, why: str) -> None:
        self.status, self.error = "degraded", why
        log.error("audit log degraded: %s", why)

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change(self)
            except Exception:
                log.exception("audit on_change failed")

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    # ---- reading ---------------------------------------------------------------------------
    @property
    def head_hash(self) -> str:
        return self._head

    @property
    def count(self) -> int:
        return len(self._entries)

    def entries(self, run: Optional[int] = None, since_seq: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self._entries if e["seq"] > since_seq and (run is None or e["run"] == run)]

    def verify(self) -> Verification:
        """Verify the file on disk against the head this process holds."""
        with self._lock:
            head, count = self._head, len(self._entries)
        if self._fh is not None:
            try:
                self._fh.flush()
            except OSError:
                pass
        v = verify_file(self.path, expected_head=head, expected_count=count)
        self.last_verification = v
        self._changed()
        log.info("audit verify: ok=%s entries=%d %s", v.ok, v.entries, v.reason)
        return v

    def info(self) -> dict[str, Any]:
        v = self.last_verification
        return {
            "enabled": True, "session_id": self.session_id, "path": str(self.path), "entries": self.count,
            "head_hash": self._head, "status": self.status, "error": self.error,
            "verified": None if v is None else v.ok,
            "verified_entries": None if v is None else v.entries,
            "verify_note": "" if v is None else (v.reason or "every link and hash re-computed from the file"),
            "last_kind": self._entries[-1]["kind"] if self._entries else None,
        }
