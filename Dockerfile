# --------------------------------------------
# ComfyUI RunPod Serverless Worker (FaceSwap)
# --------------------------------------------

# 1) Base image with CUDA + Python (matches RunPod GPU environment)
FROM runpod/base:0.4.0-cuda12.1.105

# 2) Environment setup
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKSPACE=/workspace \
    COMFYUI_ROOT=/workspace/ComfyUI \
    COMFYUI_INPUT_DIR=/workspace/ComfyUI/input \
    COMFYUI_OUTPUT_DIR=/workspace/ComfyUI/output \
    COMFYUI_MODELS_PATH=/workspace/ComfyUI/models \
    WORKFLOW_FILE=/app/workflows/faceswap_api.json

WORKDIR $WORKSPACE

# 3) Install system deps
RUN apt-get update && apt-get install -y \
    git wget curl zip \
 && rm -rf /var/lib/apt/lists/*

# 4) Install ComfyUI
RUN git clone https://github.com/comfyanonymous/ComfyUI.git ComfyUI

# 5) Install Python deps
RUN pip install --upgrade pip setuptools wheel \
 && pip install -r ComfyUI/requirements.txt \
 && pip install runpod requests

# 6) Copy repo files
# (Assumes your repo has handler.py, start.sh, workflows/, etc.)
COPY . /app
WORKDIR /app

# Ensure shell scripts are executable
RUN chmod +x /app/*.sh

# 7) Expose ComfyUI port (for internal use only)
EXPOSE 8188

# 8) Entrypoint
CMD ["bash", "start.sh"]
