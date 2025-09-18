#!/usr/bin/env bash
set -euo pipefail

# -------------------------
# download_models.sh
# Populate models for ACE++ workflow
# Run at container start inside RunPod Serverless
# -------------------------

MODEL_ROOT="/runpod-volume/models"
CURL_OPTS="-L --fail --retry 5 --retry-delay 2 --create-dirs"

echo "[download_models] START"
echo "[download_models] MODEL_ROOT = ${MODEL_ROOT}"

# Ensure all required folders exist
mkdir -p \
  "${MODEL_ROOT}/unet" \
  "${MODEL_ROOT}/loras" \
  "${MODEL_ROOT}/vae" \
  "${MODEL_ROOT}/checkpoints" \
  "${MODEL_ROOT}/diffusion_models" \
  "${MODEL_ROOT}/controlnet" \
  "${MODEL_ROOT}/clip" \
  "${MODEL_ROOT}/clip_vision" \
  "${MODEL_ROOT}/embeddings" \
  "${MODEL_ROOT}/upscale_models"

download() {
  local url="$1"
  local dest="$2"
  if [ -f "$dest" ]; then
    echo "[skip] $dest already exists"
  else
    echo "[download] $url -> $dest"
    curl $CURL_OPTS -o "$dest" "$url"
    echo "[done] $dest"
  fi
}

# -------------------------
# UNet (Diffusion Model)
# -------------------------
download \
  "https://huggingface.co/jackzheng/flux-fill-FP8/resolve/main/fluxFillFP8_v10.safetensors" \
  "${MODEL_ROOT}/unet/flux_fill_fp8.safetensors"

# -------------------------
# Loras
# -------------------------
download \
  "https://huggingface.co/ali-vilab/ACE_Plus/resolve/main/portrait/comfyui_portrait_lora64.safetensors" \
  "${MODEL_ROOT}/loras/comfyui_portrait_lora64.safetensors"

download \
  "https://huggingface.co/camenduru/FLUX.1-dev/resolve/fc63f3204a12362f98c04bc4c981a06eb9123eee/FLUX.1-Turbo-Alpha.safetensors" \
  "${MODEL_ROOT}/loras/flux_turbo_alpha.safetensors"

# -------------------------
# VAE
# -------------------------
download \
  "https://huggingface.co/lovis93/testllm/resolve/main/ae.safetensors" \
  "${MODEL_ROOT}/vae/ae.safetensors"

echo "[download_models] ✅ All models ensured"
