# ========= Base image =========
# A stable PyTorch + CUDA 12.1 image that works well on RunPod GPU workers.
FROM runpod/pytorch:2.5.1-py3.10-cuda12.1

# Make logs flush immediately
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ========= OS deps =========
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git wget curl ca-certificates ffmpeg libgl1 libglib2.0-0 unzip \
 && rm -rf /var/lib/apt/lists/*

# ========= Workspace =========
WORKDIR /workspace

# Create common dirs (also used as default bind points for network volumes)
RUN mkdir -p /workspace/inputs /workspace/outputs /workspace/comfyui/workflows

# ========= Clone ComfyUI =========
# If you want to pin a commit for reproducibility, replace "master" with a commit hash below.
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git /workspace/ComfyUI

# ========= Python deps =========
# 1) Our service deps (RunPod SDK, httpx, etc.)
COPY requirements.txt /workspace/requirements.txt
RUN pip install --no-cache-dir -r /workspace/requirements.txt

# 2) ComfyUI deps
RUN pip install --no-cache-dir -r /workspace/ComfyUI/requirements.txt

# ========= Project files =========
# (Everything you maintain in your repo)
COPY start.sh /workspace/start.sh
COPY handler.py /workspace/handler.py
COPY install_custom_nodes.py /workspace/install_custom_nodes.py
COPY custom_nodes.txt /workspace/custom_nodes.txt
COPY download_models.sh /workspace/download_models.sh
# include your workflow(s)
COPY comfyui/workflows /workspace/comfyui/workflows

# ========= Custom nodes + models (optional at build-time) =========
# If your build cache is warm, keeping these is convenient. Otherwise you can comment them out
# to speed up builds and let the serverless cold-start do installation/downloads instead.
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

# NOTE:
# The start.sh script launches ComfyUI in the background, then starts the RunPod worker (handler.py)
ENTRYPOINT ["/workspace/start.sh"]
