#!/usr/bin/env python3
"""Fetch one VitalDB case for the Sentinel-OR demo.

Run on a laptop with internet (the GX10 demo itself stays offline):
    pip install vitaldb pandas pyarrow
    python fetch_vitaldb_case.py 59            # caseid 59, 1-s interval
    python fetch_vitaldb_case.py 59 1 --outdir .   # caseid, interval (s)

Writes vitaldb_case<ID>_tracks.csv (+ .parquet if pyarrow is present),
vitaldb_case<ID>_clinical.json and vitaldb_case<ID>_labs.csv, then prints:
the laparoscopic cholecystectomy cases with an arterial line, minutes with
MAP < 65 mmHg, minutes with arterial SBP > 200 (likely flush/artifact), and
propofol/remifentanil TCI target changes.

Attribution (CC BY 4.0): Data from the VitalDB open dataset, Seoul National
University Hospital. Lee HC, Park Y, Yoon SB, Yang SM, Park D, Jung CW.
"VitalDB, a high-fidelity multi-parameter vital signs database in surgical
patients." Sci Data 2022. doi:10.1038/s41597-022-01411-5. https://vitaldb.net
Licence: https://creativecommons.org/licenses/by/4.0/
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import vitaldb
except ImportError:
    sys.exit("Missing package: pip install vitaldb pandas pyarrow")

API = "https://api.vitaldb.net"
ATTRIBUTION = (
    "Data: VitalDB open dataset (Seoul National University Hospital), CC BY 4.0 "
    "(https://creativecommons.org/licenses/by/4.0/). Lee HC, Park Y, Yoon SB, Yang SM, "
    "Park D, Jung CW. VitalDB, a high-fidelity multi-parameter vital signs database in "
    "surgical patients. Sci Data 2022. doi:10.1038/s41597-022-01411-5"
)

# (output column, exact VitalDB track name). The first 10 are the demo's core columns.
TRACKS = [
    ("ART_SBP", "Solar8000/ART_SBP"),
    ("ART_DBP", "Solar8000/ART_DBP"),
    ("ART_MBP", "Solar8000/ART_MBP"),
    ("HR", "Solar8000/HR"),
    ("SPO2", "Solar8000/PLETH_SPO2"),
    ("BIS", "BIS/BIS"),
    ("PPF20_CE", "Orchestra/PPF20_CE"),
    ("PPF20_RATE", "Orchestra/PPF20_RATE"),
    ("RFTN20_CE", "Orchestra/RFTN20_CE"),
    ("RFTN20_RATE", "Orchestra/RFTN20_RATE"),
    # Extras (appended after the core columns): cuff BP for the artifact check,
    # TCI target concentrations for spotting drug changes.
    ("NIBP_SBP", "Solar8000/NIBP_SBP"),
    ("NIBP_DBP", "Solar8000/NIBP_DBP"),
    ("NIBP_MBP", "Solar8000/NIBP_MBP"),
    ("PPF20_CT", "Orchestra/PPF20_CT"),
    ("RFTN20_CT", "Orchestra/RFTN20_CT"),
]


def load_tracks(caseid: int, names: list[str], interval: float) -> np.ndarray:
    """Return an (n, len(names)) float array; missing tracks become NaN columns."""
    errors = []
    try:
        arr = vitaldb.load_case(caseid, names, interval)
        if arr is not None and np.size(arr):
            return np.asarray(arr, dtype=float).reshape(len(arr), -1)
    except Exception as e:  # noqa: BLE001 - report and fall back
        errors.append(f"load_case: {e}")
    try:
        vf = vitaldb.VitalFile(caseid, names)
        arr = vf.to_numpy(names, interval)
        if arr is not None and np.size(arr):
            return np.asarray(arr, dtype=float).reshape(len(arr), -1)
    except Exception as e:  # noqa: BLE001
        errors.append(f"VitalFile: {e}")
    print("Bulk load failed (" + "; ".join(errors) + "); trying track by track.", file=sys.stderr)
    cols = []
    for n in names:
        try:
            cols.append(np.asarray(vitaldb.load_case(caseid, [n], interval), dtype=float).ravel())
        except Exception as e:  # noqa: BLE001
            print(f"  {n}: {e}", file=sys.stderr)
            cols.append(np.array([]))
    length = max((len(c) for c in cols), default=0)
    if length == 0:
        raise RuntimeError("No track data could be loaded for case %d" % caseid)
    out = np.full((length, len(names)), np.nan)
    for i, c in enumerate(cols):
        out[: len(c), i] = c
    return out


def fetch_tracks(caseid: int, interval: float, outdir: Path) -> pd.DataFrame:
    names = [t for _, t in TRACKS]
    arr = load_tracks(caseid, names, interval)
    if arr.shape[1] != len(names):  # defensive: never mislabel columns
        raise RuntimeError(f"Expected {len(names)} columns, got {arr.shape[1]}")
    df = pd.DataFrame(arr, columns=[c for c, _ in TRACKS])
    df.insert(0, "time_s", np.round(np.arange(len(df)) * interval, 3))
    missing = [f"{c} ({t})" for c, t in TRACKS if df[c].isna().all()]
    if missing:
        print("Missing or empty tracks (kept as empty columns): " + ", ".join(missing))
    path = outdir / f"vitaldb_case{caseid}_tracks.csv"
    df.to_csv(path, index=False, float_format="%.3f")
    print(f"Wrote {path} ({len(df)} rows, {interval} s interval, time_s from casestart)")
    try:
        df.to_parquet(path.with_suffix(".parquet"), index=False)
        print(f"Wrote {path.with_suffix('.parquet')}")
    except Exception as e:  # noqa: BLE001 - parquet is optional
        print(f"(parquet skipped: {e})")
    return df


def fetch_clinical(caseid: int, outdir: Path) -> tuple[pd.DataFrame, dict]:
    cases = pd.read_csv(f"{API}/cases")
    row = cases[cases["caseid"] == caseid]
    record = json.loads(row.iloc[0].to_json()) if len(row) else None
    if record is None:
        print(f"WARNING: caseid {caseid} not found in /cases")
    out = {
        "attribution": ATTRIBUTION,
        "source": f"{API}/cases",
        "caseid": caseid,
        "fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "note": "Real de-identified data. Event times (casestart, anestart, opstart, ...) are seconds from casestart.",
        "case": record,
    }
    path = outdir / f"vitaldb_case{caseid}_clinical.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"Wrote {path}")
    return cases, record or {}


def fetch_labs(caseid: int, outdir: Path) -> None:
    try:
        labs = pd.read_csv(f"{API}/labs")
        labs = labs[labs["caseid"] == caseid]
        path = outdir / f"vitaldb_case{caseid}_labs.csv"
        labs.to_csv(path, index=False)
        print(f"Wrote {path} ({len(labs)} rows; dt = seconds from casestart)")
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: labs not fetched: {e}")


def print_lap_chole_alines(cases: pd.DataFrame) -> None:
    need = {"opname", "approach", "aline1"}
    if not need.issubset(cases.columns):
        print(f"Cannot run lap-chole filter; missing columns {need - set(cases.columns)}")
        return
    lc = cases[cases.opname.str.contains("cholecyst", case=False, na=False) & (cases.approach == "Videoscopic")]
    with_aline = lc[lc.aline1.notna() & (lc.aline1.astype(str).str.strip() != "")]
    print(f"\nLaparoscopic cholecystectomy cases: {len(lc)}; with an arterial line: {len(with_aline)}")
    cols = [c for c in ["caseid", "age", "sex", "asa", "dx", "aline1", "intraop_eph", "intraop_phe"] if c in cases.columns]
    with pd.option_context("display.max_rows", 500, "display.width", 160):
        print(with_aline[cols].to_string(index=False))


def print_events(df: pd.DataFrame, record: dict) -> None:
    minute = (df["time_s"] // 60).astype(int)
    if df["ART_MBP"].notna().any():
        m = df["ART_MBP"].where(df["ART_MBP"].between(20, 200))  # drop unconnected line / flush
        per_min = m.groupby(minute).median()
        low = per_min[per_min < 65]
        print("\nMinutes (from casestart) with median arterial MAP < 65 mmHg (values 20-200 only):")
        print(", ".join(f"{k}: {v:.0f}" for k, v in low.items()) or "none")
        spikes = sorted(set(minute[df["ART_SBP"] > 200]))
        print(f"Minutes with arterial SBP > 200 (likely flush/artifact): {spikes or 'none'}")
    else:
        print("\nNo ART_MBP data; cannot list MAP < 65 minutes.")
    for k in ["anestart", "opstart", "opend", "aneend", "caseend"]:
        if record.get(k) is not None:
            print(f"  {k}: {record[k]} s (minute {float(record[k]) / 60:.1f})")
    for col in ["PPF20_CT", "RFTN20_CT"]:
        s = df[col].ffill()
        ch = df.loc[s.diff().fillna(0) != 0, ["time_s", col]]
        if len(ch):
            print(f"{col} target changes (first 30): " + "; ".join(f"t={t:.0f}s->{v:g}" for t, v in ch.head(30).values))
    summary = df.assign(minute=minute).groupby("minute")[["ART_MBP", "HR", "SPO2", "BIS", "PPF20_CE", "RFTN20_CE", "NIBP_MBP"]].median()
    print("\nPer-minute medians (use minutes 6-19 for the drug_effect scenario):")
    with pd.option_context("display.max_rows", 500, "display.width", 160):
        print(summary.round(1).to_string())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("caseid", nargs="?", type=int, default=59)
    ap.add_argument("interval", nargs="?", type=float, default=1.0, help="seconds between samples (default 1)")
    ap.add_argument("--outdir", type=Path, default=Path("."))
    a = ap.parse_args()
    a.outdir.mkdir(parents=True, exist_ok=True)
    print(ATTRIBUTION + "\n")
    df = fetch_tracks(a.caseid, a.interval, a.outdir)
    cases, record = fetch_clinical(a.caseid, a.outdir)
    fetch_labs(a.caseid, a.outdir)
    print_lap_chole_alines(cases)
    print_events(df, record)


if __name__ == "__main__":
    main()
