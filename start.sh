#!/usr/bin/env bash
set -euo pipefail

echo "[start] worker booting at $(date)"

# Launch ComfyUI in background
python3 ComfyUI/main.py --listen 0.0.0.0 --port 8188 \
  --output-directory /workspace/outputs \
  --input-directory /workspace/inputs > /workspace/comfyui.log 2>&1 &

COMFY_PID=$!
echo "[start] ComfyUI PID=$COMFY_PID"

# Launch handler (blocks)
exec python3 handler.py
