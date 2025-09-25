# ========= Base image =========
# Stable + available image on RunPod hub
FROM runpod/pytorch:2.1.1-py3.10-cuda12.1.1-devel-ubuntu22.04

# Keep the workspace tidy
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /workspace

# ---------- System deps ----------
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git wget curl ca-certificates ffmpeg libgl1 \
 && rm -rf /var/lib/apt/lists/*

# ---------- Copy repo ----------
COPY . /workspace

# ---------- Python deps ----------
# Your requirements currently only include runpod libs; Comfy deps come from ComfyUI repo
RUN pip install --upgrade pip \
 && pip install -r /workspace/requirements.txt \
 && pip install runpod requests tenacity

# ---------- Get ComfyUI ----------
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git /workspace/ComfyUI \
 && pip install -r /workspace/ComfyUI/requirements.txt

# ---------- Install custom nodes ----------
RUN python3 /workspace/install_custom_nodes.py

# ---------- Download models your workflow needs ----------
RUN bash /workspace/download_models.sh

# Pre-create IO dirs so handler can drop inputs/outputs
RUN mkdir -p /workspace/inputs /workspace/outputs

# ---------- Ports ----------
# ComfyUI UI/API
EXPOSE 8188
# RunPod handler port (internal)
EXPOSE 8000

# ---------- Start both ----------
# start.sh launches ComfyUI in background, waits for health,
# then keeps handler in foreground (required by RunPod)
ENTRYPOINT ["/bin/bash", "/workspace/start.sh"]
