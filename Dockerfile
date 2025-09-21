# ---- Base: CUDA 12.1 + cuDNN on Ubuntu 22.04 ----
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

# Noninteractive apt
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKSPACE=/workspace

WORKDIR $WORKSPACE

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.10 python3-pip python3.10-venv \
    git wget curl zip ca-certificates \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.10 /usr/bin/python \
    && ln -sf /usr/bin/pip3 /usr/bin/pip

# ComfyUI
RUN git clone https://github.com/comfyanonymous/ComfyUI.git ComfyUI

# Make sure we have a modern pip
RUN python -m pip install --upgrade pip setuptools wheel

# Install PyTorch w/ CUDA 12.1 wheels (aligned with the base image)
RUN pip install --index-url https://download.pytorch.org/whl/cu121 \
    torch==2.3.1 torchvision==0.18.1 torchaudio==2.3.1

# ComfyUI requirements (will reuse the already installed torch)
RUN pip install -r ComfyUI/requirements.txt

# Serverless + API bits
RUN pip install runpod fastapi uvicorn requests

# Copy your repo (handler.py, start.sh, comfyui/workflows, etc.)
COPY . $WORKSPACE

# Ensure scripts are executable
RUN chmod +x $WORKSPACE/*.sh || true

# Defaults that make local + serverless runs smooth; override in RunPod env if needed
ENV COMFY_ROOT=/workspace/ComfyUI \
    WORKFLOW_PATH=/workspace/comfylifeforlife/comfyui/workflows/APIAutoFaceACE.json \
    OUTPUT_DIR=/workspace/output \
    TMP_DIR=/workspace/tmp \
    COMFY_PORT=8188 \
    HANDLER_PORT=8000

# Create output dirs so writing won't fail
RUN mkdir -p "$OUTPUT_DIR" "$TMP_DIR"

EXPOSE 8188
EXPOSE 8000

# Start ComfyUI then the serverless handler
CMD ["bash", "start.sh"]
