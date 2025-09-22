#!/usr/bin/env bash
set -euo pipefail

# Ensure venv active for both CMD and RunPod’s worker
if [ -d "/opt/venv" ]; then
  source /opt/venv/bin/activate
fi

export COMFY_ROOT="${COMFY_ROOT:-/workspace/ComfyUI}"
export COMFY_PORT="${COMFY_PORT:-8188}"
export HOST="${HOST:-0.0.0.0}"

# Start ComfyUI in background; log to file for debugging
mkdir -p "${COMFY_ROOT}/logs"
python "${COMFY_ROOT}/main.py" \
  --listen \
  --port "${COMFY_PORT}" \
  --output-directory "${OUTPUT_DIR:-/workspace/ComfyUI/output}" \
  --input-directory  "${INPUT_DIR:-/workspace/ComfyUI/input}" \
  > "${COMFY_ROOT}/logs/comfy.out" 2>&1 &

# Tiny wait loop until 8188 is up
echo "Waiting for ComfyUI on ${HOST}:${COMFY_PORT} ..."
for i in {1..120}; do
  if curl -sf "http://127.0.0.1:${COMFY_PORT}/system_stats" >/dev/null; then
    echo "ComfyUI is up."
    break
  fi
  sleep 1
done

# Launch RunPod serverless handler (FastAPI/ASGI)
# handler.py exposes `handler = rp_serverless.run(...)` most likely
# but we’ll just run uvicorn on the provided RP_HANDLER_PORT.
export RP_HANDLER_PORT="${RP_HANDLER_PORT:-8000}"
echo "Starting handler on :${RP_HANDLER_PORT}"
python -m uvicorn handler:app --host 0.0.0.0 --port "${RP_HANDLER_PORT}"
