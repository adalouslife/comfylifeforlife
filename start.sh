#!/usr/bin/env bash
set -euo pipefail

# Defaults (can be overridden by env)
export INPUT_DIR="${INPUT_DIR:-/workspace/inputs}"
export OUTPUT_DIR="${OUTPUT_DIR:-/workspace/outputs}"
export COMFY_HOST="${COMFY_HOST:-127.0.0.1}"
export COMFY_PORT="${COMFY_PORT:-8188}"
export RP_HANDLER_PORT="${RP_HANDLER_PORT:-8000}"

mkdir -p "$INPUT_DIR" "$OUTPUT_DIR"

# ---- start ComfyUI ----
cd /workspace/ComfyUI

# If Comfy ever crashes due to a missing import, we want logs to show it.
python3 main.py \
  --listen 0.0.0.0 \
  --port "${COMFY_PORT}" \
  --output-directory "${OUTPUT_DIR}" \
  --input-directory "${INPUT_DIR}" \
  &

COMFY_PID=$!

# Small grace so health check has something to ping
sleep 2

# ---- start RunPod handler (foreground) ----
cd /workspace
exec python3 -u handler.py
