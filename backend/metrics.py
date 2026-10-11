"""Real measurements only (hard rule 6). Model metrics stay None until M0/M3."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger("sentinel.metrics")

PROC_NET_DEV = Path("/proc/net/dev")


def read_tx_bytes(path: Path = PROC_NET_DEV) -> Optional[dict[str, int]]:
    """tx_bytes per non-loopback interface, or None where /proc/net/dev is unavailable."""
    try:
        lines = path.read_text().splitlines()[2:]
    except OSError:
        return None
    out: dict[str, int] = {}
    for line in lines:
        if ":" not in line:
            continue
        name, data = line.split(":", 1)
        name = name.strip()
        fields = data.split()
        if name == "lo" or len(fields) < 9:
            continue
        out[name] = int(fields[8])
    return out


PROC_MEMINFO = Path("/proc/meminfo")


def read_mem_used_gb(path: Path = PROC_MEMINFO) -> Optional[float]:
    """System memory in use, in GB (10^9 bytes): MemTotal - MemAvailable. On the GX10 the CPU and GPU share one
    pool, so this covers the models, containers and everything else on the box. None where unavailable."""
    try:
        kb = {}
        for line in path.read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                kb[key] = int(rest.split()[0])
    except (OSError, ValueError, IndexError):
        return None
    if len(kb) != 2:
        return None
    return round((kb["MemTotal"] - kb["MemAvailable"]) * 1024 / 1e9, 1)


def bytes_out_interfaces() -> Optional[list[str]]:
    """$SENTINEL_BYTES_OUT_IFACES as a list (comma separated), or None when unset: count every
    non-loopback interface."""
    names = [n.strip() for n in os.environ.get("SENTINEL_BYTES_OUT_IFACES", "").split(",") if n.strip()]
    return names or None


class BytesOutCounter:
    """Bytes sent since construction (backend start) on the counted interfaces: every non-loopback
    interface by default, or only those named in $SENTINEL_BYTES_OUT_IFACES."""

    def __init__(self, path: Path = PROC_NET_DEV, interfaces: Optional[list[str]] = None) -> None:
        self.path = path
        self.interfaces = interfaces if interfaces is not None else bytes_out_interfaces()
        self._base = self._read()
        self._last: dict[str, int] = dict(self._base or {})
        self._total = 0

    def read(self) -> Optional[int]:
        if self._base is None:
            return None
        current = self._read()
        if current is None:
            return None
        for name, value in current.items():
            prev = self._last.get(name, 0)  # an interface that appears later counts from 0
            self._total += value - prev if value >= prev else value  # counter reset: count from 0
            self._last[name] = value
        return self._total

    def _read(self) -> Optional[dict[str, int]]:
        tx = read_tx_bytes(self.path)
        if tx is None or self.interfaces is None:
            return tx
        return {name: value for name, value in tx.items() if name in self.interfaces}

    def note(self) -> str:
        """Which interfaces the counter watches, for the metric note on screen 2."""
        seen = sorted(self._last)
        if self.interfaces is None:
            return ("sum of tx_bytes deltas on all non-loopback interfaces since backend start (/proc/net/dev): "
                    + (", ".join(seen) or "none found"))
        missing = [n for n in self.interfaces if n not in seen]
        return ("sum of tx_bytes deltas since backend start on SENTINEL_BYTES_OUT_IFACES only: " + ", ".join(self.interfaces)
                + (f" (not present: {', '.join(missing)})" if missing else "")
                + ". Other interfaces are not counted.")
