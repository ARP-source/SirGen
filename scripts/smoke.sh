#!/usr/bin/env bash
# Smoke test for a running backend: /health, /scenarios and a control round-trip.
set -euo pipefail
BASE="${BASE:-http://localhost:7000}"
PY="${PYTHON:-python3}"
j() { "$PY" -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

echo "== GET /health";    curl -fsS "$BASE/health" | j "d['status'], d['scenario_id'], 'video:', d['video']['label']"
echo "== GET /scenarios"; curl -fsS "$BASE/scenarios" | j "', '.join(s['id'] for s in d)"
echo "== GET /patient";   curl -fsS "$BASE/patient" | j "d['record_id']"
echo "== control round-trip"
curl -fsS -X POST "$BASE/control/scenario/drug_effect" | j "'select', d['scenario_id'], d['clock_s'], d['playing']"
curl -fsS -X POST "$BASE/control/play"  | j "'play', d['playing']"
sleep 1.5
curl -fsS -X POST "$BASE/control/pause" | j "'pause', d['playing'], 'clock', d['clock_s']"
curl -fsS "$BASE/state" | "$PY" -c "
import json,sys; d=json.load(sys.stdin)
assert d['scenario_id']=='drug_effect' and d['clock_s']>0.5 and not d['playing'], d['clock_s']
print('state ok: clock', d['clock_s'], 'MAP', d['vitals']['map'], '|', d['vitals']['source'][:60])"
curl -fsS -X POST "$BASE/control/trigger" | j "'trigger', d['event']['event_id'], d['event']['event_to_alert_ms'], 'ms'"
curl -fsS -X POST "$BASE/control/reset" | j "'reset', d['clock_s'], d['playing']"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/control/scenario/does_not_exist")
[ "$code" = "404" ] && echo "unknown scenario -> 404 ok"
echo "== GET /video.mjpg (first part)"
part=$(curl -sS --max-time 2 "$BASE/video.mjpg" 2>/dev/null | head -c 300 | tr -d '\0' || true)
echo "$part" | grep -a -m1 -o 'X-Frame-Id: [0-9]*' || { echo "no MJPEG frame"; exit 1; }
echo "SMOKE OK"
