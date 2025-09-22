# ---------- Base: Official, stable, CUDA 12.1 runtime on Ubuntu 22.04 ----------
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

# Avoid interactive tzdata, etc.
ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-lc"]

# ---------- OS deps ----------
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.10 python3.10-venv python3-pip \
    git curl ca-certificates \
    libgl1 libglib2.0-0 libsm6 libxrender1 libxext6 \
    ffmpeg wget unzip \
 && rm -rf /var/lib/apt/lists/*

# Make python available as `python` + `pip`
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.10 1 && \
    update-alternatives --install /usr/bin/pip pip /usr/bin/pip3 1

# ---------- Workspace ----------
WORKDIR /workspace

# (Optional) Create comfy dirs we’ll use
ENV COMFY_ROOT=/workspace/ComfyUI
ENV INPUT_DIR=/workspace/ComfyUI/input
ENV OUTPUT_DIR=/workspace/ComfyUI/output
RUN mkdir -p "${COMFY_ROOT}" "${INPUT_DIR}" "${OUTPUT_DIR}"

# ---------- Python venv (optional but clean) ----------
RUN python -m venv /opt/venv && \
    echo 'source /opt/venv/bin/activate' >> /etc/bash.bashrc
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# ---------- Install serverless runtime + GPU stack ----------
# Pin to CUDA 12.1 wheels
RUN pip install --upgrade pip wheel setuptools && \
    pip install --index-url https://download.pytorch.org/whl/cu121 \
        torch==2.4.0 torchvision==0.19.0 torchaudio==2.4.0 && \
    pip install xformers==0.0.27.post2 --extra-index-url https://download.pytorch.org/whl/cu121 && \
    pip install runpod==1.7.13 uvicorn fastapi aiohttp requests

# ---------- Get ComfyUI ----------
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git "${COMFY_ROOT}"

# ComfyUI requirements (lean + robust)
# ComfyUI’s requirements.txt is light; most heavy deps covered above.
RUN pip install -r "${COMFY_ROOT}/requirements.txt" || true

# ---------- Your repo files ----------
# Copy only what we need; keep the context small
COPY handler.py /workspace/app/handler.py
COPY start.sh   /workspace/app/start.sh
RUN chmod +x /workspace/app/start.sh

# If you keep these in repo, copy them; if not, this is safe to omit:
# COPY download_models.sh /workspace/app/download_models.sh
# RUN chmod +x /workspace/app/download_models.sh && /workspace/app/download_models.sh

# ---------- Ports & env ----------
ENV COMFY_MODE=production
ENV COMFY_PORT=8188
ENV HOST=0.0.0.0
ENV RP_HANDLER_PORT=8000

EXPOSE 8188
EXPOSE 8000

# ---------- Health (optional but helps) ----------
HEALTHCHECK --interval=30s --timeout=10s --retries=10 CMD curl -sf http://127.0.0.1:${COMFY_PORT}/system_stats || exit 1

# ---------- Start ----------
CMD ["/workspace/app/start.sh"]
