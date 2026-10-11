#!/usr/bin/env bash
# Start the Sentinel-OR model services (M0). Lightning first: NVFP4 kernels can take ~45 min to
# compile on first boot, so the cache volumes below are kept between runs.
#
#   scripts/start_models.sh start [lightning|omni|asr|tts ...]   default: all four, in that order
#   scripts/start_models.sh status
#   scripts/start_models.sh logs <service>
#   scripts/start_models.sh stop [service ...]                   stops containers; volumes are kept
#
# Safe to re-run. It never pulls an image, never restarts Docker and never replaces a running
# container: a service whose port is already listening is left alone, and a service whose image
# or weights are missing or still downloading is skipped with the reason.
# Then: scripts/smoke_models.py
#
# Lightning flags are the model card's DGX Spark recipe (assets/models/lightning/README.md), with
# a smaller memory share and context so Omni and speech fit beside it. Omni's flags and the two
# speech services are NOT verified on this box (DOWNLOADS.md section 6 was not available when
# this was written): override them with the variables below.
set -euo pipefail

ASSETS="${ASSETS:-$HOME/assets}"
MODELS="${MODELS:-$ASSETS/models}"
BIND="${BIND:-127.0.0.1}"                        # published on loopback only
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:v0.27.1-aarch64}"

LIGHTNING_DIR="${LIGHTNING_DIR:-$MODELS/lightning}"
LIGHTNING_DRAFT_DIR="${LIGHTNING_DRAFT_DIR:-$MODELS/lightning-dspark}"
LIGHTNING_DRAFT="${LIGHTNING_DRAFT:-1}"          # 0 = serve without the DSpark draft
LIGHTNING_GPU_FRAC="${LIGHTNING_GPU_FRAC:-0.40}" # share of the 128 GB unified memory (card: 0.85)
LIGHTNING_MAX_LEN="${LIGHTNING_MAX_LEN:-32768}"  # card default is 1M tokens; agent prompts are small
LIGHTNING_EXTRA_ARGS="${LIGHTNING_EXTRA_ARGS:-}"

OMNI_DIR="${OMNI_DIR:-$MODELS/omni}"
OMNI_IMAGE="${OMNI_IMAGE:-$VLLM_IMAGE}"          # needs vllm[audio] only with use_audio_in_video: true
OMNI_GPU_FRAC="${OMNI_GPU_FRAC:-0.25}"
OMNI_MAX_LEN="${OMNI_MAX_LEN:-16384}"
OMNI_EXTRA_ARGS="${OMNI_EXTRA_ARGS:-}"

# Speech: no defaults. Set the image and the command that serves POST /transcribe (:8002, wav in,
# text out) or POST /tts (:8003, text in, wav out) on the port inside the container.
ASR_IMAGE="${ASR_IMAGE:-}"; ASR_CMD="${ASR_CMD:-}"; ASR_GPU="${ASR_GPU:-1}"
TTS_IMAGE="${TTS_IMAGE:-}"; TTS_CMD="${TTS_CMD:-}"; TTS_GPU="${TTS_GPU:-1}"

SERVICES=(lightning omni asr tts)
declare -A PORT=([lightning]=8000 [omni]=8001 [asr]=8002 [tts]=8003)
# No outbound calls from inside the containers (hard rule 7).
OFFLINE_ENV=(-e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e VLLM_NO_USAGE_STATS=1 -e DO_NOT_TRACK=1)

name() { echo "sentinel-$1"; }
say()  { printf '%-10s %s\n' "$1" "$2"; }
listening() { ss -ltnH "sport = :$1" 2>/dev/null | grep -q .; }
have_image() { docker image inspect "$1" >/dev/null 2>&1; }
container_state() { docker container inspect -f '{{.State.Status}}' "$(name "$1")" 2>/dev/null | grep . || echo none; }

# A complete `hf download --local-dir` copy: config, weights, and no partial files left.
weights_problem() {
  local dir="$1"
  [ -f "$dir/config.json" ] || { echo "no config.json in $dir"; return; }
  compgen -G "$dir/*.safetensors" >/dev/null || { echo "no *.safetensors in $dir (still downloading?)"; return; }
  if [ -n "$(find "$dir/.cache" -name '*.incomplete' -print -quit 2>/dev/null)" ]; then
    echo "partial files in $dir/.cache (still downloading)"
  fi
}

# Returns 0 when the caller should go on to `docker run`.
ready_to_run() {
  local svc="$1" image="$2" state
  state="$(container_state "$svc")"
  if [ "$state" = running ]; then say "$svc" "already running ($(name "$svc")); left alone"; return 1; fi
  if listening "${PORT[$svc]}"; then say "$svc" "port ${PORT[$svc]} is already in use; left alone"; return 1; fi
  if [ "$state" != none ]; then
    docker start "$(name "$svc")" >/dev/null
    say "$svc" "restarted existing container $(name "$svc") (was $state)"; return 1
  fi
  if ! have_image "$image"; then say "$svc" "SKIPPED: image $image is not loaded (not pulling)"; return 1; fi
  return 0
}

