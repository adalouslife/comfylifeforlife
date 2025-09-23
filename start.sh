#!/usr/bin/env bash
set -euo pipefail

# Activate venv
source /opt/venv/bin/activate

# Start ComfyUI headless
mkdir -p "${INPUT_DIR:-/workspace/inputs}" "${OUTPUT_DIR:-/workspace/outputs}"
COMFY_PORT="${COMFY_PORT:-8188}"
COMFY_HOST="${COMFY_HOST:-0.0.0.0}"

# Start ComfyUI in background, keep logs
python -u "${COMFY_DIR:-/workspace/ComfyUI}/main.py" \
  --listen "$COMFY_HOST" \
  --port "$COMFY_PORT" \
  --disable-auto-launch \
  --enable-cors-header '*' \
  > /workspace/comfy.log 2>&1 &

# Small wait to let it boot; handler will also poll
sleep 2

# Start RunPod handler (blocks)
python -u -m runpod.serverless
