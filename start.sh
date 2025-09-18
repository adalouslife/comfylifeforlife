#!/usr/bin/env bash
set -euo pipefail

# start.sh — entrypoint for the RunPod serverless worker
# All files are expected in /workspace (repo root)

COMFY_DIR="/workspace/ComfyUI"
MODEL_DOWNLOADER="/workspace/download_models.sh"
CUSTOM_NODE_INSTALLER="/workspace/install_custom_nodes.py"
HANDLER="/workspace/handler.py"
EXTRA_YAML_SRC="/workspace/comfyui/extra_model_paths.yaml"
EXTRA_YAML_DST="${COMFY_DIR}/extra_model_paths.yaml"

echo "[start] worker starting at $(date)"

# 0) Ensure ComfyUI knows about /runpod-volume/models via extra_model_paths.yaml
if [ -f "${EXTRA_YAML_SRC}" ]; then
  echo "[start] syncing extra_model_paths.yaml into ComfyUI..."
  cp -f "${EXTRA_YAML_SRC}" "${EXTRA_YAML_DST}" || echo "[warn] failed to copy extra_model_paths.yaml"
else
  echo "[warn] comfyui/extra_model_paths.yaml not found — ComfyUI may not see /runpod-volume/models"
fi

# 1) Run model downloader
if [ -x "${MODEL_DOWNLOADER}" ]; then
  echo "[start] running download_models.sh ..."
  bash "${MODEL_DOWNLOADER}" || { echo "[error] download_models.sh failed"; exit 1; }
else
  echo "[warn] no download_models.sh found"
fi

# 2) Install custom nodes (optional)
if [ -f "${CUSTOM_NODE_INSTALLER}" ]; then
  echo "[start] installing custom nodes ..."
  python3 "${CUSTOM_NODE_INSTALLER}" || { echo "[error] install_custom_nodes.py failed"; exit 1; }
else
  echo "[start] no install_custom_nodes.py found, skipping"
fi

# 3) Start ComfyUI headless
if [ -d "${COMFY_DIR}" ]; then
  echo "[start] launching ComfyUI ..."
  mkdir -p /workspace/outputs /workspace/inputs
  python3 "${COMFY_DIR}/main.py" \
    --listen 0.0.0.0 --port 8188 \
    --output-directory /workspace/outputs \
    --input-directory /workspace/inputs > /workspace/comfyui.log 2>&1 &
  COMFY_PID=$!
  echo "[start] ComfyUI PID=${COMFY_PID}"
else
  echo "[error] ComfyUI folder not found at ${COMFY_DIR}"
  exit 1
fi

# 4) Finally start RunPod handler (blocks here)
echo "[start] launching runpod handler ..."
exec python3 "${HANDLER}"
