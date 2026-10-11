#!/usr/bin/env python
"""Score the live step tracker (backend/phase.py) against Cholec80 frame-level phase labels.

Run with the training venv (needs ultralytics for the detector pass):

    ~/sentinel-train/.venv/bin/python scripts/score_phases.py \
        --video ~/sentinel-demo/video13_full.mp4 \
        --phases ~/assets/data/cholec80/phase_annotations/video13-phase.txt \
        --cache ~/sentinel-demo/video13_tools_per_second.json --out ~/sentinel-demo/score_video13.json

Pipeline, mirroring the app exactly:
  * detector: ultralytics YOLO tools.pt, predict(conf=0.2, imgsz=640, half=<cuda>) on every
    5th frame (5 samples per second at 25 fps); a detection is kept if conf >= per_class_conf[name]
    (tools_classes.json) else 0.3, as vision/server.py `_boxes` does;
  * presence per 1-s step: a tool is present if it is in >= 34% of that second's sampled frames
    (backend/scene.py PRESENT_FRACTION);
  * PhaseTracker(PhaseRules.load(phase_rules.json)).update(present, t) once per second;
  * ground truth per second: majority phase label over that second's 25 frames.

The cache stores every raw detection with conf >= 0.2 per sampled frame, so the per-class
thresholds and the presence rule can be re-applied without rerunning the detector
(`--from-cache`). Only the tracker (pure Python) runs in that mode, so it works in the app venv too.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from backend.phase import PhaseRules, PhaseTracker  # noqa: E402

PRESENT_FRACTION = 0.34  # backend/scene.py
DETECT_CONF = 0.2        # vision/server.py predict(conf=0.2)
DEFAULT_CONF = 0.3       # vision/server.py _boxes default


# ---- ground truth ------------------------------------------------------------------------------
def read_phases(path: Path) -> list[str]:
    labels: list[str] = []
    with path.open() as fh:
        header = fh.readline()
        if not header.lower().startswith("frame"):
            fh.seek(0)
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                labels.append(parts[1])
    return labels


def gt_per_second(labels: list[str], fps: int) -> list[str]:
    out = []
    for s in range(len(labels) // fps):
        out.append(Counter(labels[s * fps:(s + 1) * fps]).most_common(1)[0][0])
    return out


# ---- detector pass -----------------------------------------------------------------------------
def run_detector(video: Path, model_path: Path, every: int, log_every_s: int = 60) -> dict:
    import cv2
    import torch
    from ultralytics import YOLO

    try:  # ultralytics 8.4 logs "'half' is deprecated" on every predict call; keep half= to match vision/server.py
        from ultralytics.utils import LOGGER

        LOGGER.addFilter(lambda rec: "'half' is deprecated" not in rec.getMessage())
    except Exception:  # noqa: BLE001
        pass
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = YOLO(str(model_path))
    names = model.names
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video}")
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps_i = int(round(fps))
    print(f"detector on {dev}: {video} {n_frames} frames @ {fps:g} fps, every {every}th frame", flush=True)

    frames: list[dict] = []
    idx, t0, ms_sum, n_inf = 0, time.perf_counter(), 0.0, 0
    while True:
        if idx % every != 0:
            if not cap.grab():
                break
            idx += 1
            continue
        ok, frame = cap.read()
        if not ok:
            break
        t1 = time.perf_counter()
        r = model.predict(frame, imgsz=640, conf=DETECT_CONF, half=dev == "cuda", verbose=False)[0]
        ms_sum += (time.perf_counter() - t1) * 1000
        n_inf += 1
        dets = []
        if r.boxes is not None:
            for c, k in zip(r.boxes.conf.tolist(), r.boxes.cls.tolist()):
                dets.append({"cls": names[int(k)], "conf": round(float(c), 3)})
        frames.append({"frame": idx, "t": round(idx / fps, 3), "dets": dets})
        if idx % (fps_i * log_every_s) == 0:
            print(f"  frame {idx}/{n_frames} ({idx / fps:.0f} s) mean {ms_sum / n_inf:.1f} ms/frame, "
                  f"elapsed {time.perf_counter() - t0:.0f} s", flush=True)
        idx += 1
    cap.release()
    print(f"detector done: {n_inf} frames, mean {ms_sum / max(n_inf, 1):.1f} ms/frame, "
          f"{time.perf_counter() - t0:.0f} s wall", flush=True)
    return {"video": str(video), "model": str(model_path), "device": dev, "fps": fps, "n_frames": n_frames,
            "sample_every": every, "predict": {"conf": DETECT_CONF, "imgsz": 640, "half": dev == "cuda"},
            "mean_ms_per_frame": round(ms_sum / max(n_inf, 1), 2), "frames": frames}


def presence_per_second(cache: dict, per_class_conf: dict[str, float], fraction: float) -> list[dict]:
    """Apply the per-class thresholds and the 34% rule; one record per second of video."""
    fps = cache["fps"]
    n_seconds = int(cache["n_frames"] // round(fps))
    by_second: dict[int, list[dict]] = {}
    for f in cache["frames"]:
        by_second.setdefault(int(f["frame"] // round(fps)), []).append(f)
    out = []
    for s in range(n_seconds):
        batch = by_second.get(s, [])
        counts: Counter[str] = Counter()
        maxconf: dict[str, float] = {}
        for f in batch:
            kept = {d["cls"] for d in f["dets"] if d["conf"] >= per_class_conf.get(d["cls"], DEFAULT_CONF)}
            counts.update(kept)
            for d in f["dets"]:
                maxconf[d["cls"]] = max(maxconf.get(d["cls"], 0.0), d["conf"])
        n = len(batch)
        present = sorted(k for k, v in counts.items() if n and v / n >= fraction)
        out.append({"s": s, "n_frames": n, "present": present,
                    "frames_with_tool": dict(sorted(counts.items())),
                    "max_conf": {k: round(v, 3) for k, v in sorted(maxconf.items())}})
    return out


# ---- tracker + scoring -------------------------------------------------------------------------
def run_tracker(rules: PhaseRules, seconds: list[dict]) -> list[dict]:
    tracker = PhaseTracker(rules, start_t=0.0)
    out = []
    for rec in seconds:
        present = set(rec["present"]) if rec["n_frames"] else None
        u = tracker.update(present, float(rec["s"]))
        out.append({"s": rec["s"], "phase": u.name, "candidate": u.candidate, "stale": u.stale,
                    "out_of_phase": list(u.out_of_phase)})
    return out


def segments(seq: list[str]) -> list[dict]:
    segs: list[dict] = []
    for i, p in enumerate(seq):
        if not segs or segs[-1]["phase"] != p:
            segs.append({"phase": p, "start_s": i, "end_s": i + 1})
        else:
            segs[-1]["end_s"] = i + 1
    for sg in segs:
        sg["duration_s"] = sg["end_s"] - sg["start_s"]
    return segs


def score(gt: list[str], pred: list[str], phases: list[str], skip_s: int) -> dict:
    n = min(len(gt), len(pred))
    gt, pred = gt[:n], pred[:n]
    correct = sum(g == p for g, p in zip(gt, pred))
    tail = [(g, p) for i, (g, p) in enumerate(zip(gt, pred)) if i >= skip_s]
    conf = {g: {p: 0 for p in phases} for g in phases}
    for g, p in zip(gt, pred):
        conf.setdefault(g, {q: 0 for q in phases}).setdefault(p, 0)
        conf[g][p] += 1
    per_phase = {}
    for ph in phases:
        tp = conf.get(ph, {}).get(ph, 0)
        support = sum(conf.get(ph, {}).values())
        predicted = sum(conf.get(g, {}).get(ph, 0) for g in conf)
        per_phase[ph] = {"support_s": support, "predicted_s": predicted,
                         "precision": round(tp / predicted, 4) if predicted else None,
                         "recall": round(tp / support, 4) if support else None}
    gt_segs, pred_segs = segments(gt), segments(pred)
    transitions = []
    for sg in gt_segs:
        ph, t_gt = sg["phase"], sg["start_s"]
        containing = next((p for p in pred_segs if p["phase"] == ph and p["start_s"] <= t_gt < p["end_s"]), None)
        after = next((p for p in pred_segs if p["phase"] == ph and p["start_s"] >= t_gt), None)
        hit = containing or after
        transitions.append({"gt_phase": ph, "gt_start_s": t_gt, "gt_end_s": sg["end_s"],
                            "tracker_entered_s": hit["start_s"] if hit else None,
                            "lag_s": (hit["start_s"] - t_gt) if hit else "never",
                            "tracker_phase_at_gt_start": pred[t_gt] if t_gt < n else None})
    return {
        "seconds_scored": n,
        "accuracy": round(correct / n, 4) if n else None,
        "correct_s": correct,
        f"accuracy_after_{skip_s}s": round(sum(g == p for g, p in tail) / len(tail), 4) if tail else None,
        f"correct_after_{skip_s}s": sum(g == p for g, p in tail),
        f"seconds_after_{skip_s}s": len(tail),
        "per_phase": per_phase,
        "confusion": {"rows": "ground truth", "cols": "tracker", "phases": phases, "table": conf},
        "gt_segments": gt_segs,
        "tracker_segments": pred_segs,
        "transitions": transitions,
    }


def print_report(res: dict, phases: list[str], skip_s: int) -> None:
    sc = res["score"]
    print(f"\n=== phase tracker vs ground truth: {res['video']} ===")
    print(f"seconds scored: {sc['seconds_scored']}   accuracy: {sc['accuracy']} ({sc['correct_s']} s correct)")
    print(f"accuracy ignoring first {skip_s} s: {sc[f'accuracy_after_{skip_s}s']} "
          f"({sc[f'correct_after_{skip_s}s']}/{sc[f'seconds_after_{skip_s}s']} s)")
    print("\nper phase (support = GT seconds, predicted = tracker seconds):")
    print(f"  {'phase':26s} {'support':>7s} {'pred':>6s} {'prec':>7s} {'recall':>7s}")
    for ph, m in sc["per_phase"].items():
        pr = "-" if m["precision"] is None else f"{m['precision']:.3f}"
        rc = "-" if m["recall"] is None else f"{m['recall']:.3f}"
        print(f"  {ph:26s} {m['support_s']:7d} {m['predicted_s']:6d} {pr:>7s} {rc:>7s}")
    short = {p: p[:6] for p in phases}
    print("\nconfusion (rows GT, cols tracker, seconds):")
    print(f"  {'':26s}" + "".join(f"{short[p]:>8s}" for p in phases))
    for g in phases:
        print(f"  {g:26s}" + "".join(f"{sc['confusion']['table'][g][p]:8d}" for p in phases))
    print("\nGT transitions -> tracker lag:")
    for tr in sc["transitions"]:
        print(f"  {tr['gt_phase']:26s} GT {tr['gt_start_s']:5d}-{tr['gt_end_s']:5d} s  tracker entered: "
              f"{tr['tracker_entered_s']!s:>6s}  lag: {tr['lag_s']} s  (tracker at GT start: {tr['tracker_phase_at_gt_start']})")
    print("\ntracker phase sequence:")
    for sg in sc["tracker_segments"]:
        print(f"  {sg['start_s']:5d}-{sg['end_s']:5d} s ({sg['duration_s']:4d} s)  {sg['phase']}")
    print("\nGT phase sequence:")
    for sg in sc["gt_segments"]:
        print(f"  {sg['start_s']:5d}-{sg['end_s']:5d} s ({sg['duration_s']:4d} s)  {sg['phase']}")
    if res.get("demo_stretch"):
        d = res["demo_stretch"]
        print(f"\ndemo stretch {d['start_s']}-{d['end_s']} s (clip_s | video_s | GT | tracker | tools present (frames/5) | candidate):")
        for r in d["seconds"]:
            tools = ", ".join(f"{k}({r['frames_with_tool'].get(k, 0)}/{r['n_frames']})" for k in r["present"]) or "-"
            cand = f" -> {r['candidate']}" if r["candidate"] else ""
            print(f"  {r['clip_s']:3d} | {r['video_s']:4d} | {r['gt'][:12]:12s} | {r['tracker'][:12]:12s} | {tools}{cand}")
        print("  demo stretch accuracy: "
              f"{d['accuracy']} ({d['correct_s']}/{d['seconds_n']} s); tools seen: {d['tools_seen']}")


def main() -> None:
    home = Path.home()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", type=Path, default=home / "sentinel-demo/video13_full.mp4")
    ap.add_argument("--phases", type=Path, default=home / "assets/data/cholec80/phase_annotations/video13-phase.txt")
    ap.add_argument("--model", type=Path, default=home / "sentinel-train/models/tools.pt")
    ap.add_argument("--classes", type=Path, default=home / "sentinel-train/models/tools_classes.json")
    ap.add_argument("--rules", type=Path, default=REPO / "data/procedure/phase_rules.json")
    ap.add_argument("--cache", type=Path, default=home / "sentinel-demo/video13_tools_per_second.json")
    ap.add_argument("--out", type=Path, default=home / "sentinel-demo/score_video13.json")
    ap.add_argument("--from-cache", action="store_true", help="reuse --cache instead of running the detector")
    ap.add_argument("--every", type=int, default=5, help="sample every Nth frame (5 -> 5 fps at 25 fps)")
    ap.add_argument("--present-fraction", type=float, default=PRESENT_FRACTION)
    ap.add_argument("--skip-s", type=int, default=60, help="also report accuracy ignoring the first N seconds")
    ap.add_argument("--clip-info", type=Path, default=home / "sentinel-demo/clip_info.json",
                    help="clip_info.json from cut_clip.py; its start_s/end_s define the demo stretch")
    ap.add_argument("--clip-start", type=float, help="override demo stretch start (video seconds)")
    ap.add_argument("--clip-end", type=float, help="override demo stretch end (video seconds)")
    args = ap.parse_args()

    meta = json.loads(args.classes.read_text())
    per_class_conf = {k: float(v) for k, v in meta.get("per_class_conf", {}).items()}
    rules = PhaseRules.load(args.rules)
    phases = list(rules.display)

    if args.from_cache:
        cache = json.loads(args.cache.read_text())
        print(f"loaded cache {args.cache}: {len(cache['frames'])} sampled frames", flush=True)
    else:
        cache = run_detector(args.video, args.model, args.every)
        cache["per_class_conf_at_capture"] = per_class_conf
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        args.cache.write_text(json.dumps(cache) + "\n")
        print(f"wrote cache {args.cache}", flush=True)

    fps_i = int(round(cache["fps"]))
    labels = read_phases(args.phases)
    gt = gt_per_second(labels, fps_i)
    seconds = presence_per_second(cache, per_class_conf, args.present_fraction)
    n = min(len(gt), len(seconds))
    seconds, gt = seconds[:n], gt[:n]
    tracked = run_tracker(rules, seconds)
    pred = [t["phase"] for t in tracked]
    sc = score(gt, pred, phases, args.skip_s)

    per_second = [{"s": s, "gt": gt[s], "tracker": pred[s], "candidate": tracked[s]["candidate"],
                   "out_of_phase": tracked[s]["out_of_phase"], "present": seconds[s]["present"],
                   "frames_with_tool": seconds[s]["frames_with_tool"], "n_frames": seconds[s]["n_frames"],
                   "max_conf": seconds[s]["max_conf"]} for s in range(n)]

    demo = None
    start, end = args.clip_start, args.clip_end
    if (start is None or end is None) and args.clip_info.is_file():
        ci = json.loads(args.clip_info.read_text())
        start = ci["start_s"] if start is None else start
        end = ci["end_s"] if end is None else end
    if start is not None and end is not None:
        s0, s1 = int(start), min(int(end), n)
        rows = [{"clip_s": s - s0, "video_s": s, **per_second[s]} for s in range(s0, s1)]
        tools_seen = Counter(t for r in rows for t in r["present"])
        demo = {"start_s": s0, "end_s": s1, "seconds_n": len(rows),
                "correct_s": sum(r["gt"] == r["tracker"] for r in rows),
                "accuracy": round(sum(r["gt"] == r["tracker"] for r in rows) / len(rows), 4) if rows else None,
                "tools_seen": dict(tools_seen.most_common()), "seconds": rows}

    res = {
        "video": cache["video"], "model": cache.get("model"), "device": cache.get("device"),
        "mean_ms_per_frame": cache.get("mean_ms_per_frame"), "sample_every": cache["sample_every"],
        "fps": cache["fps"], "per_class_conf": per_class_conf, "default_conf": DEFAULT_CONF,
        "present_fraction": args.present_fraction, "rules": str(args.rules),
        "rules_summary": {"window_s": rules.window_s, "confirm_steps": rules.confirm_steps,
                          "min_dwell_s": rules.min_dwell_s, "start_phase": rules.start_phase},
        "score": sc, "demo_stretch": demo, "per_second": per_second,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1) + "\n")
    print_report(res, phases, args.skip_s)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
