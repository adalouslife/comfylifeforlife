# === Base GPU image with CUDA 12.1, Ubuntu 22.04 ===
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_THREADPOOL_SIZE=64 \
    COMFY_HOST=127.0.0.1 \
    COMFY_PORT=8188 \
    STORAGE_DIR=/runpod-volume \
    WORKDIR=/workspace

WORKDIR ${WORKDIR}

# --- OS deps ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    git curl wget ca-certificates ffmpeg \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# --- Python ---
RUN python3 -m pip install --upgrade pip setuptools wheel

# --- Torch (CUDA 12.1 wheels) ---
RUN pip install --extra-index-url https://download.pytorch.org/whl/cu121 \
    torch==2.3.1 torchvision==0.18.1 torchaudio==2.3.1

# --- ComfyUI ---
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git ${WORKDIR}/ComfyUI
RUN pip install -r ${WORKDIR}/ComfyUI/requirements.txt || true

# --- Your repo files ---
# Copy only what we need for the worker
COPY requirements.txt ${WORKDIR}/requirements.txt
RUN pip install -r ${WORKDIR}/requirements.txt

COPY handler.py ${WORKDIR}/handler.py
COPY download_models.sh ${WORKDIR}/download_models.sh
COPY install_custom_nodes.py ${WORKDIR}/install_custom_nodes.py
COPY custom_nodes.txt ${WORKDIR}/custom_nodes.txt
# Optional: your workflow(s)
COPY comfyui/workflows ${WORKDIR}/comfyui/workflows

# --- Prepare models and custom nodes (best-effort; skip on failure) ---
RUN chmod +x ${WORKDIR}/download_models.sh && bash ${WORKDIR}/download_models.sh || true
RUN python3 ${WORKDIR}/install_custom_nodes.py || true

# --- Expose nothing; this is a queue worker, not HTTP ---
# EXPOSE is not required

# --- Entry point: start the Serverless queue worker ---
CMD ["python3", "-u", "/workspace/handler.py"]
