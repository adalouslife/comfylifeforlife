#!/usr/bin/env bash
set -euo pipefail

# -------- Config --------
COMFY_DIR="${COMFY_DIR:-/workspace/ComfyUI}"
COMFY_HOST="${COMFY_HOST:-127.0.0.1}"
COMFY_PORT="${COMFY_PORT:-8188}"
RP_HANDLER_PORT="${RP_HANDLER_PORT:-8000}"

LOG_DIR="/workspace/logs"
mkdir -p "$LOG_DIR"
COMFY_LOG="$LOG_DIR/comfyui.log"
WORKER_LOG="$LOG_DIR/worker.log"

echo "[start.sh] Using python: $(command -v python3 || true)"
if ! command -v python3 >/dev/null 2>&1; then
  echo "[start.sh] ERROR: python3 not found in PATH."
  exit 1
fi

# -------- Optional: custom nodes / models --------
# Run your optional installers if present; don't fail the container if they error.
if [ -f "/workspace/app/install_custom_nodes.py" ]; then
  echo "[start.sh] Installing custom nodes..."
  python3 /workspace/app/install_custom_nodes.py || echo "[start.sh] WARNING: custom nodes install failed (continuing)."
fi
if [ -f "/workspace/app/download_models.sh" ]; then
  echo "[start.sh] Downloading models (if any)..."
  bash /workspace/app/download_models.sh || echo "[start.sh] WARNING: model download failed (continuing)."
fi

# -------- Start ComfyUI --------
echo "[start.sh] Launching ComfyUI at ${COMFY_HOST}:${COMFY_PORT} ..."
# We disable auto-launch, listen on 0.0.0.0 just in case, but we health-check via 127.0.0.1.
# --disable-metadata can speed startup; remove if you need it.
set +e
nohup python3 "${COMFY_DIR}/main.py" \
  --listen 0.0.0.0 \
  --port "${COMFY_PORT}" \
  --disable-auto-launch \
  --highvram \
  > "${COMFY_LOG}" 2>&1 &
COMFY_PID=$!
set -e

echo "[start.sh] ComfyUI PID: ${COMFY_PID}"
echo "[start.sh] Tailing ComfyUI log to detect early crashes ..."
# Quick, bounded tail to surface obvious import errors without blocking forever.
timeout 5s tail -n +1 -f "${COMFY_LOG}" || true

# -------- Wait for ComfyUI readiness --------
echo "[start.sh] Waiting for ComfyUI HTTP readiness ..."
ATTEMPTS=90
SLEEP=2
READY=0
for i in $(seq 1 "${ATTEMPTS}"); do
  if curl -fsS "http://${COMFY_HOST}:${COMFY_PORT}/system_stats" >/dev/null 2>&1; then
    READY=1
    break
  fi
  # If process died, fail early with logs.
  if ! kill -0 "${COMFY_PID}" >/dev/null 2>&1; then
    echo "[start.sh] ERROR: ComfyUI process exited during startup."
    echo "------ ComfyUI Last 200 lines ------"
    tail -n 200 "${COMFY_LOG}" || true
    exit 1
  fi
  sleep "${SLEEP}"
done

if [ "${READY}" -ne 1 ]; then
  echo "[start.sh] ERROR: ComfyUI did not become ready in time."
  echo "------ ComfyUI Last 200 lines ------"
  tail -n 200 "${COMFY_LOG}" || true
  exit 1
fi

echo "[start.sh] ComfyUI is ready."

# -------- Start RunPod worker (foreground) --------
echo "[start.sh] Starting RunPod worker on port ${RP_HANDLER_PORT} ..."
exec python3 -u -m runpod.serverless.worker \
  --handler-path /workspace/app/handler.py \
  --handler-name handler \
  --rp-serve-port "${RP_HANDLER_PORT}" \
  2>&1 | tee -a "${WORKER_LOG}"
