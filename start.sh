#!/usr/bin/env bash
set -euo pipefail

# Print quick env for debugging
echo "Starting ComfyUI on ${COMFY_HOST}:${COMFY_PORT}"
echo "INPUT_DIR=${INPUT_DIR}"
echo "OUTPUT_DIR=${OUTPUT_DIR}"

# Start ComfyUI in background
cd /workspace/ComfyUI
python main.py --listen --port "${COMFY_PORT}" --disable-auto-launch >/workspace/comfy.log 2>&1 &
COMFY_PID=$!

# Wait for ComfyUI to become responsive
echo "Waiting for ComfyUI to come up..."
for i in {1..60}; do
  if curl -fsS "http://${COMFY_HOST}:${COMFY_PORT}/system_stats" >/dev/null 2>&1; then
    echo "ComfyUI is up."
    break
  fi
  sleep 2
done

# If it still isn't up, show last lines and exit hard
if ! curl -fsS "http://${COMFY_HOST}:${COMFY_PORT}/system_stats" >/dev/null 2>&1; then
  echo "ComfyUI did not come up. Last 200 lines of comfy.log:"
  tail -n 200 /workspace/comfy.log || true
  exit 1
fi

# Launch the RunPod handler (this blocks and runs the job loop)
cd /workspace
echo "Starting handler..."
exec python -u /workspace/handler.py
