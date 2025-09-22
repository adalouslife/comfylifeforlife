# Public, stable, GPU-ready base with Python available as "python"
FROM pytorch/pytorch:2.3.1-cuda12.1-cudnn8-runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates ffmpeg tini \
    && rm -rf /var/lib/apt/lists/*

# Make sure "python" exists (this image already has it, but the link keeps us safe)
RUN ln -sf /usr/bin/python /usr/local/bin/python || true

WORKDIR /workspace

# --- ComfyUI ---
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git /workspace/ComfyUI
# Create IO dirs that we’ll use
RUN mkdir -p /workspace/ComfyUI/input /workspace/ComfyUI/output /workspace/ComfyUI/models

# Upgrade pip and install ComfyUI deps
RUN python -m pip install --upgrade pip && \
    python -m pip install -r /workspace/ComfyUI/requirements.txt

# --- Worker deps (runpod + HTTP clients) ---
# (If you keep a requirements.txt in repo, we’ll still install it too.)
COPY requirements.txt /workspace/requirements.txt
RUN python -m pip install -r /workspace/requirements.txt || true
RUN python -m pip install runpod==1.7.13 requests urllib3==2.2.2 websocket-client pillow tqdm

# Copy your handler and startup script
COPY handler.py /workspace/handler.py
COPY start.sh  /workspace/start.sh
RUN chmod +x /workspace/start.sh

# (Optional) copy your workflow placeholder into container root, if you want later wiring
# COPY comfyui/workflows/APIAutoFaceACE.json /workspace/APIAutoFaceACE.json

# Env for ComfyUI + handler
ENV COMFY_HOST=127.0.0.1
ENV COMFY_PORT=8188
ENV INPUT_DIR=/workspace/ComfyUI/input
ENV OUTPUT_DIR=/workspace/ComfyUI/output
ENV RP_HANDLER_PORT=8000

EXPOSE 8188 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/workspace/start.sh"]
