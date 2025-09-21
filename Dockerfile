# ========= Base image =========
# CUDA 12.1 + cuDNN on Ubuntu 22.04 (stable, public, and available)
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

# Avoid interactive tzdata, etc.
ENV DEBIAN_FRONTEND=noninteractive

# ========= System deps =========
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.10 python3.10-venv python3-pip python3-dev \
    git curl wget ca-certificates ffmpeg libglib2.0-0 libsm6 libxext6 libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# Ensure python/pip are the expected names
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.10 1 && \
    python -m pip install --upgrade pip

# ========= Workdir layout =========
WORKDIR /workspace
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ========= Clone ComfyUI =========
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git /workspace/ComfyUI

# ========= Copy your repo code =========
# (Assumes Dockerfile is at repo root)
COPY . /workspace/app

# ========= Python env & install =========
# 1) ComfyUI requirements
RUN pip install --no-cache-dir -r /workspace/ComfyUI/requirements.txt

# 2) Your repo requirements (handler + any utils)
#    If you don’t have extra requirements, keep an empty file or remove this line.
RUN if [ -f /workspace/app/requirements.txt ]; then pip install --no-cache-dir -r /workspace/app/requirements.txt; fi

# ========= Custom nodes + models (optional) =========
# These scripts are already in your repo. They will no-op if not needed.
RUN if [ -f /workspace/app/install_custom_nodes.py ]; then python /workspace/app/install_custom_nodes.py; fi
RUN if [ -x /workspace/app/download_models.sh ]; then bash /workspace/app/download_models.sh; fi

# ========= Runtime env =========
# Internal-only ComfyUI (no external exposure), handler talks to it via 127.0.0.1
ENV COMFY_HOST=127.0.0.1 \
    COMFY_PORT=8188 \
    COMFY_MODE=production \
    INPUT_DIR=/workspace/inputs \
    OUTPUT_DIR=/workspace/outputs \
    WORKFLOW_PATH=/workspace/app/comfyui/workflows/APIAutoFaceACE.json

# Create basic dirs so first run never fails
RUN mkdir -p "${INPUT_DIR}" "${OUTPUT_DIR}"

# ========= Startup script =========
# Your start.sh should: 
#   - launch ComfyUI in the background with --listen 127.0.0.1 --port 8188
#   - wait for it to be ready, then
#   - launch: python -u /workspace/app/handler.py  (which calls runpod.serverless if you use that)
#
# If you prefer, keep controlling everything here directly; but using start.sh keeps it simple.
RUN chmod +x /workspace/app/start.sh

# ========= Health probe (optional) =========
# If you implement /health in handler or a quick port check, you can add HEALTHCHECK. Safe to omit.
# HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -sf http://127.0.0.1:3000/health || exit 1

# ========= Default command =========
# IMPORTANT: No exposed ports needed for Serverless; everything runs inside the pod.
CMD ["/bin/bash", "-lc", "/workspace/app/start.sh"]
