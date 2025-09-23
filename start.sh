#!/usr/bin/env bash
set -euo pipefail

# --- Config with safe defaults ---
export COMFY_HOST="${COMFY_HOST:-127.0.0.1}"
export COMFY_PORT="${COMFY_PORT:-8188}"
export COMFY_UI_DIR="${COMFY_UI_DIR:-/workspace/ComfyUI}"
export INPUT_DIR="${INPUT_DIR:-/workspace/ComfyUI/input}"
export OUTPUT_DIR="${OUTPUT_DIR:-/workspace/ComfyUI/output}"
export WORKFLOW_PATH="${WORKFLOW_PATH:-/workspace/comfyui/workflows/APIAutoFaceACE.json}"
export STORAGE_DIR="${STORAGE_DIR:-/runpod-volume}"

mkdir -p "$INPUT_DIR" "$OUTPUT_DIR"

echo "[bootstrap] COMFY_UI_DIR=$COMFY_UI_DIR"
echo "[bootstrap] INPUT_DIR=$INPUT_DIR"
echo "[bootstrap] OUTPUT_DIR=$OUTPUT_DIR"
echo "[bootstrap] WORKFLOW_PATH=$WORKFLOW_PATH"
echo "[bootstrap] STORAGE_DIR=$STORAGE_DIR"

# --- Start ComfyUI in background ---
cd "$COMFY_UI_DIR"

# If another stray Comfy is running from a previous attempt, kill it.
if pgrep -f "main.py --listen" >/dev/null 2>&1; then
  echo "[bootstrap] Found existing ComfyUI process. Killing it."
  pkill -f "main.py --listen" || true
  sleep 1
fi

echo "[bootstrap] Launching ComfyUI..."
python3 -u main.py \
  --listen 0.0.0.0 \
  --port "${COMFY_PORT}" \
  --enable-cors-header \
  --input-directory "${INPUT_DIR}" \
  --output-directory "${OUTPUT_DIR}" \
  >/workspace/comfyui.log 2>&1 &

COMFY_PID=$!

# --- Wait for ComfyUI to be ready ---
echo "[bootstrap] Waiting for ComfyUI on http://${COMFY_HOST}:${COMFY_PORT}/system_stats ..."
for i in {1..60}; do
  if curl -fsS "http://${COMFY_HOST}:${COMFY_PORT}/system_stats" >/dev/null 2>&1; then
    echo "[bootstrap] ComfyUI is up."
    break
  fi
  sleep 2
  if ! kill -0 $COMFY_PID 2>/dev/null; then
    echo "[bootstrap] ComfyUI process died unexpectedly. See /workspace/comfyui.log"
    cat /workspace/comfyui.log || true
    exit 1
  fi
  if [[ $i -eq 60 ]]; then
    echo "[bootstrap] ComfyUI did not come up in time. Logs:"
    sed -n '1,200p' /workspace/comfyui.log || true
    exit 1
  fi
done

# --- Start the RunPod handler (foreground) ---
echo "[bootstrap] Starting handler..."
exec python3 -u /workspace/handler.py
