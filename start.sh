#!/usr/bin/env bash
set -euo pipefail

export COMFY_HOST="${COMFY_HOST:-127.0.0.1}"
export COMFY_PORT="${COMFY_PORT:-8188}"

cd /workspace/ComfyUI
python3 -u main.py --listen "${COMFY_HOST}" --port "${COMFY_PORT}" --disable-auto-launch
