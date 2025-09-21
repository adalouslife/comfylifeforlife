#!/usr/bin/env bash
set -euo pipefail

# Defaults
export COMFY_HOST="${COMFY_HOST:-127.0.0.1}"
export COMFY_PORT="${COMFY_PORT:-8188}"
export WORKFLOW_PATH="${WORKFLOW_PATH:-/workspace/comfyui/workflows/APIAutoFaceACE.json}"
export INPUT_DIR="${INPUT_DIR:-/workspace/inputs}"
export OUTPUT_DIR="${OUTPUT_DIR:-/workspace/outputs}"
export COMFY_UI_DIR="${COMFY_UI_DIR:-/workspace/ComfyUI}"

mkdir -p "$INPUT_DIR" "$OUTPUT_DIR"

echo "[boot] Launching ComfyUI on ${COMFY_HOST}:${COMFY_PORT}"
cd "$COMFY_UI_DIR"
# Headless ComfyUI; no browser; listen only on localhost inside container.
python -u main.py --listen "$COMFY_HOST" --port "$COMFY_PORT" --disable-auto-launch >/workspace/comfyui.log 2>&1 &

# Start the RunPod worker (this process will poll the queue)
echo "[boot] Starting RunPod worker..."
cd /workspace
exec python -u handler.py
