#!/usr/bin/env bash
set -euo pipefail

echo "=== Boot ==="
python --version || true
pip --version || true

# Ensure dirs exist
mkdir -p "${INPUT_DIR:-/workspace/ComfyUI/input}"
mkdir -p "${OUTPUT_DIR:-/workspace/ComfyUI/output}"

# Start ComfyUI in the background if ComfyUI is present in repo
if [ -d "/workspace/ComfyUI" ]; then
  echo "Starting ComfyUI on ${COMFY_BIND_HOST:-127.0.0.1}:${COMFY_BIND_PORT:-8188}"
  # Start minimal ComfyUI server if your repo ships it under /workspace/ComfyUI
  # Adjust the launch command to your tree if needed.
  (
    cd /workspace/ComfyUI
    # Common ComfyUI entry point names:
    if [ -f "main.py" ]; then
      python main.py --listen "${COMFY_BIND_HOST:-127.0.0.1}" --port "${COMFY_BIND_PORT:-8188}" \
        --input "${INPUT_DIR:-/workspace/ComfyUI/input}" \
        --output "${OUTPUT_DIR:-/workspace/ComfyUI/output}"
    elif [ -f "launch.py" ]; then
      python launch.py --listen "${COMFY_BIND_HOST:-127.0.0.1}" --port "${COMFY_BIND_PORT:-8188}" \
        --input "${INPUT_DIR:-/workspace/ComfyUI/input}" \
        --output "${OUTPUT_DIR:-/workspace/ComfyUI/output}"
    else
      echo "ComfyUI folder exists but no main.py/launch.py found; skipping ComfyUI start."
      sleep 3600
    fi
  ) &

  # Wait for Comfy to accept connections (best-effort; don’t block forever)
  echo "Waiting for ComfyUI to become ready..."
  PY_WAIT="
import time, sys
import urllib.request
base=f'http://{sys.argv[1]}:{sys.argv[2]}'
for i in range(120):
    try:
        urllib.request.urlopen(base + '/system_stats', timeout=2).read()
        print('ComfyUI is up'); sys.exit(0)
    except Exception:
        time.sleep(1)
print('ComfyUI did not come up in time'); sys.exit(0)
"
  python -c \"$PY_WAIT\" \"${COMFY_BIND_HOST:-127.0.0.1}\" \"${COMFY_BIND_PORT:-8188}\"
else
  echo "No /workspace/ComfyUI directory found; running handler-only."
fi

# Launch the RunPod handler (FastAPI/Flask embedded by runpod/serverless)
echo "Starting RunPod handler on port ${RP_HANDLER_PORT:-8000}"
python -m runpod | tee /tmp/handler.log
