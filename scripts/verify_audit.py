#!/usr/bin/env python3
"""Verify an audit log file from scratch. Standalone: standard library only, no Sentinel-OR code.

    python3 scripts/verify_audit.py audit/audit-20261010-221500.jsonl [expected_head_hash]

Each line is a JSON entry whose "hash" is the SHA-256 of the entry without that field (sorted keys,
compact separators) and whose "prev_hash" is the previous entry's hash (the first uses 64 zeros).
Give the head hash you noted on screen 2 to also catch entries removed from the end.
Exit code 0 = chain intact, 1 = broken.
"""
import hashlib
import json
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    prev, n = "0" * 64, 0
    with open(sys.argv[1], encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            try:
                e = json.loads(line)
            except ValueError:
                print(f"BROKEN: line {lineno} is not valid JSON")
                return 1
            if e.get("seq") != n + 1:
                print(f"BROKEN: entry numbering breaks at line {lineno}")
                return 1
            if e.get("prev_hash") != prev:
                print(f"BROKEN: entry {e.get('seq')} does not link to the entry before it")
                return 1
            body = {k: v for k, v in e.items() if k != "hash"}
            digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
            if digest != e.get("hash"):
                print(f"BROKEN: entry {e.get('seq')} was modified")
                return 1
            prev, n = e["hash"], n + 1
    if len(sys.argv) > 2 and not prev.startswith(sys.argv[2]):
        print(f"BROKEN: file ends at entry {n} with head {prev[:16]}..., expected {sys.argv[2]}")
        return 1
    print(f"OK: {n} entries, head {prev}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