run_vllm() {  # svc image model_dir [vllm args...]
  local svc="$1" image="$2" dir="$3"; shift 3
  docker run -d --name "$(name "$svc")" --gpus all --ipc=host \
    -p "$BIND:${PORT[$svc]}:${PORT[$svc]}" \
    -v "$MODELS:/models:ro" \
    -v "sentinel-cache-$svc:/root/.cache" -v "sentinel-triton-$svc:/root/.triton" \
    "${OFFLINE_ENV[@]}" \
    "$image" --model "/models/$(basename "$dir")" --served-model-name "$svc" \
    --host 0.0.0.0 --port "${PORT[$svc]}" "$@" >/dev/null
  say "$svc" "started $(name "$svc") on $BIND:${PORT[$svc]} (logs: $0 logs $svc)"
}

start_lightning() {
  ready_to_run lightning "$VLLM_IMAGE" || return 0
  local problem draft=()
  problem="$(weights_problem "$LIGHTNING_DIR")"
  [ -z "$problem" ] || { say lightning "SKIPPED: $problem"; return 0; }
  if [ "$LIGHTNING_DRAFT" = 1 ]; then
    problem="$(weights_problem "$LIGHTNING_DRAFT_DIR")"
    [ -z "$problem" ] || { say lightning "SKIPPED: DSpark draft: $problem. LIGHTNING_DRAFT=0 serves without it."; return 0; }
    draft=(--speculative_config.model "/models/$(basename "$LIGHTNING_DRAFT_DIR")"
           --speculative_config.num_speculative_tokens 3)
  fi
  # shellcheck disable=SC2086
  run_vllm lightning "$VLLM_IMAGE" "$LIGHTNING_DIR" \
    --moe-backend marlin --kv-cache-dtype fp8 --enable-prefix-caching \
    --mamba-backend flashinfer --mamba-cache-mode align \
    --reasoning-parser nemotron_v3 \
    --gpu-memory-utilization "$LIGHTNING_GPU_FRAC" --max-model-len "$LIGHTNING_MAX_LEN" \
    "${draft[@]}" $LIGHTNING_EXTRA_ARGS
}

start_omni() {
  ready_to_run omni "$OMNI_IMAGE" || return 0
  local problem
  problem="$(weights_problem "$OMNI_DIR")"
  [ -z "$problem" ] || { say omni "SKIPPED: $problem"; return 0; }
  # shellcheck disable=SC2086
  run_vllm omni "$OMNI_IMAGE" "$OMNI_DIR" \
    --gpu-memory-utilization "$OMNI_GPU_FRAC" --max-model-len "$OMNI_MAX_LEN" $OMNI_EXTRA_ARGS
}

start_speech() {  # svc image cmd gpu
  local svc="$1" image="$2" cmd="$3" gpu="$4" gpus=()
  if [ -z "$image" ] || [ -z "$cmd" ]; then
    say "$svc" "SKIPPED: not configured (set ${svc^^}_IMAGE and ${svc^^}_CMD)"; return 0
  fi
  ready_to_run "$svc" "$image" || return 0
  [ "$gpu" = 1 ] && gpus=(--gpus all)
  docker run -d --name "$(name "$svc")" "${gpus[@]}" --ipc=host \
    -p "$BIND:${PORT[$svc]}:${PORT[$svc]}" \
    -v "$MODELS:/models:ro" -v "$(cd "$(dirname "$0")/.." && pwd):/app:ro" -w /app \
    -v "sentinel-cache-$svc:/root/.cache" \
    "${OFFLINE_ENV[@]}" -e "PORT=${PORT[$svc]}" \
    "$image" sh -c "$cmd" >/dev/null
  say "$svc" "started $(name "$svc") on $BIND:${PORT[$svc]} (logs: $0 logs $svc)"
}

start_one() {
  case "$1" in
    lightning) start_lightning ;;
    omni)      start_omni ;;
    asr)       start_speech asr "$ASR_IMAGE" "$ASR_CMD" "$ASR_GPU" ;;
    tts)       start_speech tts "$TTS_IMAGE" "$TTS_CMD" "$TTS_GPU" ;;
    *) echo "unknown service: $1 (one of: ${SERVICES[*]})" >&2; exit 2 ;;
  esac
}

status() {
  local svc port
  for svc in "${SERVICES[@]}"; do
    port="${PORT[$svc]}"
    say "$svc" "container=$(container_state "$svc") port $port $(listening "$port" && echo listening || echo closed)"
  done
  free -g | awk 'NR==2 {printf "%-10s %s GB used of %s GB\n", "memory", $3, $2}'
}

cmd="${1:-start}"; [ $# -gt 0 ] && shift
case "$cmd" in
  start)
    docker info >/dev/null 2>&1 || { echo "docker is not reachable" >&2; exit 1; }
    [ $# -gt 0 ] || set -- "${SERVICES[@]}"
    for svc in "$@"; do start_one "$svc"; done
    echo "next: scripts/smoke_models.py   (--wait 3000 to wait out the first-boot kernel compile)" ;;
  status) status ;;
  logs)   [ $# -eq 1 ] || { echo "usage: $0 logs <service>" >&2; exit 2; }
          docker logs -f --tail 100 "$(name "$1")" ;;
  stop)
    [ $# -gt 0 ] || set -- "${SERVICES[@]}"
    for svc in "$@"; do
      if [ "$(container_state "$svc")" = none ]; then say "$svc" "no container"; continue; fi
      docker rm -f "$(name "$svc")" >/dev/null && say "$svc" "stopped and removed (cache volumes kept)"
    done ;;
  *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
