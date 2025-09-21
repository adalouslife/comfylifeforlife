# Base CUDA runtime (works fine on RunPod GPU serverless)
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip python3-dev \
    git curl ca-certificates \
    libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# Workspace
WORKDIR /workspace

# --- ComfyUI ---
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git /workspace/ComfyUI

# Torch (CUDA 12.x)
RUN pip3 install --no-cache-dir --upgrade pip setuptools wheel && \
    pip3 install --no-cache-dir \
      torch==2.3.1+cu121 torchvision==0.18.1+cu121 torchaudio==2.3.1+cu121 \
      --index-url https://download.pytorch.org/whl/cu121

# ComfyUI requirements
RUN pip3 install --no-cache-dir -r /workspace/ComfyUI/requirements.txt

# App requirements
COPY requirements.txt /workspace/requirements.txt
RUN pip3 install --no-cache-dir -r /workspace/requirements.txt

# Your repo (workflows, scripts, handler, etc.)
COPY . /workspace

# Environment defaults (can be overridden in Serverless env)
# IMPORTANT: point WORKFLOW_PATH at the *repo* file you checked in.
ENV COMFY_ROOT=/workspace/ComfyUI \
    COMFY_PORT=8188 \
    HANDLER_PORT=8000 \
    WORKFLOW_PATH=/workspace/comfyui/workflows/APIAutoFaceACE.json \
    UPLOAD_PROVIDER=catbox

# Make scripts executable
RUN chmod +x /workspace/start.sh

# Start supervisor script that boots ComfyUI, waits for it, then starts FastAPI handler
ENTRYPOINT ["bash", "/workspace/start.sh"]
