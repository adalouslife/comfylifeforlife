# --------------------------
# ComfyUI RunPod Serverless
# --------------------------

# Valid, existing RunPod base image (CUDA 12.1, Python 3.10, Torch 2.1.1)
FROM runpod/pytorch:2.1.1-py3.10-cuda12.1.1-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKSPACE=/workspace

WORKDIR $WORKSPACE

# System deps (git/curl/wget/zip + common libs many nodes need)
RUN apt-get update && apt-get install -y \
    git wget curl zip ffmpeg libgl1 \
    && rm -rf /var/lib/apt/lists/*

# ComfyUI
RUN git clone https://github.com/comfyanonymous/ComfyUI.git ComfyUI

# Python deps (pin requests to avoid surprises)
RUN pip install --upgrade pip setuptools wheel \
 && pip install -r ComfyUI/requirements.txt \
 && pip install runpod requests==2.32.3

# Your repo files -> /workspace
COPY . $WORKSPACE

# Ensure scripts are executable
RUN chmod +x $WORKSPACE/*.sh || true

# ComfyUI port (optional for debugging)
EXPOSE 8188

# Start sequence
CMD ["bash", "start.sh"]
