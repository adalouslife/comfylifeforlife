# --------- Base: CUDA runtime (works with RTX 40xx) ----------
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    FORCE_CUDA=1

SHELL ["/bin/bash", "-lc"]

# System packages (with retries) + make sure python3/pip/git/curl/ffmpeg exist
RUN set -euxo pipefail && \
    apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-distutils python3-venv python3-pip \
      git curl ca-certificates wget unzip ffmpeg \
      libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/* && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    python3 -m pip install --upgrade pip

WORKDIR /workspace

# --------- Torch (CUDA 12.1 wheels) ----------
# Pin to known-good versions and use the cu121 index explicitly.
RUN pip install --extra-index-url https://download.pytorch.org/whl/cu121 \
      "torch==2.3.1+cu121" "torchvision==0.18.1+cu121" "torchaudio==2.3.1+cu121"

# --------- ComfyUI (pinned clone) ----------
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git /workspace/ComfyUI

# --------- Python deps for handler ----------
COPY requirements.txt /workspace/requirements.txt
# Keep requirements minimal to reduce breakage; torch already installed above.
# Your requirements.txt should NOT include torch/torchvision/torchaudio again.
RUN pip install -r /workspace/requirements.txt

# --------- Your repo content ----------
COPY . /workspace

# Ensure the start script is executable and has LF endings
RUN sed -i 's/\r$//' /workspace/start.sh && chmod +x /workspace/start.sh

# Defaults (override in RunPod if needed)
ENV COMFY_HOST=127.0.0.1 \
    COMFY_PORT=8188 \
    INPUT_DIR=/workspace/ComfyUI/input \
    OUTPUT_DIR=/workspace/ComfyUI/output \
    WORKFLOW_PATH=/workspace/comfyui/workflows/APIAutoFaceACE.json \
    STORAGE_DIR=/runpod-volume

EXPOSE 8188

# Health: curl will be available for the bootstrap wait loop
CMD ["bash", "-lc", "/workspace/start.sh"]
