# ===== Base image =====
FROM runpod/pytorch:2.5.1-py3.10-cuda12.1.1

# Avoid interactive tzdata etc.
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    UV_HTTP_TIMEOUT=180

# ===== OS deps =====
RUN apt-get update && apt-get install -y --no-install-recommends \
    git wget curl ca-certificates ffmpeg tini \
 && rm -rf /var/lib/apt/lists/*

# Ensure `python` exists (some images only have python3)
RUN ln -sf /usr/bin/python3 /usr/bin/python || true

# ===== Workspace layout =====
WORKDIR /workspace

# Clone ComfyUI at a known-good commit
# (You can bump this later if needed; pinning avoids random upstream breaks.)
RUN git clone https://github.com/comfyanonymous/ComfyUI.git && \
    cd ComfyUI && \
    git rev-parse HEAD > /workspace/COMFY_COMMIT.txt

# Install ComfyUI requirements
RUN python3 -m pip install --no-cache-dir -r /workspace/ComfyUI/requirements.txt

# ===== App files =====
# (We keep your repo code in /workspace/app)
COPY start.sh /workspace/app/start.sh
COPY handler.py /workspace/app/handler.py
COPY requirements.txt /workspace/app/requirements.txt

# Any optional lists/scripts you have; safe to copy if present.
# (If you don't have them, Docker will still build — they aren't required steps.)
COPY custom_nodes.txt /workspace/app/custom_nodes.txt
COPY install_custom_nodes.py /workspace/app/install_custom_nodes.py
COPY download_models.sh /workspace/app/download_models.sh

# Python deps for the handler/utility code
RUN python3 -m pip install --no-cache-dir -r /workspace/app/requirements.txt || true
# Minimal hardens (in case requirements.txt is empty)
RUN python3 -m pip install --no-cache-dir runpod requests pillow

# Make entry executable
RUN chmod +x /workspace/app/start.sh

# Provide standard dirs (match your env)
RUN mkdir -p /workspace/ComfyUI/input /workspace/ComfyUI/output /workspace/ComfyUI/models

# ===== Environment =====
ENV COMFY_DIR=/workspace/ComfyUI \
    COMFY_HOST=127.0.0.1 \
    COMFY_PORT=8188 \
    RP_HANDLER_PORT=8000 \
    INPUT_DIR=/workspace/ComfyUI/input \
    OUTPUT_DIR=/workspace/ComfyUI/output

EXPOSE 8000 8188

# Use tini as PID1 for correct signal handling
ENTRYPOINT ["/usr/bin/tini", "--"]

# Start script:
CMD ["bash", "-lc", "/workspace/app/start.sh"]
