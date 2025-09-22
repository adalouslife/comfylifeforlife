# =========================
# Comfy Faceswap Serverless
# =========================

# CUDA base that matches RunPod GPUs
FROM runpod/serverless:gpu-cuda12.1.1

# Non-interactive APT
ENV DEBIAN_FRONTEND=noninteractive

# Basic OS deps + Python tooling + ffmpeg, git, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-dev python3-pip python3-venv python3-distutils python3-setuptools python3-wheel \
    ca-certificates curl wget git git-lfs \
    ffmpeg libgl1 libglib2.0-0 libxext6 libxrender1 libsm6 \
    && rm -rf /var/lib/apt/lists/*

# Ensure `python` exists (RunPod health/tests sometimes assume it)
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3 10

# Workdir
WORKDIR /workspace

# Copy repo content
COPY . /workspace

# Make scripts executable
RUN chmod +x /workspace/start.sh /workspace/download_models.sh

# Install Python deps first (layer cache)
RUN pip install --no-cache-dir --upgrade pip \
 && if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi

# (Optional) install custom nodes + models if present
# Won't fail the build if those scripts are not required.
RUN /bin/bash -lc 'if [ -f install_custom_nodes.py ]; then python install_custom_nodes.py || true; fi'
RUN /bin/bash -lc 'if [ -f download_models.sh ]; then ./download_models.sh || true; fi'

# RunPod handler port
ENV RP_HANDLER_PORT=8000
# ComfyUI defaults
ENV COMFY_BIND_HOST=127.0.0.1
ENV COMFY_BIND_PORT=8188
ENV COMFY_MODE=production
ENV INPUT_DIR=/workspace/ComfyUI/input
ENV OUTPUT_DIR=/workspace/ComfyUI/output

# Expose handler port (RunPod sidecar will bind to this)
EXPOSE 8000

# Final command
CMD ["/workspace/start.sh"]
