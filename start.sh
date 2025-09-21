#!/usr/bin/env bash
set -euo pipefail

export PYTHONUNBUFFERED=1

# ----------------------------
# Core ports (same as before)
# ----------------------------
export COMFY_HOST="${COMFY_HOST:-127.0.0.1}"
export COMFY_PORT="${COMFY_PORT:-8188}"
export RP_HANDLER_PORT="${RP_HANDLER_PORT:-8000}"

# ----------------------------
# Optional storage wiring
# ----------------------------
# If you attached a Network Volume, RunPod typically mounts it at /runpod-volume.
# We'll support either:
#   - STORAGE_DIR env var (recommended to set to /runpod-volume), OR
#   - auto-detect /runpod-volume if it exists.
#
# We will symlink ComfyUI's models/ and output/ into the storage so assets persist.
# If the storage is missing, we do nothing (no breakage).
COMFY_ROOT="/workspace/ComfyUI"
MODELS_DIR="${COMFY_ROOT}/models"
OUTPUT_DIR="${COMFY_ROOT}/output"

# Choose a storage root, prefer env if provided
CANDIDATE_STORAGE="${STORAGE_DIR:-}"
if [[ -z "${CANDIDATE_STORAGE}" ]]; then
  if [[ -d "/runpod-volume" ]]; then
    CANDIDATE_STORAGE="/runpod-volume"
  fi
fi

if [[ -n "${CANDIDATE_STORAGE}" && -d "${CANDIDATE_STORAGE}" && -w "${CANDIDATE_STORAGE}" ]]; then
  echo "[start.sh] Using storage at: ${CANDIDATE_STORAGE}"

  # Create comfy folders in storage
  mkdir -p "${CANDIDATE_STORAGE}/comfyui-models"
  mkdir -p "${CANDIDATE_STORAGE}/comfyui-output"

  # Ensure Comfy root exists
  mkdir -p "${COMFY_ROOT}"

  # If Comfy has real dirs already, and not symlinks, keep them but prefer storage via symlink
  # Move any non-empty existing output to storage once (best-effort; ignore errors)
  if [[ -d "${OUTPUT_DIR}" && ! -L "${OUTPUT_DIR}" ]]; then
    shopt -s nullglob dotglob
    if compgen -G "${OUTPUT_DIR}/*" > /dev/null; then
      echo "[start.sh] Moving existing output/ contents to storage..."
      mv "${OUTPUT_DIR}/"* "${CANDIDATE_STORAGE}/comfyui-output/" || true
    fi
    rm -rf "${OUTPUT_DIR}"
  fi

  # DO NOT move models automatically (users often mount their own); just wire the path.
  if [[ -d "${MODELS_DIR}" && ! -L "${MODELS_DIR}" ]]; then
    # leave any existing files in place; if user wants them persisted they can copy later
    :
  fi

  # Create symlinks (idempotent)
  if [[ ! -e "${OUTPUT_DIR}" ]]; then
    ln -s "${CANDIDATE_STORAGE}/comfyui-output" "${OUTPUT_DIR}"
  fi

  if [[ ! -e "${MODELS_DIR}" && -d "${CANDIDATE_STORAGE}/comfyui-models" ]]; then
    ln -s "${CANDIDATE_STORAGE}/comfyui-models" "${MODELS_DIR}"
  fi

  echo "[start.sh] Symlinks set:"
  ls -l "${MODELS_DIR}" || true
  ls -l "${OUTPUT_DIR}" || true
else
  echo "[start.sh] No writable storage detected (STORAGE_DIR unset and /runpod-volume not present). Skipping wiring."
fi

# ----------------------------
# Start ComfyUI (headless)
# ----------------------------
echo "[start.sh] Launching ComfyUI on ${COMFY_HOST}:${COMFY_PORT} ..."
python -u /workspace/ComfyUI/main.py --listen 0.0.0.0 --port "${COMFY_PORT}" --disable-auto-open-browser >/tmp/comfy.log 2>&1 &

# Small delay to reduce races
sleep 2

# ----------------------------
# Start FastAPI handler
# ----------------------------
echo "[start.sh] Launching FastAPI handler on 0.0.0.0:${RP_HANDLER_PORT} ..."
uvicorn handler:app --host 0.0.0.0 --port "${RP_HANDLER_PORT}"
