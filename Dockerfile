# ========= Base image =========
# Official RunPod base image with CUDA 12.1 and Python 3.10
FROM runpod/base:0.4.0-cuda12.1.105

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ========= OS deps =========
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git wget curl ca-certificates ffmpeg libgl1 libglib2.0-0 unzip \
 && rm -rf /var/lib/apt/lists/*

# ========= Workspace =========
WORKDIR /workspace
RUN mkdir -p /workspace/inputs /workspace/outputs /workspace/comfyui/workflows

# ========= Clone ComfyUI =========
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git /workspace/ComfyUI

# ========= Python deps =========
COPY requirements.txt /workspace/requirements.txt
RUN pip install --no-cache-dir -r /workspace/requirements.txt
RUN pip install --no-cache-dir -r /workspace/ComfyUI/requirements.txt

# ========= Project files =========
COPY start.sh /workspace/start.sh
COPY handler.py /workspace/handler.py
COPY install_custom_nodes.py /workspace/install_custom_nodes.py
COPY custom_nodes.txt /workspace/custom_nodes.txt
COPY download_models.sh /workspace/download_models.sh
COPY comfyui/workflows /workspace/comfyui/workflows

# ========= Custom nodes + models (optional at build-time) =========
RUN --mount=type=cache,target=/root/.cache/pip \
    python /workspace/install_custom_nodes.py /workspace/custom_nodes.txt || true
RUN bash /workspace/download_models.sh || true

# ========= Permissions & entry =========
RUN chmod +x /workspace/start.sh

ENV COMFY_HOST=127.0.0.1 \
    COMFY_PORT=8188 \
    WORKFLOW_PATH=/workspace/comfyui/workflows/APIAutoFaceACE.json \
    INPUT_DIR=/workspace/inputs \
    OUTPUT_DIR=/workspace/outputs \
    UPLOAD_PROVIDER=catbox

ENTRYPOINT ["/workspace/start.sh"]
