#!/usr/bin/env bash
set -euo pipefail

# Defaults
export INPUT_DIR="${INPUT_DIR:-/workspace/inputs}"
export OUTPUT_DIR="${OUTPUT_DIR:-/workspace/outputs}"
export COMFY_HOST="${COMFY_HOST:-127.0.0.1}"
export COMFY_PORT="${COMFY_PORT:-8188}"

mkdir -p "$INPUT_DIR" "$OUTPUT_DIR"

# Start ComfyUI
# - assumes repo at /workspace/ComfyUI
cd /workspace/ComfyUI

# If custom nodes fail to import, ComfyUI exits; run once and tail logs in worker
python3 main.py \
  --listen 0.0.0.0 \
  --port "${COMFY_PORT}" \
  --output-directory "${OUTPUT_DIR}" \
  --input-directory "${INPUT_DIR}" &
COMFY_PID=$!

# Give it a short breath so health_check has something to reach
sleep 2

# Start RunPod handler (this process must stay in foreground)
cd /workspace
python3 -u handler.py
