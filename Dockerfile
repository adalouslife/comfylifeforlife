# Base image (CUDA 12.1 + Python) — this tag EXISTS
FROM runpod/base:0.6.1-cuda12.1.1

# Keep apt non-interactive
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

# OS deps and tini for 'clean' process handling
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget ca-certificates tini \
    libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# Workdir
WORKDIR /workspace

# ---- ComfyUI (optional but harmless for tests) ----
# Put ComfyUI where your scripts expect it
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git /workspace/ComfyUI

# Copy your repo files
COPY requirements.txt /workspace/requirements.txt
COPY start.sh /workspace/start.sh
COPY handler.py /workspace/handler.py
COPY install_custom_nodes.py /workspace/install_custom_nodes.py
COPY download_models.sh /workspace/download_models.sh
COPY custom_nodes.txt /workspace/custom_nodes.txt

# Permissions
RUN chmod +x /workspace/start.sh /workspace/download_models.sh

# Python deps
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install -r /workspace/requirements.txt && \
    # Always have runpod SDK available
    python3 -m pip install --no-cache-dir runpod

# Default envs (can be overridden in the endpoint)
ENV RP_HANDLER="handler" \
    RP_HANDLER_PORT="8000" \
    COMFY_PORT="8188" \
    COMFY_MODE="production" \
    INPUT_DIR="/workspace/ComfyUI/input" \
    OUTPUT_DIR="/workspace/ComfyUI/output"

# Make sure input/output directories exist
RUN mkdir -p ${INPUT_DIR} ${OUTPUT_DIR}

# Expose the ports (not strictly required for Serverless, but good hygiene)
EXPOSE 8000 8188

# Use tini as entrypoint
ENTRYPOINT ["/usr/bin/tini", "--"]

# Start your orchestrator (this should start ComfyUI and the handler)
CMD ["/workspace/start.sh"]
