#!/usr/bin/env bash
# Bring Sentinel-OR up from a cold boot: Lightning (docker), the vision service, then the app.
# Idempotent: anything already running is left alone. Each long-running part gets its own window
# in the tmux session `dl` (attach with `tmux attach -t dl`).
#   SENTINEL_VIDEO   video file (default: the HoloHub sample in ~/assets)
#   LIGHTNING_WAIT_S how long to wait for Lightning on :8000 (default 3600; NVFP4 kernels can take
#                    up to ~45 min to compile on a first boot)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SESSION=dl
VIDEO="${SENTINEL_VIDEO:-$HOME/assets/data/holoscan_sample/surgical_video.264}"
LIGHTNING_WAIT_S="${LIGHTNING_WAIT_S:-3600}"

say() { printf '[start_all] %s\n' "$*"; }
up() { curl -s -o /dev/null --max-time 3 "$1"; }  # 0 when the URL answers at all

wait_for() {  # url seconds label
  local url="$1" left="$2" label="$3"
  until curl -sf -o /dev/null --max-time 3 "$url"; do
    left=$((left - 5))
    [ "$left" -le 0 ] && { say "$label did not answer at $url in time"; return 1; }
    sleep 5
  done
  say "$label is up ($url)"
}

window() {  # name command: run the command in its own window of the tmux session
  local name="$1" cmd="$2"
  tmux has-session -t "$SESSION" 2>/dev/null || tmux new-session -d -s "$SESSION" -n bash
  if tmux list-windows -t "$SESSION" -F '#{window_name}' | grep -qx "$name"; then
    tmux send-keys -t "$SESSION:$name" "$cmd" Enter
  else
    tmux new-window -d -t "$SESSION" -n "$name" "bash"
    tmux send-keys -t "$SESSION:$name" "$cmd" Enter
  fi
}

# ---- 1. Lightning (vLLM in docker, :8000) -------------------------------------------------------
state="$(docker inspect -f '{{.State.Status}}' lightning 2>/dev/null || echo none)"
case "$state" in
  running) say "lightning container already running; left alone" ;;
  none)
    say "no container named 'lightning'. Create it with the command in HANDOFF.md (section 'Lightning'):"
    cat <<'EOF'
docker run -d --name lightning --gpus all --ipc=host -p 8000:8000 \
  -v ~/assets/models:/models:ro -v ~/.cache/vllm:/root/.cache/vllm -v ~/.cache/flashinfer:/root/.cache/flashinfer \
  -e HF_HUB_OFFLINE=1 vllm/vllm-openai:v0.27.1-aarch64 \
  --model /models/lightning --served-model-name lightning \
  --speculative_config.model /models/lightning-dspark --speculative_config.num_speculative_tokens 3 \
  --moe-backend marlin --kv-cache-dtype fp8 --enable-prefix-caching \
  --mamba-backend flashinfer --mamba-cache-mode align \
  --gpu-memory-utilization 0.35 --max-model-len 65536 --max-num-seqs 8 \
  --reasoning-parser nemotron_v3 --tool-call-parser qwen3_coder --enable-auto-tool-choice
EOF
    say "not started (this script does not create the container). Re-run after creating it."
    exit 1 ;;
  *) say "starting existing lightning container (was $state)"; docker start lightning >/dev/null || exit 1 ;;
esac
wait_for http://localhost:8000/health "$LIGHTNING_WAIT_S" "Lightning" || { say "see: docker logs --tail 30 lightning"; exit 1; }

# ---- 2. Vision service (127.0.0.1:8004) ---------------------------------------------------------
if up http://127.0.0.1:8004/health || pgrep -f "vision/server.py" >/dev/null; then
  say "vision service already running; left alone"
else
  say "starting the vision service in tmux $SESSION:vision"
  window vision "cd '$ROOT' && bash scripts/start_vision.sh"
  wait_for http://127.0.0.1:8004/health 180 "vision service" || say "continuing without it: the app marks the detector as not running"
fi

# ---- 3. App: backend :7000 + frontend :5173 -----------------------------------------------------
if up http://localhost:7000/health; then
  say "app already running on :7000; left alone"
else
  [ -f "$VIDEO" ] || say "WARNING: video not found: $VIDEO (the app will show the test pattern)"
  say "starting the app in tmux $SESSION:app"
  window app "cd '$ROOT' && SENTINEL_VIDEO='$VIDEO' bash scripts/dev.sh 2>&1 | tee -a ~/sentinel-dev.log"
  wait_for http://localhost:7000/health 90 "backend" || exit 1
fi

say "/health:"
curl -s http://localhost:7000/health
echo
say "screens: http://localhost:5173/surgical  /dashboard  /control  /post-op"
