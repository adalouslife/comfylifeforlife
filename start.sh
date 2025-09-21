#!/usr/bin/env bash
set -euo pipefail

COMFY_HOST="${COMFY_HOST:-127.0.0.1}"
COMFY_PORT="${COMFY_PORT:-8188}"
COMFY_BIND="${COMFY_BIND:-0.0.0.0}"
WORK_DIR="${WORK_DIR:-/workspace}"
COMFY_ROOT="${COMFY_ROOT:-/workspace/comfylifeforlife/comfyui}"
COMFY_APP="${COMFY_APP:-${COMFY_ROOT}/ComfyUI}"   # if you vendor ComfyUI under comfyui/ComfyUI

mkdir -p "${WORK_DIR}/logs"
LOG="${WORK_DIR}/logs/comfyui.log"

echo "Launching ComfyUI from: ${COMFY_APP}"
python -u "${COMFY_APP}/main.py" \
  --listen "${COMFY_BIND}" \
  --port "${COMFY_PORT}" \
  --disable-auto-launch \
  --dont-print-server \
  > "${LOG}" 2>&1 &

COMFY_PID=$!

echo "Waiting for ComfyUI on ${COMFY_HOST}:${COMFY_PORT} ..."
for i in {1..120}; do
  if curl -sf "http://${COMFY_HOST}:${COMFY_PORT}/system_stats" >/dev/null; then
    echo "ComfyUI is up."
    break
  fi
  sleep 1
  if ! kill -0 ${COMFY_PID} 2>/dev/null; then
    echo "ComfyUI crashed. Last log lines:"
    tail -n 200 "${LOG}" || true
    exit 1
  fi
  if [ "$i" -eq 120 ]; then
    echo "Timed out waiting for ComfyUI."
    exit 1
  fi
done

echo "Starting handler..."
python -u /workspace/handler.py
