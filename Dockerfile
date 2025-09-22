# ===== Base: stable CUDA 12.1.1 + cuDNN on Ubuntu 22.04 =====
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

# Keep apt non-interactive
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Core OS deps + Python + useful tools
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.10 python3-pip python3-venv \
        git curl wget ca-certificates \
        ffmpeg libgl1 \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && ln -sf /usr/bin/pip3    /usr/local/bin/pip \
    && rm -rf /var/lib/apt/lists/*

# Workdir
WORKDIR /workspace

# Python deps
COPY requirements.txt .
RUN python -m pip install --upgrade pip && \
    pip install -r requirements.txt

# App code
COPY . .

# ComfyUI ports (internal) + handler port
EXPOSE 8188 8000

# Default envs the handler expects
ENV RP_HANDLER_PORT=8000 \
    COMFY_MODE=production \
    INPUT_DIR=/workspace/ComfyUI/input \
    OUTPUT_DIR=/workspace/ComfyUI/output

# Start the RunPod serverless handler (handler.py: run)
CMD ["python", "-u", "-m", "runpod.serverless.start", "--handler", "handler.run"]
