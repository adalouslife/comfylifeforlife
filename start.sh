#!/usr/bin/env bash
set -euo pipefail

# Defaults (override via env in RunPod)
COMFY_HOST="${COMFY_HOST:-127.0.0.1}"
COMFY_PORT="${COMFY_PORT:-8188}"
COMFY_BIND="${COMFY_BIND:-0.0.0.0}"   # bind externally just in case
WORK_DIR="${WORK_DIR:-/workspace}"
COMFY_DIR="${COMFY_DIR:-/workspace/ComfyUI}"

# Start ComfyUI (headless)
# Adjust launch args to your repo layout. Common flags:
#  --listen 0.0.0.0  --port 8188  --disable-auto-launch
python -u "${COMFY_DIR}/main.py" \
  --listen "${COMFY_BIND}" \
  --port "${COMFY_PORT}" \
  --disable-auto-launch \
  --dont-print-server \
  > "${WORK_DIR}/comfyui.log" 2>&1 &

COMFY_PID=$!

echo "Waiting for ComfyUI on ${COMFY_HOST}:${COMFY_PORT} ..."
for i in {1..120}; do
  if curl -s "http://${COMFY_HOST}:${COMFY_PORT}/system_stats" >/dev/null; then
    echo "ComfyUI is up."
    break
  fi
  sleep 1
  if ! kill -0 ${COMFY_PID} 2>/dev/null; then
    echo "ComfyUI crashed. Tail:"
    tail -n 200 "${WORK_DIR}/comfyui.log" || true
    exit 1
  fi
  if [ "$i" -eq 120 ]; then
    echo "Timed out waiting for ComfyUI."
    exit 1
  fi
done

# Start the RunPod handler (FastAPI/Flask—whatever your handler.py uses)
python -u /workspace/handler.py
