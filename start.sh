#!/usr/bin/env bash
set -euo pipefail

export INPUT_DIR="${INPUT_DIR:-/workspace/inputs}"
export OUTPUT_DIR="${OUTPUT_DIR:-/workspace/outputs}"
export RP_HANDLER_PORT="${RP_HANDLER_PORT:-8000}"
export COMFY_HOST="127.0.0.1"
export COMFY_PORT="8188"

mkdir -p "$INPUT_DIR" "$OUTPUT_DIR"

# Put Comfy models under /workspace/ComfyUI/models by symlinking the shared model root if present
MODEL_ROOT="/runpod-volume/models"
if [ -d "$MODEL_ROOT" ]; then
  mkdir -p /workspace/ComfyUI/models
  for d in clip diffusers ipadapter unet vae clip_vision t5;text_encoders checkpoints loras controlnet upscale_models; do
    [ -d "$MODEL_ROOT/$d" ] && mkdir -p "/workspace/ComfyUI/models/$d" \
      && find "$MODEL_ROOT/$d" -maxdepth 1 -type f -exec ln -sf "{}" "/workspace/ComfyUI/models/$d/" \; || true
  done
fi

# Start ComfyUI (background)
python3 /workspace/ComfyUI/main.py \
  --listen 0.0.0.0 \
  --port "${COMFY_PORT}" \
  --output-directory "${OUTPUT_DIR}" \
  --input-directory "${INPUT_DIR}" \
  >/workspace/comfy.log 2>&1 &

COMFY_PID=$!

# Wait for ComfyUI to be reachable
echo "Waiting for ComfyUI on http://${COMFY_HOST}:${COMFY_PORT} ..."
for i in {1..120}; do
  if curl -fsS "http://${COMFY_HOST}:${COMFY_PORT}/system_stats" >/dev/null 2>&1; then
    echo "ComfyUI is up."
    break
  fi
  sleep 1
  if ! kill -0 "$COMFY_PID" 2>/dev/null; then
    echo "ComfyUI process exited unexpectedly. Logs:"
    tail -n 300 /workspace/comfy.log || true
    exit 1
  fi
  if [ "$i" -eq 120 ]; then
    echo "ComfyUI did not start in time."
    tail -n 300 /workspace/comfy.log || true
    exit 1
  fi
done

# Start the RunPod handler (foreground)
exec python3 /workspace/handler.py
