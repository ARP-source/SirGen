"""Scenario loading and the 1 Hz vitals series (keyframes or a VitalDB window).

`t_s` is seconds from the video segment start (scenario files' time base).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import numpy as np

log = logging.getLogger("sentinel.scenario")

VITAL_FIELDS: tuple[str, ...] = ("hr", "sbp", "dbp", "map", "spo2")
SourceKind = Literal["simulated", "approximate", "real_time_shifted"]


def interpolate(keyframes: list[dict[str, Any]], t: float) -> dict[str, Optional[float]]:
    """Linear interpolation of every vital field at time `t` (clamped at the ends)."""
    times = np.array([float(k["t_s"]) for k in keyframes])
    if len(times) > 1 and not np.all(np.diff(times) > 0):
        raise ValueError("keyframes must be strictly increasing in t_s")
    out: dict[str, Optional[float]] = {}
    for name in VITAL_FIELDS:
        pts = [(float(k["t_s"]), k.get(name)) for k in keyframes if k.get(name) is not None]
        if not pts:
            out[name] = None
            continue
        xs, ys = zip(*pts)
        out[name] = float(np.interp(t, np.array(xs), np.array(ys, dtype=float)))
    return out


@dataclass(frozen=True)
class VitalsSeries:
    """Vitals sampled once per second for t_s = 0..duration."""

    values: dict[str, list[Optional[float]]]
    source: str
    source_kind: SourceKind

    def __len__(self) -> int:
        return len(next(iter(self.values.values())))

    def at(self, t_s: int) -> dict[str, Optional[float]]:
        """Past the end of the recorded series nothing was measured: every field is None (hard rule 6)."""
        if int(t_s) > len(self) - 1:
            return {name: None for name in VITAL_FIELDS}
        i = max(0, int(t_s))
        return {name: self.values[name][i] for name in VITAL_FIELDS}

    def window(self, start: int, end: int) -> list[dict[str, Optional[float]]]:
        return [{"t_s": t, **self.at(t)} for t in range(max(0, start), min(end, len(self) - 1) + 1)]


def series_from_keyframes(keyframes: list[dict[str, Any]], duration_s: int,
                          source: str, kind: SourceKind) -> VitalsSeries:
    values: dict[str, list[Optional[float]]] = {name: [] for name in VITAL_FIELDS}
    for t in range(duration_s + 1):
        sample = interpolate(keyframes, t)
        for name in VITAL_FIELDS:
            v = sample[name]
            values[name].append(None if v is None else round(v, 1))
    return VitalsSeries(values=values, source=source, source_kind=kind)


def series_from_vitaldb(csv_path: Path, vitals_source: dict[str, Any], duration_s: int) -> VitalsSeries:
    """Real VitalDB samples, mapped onto the scenario clock (time-shifted)."""
    import pandas as pd

    window = vitals_source.get("real_window_s") or [0, duration_s]
    excerpt = vitals_source.get("excerpt_real_start_s")
    chosen = isinstance(excerpt, (int, float))
    start = float(excerpt) if chosen else float(window[0])
    df = pd.read_csv(csv_path)
    if "time_s" not in df.columns:
        raise ValueError(f"{csv_path.name}: no time_s column")
    df = df[(df["time_s"] >= start - 5) & (df["time_s"] <= start + duration_s + 5)]
    grid = start + np.arange(duration_s + 1, dtype=float)
    values: dict[str, list[Optional[float]]] = {}
    for name in VITAL_FIELDS:
        col = vitals_source.get("columns", {}).get(name)
        sub = df[["time_s", col]].dropna() if col and col in df.columns else df.iloc[0:0]
        if sub.empty:
            values[name] = [None] * len(grid)
            continue
        ys = np.interp(grid, sub["time_s"].to_numpy(float), sub[col].to_numpy(float))
        values[name] = [round(float(v), 1) for v in ys]
    how = "excerpt" if chosen else "window start (excerpt not chosen yet)"
    label = (f"VitalDB case 59, real samples, time-shifted: real time_s {start:.0f}-{start + duration_s:.0f} "
             f"({how}) shown as t_s 0-{duration_s}")
    return VitalsSeries(values=values, source=label, source_kind="real_time_shifted")


@dataclass
class Scenario:
    id: str
    title: str
    on_screen_label: str
    duration_s: int
    event_t_s: Optional[float]
    event_note: str
    event_type: Literal["map_drop", "art_spike"]
    alert_level: Literal["banner", "quiet"]
    series: VitalsSeries
    drug_log: list[dict[str, Any]]
    nibp: list[dict[str, Any]]
    baseline: dict[str, Any]
    video: dict[str, Any]
    raw: dict[str, Any] = field(repr=False)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "on_screen_label": self.on_screen_label,
            "duration_s": self.duration_s, "event_t_s": self.event_t_s, "event_type": self.event_type,
            "alert_level": self.alert_level, "vitals_source": self.series.source,
            "vitals_source_kind": self.series.source_kind,
        }


def _event_type(raw: dict[str, Any]) -> Literal["map_drop", "art_spike"]:
    explicit = raw.get("event_type")
    if explicit in ("map_drop", "art_spike"):
        return explicit
    return "art_spike" if "sbp jump" in raw.get("event_note", "").lower() else "map_drop"


def _alert_level(raw: dict[str, Any]) -> Literal["banner", "quiet"]:
    """The scenario's explicit `alert_level`; a banner unless it says `quiet`."""
    return "quiet" if raw.get("alert_level") == "quiet" else "banner"


def build_series(raw: dict[str, Any], data_dir: Path) -> VitalsSeries:
    vitals = raw["vitals"]
    duration = int(raw["duration_s"])
    src = vitals.get("vitals_source")
    if isinstance(src, dict) and src.get("type") == "vitaldb_window":
        csv_path = data_dir / src.get("file", "vitaldb_case59_tracks.csv")
        if csv_path.exists():
            try:
                return series_from_vitaldb(csv_path, src, duration)
            except Exception:  # fall back, but say so in the log
                log.exception("could not read %s; using fallback keyframes", csv_path)
        label = vitals.get("fallback_keyframes_label", "approximate fallback keyframes")
        return series_from_keyframes(vitals["keyframes"], duration,
                                     f"Fallback keyframes ({label})", "approximate")
    return series_from_keyframes(vitals["keyframes"], duration,
                                 "Simulated vitals (scenario keyframes, linear interpolation at 1 Hz)",
                                 "simulated")


def load_scenario(path: Path, data_dir: Path) -> Scenario:
    raw = json.loads(path.read_text())
    nibp = [n for n in raw["vitals"].get("nibp", [])
            if n.get("t_s") is not None and any(n.get(k) is not None for k in ("sbp", "dbp", "map"))]
    return Scenario(
        id=raw["id"], title=raw["title"], on_screen_label=raw.get("on_screen_label", ""),
        duration_s=int(raw["duration_s"]), event_t_s=raw.get("event_t_s"),
        event_note=raw.get("event_note", ""), event_type=_event_type(raw), alert_level=_alert_level(raw),
        series=build_series(raw, data_dir), drug_log=list(raw.get("drug_log", [])), nibp=nibp,
        baseline=dict(raw.get("baseline_rule", {})), video=dict(raw.get("video", {})), raw=raw,
    )


def load_all(data_dir: Path) -> dict[str, Scenario]:
    scenarios = {}
    for path in sorted((data_dir / "scenarios").glob("*.json")):
        sc = load_scenario(path, data_dir)
        scenarios[sc.id] = sc
    return scenarios
