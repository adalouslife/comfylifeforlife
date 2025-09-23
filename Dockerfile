# ---- Base: CUDA + Python on Ubuntu 22.04 ----
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COMFY_PORT=8188 \
    COMFY_HOST=0.0.0.0

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip python3-dev \
    git curl ca-certificates ffmpeg libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Make /usr/bin/python point to python3 for tools that call "python"
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3 1

# ---- Workspace layout ----
WORKDIR /workspace
RUN mkdir -p /workspace/ComfyUI /workspace/models /workspace/inputs /workspace/outputs /app

# ---- ComfyUI ----
RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git /workspace/ComfyUI

# Python venv (keeps deps clean)
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# ---- Copy app files (handler, shell, reqs, installers) ----
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY install_custom_nodes.py /app/install_custom_nodes.py
COPY custom_nodes.txt /app/custom_nodes.txt
RUN python /app/install_custom_nodes.py || true

COPY download_models.sh /app/download_models.sh
RUN chmod +x /app/download_models.sh && /app/download_models.sh || true

# so handler can find ComfyUI & models
ENV COMFY_DIR=/workspace/ComfyUI \
    INPUT_DIR=/workspace/inputs \
    OUTPUT_DIR=/workspace/outputs

# your workflow folder lives inside repo ComfyUI dir (mount/commit yours as needed)
# If your workflow is in the repo at comfyui/workflows/APIAutoFaceACE.json,
# copy it into ComfyUI/workflows:
COPY comfyui/workflows /workspace/ComfyUI/workflows

# ---- App runtime files ----
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

COPY handler.py /app/handler.py

# Expose ComfyUI (internal)
EXPOSE 8188

# RunPod looks for this by default (python -m runpod)
CMD ["/app/start.sh"]
