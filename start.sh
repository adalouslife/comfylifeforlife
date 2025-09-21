#!/usr/bin/env bash
set -euo pipefail

# ---- config ----
COMFY_ROOT="${COMFY_ROOT:-/workspace/ComfyUI}"
COMFY_APP="${COMFY_APP:-${COMFY_ROOT}}"
COMFY_PORT="${COMFY_PORT:-8188}"
HANDLER_PORT="${HANDLER_PORT:-8000}"
WAIT_SECONDS="${WAIT_SECONDS:-120}"

echo "[start] COMFY_ROOT=${COMFY_ROOT}"
echo "[start] COMFY_APP=${COMFY_APP}"
echo "[start] COMFY_PORT=${COMFY_PORT}  HANDLER_PORT=${HANDLER_PORT}"

# If COMFY_APP doesn’t have main.py (e.g., COMFY_ROOT points to /runpod-volume/ComfyUI),
# seed it from the baked-in /workspace/ComfyUI.
if [ ! -f "${COMFY_APP}/main.py" ]; then
  echo "[start] Seeding ComfyUI into ${COMFY_APP} ..."
  mkdir -p "$(dirname "${COMFY_APP}")"
  rm -rf "${COMFY_APP}" || true
  cp -r /workspace/ComfyUI "${COMFY_APP}"
fi

# Ensure ComfyUI input/output exist
mkdir -p "${COMFY_APP}/input" "${COMFY_APP}/output" /workspace/logs

# ---- start ComfyUI ----
echo "[start] Launching ComfyUI..."
python3 -u "${COMFY_APP}/main.py" \
  --listen 0.0.0.0 --port "${COMFY_PORT}" \
  --disable-auto-launch \
  > /workspace/logs/comfyui.log 2>&1 &

COMFY_PID=$!

# Wait for ComfyUI health
echo "[start] Waiting for ComfyUI to be ready..."
ready=0
for i in $(seq 1 "${WAIT_SECONDS}"); do
  if curl -fsS "http://127.0.0.1:${COMFY_PORT}/system_stats" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done

if [ "${ready}" -ne 1 ]; then
  echo "[start][ERROR] ComfyUI did not become ready in ${WAIT_SECONDS}s"
  echo "---- ComfyUI log tail ----"
  tail -n 200 /workspace/logs/comfyui.log || true
  exit 1
fi

echo "[start] ComfyUI is up."

# ---- start FastAPI handler (serves RunPod serverless requests) ----
echo "[start] Launching API handler..."
python3 -u /workspace/handler.py
