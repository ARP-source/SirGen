#!/usr/bin/env python3
"""Print the measured numbers for the pitch, read back out of the audit log (standard library only).

    python3 scripts/pitch_numbers.py                      # asks the running backend (localhost:7000)
    python3 scripts/pitch_numbers.py audit/audit-....jsonl   # or reads a log file

Every deliberation ends with a `metrics_snapshot` entry (backend/agents/orchestrator.py). This prints one row per
snapshot and the medians, mapped to the placeholders in pitch/pitch_script.md and pitch/judge_qa.md.
"None" means not measured: do not quote it. Also quote the alert-to-banner time from the `alert` entries.
"""
import json
import statistics
import sys
import urllib.request


def load() -> list[dict]:
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]
    with urllib.request.urlopen("http://localhost:7000/audit/entries?limit=5000") as r:
        return json.load(r)["entries"]


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None


def main() -> None:
    es = load()
    snaps = [e for e in es if e["kind"] == "metrics_snapshot"]
    alerts = [e for e in es if e["kind"] == "alert"]
    outcomes = {e["data"].get("event_id"): e["data"].get("outcome") for e in es if e["kind"] == "deliberation_result"}
    print(f"{len(es)} log entries, {len(alerts)} alerts, {len(snaps)} deliberation snapshots\n")
    cols = ["scenario_id", "deliberation_s", "llm_tps_aggregate", "llm_tps_per_agent", "event_to_alert_ms", "gb_used",
            "render_fps", "detector_fps", "detector_ms", "bytes_out"]
    print("| run | " + " | ".join(cols) + " |")
    print("|" + "---|" * (len(cols) + 1))
    for e in snaps:
        print(f"| {e['run']} | " + " | ".join(str(e["data"].get(c)) for c in cols) + " |")
    a_ms = [a["data"].get("event_to_alert_ms") for a in alerts]
    print("\nMedians (None = not measured, do not quote):")
    rows = [
        ("[event-to-alert ms]", med(a_ms)),
        ("[deliberation s]", med(e["data"].get("deliberation_s") for e in snaps)),
        ("[tokens/s, three agents at once]", med(e["data"].get("llm_tps_aggregate") for e in snaps)),
        ("[tokens/s, per agent call]", med(e["data"].get("llm_tps_per_agent") for e in snaps)),
        ("[GB used]", med(e["data"].get("gb_used") for e in snaps)),
        ("[detector ms]", med(e["data"].get("detector_ms") for e in snaps)),
        ("[detector FPS]", med(e["data"].get("detector_fps") for e in snaps)),
        ("[render FPS]", med(e["data"].get("render_fps") for e in snaps)),
        ("[bytes out] (max)", max((e["data"].get("bytes_out") or 0 for e in snaps), default=None)),
    ]
    for k, v in rows:
        print(f"  {k:36s} {v}")
    print("\nNotes: tokens/s includes prompt processing and queueing, so say 'about N tokens a second across three agents',"
          " not 'decode speed'. GB used is the whole box (models, containers, everything). A laptop comparison exists only"
          " if someone ran one: leave it out otherwise.")


if __name__ == "__main__":
    main()
