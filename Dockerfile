# Base image with CUDA and Ubuntu 22.04
FROM runpod/base:0.5.4-cuda12.1.1-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-lc"]

# System deps (curl + ffmpeg + git + python3 + pip)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      git curl ca-certificates wget unzip ffmpeg \
      libgl1 libglib2.0-0 python3 python3-pip && \
    rm -rf /var/lib/apt/lists/* && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    python3 -m pip install --upgrade pip

WORKDIR /workspace

# Install Torch (CUDA 12.1 wheels)
RUN pip install --extra-index-url https://download.pytorch.org/whl/cu121 \
      "torch==2.3.1+cu121" "torchvision==0.18.1+cu121" "torchaudio==2.3.1+cu121"

# Clone ComfyUI once (pinned)
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git /workspace/ComfyUI

# Requirements for the handler and utilities
COPY requirements.txt /workspace/requirements.txt
RUN pip install -r /workspace/requirements.txt

# Copy the rest of your repo
COPY . /workspace

# Make scripts executable
RUN chmod +x /workspace/start.sh || true

# Default environment (can be overridden in RunPod UI)
ENV COMFY_PORT=8188
ENV INPUT_DIR=/workspace/ComfyUI/input
ENV OUTPUT_DIR=/workspace/ComfyUI/output
ENV WORKFLOW_PATH=/workspace/comfyui/workflows/APIAutoFaceACE.json
ENV STORAGE_DIR=/runpod-volume

EXPOSE 8188

# Start both Comfy and the handler
CMD ["bash", "-lc", "/workspace/start.sh"]
